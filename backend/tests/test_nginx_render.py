"""Rendering tests for the nginx config generator.

The renderer is a pure function of a :class:`DesiredState`, so these exercise
the full feature matrix — load-balancing methods, TLS, HSTS, websockets,
exploit blocking, caching, advanced config — with no database or filesystem.
"""

from __future__ import annotations

import pytest
from app.services.nginx import render_config
from app.services.nginx.renderer import render_default_site
from app.services.nginx.state import (
    BackendSpec,
    BanPageSpec,
    CertificateSpec,
    DefaultSiteSpec,
    DefaultTlsSpec,
    DesiredState,
    LocationSpec,
    ProxyHostSpec,
    UpstreamSpec,
)


def _pool(**kw) -> UpstreamSpec:
    base = {
        "id": 1,
        "name": "web-pool",
        "lb_method": "round_robin",
        "backends": (BackendSpec(host="10.0.0.1", port=8080),),
    }
    base.update(kw)
    return UpstreamSpec(**base)


def _host(**kw) -> ProxyHostSpec:
    base = {"id": 1, "domain_names": ("example.com", "www.example.com"), "upstream_id": 1}
    base.update(kw)
    return ProxyHostSpec(**base)


def test_filenames_are_stable_per_object() -> None:
    state = DesiredState(proxy_hosts=(_host(),), http_upstreams=(_pool(),))
    files = render_config(state)
    assert set(files) == {"megoopm-upstream-1.conf", "megoopm-proxy-1.conf"}


def test_upstream_lists_backends_with_tuning() -> None:
    pool = _pool(
        backends=(
            BackendSpec(host="10.0.0.1", port=8080, weight=5, max_fails=3, fail_timeout_seconds=20),
            BackendSpec(host="10.0.0.2", port=8080, backup=True),
            BackendSpec(host="10.0.0.3", port=8080, down=True),
        )
    )
    out = render_config(DesiredState(http_upstreams=(pool,), proxy_hosts=(_host(),)))
    up = out["megoopm-upstream-1.conf"]
    assert "upstream megoopm_upstream_1 {" in up
    assert "server 10.0.0.1:8080 weight=5 max_fails=3 fail_timeout=20s;" in up
    assert "server 10.0.0.2:8080 weight=1 max_fails=1 fail_timeout=10s backup;" in up
    assert "server 10.0.0.3:8080 weight=1 max_fails=1 fail_timeout=10s down;" in up


def test_lb_method_directives() -> None:
    def directive(method: str) -> str:
        out = render_config(
            DesiredState(http_upstreams=(_pool(lb_method=method),), proxy_hosts=(_host(),))
        )
        return out["megoopm-upstream-1.conf"]

    assert "least_conn;" in directive("least_conn")
    assert "ip_hash;" in directive("ip_hash")
    assert "hash $remote_addr consistent;" in directive("hash")
    assert "random;" in directive("random")
    # round_robin is nginx's default and emits no directive line.
    rr = directive("round_robin")
    assert "least_conn" not in rr and "ip_hash" not in rr


def test_plain_http_host_proxies_to_pool() -> None:
    out = render_config(DesiredState(proxy_hosts=(_host(),), http_upstreams=(_pool(),)))
    server = out["megoopm-proxy-1.conf"]
    assert "listen 80;" in server
    assert "server_name example.com www.example.com;" in server
    assert "proxy_pass http://megoopm_upstream_1;" in server
    # No certificate → no TLS server block.
    assert "listen 443" not in server


def test_tls_host_emits_https_server_and_redirect() -> None:
    cert = CertificateSpec(
        id=7,
        fullchain_path="/etc/nginx/certs/7/fullchain.pem",
        privkey_path="/etc/nginx/certs/7/privkey.pem",
    )
    host = _host(
        certificate=cert,
        ssl_forced=True,
        http2_support=True,
        hsts_enabled=True,
        hsts_subdomains=True,
    )
    out = render_config(DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(),)))
    server = out["megoopm-proxy-1.conf"]
    assert "listen 443 ssl http2;" in server
    assert "ssl_certificate /etc/nginx/certs/7/fullchain.pem;" in server
    assert "ssl_certificate_key /etc/nginx/certs/7/privkey.pem;" in server
    # ssl_forced makes the :80 server redirect to https.
    assert "return 301 https://$host$request_uri;" in server
    # HSTS with subdomains.
    assert "includeSubDomains" in server


