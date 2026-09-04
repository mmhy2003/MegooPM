"""The loader wiring for the branded error pages, against real rows.

The document set is unit-tested in ``test_error_pages_render.py``; this covers
that ``load_desired_state`` actually reads the bindings and dereferences the
chosen page. Skipped without Postgres: ``proxy_hosts.domain_names`` is an ARRAY
the SQLite test engine cannot render.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import settings
from app.db.base import Base
from app.models.custom_page import CustomPage
from app.models.enums import ErrorPageMode
from app.models.error_page import ErrorPage
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


async def test_a_bound_page_is_dereferenced(pg_session: AsyncSession) -> None:
    page = CustomPage(name="Maintenance", html="<h1>Back soon</h1>")
    pg_session.add(page)
    await pg_session.flush()
    pg_session.add(ErrorPage(code=404, mode=ErrorPageMode.custom_page, custom_page_id=page.id))
    await pg_session.flush()

    state = await load_desired_state(pg_session, certs_dir="/data/certs")

    assert [(spec.code, spec.html) for spec in state.error_pages] == [(404, "<h1>Back soon</h1>")]


async def test_nothing_configured_reaches_the_renderer_as_nothing(
    pg_session: AsyncSession,
) -> None:
    # An absent row *is* the branded default; the loader must not invent a
    # spec, or the renderer could not tell "unset" from "set to the default".
    state = await load_desired_state(pg_session, certs_dir="/data/certs")

    assert state.error_pages == ()
