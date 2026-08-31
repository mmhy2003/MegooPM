"""Turn a :class:`DesiredState` into nginx ``.conf`` file contents.

This module is a *pure* function of its inputs: given the same
:class:`DesiredState` it always returns byte-identical output, mapping a stable
filename to each managed object. That determinism is the foundation of the
engine's idempotency — the applier can compare rendered bytes against what is on
disk and skip the reload when nothing changed.

Nothing here touches the database or the filesystem, so the full matrix of
rendering behaviour (TLS, HSTS, websockets, load-balancing methods, …) is
unit-testable without any infrastructure.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

import app as app_pkg
from app.core.config import settings
from app.services.nginx.state import (
    AccessListSpec,
    DeadHostSpec,
    DesiredState,
    LocationSpec,
    ProxyHostSpec,
    RedirectionHostSpec,
    StreamSpec,
    UpstreamSpec,
)

TEMPLATES_DIR = Path(app_pkg.__file__).resolve().parent / "templates" / "nginx"

# nginx directive emitted at the top of an ``upstream`` block for each
# load-balancing method. round_robin is nginx's default and needs no directive.
_LB_DIRECTIVES = {
    "round_robin": "",
    "least_conn": "least_conn;",
    "ip_hash": "ip_hash;",
    "hash": "hash $remote_addr consistent;",
    "random": "random;",
}

# ip_hash exists only in http{}. Pool validation keeps it off stream-capable
# pools, so reaching the lookup miss below means a row was edited outside the
# API — fail loudly rather than emit config that breaks nginx -t on every node.
_STREAM_LB_DIRECTIVES = {k: v for k, v in _LB_DIRECTIVES.items() if k != "ip_hash"}


def _stream_directives(upstream: UpstreamSpec) -> dict[str, str]:
    if upstream.lb_method not in _STREAM_LB_DIRECTIVES:
        raise ValueError(
            f"pool {upstream.name!r} uses {upstream.lb_method}, which nginx's "
            "stream module does not support"
        )
    return _STREAM_LB_DIRECTIVES


@lru_cache(maxsize=1)
def _env() -> Environment:
    """Build the Jinja environment once and cache it."""
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,  # fail loudly on a typo'd template variable
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )


def pool_name(upstream_id: int) -> str:
    """The nginx ``upstream`` identifier for a pool id (referenced by hosts)."""
    return f"megoopm_upstream_{upstream_id}"


def htpasswd_filename(access_list_id: int) -> str:
    """Managed filename for an access list's htpasswd file (not a ``.conf``).

    Deliberately not ``*.conf`` so nginx's ``include conf.d/*.conf`` never tries
    to parse it as configuration; it is referenced only via ``auth_basic_user_file``.
    """
    return f"megoopm-access-{access_list_id}.htpasswd"


def htpasswd_path(access_list_id: int) -> str:
    """Absolute path an ``auth_basic_user_file`` directive should reference."""
    return f"{settings.nginx_confd_dir}/{htpasswd_filename(access_list_id)}"


def _render_htpasswd(access_list: AccessListSpec) -> str:
    """Render an access list's basic-auth users as htpasswd file contents."""
    lines = [f"{u.username}:{u.password_hash}" for u in access_list.auth_users]
    return "".join(f"{line}\n" for line in lines)


def _render_upstream(
    upstream: UpstreamSpec, directives: dict[str, str] | None = None
) -> str:
    directive = (directives or _LB_DIRECTIVES).get(upstream.lb_method, "")
    return _env().get_template("upstream.conf.j2").render(
        upstream=upstream,
        pool_name=pool_name(upstream.id),
        lb_directive=directive,
    )


def _target(spec: ProxyHostSpec | LocationSpec) -> str:
    """The ``proxy_pass`` destination: a pool name, or a literal ``host:port``.

    The template's ``proxy_block`` macro never inspects this — it only
    interpolates it after the scheme — so both shapes flow through one path and
    the template needs no branch of its own.
    """
    if spec.upstream_id is not None:
        return pool_name(spec.upstream_id)
    return f"{spec.forward_host}:{spec.forward_port}"