def test_websocket_and_exploit_and_cache_flags() -> None:
    host = _host(
        allow_websocket_upgrade=True,
        block_exploits=True,
        caching_enabled=True,
    )
    server = render_config(
        DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(),))
    )["megoopm-proxy-1.conf"]
    assert "proxy_set_header Upgrade $http_upgrade;" in server
    assert "proxy_set_header Connection $connection_upgrade;" in server
    assert "return 403;" in server  # exploit blocking rules
    assert "expires 1d;" in server  # asset caching location


def test_crowdsec_bouncer_renders_per_host() -> None:
    off = render_config(
        DesiredState(proxy_hosts=(_host(),), http_upstreams=(_pool(),))
    )["megoopm-proxy-1.conf"]
    # Disabled by default: no bouncer directives leak into the config.
    assert "access_by_lua_file" not in off
    assert "megoopm_crowdsec_appsec" not in off

    on = render_config(
        DesiredState(proxy_hosts=(_host(crowdsec_enabled=True),), http_upstreams=(_pool(),))
    )["megoopm-proxy-1.conf"]
    assert "access_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;" in on
    # Bouncer on, AppSec off.
    assert "set $megoopm_crowdsec_appsec off;" in on


def test_crowdsec_appsec_toggle_renders() -> None:
    host = _host(crowdsec_enabled=True, crowdsec_appsec_enabled=True)
    server = render_config(
        DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(),))
    )["megoopm-proxy-1.conf"]
    # The per-host flag still renders the `$megoopm_crowdsec_appsec` marker, but
    # AppSec enforcement is global (see docs/crowdsec.md, MEG-32/D3): the marker
    # is reserved for a future per-host reintroduction, not gated on today. The
    # bouncer handler is wired regardless.
    assert "set $megoopm_crowdsec_appsec on;" in server
    assert "access_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;" in server


def test_crowdsec_appsec_requires_bouncer() -> None:
    # AppSec on but bouncer off → nothing renders (AppSec is meaningless alone).
    host = _host(crowdsec_enabled=False, crowdsec_appsec_enabled=True)
    server = render_config(
        DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(),))
    )["megoopm-proxy-1.conf"]
    assert "megoopm_crowdsec_appsec" not in server
    assert "access_by_lua_file" not in server


def test_crowdsec_applies_to_tls_and_redirect_servers() -> None:
    cert = CertificateSpec(
        id=7,
        fullchain_path="/etc/nginx/certs/7/fullchain.pem",
        privkey_path="/etc/nginx/certs/7/privkey.pem",
    )
    host = _host(certificate=cert, ssl_forced=True, crowdsec_enabled=True)
    server = render_config(
        DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(),))
    )["megoopm-proxy-1.conf"]
    # Both the :80 redirect server and the :443 server enforce the bouncer, so
    # a banned IP is blocked even before the HTTPS redirect.
    assert server.count("access_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;") == 2


def test_advanced_config_is_injected() -> None:
    host = _host(advanced_config="client_max_body_size 50m;")
    server = render_config(
        DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(),))
    )["megoopm-proxy-1.conf"]
    assert "client_max_body_size 50m;" in server


def test_render_is_deterministic() -> None:
    state = DesiredState(proxy_hosts=(_host(),), http_upstreams=(_pool(),))
    assert render_config(state) == render_config(state)


