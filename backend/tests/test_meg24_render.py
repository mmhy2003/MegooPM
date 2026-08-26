"""Rendering tests for MEG-24: streams, redirection hosts, dead (404) hosts.

The renderer is a pure function of a :class:`DesiredState`, so these exercise
the full feature matrix (TLS, HSTS, redirect codes/schemes, TCP/UDP) with no
database or filesystem. A separate real-``nginx -t`` test confirms the combined
http{} + stream{} output actually parses when the binary is available.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from app.services.nginx import render_config, render_stream_config
from app.services.nginx.state import (
    CertificateSpec,
    DeadHostSpec,
    DesiredState,
    RedirectionHostSpec,
    StreamSpec,
)

_CERT = CertificateSpec(
    id=7,
    fullchain_path="/etc/nginx/certs/7/fullchain.pem",
    privkey_path="/etc/nginx/certs/7/privkey.pem",
)


# --- Redirection hosts -----------------------------------------------------


def _redirect(**kw) -> RedirectionHostSpec:
    base = {
        "id": 1,
        "domain_names": ("old.example.com",),
        "forward_domain_name": "new.example.com",
    }
    base.update(kw)
    return RedirectionHostSpec(**base)


def test_redirect_default_302_preserves_path_and_scheme() -> None:
    out = render_config(DesiredState(redirection_hosts=(_redirect(),)))
    conf = out["megoopm-redirect-1.conf"]
    assert "listen 80;" in conf
    assert "server_name old.example.com;" in conf
    # auto scheme -> $scheme; default 302; preserve path -> $request_uri.
    assert "return 302 $scheme://new.example.com$request_uri;" in conf
    assert "listen 443" not in conf


def test_redirect_301_forced_https_scheme_no_path() -> None:
    out = render_config(
        DesiredState(
            redirection_hosts=(
                _redirect(forward_http_code=301, forward_scheme="https", preserve_path=False),
            )
        )
    )
    conf = out["megoopm-redirect-1.conf"]
    assert "return 301 https://new.example.com;" in conf
    # No path preservation → no $request_uri appended to the target.
    assert "new.example.com$request_uri" not in conf


def test_redirect_tls_emits_https_server_and_redirect() -> None:
    out = render_config(
        DesiredState(
            redirection_hosts=(_redirect(certificate=_CERT, ssl_forced=True, hsts_enabled=True),)
        )
    )
    conf = out["megoopm-redirect-1.conf"]
    assert "listen 443 ssl;" in conf
    assert "ssl_certificate /etc/nginx/certs/7/fullchain.pem;" in conf
    # ssl_forced :80 bounces to https; the :443 server does the real redirect.
    assert "return 301 https://$host$request_uri;" in conf
    assert "return 302 $scheme://new.example.com$request_uri;" in conf
    assert "Strict-Transport-Security" in conf


# --- Dead (404) hosts ------------------------------------------------------


def _dead(**kw) -> DeadHostSpec:
    base = {"id": 2, "domain_names": ("parked.example.com",)}
    base.update(kw)
    return DeadHostSpec(**base)


def test_dead_host_returns_404_on_plain_80() -> None:
    out = render_config(DesiredState(dead_hosts=(_dead(),)))
    conf = out["megoopm-dead-2.conf"]
    assert "listen 80;" in conf
    assert "server_name parked.example.com;" in conf
    assert "return 404;" in conf
    assert "listen 443" not in conf


def test_dead_host_tls_returns_404_over_https() -> None:
    out = render_config(DesiredState(dead_hosts=(_dead(certificate=_CERT, http2_support=True),)))
    conf = out["megoopm-dead-2.conf"]
    assert "listen 443 ssl http2;" in conf
    assert "ssl_certificate /etc/nginx/certs/7/fullchain.pem;" in conf
    assert "return 404;" in conf


# --- Streams (TCP/UDP) -----------------------------------------------------


def _stream(**kw) -> StreamSpec:
    base = {"id": 3, "incoming_port": 5432, "forward_host": "10.0.0.5", "forward_port": 5432}
    base.update(kw)
    return StreamSpec(**base)


def test_stream_tcp_only_renders_valid_block() -> None:
    out = render_stream_config(DesiredState(streams=(_stream(),)))
    conf = out["megoopm-stream-3.conf"]
    assert "server {" in conf
    assert "listen 5432;" in conf
    assert "proxy_pass 10.0.0.5:5432;" in conf
    # TCP only → no udp listener.
    assert "udp;" not in conf
    # Streams are NOT part of the http{} conf.d output.
    assert "megoopm-stream-3.conf" not in render_config(DesiredState(streams=(_stream(),)))


def test_stream_udp_and_both_protocols() -> None:
    udp = render_stream_config(
        DesiredState(streams=(_stream(tcp_forwarding=False, udp_forwarding=True),))
    )["megoopm-stream-3.conf"]
    assert "listen 5432 udp;" in udp
    assert udp.count("listen 5432") == 1  # only the udp listener

    both = render_stream_config(
        DesiredState(streams=(_stream(tcp_forwarding=True, udp_forwarding=True),))
    )["megoopm-stream-3.conf"]
    assert "listen 5432;" in both
    assert "listen 5432 udp;" in both


def test_stream_tls_terminates_on_tcp_listener() -> None:
    conf = render_stream_config(DesiredState(streams=(_stream(certificate=_CERT),)))[
        "megoopm-stream-3.conf"
    ]
    assert "listen 5432 ssl;" in conf
    assert "ssl_certificate /etc/nginx/certs/7/fullchain.pem;" in conf
    assert "ssl_certificate_key /etc/nginx/certs/7/privkey.pem;" in conf


def test_render_is_deterministic() -> None:
    state = DesiredState(
        redirection_hosts=(_redirect(),), dead_hosts=(_dead(),), streams=(_stream(),)
    )
    assert render_config(state) == render_config(state)
    assert render_stream_config(state) == render_stream_config(state)


@pytest.mark.skipif(shutil.which("nginx") is None, reason="nginx binary not installed")
def test_generated_config_passes_real_nginx_t(tmp_path: Path) -> None:
    """The combined http{} + stream{} output must pass a real ``nginx -t``."""
    from app.services.nginx.controller import ShellNginxController

    state = DesiredState(
        redirection_hosts=(_redirect(),),
        dead_hosts=(_dead(),),
        streams=(_stream(tcp_forwarding=True, udp_forwarding=True),),
    )

    confd = tmp_path / "conf.d"
    streamd = confd / "stream"
    streamd.mkdir(parents=True)
    for name, content in render_config(state).items():
        (confd / name).write_text(content)
    for name, content in render_stream_config(state).items():
        (streamd / name).write_text(content)

    main_conf = tmp_path / "nginx.conf"
    main_conf.write_text(
        "events {}\n"
        "http {\n"
        f"  include {confd}/*.conf;\n"
        "}\n"
        "stream {\n"
        f"  include {streamd}/*.conf;\n"
        "}\n"
    )
    ctrl = ShellNginxController(test_command=f"nginx -t -c {main_conf}")
    assert ctrl.test().ok
