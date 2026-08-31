"""Pool context — which nginx context a pool may be rendered into.

``upstream`` blocks are context-local: one defined in ``http {}`` is invisible
to ``stream {}``. A pool therefore has to declare where it may be attached, and
that also constrains its load-balancing method, because ``ip_hash`` exists only
in ``http``.
"""

from __future__ import annotations

from pathlib import Path

from app.models.enums import UpstreamContext
from app.models.upstream import Upstream
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pools.db'}", future=True)
    Upstream.__table__.create(engine)
    return engine


def test_context_defaults_to_http_on_insert(tmp_path: Path) -> None:
    """Every pool in the database today backs a proxy host; http keeps them working.

    The default applies at INSERT, so this has to round-trip through a flush —
    asserting on an unflushed instance would pass against ``None`` and prove
    nothing.
    """
    engine = _engine(tmp_path)
    with Session(engine) as session:
        pool = Upstream(name="app-pool")
        session.add(pool)
        session.flush()
        session.refresh(pool)
        assert pool.context is UpstreamContext.http


def test_context_round_trips_when_set(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with Session(engine) as session:
        pool = Upstream(name="db-pool", context=UpstreamContext.stream)
        session.add(pool)
        session.flush()
        session.refresh(pool)
        assert pool.context is UpstreamContext.stream


def test_context_values_are_stable_strings() -> None:
    # The value, not the member name, is what lands in Postgres.
    assert [c.value for c in UpstreamContext] == ["http", "stream", "both"]