def test_extra_locations_render_prefix_blocks_per_pool() -> None:
    api_pool = _pool(id=2, name="api-pool")
    host = _host(
        allow_websocket_upgrade=True,
        locations=(LocationSpec(path="/api/", upstream_id=2, forward_scheme="https"),),
    )
    out = render_config(DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(), api_pool)))
    server = out["megoopm-proxy-1.conf"]
    # Root keeps its plain prefix location; the extra one uses ^~ so it beats the
    # cache-assets regex location for paths under it.
    assert "location / {" in server
    assert "location ^~ /api/ {" in server
    assert "proxy_pass http://megoopm_upstream_1;" in server
    assert "proxy_pass https://megoopm_upstream_2;" in server
    # Host-wide extras apply to every location.
    assert server.count("proxy_set_header Upgrade $http_upgrade;") == 2
    assert server.count("proxy_http_version 1.1;") == 2
    assert "megoopm-upstream-2.conf" in out


def test_extra_locations_appear_in_both_servers_of_a_tls_host() -> None:
    cert = CertificateSpec(
        id=3,
        fullchain_path="/etc/nginx/certs/3/fullchain.pem",
        privkey_path="/etc/nginx/certs/3/privkey.pem",
    )
    host = _host(
        certificate=cert,
        ssl_forced=False,
        locations=(LocationSpec(path="/ws", upstream_id=2),),
    )
    out = render_config(DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(), _pool(id=2))))
    server = out["megoopm-proxy-1.conf"]
    assert server.count("location ^~ /ws {") == 2  # :80 and :443 servers
    assert server.count("proxy_pass http://megoopm_upstream_2;") == 2


def test_cache_location_is_unchanged_with_extra_locations() -> None:
    host = _host(caching_enabled=True, locations=(LocationSpec(path="/api/", upstream_id=2),))
    out = render_config(DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(), _pool(id=2))))
    server = out["megoopm-proxy-1.conf"]
    assert server.count("expires 1d;") == 1
    assert "location ^~ /api/ {" in server


# --- pools render into the context they belong to ---------------------------


def test_stream_pools_render_into_the_stream_directory() -> None:
    """A pool for a stream must not be emitted into http{}, nor vice versa.

    nginx upstream blocks are context-local: one defined in http{} is invisible
    to stream{}. Splitting the field means render_config cannot emit a
    stream-only pool by accident.
    """
    from app.services.nginx import render_stream_config

    pool = UpstreamSpec(id=9, name="db", backends=(BackendSpec(host="10.0.0.9", port=5432),))
    state = DesiredState(stream_upstreams=(pool,))

    http_files = render_config(state)
    stream_files = render_stream_config(state)

    assert "megoopm-upstream-9.conf" not in http_files
    assert "megoopm-upstream-9.conf" in stream_files
    assert "upstream megoopm_upstream_9 {" in stream_files["megoopm-upstream-9.conf"]


def test_http_pools_stay_out_of_the_stream_directory() -> None:
    from app.services.nginx import render_stream_config

    pool = UpstreamSpec(id=4, name="web", backends=(BackendSpec(host="10.0.0.4", port=8080),))
    state = DesiredState(http_upstreams=(pool,))

    assert "megoopm-upstream-4.conf" in render_config(state)
    assert "megoopm-upstream-4.conf" not in render_stream_config(state)


def test_a_shared_pool_renders_into_both_directories() -> None:
    """Separate namespaces, so the same nginx name in each is not a collision."""
    from app.services.nginx import render_stream_config

    pool = UpstreamSpec(id=7, name="shared", backends=(BackendSpec(host="10.0.0.7", port=99),))
    state = DesiredState(http_upstreams=(pool,), stream_upstreams=(pool,))

    assert "megoopm-upstream-7.conf" in render_config(state)
    assert "megoopm-upstream-7.conf" in render_stream_config(state)


def test_pooled_stream_proxies_to_the_pool() -> None:
    from app.services.nginx import render_stream_config
    from app.services.nginx.state import StreamSpec

    pool = UpstreamSpec(id=9, name="db", backends=(BackendSpec(host="10.0.0.9", port=5432),))
    stream = StreamSpec(id=1, incoming_port=5432, upstream_id=9, tcp_forwarding=True)

    files = render_stream_config(DesiredState(streams=(stream,), stream_upstreams=(pool,)))

    assert "proxy_pass megoopm_upstream_9;" in files["megoopm-stream-1.conf"]
    assert "upstream megoopm_upstream_9 {" in files["megoopm-upstream-9.conf"]


