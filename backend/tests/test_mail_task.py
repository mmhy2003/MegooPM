"""The Celery send task: registered, and renders-then-sends."""

from __future__ import annotations

import pytest
from app.core.celery_app import celery_app
from app.models.enums import SmtpSecurity
from app.services.mail import sender as sender_module
from app.services.mail.config import MailConfig
from app.tasks import mail as mail_tasks


def test_the_task_is_registered_with_the_worker() -> None:
    # The existing guard checks only beat_schedule. This task is dispatched
    # with .delay() and would slip past it — and a missing TASK_MODULES entry
    # shows up as the first reset email silently never sending.
    celery_app.loader.import_default_modules()
    assert "app.tasks.mail.send_email" in celery_app.tasks


def test_it_renders_and_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict = {}

    def fake_send(config, *, to, email, timeout=15.0):
        sent["to"] = to
        sent["subject"] = email.subject
        sent["html"] = email.html

    monkeypatch.setattr(sender_module, "send_email", fake_send)
    monkeypatch.setattr(
        mail_tasks,
        "_load_config",
        lambda: MailConfig(
            host="mail.example.com",
            port=587,
            security=SmtpSecurity.starttls,
            username=None,
            password=None,
            from_address="megoopm@example.com",
            from_name="MegooPM",
        ),
    )

    result = mail_tasks.send_email(
        to="ops@example.com",
        template="password_changed",
        subject="Your password was changed",
        context={"app_name": "MegooPM"},
    )

    assert result == {"sent": True, "to": "ops@example.com"}
    assert sent["to"] == "ops@example.com"
    assert sent["subject"] == "Your password was changed"
    assert "password" in sent["html"].lower()
