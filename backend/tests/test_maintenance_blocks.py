"""How a host under maintenance renders.

Every assertion here corresponds to a draft that failed against
openresty/openresty:1.25.3.2-alpine-fat. See the spec's table: a server-level
`if` bypasses error_page entirely, and the mapping placed after the errors
include silently loses to the branded 503.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.nginx.renderer import ERRORS_CONF, MAINTENANCE_HTML, render_config
from app.services.nginx.state import (
    BackendSpec,
    CertificateSpec,
    DesiredState,
    LocationSpec,
    MaintenanceSpec,
    ProxyHostSpec,
    UpstreamSpec,
)


def _pool(id: int = 1) -> UpstreamSpec:
    return UpstreamSpec(id=id, name=f"pool-{id}", backends=(BackendSpec(host="10.0.0.5", port=80),))


def _render(**over) -> str:
    host = ProxyHostSpec(id=7, domain_names=("app.example.com",), upstream_id=1, **over)
    state = DesiredState(
        proxy_hosts=(host,),
        http_upstreams=(_pool(),),
        maintenance=MaintenanceSpec(mode="megoopm", retry_after_minutes=15),
    )
    return render_config(state)["megoopm-proxy-7.conf"]


def test_a_host_not_under_maintenance_is_unchanged() -> None:
    conf = _render()
    assert "mgm_maint" not in conf
    assert MAINTENANCE_HTML not in conf


def test_the_bypass_list_becomes_a_geo_block() -> None:
    conf = _render(maintenance_enabled=True, maintenance_allow=("203.0.113.5", "10.0.0.0/8"))

    # Named per host id: two hosts under maintenance must not collide.
    assert "geo $mgm_maint_7 {" in conf
    assert "default 1;" in conf
    assert "203.0.113.5 0;" in conf
    assert "10.0.0.0/8 0;" in conf


def test_every_location_is_guarded() -> None:
    conf = _render(
        maintenance_enabled=True,
        caching_enabled=True,
        locations=(LocationSpec(path="/api/", upstream_id=1),),
    )

    # The root route, the extra location and the asset-cache location. A
    # server-level guard would be tidier and does not work: a return from the
    # rewrite phase bypasses error_page and nginx serves its own body.
    assert conf.count("if ($mgm_maint_7) { return 503; }") == 3


def test_the_acme_challenge_is_never_guarded() -> None:
    """A host left under maintenance must still renew its certificate.

    Maintenance lasts hours or days and renewals are time-sensitive, so
    guarding this turns planned downtime into an expired certificate days
    later, far from the change that caused it.
    """
    conf = _render(maintenance_enabled=True)

    acme = conf[conf.index("/.well-known/acme-challenge/") :]
    acme = acme[: acme.index("}")]
    assert "mgm_maint" not in acme


def test_the_maintenance_mapping_comes_before_the_errors_include() -> None:
    """The ordering the whole feature depends on.

    At one configuration level the *first* error_page for a status wins.
    Reversed, the branded 503 is served and maintenance silently does nothing
    — a config that starts cleanly and is wrong.
    """
    conf = _render(maintenance_enabled=True)

    assert conf.index(f"error_page 503 /{MAINTENANCE_HTML};") < conf.index(ERRORS_CONF)


def test_the_document_is_internal_and_carries_retry_after() -> None:
    conf = _render(maintenance_enabled=True)

    assert f"location = /{MAINTENANCE_HTML} {{" in conf
    assert "internal;" in conf
    assert f"root {settings.nginx_default_dir};" in conf
    # 15 minutes, as configured. Retry-After is seconds.
    assert "add_header Retry-After 900 always;" in conf


def test_both_servers_of_a_tls_host_are_guarded() -> None:
    cert = CertificateSpec(
        id=1,
        fullchain_path="/data/certs/1/fullchain.pem",
        privkey_path="/data/certs/1/privkey.pem",
    )
    conf = _render(maintenance_enabled=True, certificate=cert)

    # One geo block for the file, but the mapping in each server: the :80
    # server serves the proxy too when ssl_forced is off.
    assert conf.count("geo $mgm_maint_7 {") == 1
    assert conf.count(f"error_page 503 /{MAINTENANCE_HTML};") == 2
