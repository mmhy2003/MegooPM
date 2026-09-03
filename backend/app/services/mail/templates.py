"""Render a named email to an HTML + plain-text pair.

Two Jinja environments, deliberately: the HTML one autoescapes, the text one
must not. Escaping in plain text shows the reader a literal ``&amp;``.

This module opens no socket and reads no database, so its tests are fast and its
failures are unambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.services.mail.palette import DARK, LIGHT

_TEMPLATE_DIR = Path(__file__).parent / "templates"

#: The product name as it appears in email. The backend ``Settings`` has no
#: ``app_name``, and inventing a config field for a constant nobody varies would
#: be a knob with one position.
APP_NAME = "MegooPM"

#: Content-ID the logo is attached under; the templates reference ``cid:`` + this.
LOGO_CID = "megoopm-logo"

#: The embedded logo. 96px for a 48px display box — see the spec.
LOGO_PATH = Path(__file__).parent / "assets" / "logo-email.png"

# StrictUndefined on both: a typo'd variable should fail the test suite, not
# render an email with a silent blank where a reset link belonged.
_html_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "j2"]),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
_text_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    """One message, both bodies. The sender turns this into MIME."""

    subject: str
    html: str
    text: str


def _reject_newlines(subject: str) -> str:
    """Refuse a subject that could inject headers.

    A CR or LF ends the Subject header and begins another, letting a caller
    append a ``Bcc:`` of their choosing to every message.
    """
    if "\r" in subject or "\n" in subject:
        raise ValueError("email subject must not contain a newline")
    return subject


def render(name: str, *, subject: str, **context: object) -> RenderedEmail:
    """Render ``<name>.html.j2`` and ``<name>.txt.j2`` with the shared context."""
    shared = {"light": LIGHT, "dark": DARK, "logo_cid": LOGO_CID, **context}
    return RenderedEmail(
        subject=_reject_newlines(subject),
        html=_html_env.get_template(f"{name}.html.j2").render(**shared),
        text=_text_env.get_template(f"{name}.txt.j2").render(**shared),
    )


__all__ = ["APP_NAME", "LOGO_CID", "LOGO_PATH", "RenderedEmail", "render"]
