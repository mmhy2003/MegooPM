"""Validation rules for the user-management request schemas."""

from __future__ import annotations

import pytest
from app.schemas.user import PasswordChange, PasswordReset, ProfileUpdate, UserUpdate
from pydantic import ValidationError


def test_user_update_rejects_email() -> None:
    with pytest.raises(ValidationError):
        UserUpdate(email="new@example.com", full_name="x")  # type: ignore[call-arg]


def test_user_update_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        UserUpdate()


def test_user_update_accepts_a_single_field() -> None:
    body = UserUpdate(role="admin")
    assert body.role == "admin"
    assert body.full_name is None
    assert body.is_active is None


def test_password_reset_enforces_min_length() -> None:
    with pytest.raises(ValidationError):
        PasswordReset(password="short")
    assert PasswordReset(password="longenough").password == "longenough"


def test_password_change_enforces_new_password_min_length_only() -> None:
    with pytest.raises(ValidationError):
        PasswordChange(current_password="whatever", new_password="short")
    body = PasswordChange(current_password="x", new_password="longenough")
    assert body.current_password == "x"


def test_profile_update_rejects_role_changes() -> None:
    with pytest.raises(ValidationError):
        ProfileUpdate(full_name="Me", role="admin")  # type: ignore[call-arg]
    assert ProfileUpdate(full_name="Me").full_name == "Me"
