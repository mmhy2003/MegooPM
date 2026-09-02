"""The loader wiring for the CrowdSec ban page, against real rows.

The mode matrix is unit-tested in ``test_nginx_render.py``; this covers that
``load_desired_state`` actually reads the setting and dereferences the chosen
page. Skipped without Postgres: ``proxy_hosts.domain_names`` is an ARRAY the
SQLite test engine cannot render.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import settings
from app.db.base import Base
from app.models.custom_page import CustomPage
from app.models.enums import CrowdSecBanMode, DefaultSiteMode
from app.models.instance_settings import InstanceSettings
from app.services.nginx.loader import load_desired_state
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def pg_session() -> AsyncIterator[AsyncSession]:
    """A session in one rolled-back transaction, so nothing is ever committed."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception:  # pragma: no cover - environment without a database
        await engine.dispose()
        pytest.skip("No database reachable at DATABASE_URL")

    trans = await conn.begin()
    await conn.run_sync(Base.metadata.create_all)
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await engine.dispose()


async def _settings(session: AsyncSession, **kw) -> InstanceSettings:
    row = InstanceSettings(id=1, default_site_mode=DefaultSiteMode.not_found, **kw)
    session.add(row)
    await session.flush()
    return row


async def test_the_chosen_page_is_dereferenced(pg_session: AsyncSession) -> None:
    page = CustomPage(name="Blocked", html="<h1>Blocked</h1>")
    pg_session.add(page)
    await pg_session.flush()
    await _settings(
        pg_session,
        crowdsec_ban_mode=CrowdSecBanMode.custom_page,
        crowdsec_ban_page_id=page.id,
    )

    state = await load_desired_state(pg_session, certs_dir="/data/certs")

    assert state.ban_page is not None
    assert state.ban_page.mode == "custom_page"
    assert state.ban_page.html == "<h1>Blocked</h1>"


async def test_the_megoopm_mode_needs_no_document(pg_session: AsyncSession) -> None:
    # The renderer supplies the document; the loader must not invent one.
    await _settings(pg_session, crowdsec_ban_mode=CrowdSecBanMode.megoopm)

    state = await load_desired_state(pg_session, certs_dir="/data/certs")

    assert state.ban_page is not None
    assert state.ban_page.mode == "megoopm"
    assert state.ban_page.html == ""


async def test_the_none_mode_reaches_the_renderer_as_none(pg_session: AsyncSession) -> None:
    """It must arrive as a real mode, not as an absent spec — the renderer
    distinguishes 'no setting row' from 'the operator chose no page'."""
    await _settings(pg_session, crowdsec_ban_mode=CrowdSecBanMode.none)

    state = await load_desired_state(pg_session, certs_dir="/data/certs")

    assert state.ban_page is not None
    assert state.ban_page.mode == "none"
