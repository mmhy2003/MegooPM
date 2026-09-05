"""Rendering tests for access lists (MEG-21): auth_basic + allow/deny.

Pure-function tests over the renderer — no database or filesystem. They cover
the htpasswd sidecar file, the auth_basic / allow-deny / satisfy directives, the
Authorization-stripping behaviour, the ACME-challenge bypass, and access-list
sharing across hosts.
"""

from __future__ import annotations

from app.services.nginx import render_config
from app.services.nginx.renderer import htpasswd_path
from app.services.nginx.state import (
    AccessListSpec,
    AuthUserSpec,
    BackendSpec,
    ClientRuleSpec,
    DesiredState,
    ProxyHostSpec,
    UpstreamSpec,
)

# A stable apr1 hash (salt "abcd1234", password "pw") so assertions are exact.
_HASH = "$apr1$abcd1234$UEURWw71lGBk.LwDG1Xr4/"


def _pool(id_: int = 1) -> UpstreamSpec:
    return UpstreamSpec(id=id_, name="pool", backends=(BackendSpec(host="10.0.0.1", port=80),))


def _host(access_list: AccessListSpec | None, id_: int = 1, upstream_id: int = 1) -> ProxyHostSpec:
    return ProxyHostSpec(
        id=id_,
        domain_names=("app.example.com",),
        upstream_id=upstream_id,
        access_list=access_list,
    )


def test_basic_auth_emits_htpasswd_and_directives() -> None:
    al = AccessListSpec(id=7, name="Ops", auth_users=(AuthUserSpec("alice", _HASH),))
    files = render_config(DesiredState(proxy_hosts=(_host(al),), http_upstreams=(_pool(),)))

    # A sidecar htpasswd file (not a .conf, so nginx never parses it as config).
    assert "megoopm-access-7.htpasswd" in files
    assert files["megoopm-access-7.htpasswd"] == f"alice:{_HASH}\n"

    conf = files["megoopm-proxy-1.conf"]
    assert 'auth_basic "Ops";' in conf
    assert f"auth_basic_user_file {htpasswd_path(7)};" in conf
    # No client rules → no satisfy directive.
    assert "satisfy" not in conf


def test_allow_deny_rules_render_in_order() -> None:
    al = AccessListSpec(
        id=3,
        name="ip-only",
        client_rules=(ClientRuleSpec("allow", "10.0.0.0/8"), ClientRuleSpec("deny", "all")),
    )
    files = render_config(DesiredState(proxy_hosts=(_host(al),), http_upstreams=(_pool(),)))
    conf = files["megoopm-proxy-1.conf"]
    assert "allow 10.0.0.0/8;" in conf
    assert "deny all;" in conf
    assert conf.index("allow 10.0.0.0/8;") < conf.index("deny all;")
    # IP-only list has no basic auth → no htpasswd file, no auth gate.
    assert "megoopm-access-3.htpasswd" not in files
    assert "auth_basic_user_file" not in conf
    assert 'auth_basic "' not in conf  # the "off" in the ACME bypass is expected
    # satisfy only appears when BOTH gates are present.
    assert "satisfy" not in conf


def test_satisfy_reflects_flag_when_both_gates_present() -> None:
    both = AccessListSpec(
        id=1,
        name="both",
        satisfy_any=True,
        auth_users=(AuthUserSpec("u", _HASH),),
        client_rules=(ClientRuleSpec("allow", "192.168.0.0/16"),),
    )
    conf = render_config(DesiredState(proxy_hosts=(_host(both),), http_upstreams=(_pool(),)))[
        "megoopm-proxy-1.conf"
    ]
    assert "satisfy any;" in conf

    allq = AccessListSpec(
        id=1,
        name="both",
        satisfy_any=False,
        auth_users=(AuthUserSpec("u", _HASH),),
        client_rules=(ClientRuleSpec("allow", "192.168.0.0/16"),),
    )
    conf2 = render_config(DesiredState(proxy_hosts=(_host(allq),), http_upstreams=(_pool(),)))[
        "megoopm-proxy-1.conf"
    ]
    assert "satisfy all;" in conf2


def test_authorization_header_stripped_unless_pass_auth() -> None:
    strip = AccessListSpec(id=1, name="a", auth_users=(AuthUserSpec("u", _HASH),))
    conf = render_config(DesiredState(proxy_hosts=(_host(strip),), http_upstreams=(_pool(),)))[
        "megoopm-proxy-1.conf"
    ]
    assert 'proxy_set_header Authorization "";' in conf

    forward = AccessListSpec(id=1, name="a", pass_auth=True, auth_users=(AuthUserSpec("u", _HASH),))
    conf2 = render_config(DesiredState(proxy_hosts=(_host(forward),), http_upstreams=(_pool(),)))[
        "megoopm-proxy-1.conf"
    ]
    assert 'proxy_set_header Authorization "";' not in conf2


def test_acme_challenge_bypasses_access_control() -> None:
    al = AccessListSpec(
        id=1,
        name="a",
        auth_users=(AuthUserSpec("u", _HASH),),
        client_rules=(ClientRuleSpec("deny", "all"),),
    )
    conf = render_config(DesiredState(proxy_hosts=(_host(al),), http_upstreams=(_pool(),)))[
        "megoopm-proxy-1.conf"
    ]
    start = conf.index("acme-challenge/ {")
    challenge = conf[start : conf.index("}", start)]
    assert "auth_basic off;" in challenge
    assert "allow all;" in challenge


def test_shared_access_list_emits_one_htpasswd() -> None:
    al = AccessListSpec(id=9, name="shared", auth_users=(AuthUserSpec("u", _HASH),))
    state = DesiredState(
        proxy_hosts=(_host(al, id_=1, upstream_id=1), _host(al, id_=2, upstream_id=2)),
        http_upstreams=(_pool(1), _pool(2)),
    )
    files = render_config(state)
    # Both hosts reference the same single htpasswd file.
    assert "megoopm-access-9.htpasswd" in files
    assert htpasswd_path(9) in files["megoopm-proxy-1.conf"]
    assert htpasswd_path(9) in files["megoopm-proxy-2.conf"]


def test_no_access_list_renders_no_auth() -> None:
    conf = render_config(DesiredState(proxy_hosts=(_host(None),), http_upstreams=(_pool(),)))[
        "megoopm-proxy-1.conf"
    ]
    assert "auth_basic" not in conf
    assert "satisfy" not in conf
