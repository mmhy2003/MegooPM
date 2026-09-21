"""The base nginx.conf's default server: bans enforced, healthcheck exempt.

Reads the file directly, like the compose tests, so it runs anywhere the repo
is checked out. The end-to-end proof is infra/nginx/tests/bouncer-phase.sh.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

NGINX_CONF = Path(__file__).resolve().parents[2] / "infra" / "nginx" / "nginx.conf"


def _default_server() -> str:
    if not NGINX_CONF.exists():  # pragma: no cover - partial checkout
        pytest.skip(f"{NGINX_CONF} not present")
    text = NGINX_CONF.read_text(encoding="utf-8")
    start = text.index("listen      80 default_server;")
    return text[start : text.index("include /data/nginx/default/*.conf;", start)]


def test_the_default_server_runs_the_bouncer_before_any_return() -> None:
    block = _default_server()
    assert "server_rewrite_by_lua_block" in block
    assert 'require("megoopm_crowdsec_check").check()' in block


def test_the_healthcheck_is_exempt() -> None:
    # AppSec fails closed; a CrowdSec outage must not mark nginx unhealthy.
    block = _default_server()
    assert re.search(r'if ngx\.var\.uri ~= "/healthz" then', block)
