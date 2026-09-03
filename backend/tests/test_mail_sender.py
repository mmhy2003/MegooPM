"""Sender tests.

No fake SMTP server, so no dev dependency: ``smtplib.SMTP`` and
``smtplib.SMTP_SSL`` are replaced with a recorder.
"""

from __future__ import annotations

import smtplib
from typing import Any

import pytest
from app.models.enums import SmtpSecurity
from app.services.mail.config import MailConfig, MailNotConfigured
from app.services.mail.sender import build_message, send_email
from app.services.mail.templates import LOGO_CID, render


def _config(**over: Any) -> MailConfig:
    base: dict[str, Any] = {
        "host": "mail.example.com",
        "port": 587,
        "security": SmtpSecurity.starttls,
        "username": "user",
        "password": "secret",
        "from_address": "megoopm@example.com",
        "from_name": "MegooPM",
    }
    base.update(over)
    return MailConfig(**base)


def _email():
    return render("test_email", subject="MegooPM test email", app_name="MegooPM")


class FakeSMTP:
    """Records what the sender did instead of opening a socket."""

    instances: list[FakeSMTP] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent: Any = None
        self.quit_called = False
        FakeSMTP.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        self.quit_called = True

    def starttls(self, context: Any = None) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.login_args = (user, password)

    def send_message(self, message: Any) -> None:
        self.sent = message


@pytest.fixture(autouse=True)
def _fake_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)


# --- message shape ---------------------------------------------------------


def test_message_is_multipart_alternative_with_both_bodies() -> None:
    # HTML-only mail is a spam signal.
    message = build_message(_config(), to="ops@example.com", email=_email())
    bodies = {part.get_content_type() for part in message.walk()}
    assert "text/plain" in bodies
    assert "text/html" in bodies


def test_logo_is_attached_under_the_expected_content_id() -> None:
    message = build_message(_config(), to="ops@example.com", email=_email())
    cids = [part.get("Content-ID") for part in message.walk() if part.get("Content-ID")]
    assert f"<{LOGO_CID}>" in cids


def test_logo_is_marked_inline_not_an_attachment() -> None:
    # Otherwise clients show a paperclip and the header renders empty.
    message = build_message(_config(), to="ops@example.com", email=_email())
    logo = next(p for p in message.walk() if p.get("Content-ID") == f"<{LOGO_CID}>")
    assert logo.get_content_disposition() == "inline"


def test_from_header_carries_the_display_name() -> None:
    message = build_message(_config(), to="ops@example.com", email=_email())
    assert message["From"] == "MegooPM <megoopm@example.com>"


def test_from_header_is_the_bare_address_without_a_display_name() -> None:
    message = build_message(_config(from_name=None), to="ops@example.com", email=_email())
    assert message["From"] == "megoopm@example.com"


def test_recipient_and_subject_are_set() -> None:
    message = build_message(_config(), to="ops@example.com", email=_email())
    assert message["To"] == "ops@example.com"
    assert message["Subject"] == "MegooPM test email"


def test_a_recipient_containing_a_newline_is_refused() -> None:
    # Header injection through the one field the caller controls at send time.
    with pytest.raises(ValueError, match="newline"):
        build_message(_config(), to="a@b.c\r\nBcc: attacker@example.com", email=_email())


# --- transport -------------------------------------------------------------


def test_starttls_upgrades_the_connection() -> None:
    send_email(_config(security=SmtpSecurity.starttls), to="ops@example.com", email=_email())
    assert FakeSMTP.instances[0].started_tls is True


def test_ssl_does_not_call_starttls() -> None:
    # SMTPS is encrypted from the first byte; STARTTLS on top of it errors.
    send_email(_config(security=SmtpSecurity.ssl, port=465), to="ops@example.com", email=_email())
    assert FakeSMTP.instances[0].started_tls is False


def test_none_leaves_the_connection_in_the_clear() -> None:
    send_email(_config(security=SmtpSecurity.none, port=25), to="ops@example.com", email=_email())
    assert FakeSMTP.instances[0].started_tls is False


def test_credentials_are_sent_when_present() -> None:
    send_email(_config(), to="ops@example.com", email=_email())
    assert FakeSMTP.instances[0].login_args == ("user", "secret")


def test_no_login_is_attempted_without_a_username() -> None:
    # An open local relay needs no credentials, and AUTH against one errors.
    send_email(_config(username=None, password=None), to="ops@example.com", email=_email())
    assert FakeSMTP.instances[0].login_args is None


def test_the_connection_is_closed_even_though_send_succeeded() -> None:
    send_email(_config(), to="ops@example.com", email=_email())
    assert FakeSMTP.instances[0].quit_called is True


def test_sending_without_a_host_raises_mail_not_configured() -> None:
    with pytest.raises(MailNotConfigured):
        send_email(_config(host=None), to="ops@example.com", email=_email())
