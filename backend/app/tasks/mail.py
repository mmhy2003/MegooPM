"""Send one email from a worker.

The HTTP request that queued this has already returned. A slow or dead mail
server therefore never fails a user-facing action — the user's password is
reset either way, and the email arrives when it arrives.

Retries three times with backoff on any failure, then gives up and logs. A
reset link that never arrives after a mail-server outage is the user clicking
"forgot password" again, which the rate limit permits.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.services import instance_settings as settings_service
from app.services.mail import sender as mail_sender
from app.services.mail.config import MailConfig
from app.services.mail.templates import render

log = logging.getLogger(__name__)


async def _load_config_async() -> MailConfig:
    engine = create_async_engine(settings.database_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            row = await settings_service.get_instance_settings(session)
            return settings_service.mail_config_from_row(row)
    finally:
        await engine.dispose()


def _load_config() -> MailConfig:
    """Read the SMTP config. Separate so a test can replace it."""
    return asyncio.run(_load_config_async())


@celery_app.task(
    name="app.tasks.mail.send_email",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_email(self, *, to: str, template: str, subject: str, context: dict) -> dict:
    """Render ``template`` with ``context`` and send it to ``to``."""
    config = _load_config()
    email = render(template, subject=subject, **context)
    mail_sender.send_email(config, to=to, email=email)
    log.info("sent %s to %s", template, to)
    return {"sent": True, "to": to}


__all__ = ["send_email"]
