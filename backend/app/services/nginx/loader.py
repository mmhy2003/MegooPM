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
import hashlib
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.access_list import AccessList
from app.models.certificate import Certificate
from app.models.custom_page import CustomPage
from app.models.dead_host import DeadHost
from app.models.enums import (
    CertificateStatus,
    CrowdSecBanMode,
    DefaultSiteMode,
    LocationTarget,
)
from app.models.error_page import ErrorPage
from app.models.instance_settings import InstanceSettings
from app.models.proxy_host import ProxyHost, ProxyHostLocation
from app.models.redirection_host import RedirectionHost
from app.models.stream import Stream
from app.models.upstream import Upstream
from app.services.nginx.default_tls import claimed_tls_names, plan_default_tls
from app.services.nginx.state import (
    AccessListSpec,
    AuthUserSpec,
    BackendSpec,
    BanPageSpec,
    CertificateSpec,
    ClientRuleSpec,
    DeadHostSpec,
    DefaultSiteSpec,
    DesiredState,
    ErrorPageSpec,
    LocationSpec,
    ProxyHostSpec,
    RedirectionHostSpec,
    StreamSpec,
    UpstreamSpec,
)


def _certificate_fingerprint(certificate) -> str:
    """A short, stable id for the material currently on disk for this cert.

    Built from the columns issuance updates — ``expires_on`` and
    ``meta['issued_at']`` — so it changes on every renewal and on nothing else.
    Deterministic across nodes (same row in the shared database), so two nodes
    rendering the same state still produce byte-identical config.
    """
    meta = certificate.meta or {}
    expires = certificate.expires_on.isoformat() if certificate.expires_on else ""
    material = f"{expires}|{meta.get('issued_at', '')}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _certificate_spec(certificate, certs_dir: str) -> CertificateSpec:
    return CertificateSpec(
        id=certificate.id,
        fullchain_path=f"{certs_dir}/{certificate.id}/fullchain.pem",
        privkey_path=f"{certs_dir}/{certificate.id}/privkey.pem",
        fingerprint=_certificate_fingerprint(certificate),
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
            selectinload(ProxyHost.locations)
            .selectinload(ProxyHostLocation.upstream)
            .selectinload(Upstream.backends),
            # The document of a custom-page location is read during the render.
            selectinload(ProxyHost.locations).selectinload(ProxyHostLocation.custom_page),
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
        # Only a pool-targeted host can be unrenderable for pool reasons. A host
        # forwarding to a literal backend has no pool by design, so running
        # these checks against it would drop it from the config entirely — the
        # site would stop being served with nothing reporting an error.
        if host.upstream_id is not None:
            pool = host.upstream
            if pool is None or not pool.enabled:
                continue  # nothing healthy to forward to
            if pool.id not in upstreams:
                upstreams[pool.id] = _upstream_spec(pool)
            if not upstreams[pool.id].backends:
                continue  # empty pool → skip host rather than emit an invalid block

        certificate = (
            _certificate_spec(host.certificate, certs_dir) if host.certificate is not None else None
        )
        location_specs: list[LocationSpec] = []
        for location in sorted(host.locations, key=lambda loc: loc.path):
            # As for the host: only a pool-targeted location can be dropped for
            # pool reasons. A literal backend has no pool to be missing.
            if location.target is LocationTarget.pool and location.upstream_id is not None:
                loc_pool = location.upstream
                if loc_pool is None or not loc_pool.enabled:
                    continue
                if loc_pool.id not in upstreams:
                    upstreams[loc_pool.id] = _upstream_spec(loc_pool)
                if not upstreams[loc_pool.id].backends:
                    continue  # empty pool → drop this location, keep the host
            # Dereferenced here, so the renderer stays a pure function of
            # explicit data. A missing page means the row was edited outside
            # the API (the FK is RESTRICT); render an empty document rather
            # than dropping the whole host's config.
            html = ""
            if location.target is LocationTarget.custom_page:
                page = location.custom_page
                html = page.html if page is not None else ""
            location_specs.append(
                LocationSpec(
                    path=location.path,
                    target=str(location.target),
                    upstream_id=location.upstream_id,
                    forward_host=location.forward_host,
                    forward_port=location.forward_port,
                    forward_scheme=str(location.forward_scheme),
                    id=location.id,
                    html=html,
                )
            )
        host_specs.append(
            ProxyHostSpec(
                id=host.id,
                domain_names=tuple(host.domain_names),
                # From the row, not the loop's `pool`: that is only bound on the
                # pool-targeted branch and would otherwise leak the previous
                # iteration's value into a host-targeted row.
                upstream_id=host.upstream_id,
                forward_host=host.forward_host,
                forward_port=host.forward_port,
                forward_scheme=str(host.forward_scheme),
                certificate=certificate,
                access_list=(
                    _access_list_spec(host.access_list) if host.access_list is not None else None
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
                locations=tuple(location_specs),
            )
        )

    # Only emit pools actually referenced by an included host, in id order.
    # These render into http{}; stream-referenced pools are collected separately.
    referenced = {h.upstream_id for h in host_specs if h.upstream_id is not None}
    referenced |= {
        loc.upstream_id for h in host_specs for loc in h.locations if loc.upstream_id is not None
    }
    upstream_specs = tuple(upstreams[i] for i in sorted(referenced))

    redirection_specs = await _load_redirection_hosts(session, certs_dir)
    dead_specs = await _load_dead_hosts(session, certs_dir)
    stream_specs, stream_upstream_specs = await _load_streams(session, certs_dir)
    default_site = await _load_default_site(session)
    ban_page = await _load_ban_page(session)
    error_pages = await _load_error_pages(session)

    state = DesiredState(
        proxy_hosts=tuple(host_specs),
        http_upstreams=upstream_specs,
        redirection_hosts=redirection_specs,
        dead_hosts=dead_specs,
        streams=stream_specs,
        stream_upstreams=stream_upstream_specs,
        default_site=default_site,
        ban_page=ban_page,
        error_pages=error_pages,
    )
    # Built from the finished state so the claimed-name set comes from exactly
    # the specs that render :443 blocks — the two cannot drift.
    certificates = await _load_certificates(session)
    return replace(
        state,
        default_tls=plan_default_tls(certificates, claimed_tls_names(state), certs_dir),
    )


async def _load_certificates(session: AsyncSession) -> tuple[Certificate, ...]:
    """Every active certificate.

    Status gates it because a ``pending`` row's files are not on disk yet, and
    referencing one fails ``nginx -t``, which rolls back the entire apply for
    the instance. Ordered by id so the render is deterministic across nodes.
    """
    rows = await session.scalars(
        select(Certificate)
        .where(Certificate.status == CertificateStatus.active)
        .order_by(Certificate.id)
    )
    return tuple(rows)


async def _load_error_pages(session: AsyncSession) -> tuple[ErrorPageSpec, ...]:
    """Configured codes only, with each document dereferenced.

    A row whose page has gone missing (edited outside the API — the FK is
    RESTRICT) yields an empty ``html``, which the renderer reads as "use the
    shipped page". An empty error page would be worse than a generic one.
    """
    stmt = select(ErrorPage).options(selectinload(ErrorPage.custom_page))
    rows = (await session.scalars(stmt)).all()
    specs = [
        ErrorPageSpec(
            code=row.code,
            html=row.custom_page.html if row.custom_page is not None else "",
        )
        for row in rows
    ]
    return tuple(sorted(specs, key=lambda spec: spec.code))


async def _load_ban_page(session: AsyncSession) -> BanPageSpec | None:
    """Read the ban-page setting, resolving a referenced page into its HTML.

    Dereferenced here for the same reason the default site is: the renderer
    stays a pure function of explicit data.
    """
    row = await session.get(InstanceSettings, 1)
    if row is None:
        return None

    html = ""
    if row.crowdsec_ban_mode is CrowdSecBanMode.custom_page and row.crowdsec_ban_page_id:
        page = await session.get(CustomPage, row.crowdsec_ban_page_id)
        # The FK is RESTRICT, so a missing page means the row was edited outside
        # the API. Leaving html empty makes the renderer write no file, which
        # degrades to the bare 403 rather than to a blank white page.
        html = page.html if page is not None else ""

    return BanPageSpec(mode=row.crowdsec_ban_mode.value, html=html)


async def _load_default_site(session: AsyncSession) -> DefaultSiteSpec | None:
    """Read the default-site setting, resolving a referenced page into its HTML.

    The page is dereferenced *here* so the renderer stays a pure function of
    explicit data. ``None`` (no settings row at all) means no file is written
    and nginx falls back to its own no-location-match 404.
    """
    row = await session.get(InstanceSettings, 1)
    if row is None:
        return None

    html = ""
    if row.default_site_mode is DefaultSiteMode.custom_page and row.default_site_page_id:
        page = await session.get(CustomPage, row.default_site_page_id)
        # The FK is RESTRICT, so a missing page means the row was edited outside
        # the API. Render an empty document rather than dropping the whole config.
        html = page.html if page is not None else ""

    return DefaultSiteSpec(
        mode=row.default_site_mode.value,
        redirect_url=row.default_site_redirect_url or "",
        html=html,
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
                _certificate_spec(r.certificate, certs_dir) if r.certificate is not None else None
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
                _certificate_spec(d.certificate, certs_dir) if d.certificate is not None else None
            ),
            ssl_forced=d.ssl_forced,
            http2_support=d.http2_support,
            hsts_enabled=d.hsts_enabled,
            hsts_subdomains=d.hsts_subdomains,
            advanced_config=d.advanced_config,
        )
        for d in rows
    )


