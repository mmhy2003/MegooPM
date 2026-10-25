"""Every generated server block emits to the live log relay, tagged with its host key.

Counted per block, not per file: a TLS host renders two servers, and one of
them missing the directives would leave half the host invisible on the page.
"""

from __future__ import annotations

import re

from app.services.nginx import render_config, render_stream_config
from app.services.nginx.state import (
    BackendSpec,
    CertificateSpec,
    DeadHostSpec,
    DefaultTlsSpec,
    DesiredState,
    ProxyHostSpec,
    RedirectionHostSpec,
    StreamSpec,
    UpstreamSpec,
)

CERT = CertificateSpec(
    id=3,
    fullchain_path="/etc/nginx/certs/3/fullchain.pem",
    privkey_path="/etc/nginx/certs/3/privkey.pem",
)
INCLUDE = "include /etc/nginx/logging-targets.conf;"


def _pool() -> UpstreamSpec:
    return UpstreamSpec(
        id=1,
        name="web-pool",
        lb_method="round_robin",
        backends=(BackendSpec(host="10.0.0.1", port=8080),),
    )


def _blocks(config: str) -> list[str]:
    """Each top-level `server {}` block, from its opening line to the next one."""
    starts = [m.start() for m in re.finditer(r"^server \{", config, re.M)]
    assert starts, "no server block rendered — the fixture is wrong, not the code"
    return [config[a:b] for a, b in zip(starts, [*starts[1:], len(config)], strict=True)]


def _assert_live(block: str, tag: str) -> None:
    assert INCLUDE in block, block
    assert f"access_log syslog:server=127.0.0.1:5140,tag={tag},nohostname megoopm_live;" in block
    assert f"error_log syslog:server=127.0.0.1:5140,tag={tag},nohostname info;" in block
    assert 'set $mgm_cs "";' in block, block


def _tls_proxy(**kw) -> dict[str, str]:
    host = ProxyHostSpec(
        id=7, domain_names=("a.example.com",), upstream_id=1, certificate=CERT, **kw
    )
    return render_config(DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(),)))


def test_every_proxy_server_block_logs_live() -> None:
    for forced in (False, True):
        blocks = _blocks(_tls_proxy(ssl_forced=forced)["megoopm-proxy-7.conf"])
        assert len(blocks) == 2
        for block in blocks:
            _assert_live(block, "mgm_p7")
    plain = ProxyHostSpec(id=7, domain_names=("a.example.com",), upstream_id=1)
    out = render_config(DesiredState(proxy_hosts=(plain,), http_upstreams=(_pool(),)))
    (block,) = _blocks(out["megoopm-proxy-7.conf"])
    _assert_live(block, "mgm_p7")


def test_every_redirection_server_block_logs_live() -> None:
    def host(**kw) -> RedirectionHostSpec:
        return RedirectionHostSpec(
            id=4,
            domain_names=("r.example.com",),
            forward_domain_name="example.com",
            forward_scheme="auto",
            forward_http_code=301,
            **kw,
        )

    plain = render_config(DesiredState(redirection_hosts=(host(),)))
    tls = render_config(DesiredState(redirection_hosts=(host(certificate=CERT),)))
    blocks = _blocks(plain["megoopm-redirect-4.conf"]) + _blocks(tls["megoopm-redirect-4.conf"])
    assert len(blocks) == 3
    for block in blocks:
        _assert_live(block, "mgm_r4")


def test_every_dead_server_block_logs_live() -> None:
    plain = render_config(DesiredState(dead_hosts=(DeadHostSpec(id=5, domain_names=("d.com",)),)))
    tls = render_config(
        DesiredState(dead_hosts=(DeadHostSpec(id=5, domain_names=("d.com",), certificate=CERT),))
    )
    blocks = _blocks(plain["megoopm-dead-5.conf"]) + _blocks(tls["megoopm-dead-5.conf"])
    assert len(blocks) == 3
    for block in blocks:
        _assert_live(block, "mgm_d5")


def test_default_tls_logs_live_as_unclaimed() -> None:
    spec = DefaultTlsSpec(certificate=CERT, server_names=("x.example.com",))
    out = render_config(DesiredState(default_tls=(spec,)))
    (block,) = _blocks(out["megoopm-default-tls-3.conf"])
    _assert_live(block, "mgm_u")


def test_a_stream_host_logs_live() -> None:
    stream = StreamSpec(id=9, incoming_port=5432, forward_host="10.0.0.1", forward_port=5432)
    out = render_stream_config(DesiredState(streams=(stream,)))
    (block,) = _blocks(out["megoopm-stream-9.conf"])
    assert "access_log syslog:server=127.0.0.1:5140,tag=mgm_s9,nohostname megoopm_stream;" in block
    assert "error_log syslog:server=127.0.0.1:5140,tag=mgm_s9,nohostname info;" in block
    # The http `combined` targets are not valid in stream{}: no format of that name.
    assert INCLUDE not in block
    assert "$mgm_cs" not in block


def test_the_targets_are_restated_before_the_live_one() -> None:
    blocks = _blocks(_tls_proxy(ssl_forced=False)["megoopm-proxy-7.conf"])
    blocks += _blocks(
        render_config(
            DesiredState(
                default_tls=(DefaultTlsSpec(certificate=CERT, server_names=("x.example.com",)),)
            )
        )["megoopm-default-tls-3.conf"]
    )
    for block in blocks:
        assert block.index(INCLUDE) < block.index("access_log syslog:server=127.0.0.1:5140")
        assert block.index(INCLUDE) < block.index("error_log syslog:server=127.0.0.1:5140")


def test_a_server_that_bounces_in_the_rewrite_phase_silences_the_unset_variable_warning() -> None:
    # The bouncer ends the request before `set $mgm_cs` runs; nginx would warn
    # on every denied request. Only blocks that can do that carry the line.
    forced = _blocks(_tls_proxy(ssl_forced=True, crowdsec_enabled=True)["megoopm-proxy-7.conf"])
    assert "uninitialized_variable_warn off;" in forced[0]
    assert "uninitialized_variable_warn off;" not in forced[1]
    spec = DefaultTlsSpec(certificate=CERT, server_names=("x.example.com",))
    (tls,) = _blocks(render_config(DesiredState(default_tls=(spec,)))["megoopm-default-tls-3.conf"])
    assert "uninitialized_variable_warn off;" in tls
