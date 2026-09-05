"""nginx rendering of the ACME HTTP-01 challenge location (MEG-19)."""

from __future__ import annotations

from app.services.nginx import render_config
from app.services.nginx.state import (
    BackendSpec,
    CertificateSpec,
    DesiredState,
    ProxyHostSpec,
    UpstreamSpec,
)

_CHALLENGE_LOCATION = "location ^~ /.well-known/acme-challenge/"


def _pool() -> UpstreamSpec:
    return UpstreamSpec(id=1, name="p", backends=(BackendSpec(host="10.0.0.1", port=80),))


def _render(host: ProxyHostSpec) -> str:
    out = render_config(DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(),)))
    return out["megoopm-proxy-1.conf"]


def test_plain_host_serves_acme_challenge_on_port_80() -> None:
    host = ProxyHostSpec(id=1, domain_names=("example.com",), upstream_id=1)
    server = _render(host)
    assert _CHALLENGE_LOCATION in server
    assert "alias" in server


def test_ssl_forced_host_serves_challenge_before_redirect() -> None:
    cert = CertificateSpec(
        id=7,
        fullchain_path="/etc/nginx/certs/7/fullchain.pem",
        privkey_path="/etc/nginx/certs/7/privkey.pem",
    )
    host = ProxyHostSpec(
        id=1, domain_names=("example.com",), upstream_id=1, certificate=cert, ssl_forced=True
    )
    server = _render(host)

    # Challenge location appears, and the redirect is scoped to `location /` so
    # the challenge is not swept up by a server-level return.
    assert _CHALLENGE_LOCATION in server
    assert "location / {" in server
    assert "return 301 https://$host$request_uri;" in server
    # Challenge is served on :80 ahead of the redirect line.
    assert server.index(_CHALLENGE_LOCATION) < server.index("return 301")


def test_challenge_not_in_443_server() -> None:
    cert = CertificateSpec(
        id=7,
        fullchain_path="/etc/nginx/certs/7/fullchain.pem",
        privkey_path="/etc/nginx/certs/7/privkey.pem",
    )
    host = ProxyHostSpec(id=1, domain_names=("example.com",), upstream_id=1, certificate=cert)
    server = _render(host)
    # Split at the 443 server; the challenge belongs only to the :80 block.
    tls_block = server.split("listen 443")[1]
    assert _CHALLENGE_LOCATION not in tls_block


def test_renewal_changes_the_rendered_config() -> None:
    """A renewal must produce a different config, or no node ever reloads.

    Renewal rewrites fullchain.pem/privkey.pem in place, so the paths — and
    before the fingerprint, the whole rendered file — were byte-identical.
    ``apply_config`` then reported ``changed=False``, skipping the reload and the
    ``config_version`` bump, so every node kept serving the expiring certificate
    from memory until an unrelated edit happened to trigger a reload.
    """

    def render_with(fingerprint: str) -> str:
        cert = CertificateSpec(
            id=7,
            fullchain_path="/data/certs/7/fullchain.pem",
            privkey_path="/data/certs/7/privkey.pem",
            fingerprint=fingerprint,
        )
        return _render(
            ProxyHostSpec(
                id=1,
                domain_names=("example.com",),
                upstream_id=1,
                certificate=cert,
            )
        )

    before = render_with("aaaaaaaaaaaaaaaa")
    after = render_with("bbbbbbbbbbbbbbbb")

    assert before != after
    # The paths themselves are unchanged — the fingerprint is the only signal.
    assert "/data/certs/7/fullchain.pem" in before
    assert "/data/certs/7/fullchain.pem" in after


def test_identical_material_renders_identically() -> None:
    """Idempotency must survive: same material, same bytes, no needless reload."""
    cert = CertificateSpec(
        id=7,
        fullchain_path="/data/certs/7/fullchain.pem",
        privkey_path="/data/certs/7/privkey.pem",
        fingerprint="cafecafecafecafe",
    )
    host = ProxyHostSpec(id=1, domain_names=("example.com",), upstream_id=1, certificate=cert)
    assert _render(host) == _render(host)
