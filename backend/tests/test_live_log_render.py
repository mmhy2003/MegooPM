"""Every generated server block emits to the live log relay, keyed with its host key.

Counted per block, not per file: a TLS host renders two servers, and one of
them missing the directives would leave half the host invisible on the page.

Access lines go out through one access log at the http (stream) level of
infra/nginx/nginx.conf, which every block inherits; a block only names its key
(``set $mgm_key``). A block that declared an access log of its own would stop
inheriting that one, and each syslog target it named would be one more socket
per worker, per host. The error log stays per block, tagged with the key.
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
INCLUDE = "include /etc/nginx/logging-error-targets.conf;"


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


def _relay_error_log(key: str) -> str:
    return f"error_log syslog:server=127.0.0.1:5140,tag=mgm_{key},nohostname info;"


def _assert_live(block: str, key: str) -> None:
    assert block.count(f"set $mgm_key {key};") == 1, block
    assert 'set $mgm_cs "";' in block, block
    assert block.count(INCLUDE) == 1, block
    assert block.count(_relay_error_log(key)) == 1, block
    # The shared http-level access log carries this block's lines; one here
    # would replace it (and the stdout and CrowdSec targets with it).
    assert "access_log" not in block, block
    # The combined file restates the access targets too.
    assert "logging-targets.conf" not in block, block


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
            _assert_live(block, "p7")
    plain = ProxyHostSpec(id=7, domain_names=("a.example.com",), upstream_id=1)
    out = render_config(DesiredState(proxy_hosts=(plain,), http_upstreams=(_pool(),)))
    (block,) = _blocks(out["megoopm-proxy-7.conf"])
    _assert_live(block, "p7")


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
        _assert_live(block, "r4")


def test_every_dead_server_block_logs_live() -> None:
    plain = render_config(DesiredState(dead_hosts=(DeadHostSpec(id=5, domain_names=("d.com",)),)))
    tls = render_config(
        DesiredState(dead_hosts=(DeadHostSpec(id=5, domain_names=("d.com",), certificate=CERT),))
    )
    blocks = _blocks(plain["megoopm-dead-5.conf"]) + _blocks(tls["megoopm-dead-5.conf"])
    assert len(blocks) == 3
    for block in blocks:
        _assert_live(block, "d5")


def test_default_tls_logs_live_as_unclaimed() -> None:
    spec = DefaultTlsSpec(certificate=CERT, server_names=("x.example.com",))
    out = render_config(DesiredState(default_tls=(spec,)))
    (block,) = _blocks(out["megoopm-default-tls-3.conf"])
    _assert_live(block, "u")


def test_a_stream_host_logs_live() -> None:
    stream = StreamSpec(id=9, incoming_port=5432, forward_host="10.0.0.1", forward_port=5432)
    out = render_stream_config(DesiredState(streams=(stream,)))
    (block,) = _blocks(out["megoopm-stream-9.conf"])
    assert block.count("set $mgm_key s9;") == 1
    assert block.count(_relay_error_log("s9")) == 1
    # stream{} has one access log for every server, like http{}.
    assert "access_log" not in block
    # The targets files are http{} directives (the `combined` format).
    assert "logging-" not in block
    assert "$mgm_cs" not in block
    # The one target a stream server loses by declaring an error log.
    file_log = "error_log /usr/local/openresty/nginx/logs/error.log warn;"
    assert block.index(file_log) < block.index(_relay_error_log("s9"))


def test_the_error_targets_are_restated_before_the_live_one() -> None:
    blocks = _blocks(_tls_proxy(ssl_forced=False)["megoopm-proxy-7.conf"])
    blocks += _blocks(
        render_config(
            DesiredState(
                default_tls=(DefaultTlsSpec(certificate=CERT, server_names=("x.example.com",)),)
            )
        )["megoopm-default-tls-3.conf"]
    )
    for block in blocks:
        assert block.index("set $mgm_key") < block.index(INCLUDE)
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


def _rewrite_hook(key: str) -> str:
    return f'server_rewrite_by_lua_block {{ require("megoopm_crowdsec_check").check("{key}") }}'


def test_a_rewrite_phase_bouncer_names_the_key_itself() -> None:
    # The hook runs before the block's `set $mgm_key`, and a denial ends the
    # request there: without the key, the denied request's line would land on
    # Unclaimed (the map default in nginx.conf) instead of its host.
    forced = _blocks(_tls_proxy(ssl_forced=True, crowdsec_enabled=True)["megoopm-proxy-7.conf"])
    assert forced[0].count(_rewrite_hook("p7")) == 1
    assert "server_rewrite_by_lua" not in forced[1]
    redirect = RedirectionHostSpec(
        id=4,
        domain_names=("r.example.com",),
        forward_domain_name="example.com",
        forward_scheme="auto",
        forward_http_code=301,
        certificate=CERT,
    )
    blocks = _blocks(
        render_config(DesiredState(redirection_hosts=(redirect,)))["megoopm-redirect-4.conf"]
    )
    assert [b.count(_rewrite_hook("r4")) for b in blocks] == [1, 1]
    dead = DeadHostSpec(id=5, domain_names=("d.com",), certificate=CERT)
    blocks = _blocks(render_config(DesiredState(dead_hosts=(dead,)))["megoopm-dead-5.conf"])
    assert [b.count(_rewrite_hook("d5")) for b in blocks] == [1, 1]
