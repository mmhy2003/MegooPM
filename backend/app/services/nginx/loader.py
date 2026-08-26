"""Build a :class:`DesiredState` from the database — the single source of truth.

Two entry points share one mapping:

* :func:`load_desired_state` — async, for request handlers that already hold an
  :class:`AsyncSession` (e.g. the config-preview endpoint).
* :func:`load_desired_state_sync` — a synchronous wrapper for the Celery task,
  which runs outside FastAPI's event loop. It spins up a short-lived async
  engine so the reload worker needs no separate sync database driver.

Only *enabled* objects contribute to the config, and a host whose pool has no
usable backend is skipped rather than emitted as an invalid (empty)
``upstream`` block — one bad host must not poison the whole reload.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.access_list import AccessList
from app.models.dead_host import DeadHost
from app.models.proxy_host import ProxyHost
from app.models.redirection_host import RedirectionHost
from app.models.stream import Stream
from app.models.upstream import Upstream
from app.services.nginx.state import (
    AccessListSpec,
    AuthUserSpec,
    BackendSpec,
    CertificateSpec,
    ClientRuleSpec,
    DeadHostSpec,
    DesiredState,
    ProxyHostSpec,
    RedirectionHostSpec,
    StreamSpec,
    UpstreamSpec,
)


def _certificate_spec(certificate, certs_dir: str) -> CertificateSpec:
    return CertificateSpec(
        id=certificate.id,
        fullchain_path=f"{certs_dir}/{certificate.id}/fullchain.pem",
        privkey_path=f"{certs_dir}/{certificate.id}/privkey.pem",
    )


def _access_list_spec(access_list: AccessList) -> AccessListSpec:
    return AccessListSpec(
        id=access_list.id,
        name=access_list.name,
        satisfy_any=access_list.satisfy_any,
        pass_auth=access_list.pass_auth,
        auth_users=tuple(
            AuthUserSpec(username=u.username, password_hash=u.password_hash)
            for u in sorted(access_list.auth_users, key=lambda u: u.username)
        ),
        client_rules=tuple(
            ClientRuleSpec(directive=str(c.directive), address=c.address)
            for c in sorted(access_list.client_rules, key=lambda c: c.id)
        ),
    )


def _upstream_spec(upstream: Upstream) -> UpstreamSpec:
    backends = tuple(
        BackendSpec(
            host=b.host,
            port=b.port,
            weight=b.weight,
            max_fails=b.max_fails,
            fail_timeout_seconds=b.fail_timeout_seconds,
            backup=b.backup,
            down=b.down,
        )
        for b in sorted(upstream.backends, key=lambda b: b.id)
        if b.enabled
    )
    return UpstreamSpec(
        id=upstream.id,
        name=upstream.name,
        lb_method=str(upstream.lb_method),
        backends=backends,
    )


async def load_desired_state(
    session: AsyncSession, *, certs_dir: str | None = None
) -> DesiredState:
    """Read enabled proxy hosts (and their pools) into a :class:`DesiredState`."""
    certs_dir = certs_dir or settings.nginx_certs_dir

    stmt = (
        select(ProxyHost)
        .where(ProxyHost.enabled.is_(True))
        .options(
            selectinload(ProxyHost.upstream).selectinload(Upstream.backends),
            selectinload(ProxyHost.certificate),
            selectinload(ProxyHost.access_list).selectinload(AccessList.auth_users),
            selectinload(ProxyHost.access_list).selectinload(AccessList.client_rules),
        )
        .order_by(ProxyHost.id)
    )
    hosts = (await session.scalars(stmt)).all()

    upstreams: dict[int, UpstreamSpec] = {}
    host_specs: list[ProxyHostSpec] = []

    for host in hosts:
        pool = host.upstream
        if pool is None or not pool.enabled:
            continue  # nothing healthy to forward to
        if pool.id not in upstreams:
            upstreams[pool.id] = _upstream_spec(pool)
        if not upstreams[pool.id].backends:
            continue  # empty pool → skip host rather than emit an invalid block

        certificate = (
            _certificate_spec(host.certificate, certs_dir)
            if host.certificate is not None
            else None
        )
        host_specs.append(
            ProxyHostSpec(
                id=host.id,
                domain_names=tuple(host.domain_names),
                upstream_id=pool.id,
                forward_scheme=str(host.forward_scheme),
                certificate=certificate,
                access_list=(
                    _access_list_spec(host.access_list)
                    if host.access_list is not None
                    else None
                ),
                ssl_forced=host.ssl_forced,
                http2_support=host.http2_support,
                hsts_enabled=host.hsts_enabled,
                hsts_subdomains=host.hsts_subdomains,
                caching_enabled=host.caching_enabled,
                block_exploits=host.block_exploits,
                allow_websocket_upgrade=host.allow_websocket_upgrade,
                crowdsec_enabled=host.crowdsec_enabled,
                crowdsec_appsec_enabled=host.crowdsec_appsec_enabled,
                advanced_config=host.advanced_config,
            )
        )

    # Only emit pools actually referenced by an included host, in id order.
    referenced = {h.upstream_id for h in host_specs}
    upstream_specs = tuple(upstreams[i] for i in sorted(referenced))

    redirection_specs = await _load_redirection_hosts(session, certs_dir)
    dead_specs = await _load_dead_hosts(session, certs_dir)
    stream_specs = await _load_streams(session, certs_dir)

    return DesiredState(
        proxy_hosts=tuple(host_specs),
        upstreams=upstream_specs,
        redirection_hosts=redirection_specs,
        dead_hosts=dead_specs,
        streams=stream_specs,
    )


async def _load_redirection_hosts(
    session: AsyncSession, certs_dir: str
) -> tuple[RedirectionHostSpec, ...]:
    stmt = (
        select(RedirectionHost)
        .where(RedirectionHost.enabled.is_(True))
        .options(selectinload(RedirectionHost.certificate))
        .order_by(RedirectionHost.id)
    )
    rows = (await session.scalars(stmt)).all()
    return tuple(
        RedirectionHostSpec(
            id=r.id,
            domain_names=tuple(r.domain_names),
            forward_domain_name=r.forward_domain_name,
            forward_http_code=r.forward_http_code,
            forward_scheme=str(r.forward_scheme),
            preserve_path=r.preserve_path,
            certificate=(
                _certificate_spec(r.certificate, certs_dir)
                if r.certificate is not None
                else None
            ),
            ssl_forced=r.ssl_forced,
            http2_support=r.http2_support,
            hsts_enabled=r.hsts_enabled,
            hsts_subdomains=r.hsts_subdomains,
            block_exploits=r.block_exploits,
            advanced_config=r.advanced_config,
        )
        for r in rows
    )


async def _load_dead_hosts(session: AsyncSession, certs_dir: str) -> tuple[DeadHostSpec, ...]:
    stmt = (
        select(DeadHost)
        .where(DeadHost.enabled.is_(True))
        .options(selectinload(DeadHost.certificate))
        .order_by(DeadHost.id)
    )
    rows = (await session.scalars(stmt)).all()
    return tuple(
        DeadHostSpec(
            id=d.id,
            domain_names=tuple(d.domain_names),
            certificate=(
                _certificate_spec(d.certificate, certs_dir)
                if d.certificate is not None
                else None
            ),
            ssl_forced=d.ssl_forced,
            http2_support=d.http2_support,
            hsts_enabled=d.hsts_enabled,
            hsts_subdomains=d.hsts_subdomains,
            advanced_config=d.advanced_config,
        )
        for d in rows
    )


async def _load_streams(session: AsyncSession, certs_dir: str) -> tuple[StreamSpec, ...]:
    stmt = (
        select(Stream)
        .where(Stream.enabled.is_(True))
        .options(selectinload(Stream.certificate))
        .order_by(Stream.id)
    )
    rows = (await session.scalars(stmt)).all()
    return tuple(
        StreamSpec(
            id=s.id,
            incoming_port=s.incoming_port,
            forward_host=s.forward_host,
            forward_port=s.forward_port,
            tcp_forwarding=s.tcp_forwarding,
            udp_forwarding=s.udp_forwarding,
            certificate=(
                _certificate_spec(s.certificate, certs_dir)
                if s.certificate is not None
                else None
            ),
        )
        for s in rows
    )


def load_desired_state_sync(
    *, database_url: str | None = None, certs_dir: str | None = None
) -> DesiredState:
    """Synchronous loader for the Celery worker (no ambient event loop)."""
    url = database_url or settings.database_url

    async def _run() -> DesiredState:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                return await load_desired_state(session, certs_dir=certs_dir)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


__all__ = ["load_desired_state", "load_desired_state_sync"]