def test_host_target_stream_is_unchanged() -> None:
    from app.services.nginx import render_stream_config
    from app.services.nginx.state import StreamSpec

    stream = StreamSpec(
        id=1,
        incoming_port=5432,
        forward_host="db.internal",
        forward_port=5432,
        tcp_forwarding=True,
    )

    files = render_stream_config(DesiredState(streams=(stream,)))

    assert "proxy_pass db.internal:5432;" in files["megoopm-stream-1.conf"]


def test_ip_hash_is_refused_in_the_stream_context() -> None:
    """Validation should make this unreachable; a hand-edited row must not
    silently emit a directive that breaks nginx -t on every node."""
    from app.services.nginx import render_stream_config

    pool = UpstreamSpec(
        id=3, name="bad", lb_method="ip_hash", backends=(BackendSpec(host="10.0.0.3", port=1),)
    )
    with pytest.raises(ValueError, match="ip_hash"):
        render_stream_config(DesiredState(stream_upstreams=(pool,)))


# --- a proxy host may forward to a literal backend --------------------------


def test_host_target_renders_a_literal_backend() -> None:
    host = ProxyHostSpec(
        id=1, domain_names=("a.example.com",), forward_host="10.0.0.1", forward_port=8080
    )
    out = render_config(DesiredState(proxy_hosts=(host,)))["megoopm-proxy-1.conf"]
    assert "proxy_pass http://10.0.0.1:8080;" in out
    # No pool is referenced, so no upstream block should exist for this host.
    assert "megoopm_upstream_" not in out


def test_pool_target_is_unchanged() -> None:
    host = ProxyHostSpec(id=1, domain_names=("a.example.com",), upstream_id=1)
    out = render_config(DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(),)))
    assert "proxy_pass http://megoopm_upstream_1;" in out["megoopm-proxy-1.conf"]


def test_host_target_honours_the_forward_scheme() -> None:
    host = ProxyHostSpec(
        id=1,
        domain_names=("a.example.com",),
        forward_host="10.0.0.1",
        forward_port=8443,
        forward_scheme="https",
    )
    out = render_config(DesiredState(proxy_hosts=(host,)))["megoopm-proxy-1.conf"]
    assert "proxy_pass https://10.0.0.1:8443;" in out


def test_locations_render_both_target_kinds() -> None:
    """A host may mix a pooled location with a literal-backend one."""
    from app.services.nginx.state import LocationSpec

    host = ProxyHostSpec(
        id=1,
        domain_names=("a.example.com",),
        upstream_id=1,
        locations=(
            LocationSpec(path="/api", upstream_id=2),
            LocationSpec(path="/img", forward_host="10.0.0.9", forward_port=9000),
        ),
    )
    out = render_config(
        DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(id=1), _pool(id=2)))
    )["megoopm-proxy-1.conf"]

    assert "proxy_pass http://megoopm_upstream_2;" in out
    assert "proxy_pass http://10.0.0.9:9000;" in out


# --- The default site over TLS --------------------------------------------


def _default_tls(**kw) -> DefaultTlsSpec:
    base = {
        "certificate": CertificateSpec(
            id=3,
            fullchain_path="/data/certs/3/fullchain.pem",
            privkey_path="/data/certs/3/privkey.pem",
            fingerprint="abc123",
        ),
        "server_names": ("disabled.example.com", "*.example.com"),
    }
    base.update(kw)
    return DefaultTlsSpec(**base)


def test_default_tls_block_is_named_per_certificate() -> None:
    files = render_config(DesiredState(default_tls=(_default_tls(),)))
    assert set(files) == {"megoopm-default-tls-3.conf"}


def test_default_tls_block_serves_the_names_on_443_with_the_certificate() -> None:
    conf = render_config(DesiredState(default_tls=(_default_tls(),)))[
        "megoopm-default-tls-3.conf"
    ]
    assert "listen 443 ssl;" in conf
    assert "server_name disabled.example.com *.example.com;" in conf
    assert "ssl_certificate /data/certs/3/fullchain.pem;" in conf
    assert "ssl_certificate_key /data/certs/3/privkey.pem;" in conf