def _render_proxy_host(host: ProxyHostSpec) -> str:
    access_list = host.access_list
    return _env().get_template("server.conf.j2").render(
        host=host,
        target=_target(host),
        # Keyed by path, not upstream id: a host-targeted location has no id to
        # key on, and two of them would collide on None.
        location_targets={loc.path: _target(loc) for loc in host.locations},
        server_names=" ".join(host.domain_names),
        # Deployment-constant webroot the ACME HTTP-01 challenge location serves
        # from; matches where the issuer drops tokens (settings-driven, stable).
        acme_challenge_root=settings.acme_http_challenge_dir,
        # Absolute path of the host's htpasswd file, when its access list has any
        # basic-auth users; None otherwise (auth_basic gate is then omitted).
        htpasswd_path=(
            htpasswd_path(access_list.id)
            if access_list is not None and access_list.auth_users
            else None
        ),
    )


def _render_redirection_host(host: RedirectionHostSpec) -> str:
    return _env().get_template("redirect.conf.j2").render(
        host=host,
        server_names=" ".join(host.domain_names),
        acme_challenge_root=settings.acme_http_challenge_dir,
    )


def _render_dead_host(host: DeadHostSpec) -> str:
    return _env().get_template("dead.conf.j2").render(
        host=host,
        server_names=" ".join(host.domain_names),
        acme_challenge_root=settings.acme_http_challenge_dir,
    )


def _render_stream(stream: StreamSpec) -> str:
    return _env().get_template("stream.conf.j2").render(
        stream=stream,
        # StrictUndefined means the template cannot reference an absent name, so
        # this is always passed even on the host:port branch that ignores it.
        pool_name=pool_name(stream.upstream_id) if stream.upstream_id else "",
    )


def render_config(state: DesiredState) -> dict[str, str]:
    """Render the HTTP-context files to a ``{filename: contents}`` mapping.

    Covers upstreams, proxy hosts, redirection hosts and dead hosts — everything
    that lives inside nginx's ``http {}`` context (the shared ``conf.d`` dir).
    Streams render separately via :func:`render_stream_config` because TCP/UDP
    forwarding must live in the top-level ``stream {}`` context.

    Filenames are stable per object id so an update rewrites the same file
    rather than accumulating duplicates. The result is deterministic and the
    keys are returned in sorted order.
    """
    files: dict[str, str] = {}
    for upstream in state.http_upstreams:
        files[f"megoopm-upstream-{upstream.id}.conf"] = _render_upstream(upstream)
    for host in state.proxy_hosts:
        files[f"megoopm-proxy-{host.id}.conf"] = _render_proxy_host(host)
        # One htpasswd file per referenced access list that has basic-auth users.
        # Lists are shareable across hosts, so key by access-list id to dedupe.
        access_list = host.access_list
        if access_list is not None and access_list.auth_users:
            files[htpasswd_filename(access_list.id)] = _render_htpasswd(access_list)
    for redirect in state.redirection_hosts:
        files[f"megoopm-redirect-{redirect.id}.conf"] = _render_redirection_host(redirect)
    for dead in state.dead_hosts:
        files[f"megoopm-dead-{dead.id}.conf"] = _render_dead_host(dead)
    return {name: files[name] for name in sorted(files)}


def render_stream_config(state: DesiredState) -> dict[str, str]:
    """Render the ``stream {}``-context files to a ``{filename: contents}`` map.

    These are written to a separate directory the base config includes from the
    top-level ``stream {}`` block, keeping TCP/UDP forwards out of ``http {}``.
    Deterministic and sorted, mirroring :func:`render_config`.
    """
    files: dict[str, str] = {}
    for upstream in state.stream_upstreams:
        files[f"megoopm-upstream-{upstream.id}.conf"] = _render_upstream(
            upstream, _stream_directives(upstream)
        )
    for stream in state.streams:
        files[f"megoopm-stream-{stream.id}.conf"] = _render_stream(stream)
    return {name: files[name] for name in sorted(files)}


__all__ = [
    "render_config",
    "render_stream_config",
    "pool_name",
    "htpasswd_filename",
    "htpasswd_path",
    "TEMPLATES_DIR",
]
