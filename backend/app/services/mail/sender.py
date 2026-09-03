"""Build the MIME message and hand it to SMTP.

Stdlib ``smtplib`` rather than ``aiosmtplib``: the same code has to run from an
async route and, once notifications exist, a sync Celery task.
``asyncio.to_thread`` bridges sync-into-async in one line; the reverse means
running an event loop inside a worker. It also keeps this feature at zero new
dependencies.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.models.enums import SmtpSecurity
from app.services.mail.config import MailConfig, MailNotConfigured
from app.services.mail.templates import LOGO_CID, LOGO_PATH, RenderedEmail


def build_message(config: MailConfig, *, to: str, email: RenderedEmail) -> EmailMessage:
    """Assemble one message: text, HTML alternative, and the inline logo."""
    if "\r" in to or "\n" in to:
        raise ValueError("recipient address must not contain a newline")
    if not config.from_address:
        raise MailNotConfigured("No From address is configured.")

    message = EmailMessage()
    message["From"] = (
        formataddr((config.from_name, config.from_address))
        if config.from_name
        else config.from_address
    )
    message["To"] = to
    message["Subject"] = email.subject

    # Plain text is the message body; HTML is the alternative. Setting them in
    # this order is what makes the part multipart/alternative.
    message.set_content(email.text)
    message.add_alternative(email.html, subtype="html")

    # The logo joins the HTML part, not the message. That is what makes *that
    # part* multipart/related — the structure a client needs to resolve `cid:`
    # without fetching anything remote.
    html_part = message.get_payload()[1]
    html_part.add_related(
        LOGO_PATH.read_bytes(),
        maintype="image",
        subtype="png",
        cid=f"<{LOGO_CID}>",
        # Inline, not attachment: otherwise clients show a paperclip and the
        # header renders empty.
        disposition="inline",
        filename="logo.png",
    )
    return message


def send_email(config: MailConfig, *, to: str, email: RenderedEmail, timeout: float = 15.0) -> None:
    """Send one message, blocking. Raises on any SMTP failure."""
    if not config.host:
        raise MailNotConfigured("No SMTP host is configured.")

    message = build_message(config, to=to, email=email)

    if config.security is SmtpSecurity.ssl:
        client = smtplib.SMTP_SSL(config.host, config.port, timeout=timeout)
    else:
        client = smtplib.SMTP(config.host, config.port, timeout=timeout)

    with client as connection:
        if config.security is SmtpSecurity.starttls:
            connection.starttls(context=ssl.create_default_context())
        if config.username:
            connection.login(config.username, config.password or "")
        connection.send_message(message)


__all__ = ["build_message", "send_email"]
