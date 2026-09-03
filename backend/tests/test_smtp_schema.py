from __future__ import annotations

import pytest
from app.models.enums import SmtpSecurity
from app.schemas.instance_settings import MailTestRequest, SmtpSettingsUpdate
from pydantic import ValidationError


def _valid(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "smtp_enabled": True,
        "smtp_host": "mail.example.com",
        "smtp_port": 587,
        "smtp_security": SmtpSecurity.starttls,
        "smtp_from": "megoopm@example.com",
    }
    base.update(over)
    return base


def test_accepts_a_complete_configuration() -> None:
    body = SmtpSettingsUpdate(**_valid())
    assert body.smtp_host == "mail.example.com"


def test_enabling_without_a_host_is_refused() -> None:
    # Mirrors the database CHECK constraint, with a usable message.
    with pytest.raises(ValidationError, match="smtp_host"):
        SmtpSettingsUpdate(**_valid(smtp_host=None))


def test_enabling_without_a_from_address_is_refused() -> None:
    # A message with no From is rejected by every receiving server.
    with pytest.raises(ValidationError, match="smtp_from"):
        SmtpSettingsUpdate(**_valid(smtp_from=None))


def test_disabled_needs_nothing() -> None:
    body = SmtpSettingsUpdate(smtp_enabled=False, smtp_security=SmtpSecurity.starttls)
    assert body.smtp_host is None


def test_blank_strings_become_none() -> None:
    # An empty input box means "not set", not "the empty string".
    body = SmtpSettingsUpdate(**_valid(smtp_username="   "))
    assert body.smtp_username is None


def test_a_from_address_containing_a_newline_is_refused() -> None:
    # Header injection: a newline lets an attacker append a Bcc of their
    # choosing to every message the system sends.
    with pytest.raises(ValidationError, match="newline"):
        SmtpSettingsUpdate(**_valid(smtp_from="a@b.c\r\nBcc: attacker@example.com"))


def test_a_from_name_containing_a_newline_is_refused() -> None:
    with pytest.raises(ValidationError, match="newline"):
        SmtpSettingsUpdate(**_valid(smtp_from_name="MegooPM\nBcc: attacker@example.com"))


def test_app_url_must_be_absolute_http() -> None:
    with pytest.raises(ValidationError, match="http"):
        SmtpSettingsUpdate(**_valid(app_url="pm.example.com"))


def test_app_url_accepts_https() -> None:
    body = SmtpSettingsUpdate(**_valid(app_url="https://pm.example.com"))
    assert body.app_url == "https://pm.example.com"


def test_port_is_bounded() -> None:
    with pytest.raises(ValidationError):
        SmtpSettingsUpdate(**_valid(smtp_port=70000))


def test_password_absent_and_password_null_are_different() -> None:
    # Absent keeps the stored password; explicit null clears it. Flattening the
    # two wipes a working password on every save.
    keep = SmtpSettingsUpdate(**_valid())
    clear = SmtpSettingsUpdate(**_valid(smtp_password=None))
    assert "smtp_password" not in keep.model_fields_set
    assert "smtp_password" in clear.model_fields_set


def test_test_request_recipient_is_optional() -> None:
    # Omitted means "send it to me" — the route fills in the admin's address.
    assert MailTestRequest().to is None
    assert MailTestRequest(to="ops@example.com").to == "ops@example.com"
