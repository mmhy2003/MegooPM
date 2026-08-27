"""Rendering tests for the nginx config generator.

The renderer is a pure function of a :class:`DesiredState`, so these exercise
the full feature matrix — load-balancing methods, TLS, HSTS, websockets,
exploit blocking, caching, advanced config — with no database or filesystem.
"""

from __future__ import annotations

from app.services.nginx import render_config
from app.services.nginx.state import (
    BackendSpec,
    CertificateSpec,
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
    state = DesiredState(proxy_hosts=(_host(),), upstreams=(_pool(),))
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
    out = render_config(DesiredState(upstreams=(pool,), proxy_hosts=(_host(),)))
    up = out["megoopm-upstream-1.conf"]
    assert "upstream megoopm_upstream_1 {" in up
    assert "server 10.0.0.1:8080 weight=5 max_fails=3 fail_timeout=20s;" in up
    assert "server 10.0.0.2:8080 weight=1 max_fails=1 fail_timeout=10s backup;" in up
    assert "server 10.0.0.3:8080 weight=1 max_fails=1 fail_timeout=10s down;" in up


def test_lb_method_directives() -> None:
    def directive(method: str) -> str:
        out = render_config(
            DesiredState(upstreams=(_pool(lb_method=method),), proxy_hosts=(_host(),))
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
    out = render_config(DesiredState(proxy_hosts=(_host(),), upstreams=(_pool(),)))
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
    out = render_config(DesiredState(proxy_hosts=(host,), upstreams=(_pool(),)))
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
        DesiredState(proxy_hosts=(host,), upstreams=(_pool(),))
    )["megoopm-proxy-1.conf"]
    assert "proxy_set_header Upgrade $http_upgrade;" in server
    assert "proxy_set_header Connection $connection_upgrade;" in server
    assert "return 403;" in server  # exploit blocking rules
    assert "expires 1d;" in server  # asset caching location


def test_crowdsec_bouncer_renders_per_host() -> None:
    off = render_config(
        DesiredState(proxy_hosts=(_host(),), upstreams=(_pool(),))
    )["megoopm-proxy-1.conf"]
    # Disabled by default: no bouncer directives leak into the config.
    assert "access_by_lua_file" not in off
    assert "megoopm_crowdsec_appsec" not in off

    on = render_config(
        DesiredState(proxy_hosts=(_host(crowdsec_enabled=True),), upstreams=(_pool(),))
    )["megoopm-proxy-1.conf"]
    assert "access_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;" in on
    # Bouncer on, AppSec off.
    assert "set $megoopm_crowdsec_appsec off;" in on


def test_crowdsec_appsec_toggle_renders() -> None:
    host = _host(crowdsec_enabled=True, crowdsec_appsec_enabled=True)
    server = render_config(
        DesiredState(proxy_hosts=(host,), upstreams=(_pool(),))
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
        DesiredState(proxy_hosts=(host,), upstreams=(_pool(),))
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
        DesiredState(proxy_hosts=(host,), upstreams=(_pool(),))
    )["megoopm-proxy-1.conf"]
    # Both the :80 redirect server and the :443 server enforce the bouncer, so
    # a banned IP is blocked even before the HTTPS redirect.
    assert server.count("access_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;") == 2


def test_advanced_config_is_injected() -> None:
    host = _host(advanced_config="client_max_body_size 50m;")
    server = render_config(
        DesiredState(proxy_hosts=(host,), upstreams=(_pool(),))
    )["megoopm-proxy-1.conf"]
    assert "client_max_body_size 50m;" in server


def test_render_is_deterministic() -> None:
    state = DesiredState(proxy_hosts=(_host(),), upstreams=(_pool(),))
    assert render_config(state) == render_config(state)


def test_extra_locations_render_prefix_blocks_per_pool() -> None:
    api_pool = _pool(id=2, name="api-pool")
    host = _host(
        allow_websocket_upgrade=True,
        locations=(LocationSpec(path="/api/", upstream_id=2, forward_scheme="https"),),
    )
    out = render_config(DesiredState(proxy_hosts=(host,), upstreams=(_pool(), api_pool)))
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
    out = render_config(DesiredState(proxy_hosts=(host,), upstreams=(_pool(), _pool(id=2))))
    server = out["megoopm-proxy-1.conf"]
    assert server.count("location ^~ /ws {") == 2  # :80 and :443 servers
    assert server.count("proxy_pass http://megoopm_upstream_2;") == 2


def test_cache_location_is_unchanged_with_extra_locations() -> None:
    host = _host(caching_enabled=True, locations=(LocationSpec(path="/api/", upstream_id=2),))
    out = render_config(DesiredState(proxy_hosts=(host,), upstreams=(_pool(), _pool(id=2))))
    server = out["megoopm-proxy-1.conf"]
    assert server.count("expires 1d;") == 1
    assert "location ^~ /api/ {" in server
