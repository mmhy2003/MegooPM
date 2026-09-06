"""The maintenance document: written when needed, swept when not."""

from __future__ import annotations

from app.services.nginx.renderer import MAINTENANCE_HTML, render_default_site
from app.services.nginx.state import (
    BackendSpec,
    DesiredState,
    MaintenanceSpec,
    ProxyHostSpec,
    UpstreamSpec,
)


def _pool(id: int = 1) -> UpstreamSpec:
    return UpstreamSpec(id=id, name=f"pool-{id}", backends=(BackendSpec(host="10.0.0.5", port=80),))


def _host(**over) -> ProxyHostSpec:
    return ProxyHostSpec(id=1, domain_names=("app.example.com",), upstream_id=1, **over)


def test_no_host_under_maintenance_writes_no_document() -> None:
    # The shared directory is reconciled by prefix, so an unrendered file is
    # swept. Writing one nobody serves would leave a page behind for good.
    files = render_default_site(
        DesiredState(
            proxy_hosts=(_host(),),
            http_upstreams=(_pool(),),
            maintenance=MaintenanceSpec(mode="megoopm"),
        )
    )
    assert MAINTENANCE_HTML not in files


def test_the_shipped_page_is_written_for_a_host_under_maintenance() -> None:
    files = render_default_site(
        DesiredState(
            proxy_hosts=(_host(maintenance_enabled=True),),
            http_upstreams=(_pool(),),
            maintenance=MaintenanceSpec(mode="megoopm"),
        )
    )

    page = files[MAINTENANCE_HTML]
    assert "<!doctype html>" in page.lower()
    # Its own message, not the 503's: planned work, not a failure.
    assert "maintenance" in page.lower()


def test_a_custom_page_is_used_verbatim() -> None:
    files = render_default_site(
        DesiredState(
            proxy_hosts=(_host(maintenance_enabled=True),),
            http_upstreams=(_pool(),),
            maintenance=MaintenanceSpec(mode="custom_page", html="<h1>Back soon</h1>"),
        )
    )
    assert files[MAINTENANCE_HTML] == "<h1>Back soon</h1>"


def test_a_custom_page_that_went_missing_falls_back_to_the_shipped_one() -> None:
    # An empty maintenance page is worse than a generic one: the visitor gets
    # a blank screen and cannot tell whether anything is wrong.
    files = render_default_site(
        DesiredState(
            proxy_hosts=(_host(maintenance_enabled=True),),
            http_upstreams=(_pool(),),
            maintenance=MaintenanceSpec(mode="custom_page", html=""),
        )
    )
    assert "maintenance" in files[MAINTENANCE_HTML].lower()


def test_the_page_makes_no_external_request() -> None:
    """It is reachable by anyone and served when things are already broken.

    A webfont or a CDN script would leave it half-rendered exactly when it
    matters, and would tell a third party who is visiting.
    """
    files = render_default_site(
        DesiredState(
            proxy_hosts=(_host(maintenance_enabled=True),),
            http_upstreams=(_pool(),),
            maintenance=MaintenanceSpec(mode="megoopm"),
        )
    )
    page = files[MAINTENANCE_HTML]

    assert "http://" not in page
    assert "https://" not in page
    for src in page.split('src="')[1:]:
        assert src.startswith("data:")


def test_the_page_names_nothing_about_the_instance() -> None:
    # Anyone can see it, and naming a backend tells a prober how this is built.
    files = render_default_site(
        DesiredState(
            proxy_hosts=(_host(maintenance_enabled=True),),
            http_upstreams=(_pool(),),
            maintenance=MaintenanceSpec(mode="megoopm"),
        )
    )
    page = files[MAINTENANCE_HTML]

    assert "app.example.com" not in page
    assert "10.0.0.5" not in page
