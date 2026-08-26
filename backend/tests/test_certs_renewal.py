"""Auto-renewal selection logic (pure date arithmetic, no DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.certs.renewal import is_due_for_renewal

NOW = datetime(2026, 8, 26, tzinfo=UTC)


def test_due_when_within_window() -> None:
    expires = NOW + timedelta(days=20)
    assert is_due_for_renewal(expires, now=NOW, before_days=30) is True


def test_not_due_when_outside_window() -> None:
    expires = NOW + timedelta(days=45)
    assert is_due_for_renewal(expires, now=NOW, before_days=30) is False


def test_due_when_already_expired() -> None:
    expires = NOW - timedelta(days=1)
    assert is_due_for_renewal(expires, now=NOW, before_days=30) is True


def test_due_when_expiry_unknown() -> None:
    # Never successfully issued (no expiry recorded yet) → treat as due.
    assert is_due_for_renewal(None, now=NOW, before_days=30) is True


def test_boundary_is_inclusive() -> None:
    expires = NOW + timedelta(days=30)
    assert is_due_for_renewal(expires, now=NOW, before_days=30) is True
