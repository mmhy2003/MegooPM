"""Streams targeting an upstream pool instead of a single host:port.

A stream forwards to exactly one of the two, never both and never neither. The
database enforces it so a bad row cannot exist even if a caller bypasses the
schema layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.models.stream import Stream
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@pytest.fixture
def engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'streams.db'}", future=True)
    Stream.__table__.create(engine)
    # SQLite ignores CHECK constraints unless foreign_keys/legacy pragmas allow
    # them; they are enforced by default for CHECK, which is what we rely on.
    return engine


def test_rejects_both_targets(engine) -> None:
    with Session(engine) as s, pytest.raises(IntegrityError):
        s.add(Stream(incoming_port=1, forward_host="h", forward_port=2, upstream_id=1))
        s.commit()


def test_rejects_neither_target(engine) -> None:
    with Session(engine) as s, pytest.raises(IntegrityError):
        s.add(Stream(incoming_port=1))
        s.commit()


def test_accepts_a_pool_only_target(engine) -> None:
    with Session(engine) as s:
        s.add(Stream(incoming_port=1, upstream_id=5))
        s.commit()
        assert s.query(Stream).one().upstream_id == 5


def test_accepts_a_host_only_target(engine) -> None:
    """The existing shape keeps working; there is no data migration."""
    with Session(engine) as s:
        s.add(Stream(incoming_port=2, forward_host="db.internal", forward_port=5432))
        s.commit()
        row = s.query(Stream).one()
        assert (row.forward_host, row.forward_port) == ("db.internal", 5432)
        assert row.upstream_id is None


def test_port_range_still_enforced_when_a_host_is_given(engine) -> None:
    with Session(engine) as s, pytest.raises(IntegrityError):
        s.add(Stream(incoming_port=3, forward_host="h", forward_port=70000))
        s.commit()
