"""Every managed `server {}` block includes the error fragment.

Counted per block, not per file: a TLS host renders two servers, and one of
them silently missing the include is exactly the bug this catches.
"""

from __future__ import annotations

from app.services.nginx import render_config
from app.services.nginx.renderer import ERRORS_CONF
from app.services.nginx.state import (
    BackendSpec,
    CertificateSpec,
    DeadHostSpec,
    DesiredState,
    ProxyHostSpec,
    RedirectionHostSpec,
    UpstreamSpec,
)

CERT = CertificateSpec(
    id=3,
    fullchain_path="/etc/nginx/certs/3/fullchain.pem",
    privkey_path="/etc/nginx/certs/3/privkey.pem",
)


def _pool() -> UpstreamSpec:
    return UpstreamSpec(
        id=1,
        name="web-pool",
        lb_method="round_robin",
        backends=(BackendSpec(host="10.0.0.1", port=8080),),
    )


def _assert_every_block_includes(config: str) -> None:
    blocks = config.count("\nserver {")
    assert blocks > 0, "no server block rendered — the fixture is wrong, not the code"
    assert config.count(ERRORS_CONF) == blocks, config


def test_a_plain_proxy_host_includes_it() -> None:
    state = DesiredState(
        proxy_hosts=(ProxyHostSpec(id=1, domain_names=("a.example.com",), upstream_id=1),),
        http_upstreams=(_pool(),),
    )
    _assert_every_block_includes(render_config(state)["megoopm-proxy-1.conf"])


def test_a_tls_proxy_host_includes_it_in_both_servers() -> None:
    host = ProxyHostSpec(
        id=1, domain_names=("a.example.com",), upstream_id=1, certificate=CERT, ssl_forced=False
    )
    state = DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(),))
    config = render_config(state)["megoopm-proxy-1.conf"]
    assert config.count(ERRORS_CONF) == 2


def test_a_redirection_host_includes_it() -> None:
    state = DesiredState(
        redirection_hosts=(
            RedirectionHostSpec(
                id=1,
                domain_names=("r.example.com",),
                forward_domain_name="example.com",
                forward_scheme="auto",
                forward_http_code=301,
            ),
        )
    )
    _assert_every_block_includes(render_config(state)["megoopm-redirect-1.conf"])


def test_a_dead_host_includes_it() -> None:
    state = DesiredState(dead_hosts=(DeadHostSpec(id=1, domain_names=("d.example.com",)),))
    _assert_every_block_includes(render_config(state)["megoopm-dead-1.conf"])


def test_a_tls_dead_host_includes_it_in_both_servers() -> None:
    state = DesiredState(
        dead_hosts=(
            DeadHostSpec(id=1, domain_names=("d.example.com",), certificate=CERT, ssl_forced=False),
        )
    )
    assert render_config(state)["megoopm-dead-1.conf"].count(ERRORS_CONF) == 2