def test_default_tls_block_includes_the_existing_default_site_fragment() -> None:
    """Reusing the fragment is what makes the Settings choice apply to HTTPS."""
    conf = render_config(DesiredState(default_tls=(_default_tls(),)))[
        "megoopm-default-tls-3.conf"
    ]
    assert "include" in conf
    assert "*.conf;" in conf


def test_default_tls_block_records_the_certificate_material() -> None:
    """Renewal rewrites the files in place; without this the rendered config is
    unchanged and no node reloads onto the new certificate."""
    conf = render_config(DesiredState(default_tls=(_default_tls(),)))[
        "megoopm-default-tls-3.conf"
    ]
    assert "# cert-material 3:abc123" in conf
    other = render_config(
        DesiredState(
            default_tls=(
                _default_tls(
                    certificate=CertificateSpec(
                        id=3,
                        fullchain_path="/data/certs/3/fullchain.pem",
                        privkey_path="/data/certs/3/privkey.pem",
                        fingerprint="def456",
                    )
                ),
            )
        )
    )["megoopm-default-tls-3.conf"]
    assert conf != other


def test_default_tls_block_has_a_root_so_nothing_falls_through_to_openresty() -> None:
    """Without it an unmatched request is served OpenResty's welcome page."""
    conf = render_config(DesiredState(default_tls=(_default_tls(),)))[
        "megoopm-default-tls-3.conf"
    ]
    assert "root /var/empty/megoopm;" in conf


def test_no_default_tls_blocks_when_there_are_none() -> None:
    assert render_config(DesiredState()) == {}


# --- The CrowdSec ban page -------------------------------------------------


def test_ban_page_writes_the_megoopm_document() -> None:
    files = render_default_site(DesiredState(ban_page=BanPageSpec(mode="megoopm")))
    assert "megoopm-ban.html" in files
    assert "<html" in files["megoopm-ban.html"].lower()


def test_ban_page_writes_the_referenced_custom_page() -> None:
    files = render_default_site(
        DesiredState(ban_page=BanPageSpec(mode="custom_page", html="<h1>Blocked</h1>"))
    )
    assert files["megoopm-ban.html"] == "<h1>Blocked</h1>"


def test_ban_page_none_writes_no_file_at_all() -> None:
    """An empty file would be served as a blank page with a 403; ban.lua guards
    on the file EXISTING, so the absence is what restores the bare 403."""
    files = render_default_site(DesiredState(ban_page=BanPageSpec(mode="none")))
    assert "megoopm-ban.html" not in files


def test_ban_page_custom_mode_with_a_missing_document_writes_no_file() -> None:
    """A blank white page reads as a broken deployment; the bare 403 does not."""
    files = render_default_site(
        DesiredState(ban_page=BanPageSpec(mode="custom_page", html=""))
    )
    assert "megoopm-ban.html" not in files


def test_the_megoopm_ban_document_leaks_nothing_about_the_decision() -> None:
    """It is static — the bouncer emits it verbatim — so anything specific in it
    would be a lie, and an IP or ban duration would help someone probing."""
    body = render_default_site(DesiredState(ban_page=BanPageSpec(mode="megoopm")))[
        "megoopm-ban.html"
    ].lower()
    for leak in ("{{", "duration", "your ip", "expires"):
        assert leak not in body


def test_a_default_site_and_a_ban_page_coexist_in_one_directory() -> None:
    """They share a reconciliation target; neither may displace the other."""
    files = render_default_site(
        DesiredState(
            default_site=DefaultSiteSpec(mode="not_found"),
            ban_page=BanPageSpec(mode="megoopm"),
        )
    )
    assert {"megoopm-default.conf", "megoopm-ban.html"} <= set(files)


def test_the_ban_page_is_written_even_with_no_default_site() -> None:
    """The two settings are independent; an early return for one must not
    silently disable the other."""
    files = render_default_site(DesiredState(ban_page=BanPageSpec(mode="megoopm")))
    assert "megoopm-ban.html" in files