async def _load_streams(
    session: AsyncSession, certs_dir: str
) -> tuple[tuple[StreamSpec, ...], tuple[UpstreamSpec, ...]]:
    """Enabled streams plus the pools they reference, for the stream context.

    A stream whose pool is disabled or has no usable backend is skipped, exactly
    as a proxy host with an empty pool is: emitting a ``server`` block that names
    a non-existent ``upstream`` fails ``nginx -t`` and rolls back the whole
    apply, so dropping the one broken object is strictly better.
    """
    stmt = (
        select(Stream)
        .where(Stream.enabled.is_(True))
        .options(
            selectinload(Stream.certificate),
            selectinload(Stream.upstream).selectinload(Upstream.backends),
        )
        .order_by(Stream.id)
    )
    rows = (await session.scalars(stmt)).all()

    pools: dict[int, UpstreamSpec] = {}
    specs: list[StreamSpec] = []
    for s in rows:
        if s.upstream_id is not None:
            pool = s.upstream
            if pool is None or not pool.enabled:
                continue  # nothing healthy to forward to
            if pool.id not in pools:
                pools[pool.id] = _upstream_spec(pool)
            if not pools[pool.id].backends:
                continue  # empty pool → skip the stream, not the whole apply
        specs.append(
            StreamSpec(
                id=s.id,
                incoming_port=s.incoming_port,
                forward_host=s.forward_host,
                forward_port=s.forward_port,
                upstream_id=s.upstream_id,
                tcp_forwarding=s.tcp_forwarding,
                udp_forwarding=s.udp_forwarding,
                certificate=(
                    _certificate_spec(s.certificate, certs_dir)
                    if s.certificate is not None
                    else None
                ),
            )
        )
    # Only pools an *included* stream actually references, in id order.
    referenced = {sp.upstream_id for sp in specs if sp.upstream_id is not None}
    return tuple(specs), tuple(pools[i] for i in sorted(referenced))


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
