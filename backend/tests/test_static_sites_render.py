"""Rendering tests for static sites.

The renderer is a pure function of a :class:`DesiredState`, so most of these
need no database or filesystem. ``nginx -t`` runs when the binary is available;
the loader test needs Postgres.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from app.core.config import settings
from app.db.base import Base
from app.models.error_page import ERROR_CODES
from app.models.static_site import StaticSite
from app.services.nginx import render_config, render_default_site
from app.services.nginx.loader import load_desired_state
from app.services.nginx.renderer import ERRORS_CONF, htpasswd_filename, htpasswd_path
from app.services.nginx.state import (
    AccessListSpec,
    AuthUserSpec,
    CertificateSpec,
    ClientRuleSpec,
    DesiredState,
    StaticSiteSpec,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

_CERT = CertificateSpec(
    id=7,
    fullchain_path="/etc/nginx/certs/7/fullchain.pem",
    privkey_path="/etc/nginx/certs/7/privkey.pem",
)
_ACCESS_LIST = AccessListSpec(
    id=4,
    name="team",
    satisfy_any=False,
    auth_users=(AuthUserSpec(username="alice", password_hash="$apr1$x$y"),),
    client_rules=(ClientRuleSpec(directive="allow", address="10.0.0.0/8"),),
)


def _site(**kw) -> StaticSiteSpec:
    base: dict[str, object] = {
        "id": 1,
        "domain_names": ("site.example.com",),
        "folder": "company",
    }
    base.update(kw)
    return StaticSiteSpec(**base)


def _conf(**kw) -> str:
    site = _site(**kw)
    return render_config(DesiredState(static_sites=(site,)))[f"megoopm-static-{site.id}.conf"]


def test_plain_site_serves_folder() -> None:
    conf = _conf()

    assert "listen 80;" in conf
    assert "listen 443" not in conf
    assert "server_name site.example.com;" in conf
    assert f"root {settings.sites_root}/company;" in conf
    assert "index index.html;" in conf
    assert "autoindex off;" in conf
    assert "disable_symlinks on;" in conf
    assert "try_files $uri $uri/ =404;" in conf


def test_hidden_paths_404_but_well_known_served() -> None:
    conf = _conf()

    assert "location ~ /\\. {" in conf
    assert "location ^~ /.well-known/ {" in conf
    assert "location ^~ /.well-known/acme-challenge/ {" in conf
    # ^~ prefixes win over the hidden-path regex, so the order is only for reading.
    assert conf.index("location ^~ /.well-known/ {") < conf.index("location ~ /\\. {")


def test_site_error_pages_come_before_branded_include() -> None:
    conf = _conf()

    for code in ERROR_CODES:
        assert conf.index(f"error_page {code} @site_{code};") < conf.index(ERRORS_CONF)
        assert f"location @site_{code} {{" in conf
        assert f"try_files /{code}.html @mgm_{code};" in conf
        assert f"location @mgm_{code} {{" in conf


def test_tls_and_forced_ssl() -> None:
    conf = _conf(certificate=_CERT, ssl_forced=True, hsts_enabled=True, http2_support=True)

    assert "listen 443 ssl;" in conf
    assert "http2 on;" in conf
    assert "ssl_certificate /etc/nginx/certs/7/fullchain.pem;" in conf
    assert "ssl_certificate_key /etc/nginx/certs/7/privkey.pem;" in conf
    assert "Strict-Transport-Security" in conf
    http_block = conf.split("listen 443 ssl;")[0]
    assert "return 301 https://$host$request_uri;" in http_block
    assert f"root {settings.sites_root}" not in http_block


def test_tls_without_forced_ssl_serves_both() -> None:
    conf = _conf(certificate=_CERT)

    assert conf.count(f"root {settings.sites_root}/company;") == 2
    assert "return 301 https://" not in conf


def test_forced_ssl_without_a_certificate_stays_http() -> None:
    conf = _conf(ssl_forced=True)

    assert "ssl_certificate" not in conf
    assert "return 301 https://" not in conf
    assert f"root {settings.sites_root}/company;" in conf


def test_access_list_basic_auth_and_acme_open() -> None:
    site = _site(access_list=_ACCESS_LIST)
    files = render_config(DesiredState(static_sites=(site,)))
    conf = files["megoopm-static-1.conf"]

    assert f"auth_basic_user_file {htpasswd_path(4)};" in conf
    assert "allow 10.0.0.0/8;" in conf
    assert "satisfy all;" in conf
    acme = conf.split("location ^~ /.well-known/acme-challenge/ {")[1].split("}")[0]
    assert "auth_basic off;" in acme
    assert "allow all;" in acme
    assert htpasswd_filename(4) in files


def test_block_exploits() -> None:
    assert "return 403;" in _conf(block_exploits=True)
    assert "return 403;" not in _conf()


def test_crowdsec_off_drops_bouncer() -> None:
    assert "megoopm_crowdsec.lua" in _conf()
    assert "megoopm_crowdsec.lua" not in _conf(crowdsec_enabled=False)


def test_live_log_key_w() -> None:
    assert "w7" in _conf(id=7)


def test_render_config_file_name() -> None:
    files = render_config(DesiredState(static_sites=(_site(id=3),)))
    assert "megoopm-static-3.conf" in files


def test_advanced_config_is_appended() -> None:
    assert 'add_header X-Static "yes";' in _conf(advanced_config='add_header X-Static "yes";')


# --- Loader ------------------------------------------------------------------


@pytest.fixture
async def pg_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception:  # pragma: no cover - environment without a database
        await engine.dispose()
        pytest.skip("No database reachable at DATABASE_URL")
    trans = await conn.begin()
    await conn.run_sync(Base.metadata.create_all)
    session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def test_disabled_site_not_loaded(pg_session: AsyncSession) -> None:
    pg_session.add_all(
        [
            StaticSite(domain_names=["on.example.com"], folder="on", enabled=True),
            StaticSite(domain_names=["off.example.com"], folder="off", enabled=False),
        ]
    )
    await pg_session.flush()

    state = await load_desired_state(pg_session)

    assert [s.folder for s in state.static_sites] == ["on"]
    assert state.static_sites[0].domain_names == ("on.example.com",)


# --- Real nginx -t -----------------------------------------------------------


@pytest.mark.skipif(shutil.which("nginx") is None, reason="nginx binary not installed")
def test_nginx_t_accepts_site_with_missing_folder(tmp_path: Path) -> None:
    from app.services.nginx.controller import ShellNginxController

    state = DesiredState(
        static_sites=(
            _site(folder="not-there"),
            _site(
                id=2,
                domain_names=("tls.example.com",),
                certificate=_CERT,
                ssl_forced=True,
                access_list=_ACCESS_LIST,
            ),
        )
    )
    conf_d = tmp_path / "conf.d"
    conf_d.mkdir()
    for name, body in render_config(state).items():
        (conf_d / name).write_text(body)
    default_dir = tmp_path / "default"
    default_dir.mkdir()
    for name, body in render_default_site(state).items():
        (default_dir / name).write_text(body)
    main_conf = tmp_path / "nginx.conf"
    main_conf.write_text(f"events {{}}\nhttp {{\n    include {conf_d}/*.conf;\n}}\n")
    ctrl = ShellNginxController(test_command=f"nginx -t -c {main_conf}")
    assert ctrl.test().ok
