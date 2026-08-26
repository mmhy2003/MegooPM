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
from app.services.nginx.state import DesiredState, ProxyHostSpec, UpstreamSpec

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


def _render_upstream(upstream: UpstreamSpec) -> str:
    directive = _LB_DIRECTIVES.get(upstream.lb_method, "")
    return _env().get_template("upstream.conf.j2").render(
        upstream=upstream,
        pool_name=pool_name(upstream.id),
        lb_directive=directive,
    )


def _render_proxy_host(host: ProxyHostSpec) -> str:
    return _env().get_template("server.conf.j2").render(
        host=host,
        pool_name=pool_name(host.upstream_id),
        server_names=" ".join(host.domain_names),
        # Deployment-constant webroot the ACME HTTP-01 challenge location serves
        # from; matches where the issuer drops tokens (settings-driven, stable).
        acme_challenge_root=settings.acme_http_challenge_dir,
    )


def render_config(state: DesiredState) -> dict[str, str]:
    """Render ``state`` to a ``{filename: contents}`` mapping.

    Filenames are stable per object id so an update rewrites the same file
    rather than accumulating duplicates. The result is deterministic and the
    keys are returned in sorted order.
    """
    files: dict[str, str] = {}
    for upstream in state.upstreams:
        files[f"megoopm-upstream-{upstream.id}.conf"] = _render_upstream(upstream)
    for host in state.proxy_hosts:
        files[f"megoopm-proxy-{host.id}.conf"] = _render_proxy_host(host)
    return {name: files[name] for name in sorted(files)}


__all__ = ["render_config", "pool_name", "TEMPLATES_DIR"]
