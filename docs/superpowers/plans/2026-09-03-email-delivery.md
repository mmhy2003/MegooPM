# Email Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An operator configures SMTP in Settings, clicks Send test email, and receives a themed message carrying the MegooPM logo.

**Architecture:** SMTP configuration joins the `instance_settings` singleton in the same shape as the existing LLM block, with the password Fernet-encrypted by the existing `app/core/crypto.py`. A three-module `app/services/mail/` package renders Jinja2 templates to an HTML + plain-text pair and sends them over stdlib `smtplib`. The test send is synchronous so the operator sees the real SMTP error.

**Tech Stack:** FastAPI · SQLAlchemy 2.0 async · Alembic · Pydantic v2 · Jinja2 · stdlib `smtplib`/`email` · pytest — backend. Next.js 16 / React 19 / base-ui / vitest — frontend. **No new dependencies in either.**

**Spec:** `docs/superpowers/specs/2026-09-03-email-delivery-design.md`

## Global Constraints

- **No new packages.** `jinja2` and `cryptography` are already dependencies; the mailer uses stdlib `smtplib`. If a task seems to need a package, stop and raise it.
- **Colours are inlined as hex.** A `<style>` block carries *only* the `@media (prefers-color-scheme: dark)` overrides. `oklch()` works in no email client.
- **Every message is `multipart/alternative` with a real plain-text part**, written rather than stripped from the HTML.
- **The logo is embedded by Content-ID**, never hotlinked.
- **The SMTP password is never returned by any endpoint.** The read schema exposes `smtp_password_set: bool`, mirroring `llm_api_key_set`.
- **CR and LF are rejected** in `smtp_from`, `smtp_from_name`, and every rendered subject.
- **P1 builds no Celery task.** It has no caller here; P2 adds one when it has a real message to send.
- **`app_url` is stored and validated but unused in P1.** Do not invent a use for it.
- **Backend tests cannot run natively on Windows** (`app/services/cluster/locks.py` imports `fcntl`). Use the throwaway container recipe in Task 3, Step 3.
- Frontend commands run from `frontend/`: `npm test`, `npm run typecheck`, `npm run lint`.

## File Structure

**Backend**

| file | responsibility |
| --- | --- |
| `app/services/mail/palette.py` | The transcoded hex constants. Data only. |
| `app/services/mail/templates.py` | Render a named template → `RenderedEmail`. Touches no socket. |
| `app/services/mail/templates/base.html.j2` | Layout, header, footer, dark-mode block. Every cross-client workaround lives here. |
| `app/services/mail/templates/test_email.{html,txt}.j2` | The one message P1 sends. |
| `app/services/mail/assets/logo-email.png` | 96px logo, embedded by CID. |
| `app/services/mail/config.py` | Settings row → `MailConfig`; raises `MailNotConfigured`. |
| `app/services/mail/sender.py` | Build MIME, hand to `smtplib`. Renders nothing. |
| `app/models/instance_settings.py` | The nine new columns + CHECK constraint. |
| `alembic/versions/0024_smtp_settings.py` | The migration. |
| `app/schemas/instance_settings.py` | `SmtpSettingsUpdate`, `MailTestRequest`, `MailTestResult`. |
| `app/services/instance_settings.py` | `update_smtp_settings`, `mail_config_from_row`. |
| `app/api/routes/settings.py` | `PATCH /settings/smtp`, `POST /settings/smtp/test`. |

Splitting `templates.py` from `sender.py` is what lets the template tests run without a socket and the sender tests run without Jinja.

**Frontend**

| file | responsibility |
| --- | --- |
| `src/components/settings/lib.ts` | `SmtpFormState` + pure state/payload/validation helpers, beside the LLM ones. |
| `src/components/settings/smtp-card.tsx` | The card. |
| `src/components/settings/settings-view.tsx` | Mount it. |
| `src/lib/api/resources/settings.ts` | `updateSmtp`, `testSmtp`, and the types. |

---

### Task 1: The email palette

The app's palette is authored in `oklch()`, which no email client supports. These
are hand-transcoded copies. The test re-runs the conversion so a mistyped hex is
caught; nothing can catch the copy drifting from `globals.css`, which is why the
module says so out loud.

**Files:**
- Create: `backend/app/services/mail/__init__.py`
- Create: `backend/app/services/mail/palette.py`
- Test: `backend/tests/test_mail_palette.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `LIGHT: dict[str, str]` and `DARK: dict[str, str]`, both keyed by
  `background`, `card`, `foreground`, `primary`, `primary_foreground`,
  `muted_foreground`, `border`, `destructive`, `success`, with `#rrggbb` values.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_mail_palette.py`:

```python
"""The email palette is a hand-transcoded copy of the app's oklch tokens.

The converter lives here rather than in the package: production never converts
anything, and shipping a colour-space routine that nothing calls is dead weight.
It exists so a mistyped hex constant is caught.
"""

from __future__ import annotations

import math

from app.services.mail.palette import DARK, LIGHT

# The oklch sources, copied from frontend/src/app/globals.css.
LIGHT_SOURCE = {
    "background": (0.975, 0.01, 220),
    "card": (1.0, 0.0, 0.0),
    "foreground": (0.22, 0.05, 290),
    "primary": (0.50, 0.14, 205),
    "primary_foreground": (0.99, 0.01, 200),
    "muted_foreground": (0.48, 0.05, 260),
    "border": (0.86, 0.04, 210),
    "destructive": (0.54, 0.24, 20),
    "success": (0.50, 0.16, 145),
}
DARK_SOURCE = {
    "background": (0.15, 0.03, 285),
    "card": (0.19, 0.035, 285),
    "foreground": (0.95, 0.02, 200),
    "primary": (0.85, 0.16, 195),
    "primary_foreground": (0.15, 0.03, 285),
    "muted_foreground": (0.72, 0.04, 215),
    "border": (0.30, 0.05, 285),
    "destructive": (0.70, 0.24, 15),
    "success": (0.85, 0.22, 135),
}


def oklch_to_hex(lightness: float, chroma: float, hue_deg: float) -> str:
    """Convert an oklch triple to an sRGB hex string."""
    hue = math.radians(hue_deg)
    a, b = chroma * math.cos(hue), chroma * math.sin(hue)
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    long_, med, short = l_**3, m_**3, s_**3
    red = 4.0767416621 * long_ - 3.3077115913 * med + 0.2309699292 * short
    green = -1.2684380046 * long_ + 2.6097574011 * med - 0.3413193965 * short
    blue = -0.0041960863 * long_ - 0.7034186147 * med + 1.7076147010 * short

    def encode(channel: float) -> int:
        srgb = (
            1.055 * (channel ** (1 / 2.4)) - 0.055 if channel > 0.0031308 else 12.92 * channel
        )
        return max(0, min(255, round(srgb * 255)))

    return "#%02x%02x%02x" % (encode(red), encode(green), encode(blue))


def test_light_palette_matches_its_oklch_source() -> None:
    for name, (lightness, chroma, hue) in LIGHT_SOURCE.items():
        assert LIGHT[name] == oklch_to_hex(lightness, chroma, hue), name


def test_dark_palette_matches_its_oklch_source() -> None:
    for name, (lightness, chroma, hue) in DARK_SOURCE.items():
        assert DARK[name] == oklch_to_hex(lightness, chroma, hue), name


def test_both_themes_define_the_same_tokens() -> None:
    # A token present in one theme and missing in the other renders a template
    # correctly in light mode and raises a KeyError in dark.
    assert LIGHT.keys() == DARK.keys()


def test_every_value_is_a_six_digit_hex_colour() -> None:
    # Email clients accept #rrggbb. Shorthand and named colours are not
    # uniformly supported, and oklch() is supported nowhere.
    for theme in (LIGHT, DARK):
        for name, value in theme.items():
            assert len(value) == 7 and value.startswith("#"), name
            int(value[1:], 16)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_mail_palette.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.mail'`.
(If the container is not running, start it with the recipe in Task 3, Step 3.)

- [ ] **Step 3: Write the implementation**

Create `backend/app/services/mail/__init__.py`:

```python
"""Transactional email: configuration, templates, and delivery."""
```

Create `backend/app/services/mail/palette.py`:

```python
"""The app's colour tokens, transcoded for email.

`frontend/src/app/globals.css` authors the palette in `oklch()`, which no email
client supports, so these are hex copies converted at authoring time.

**They will not follow globals.css.** A build step that regenerated them is more
machinery than nine colours justify; the oklch source sits in the comment beside
each value, and `tests/test_mail_palette.py` re-runs the conversion so a typo is
caught. Drift from the app's real palette is not caught by anything — if you
change a token there, change it here.
"""

from __future__ import annotations

LIGHT: dict[str, str] = {
    "background": "#f0f9fb",          # oklch(0.975 0.01 220)
    "card": "#ffffff",                # oklch(1 0 0)
    "foreground": "#1b1630",          # oklch(0.22 0.05 290)
    "primary": "#007789",             # oklch(0.50 0.14 205)
    "primary_foreground": "#f4fefe",  # oklch(0.99 0.01 200)
    "muted_foreground": "#4d5e7a",    # oklch(0.48 0.05 260)
    "border": "#b4d9e0",              # oklch(0.86 0.04 210)
    "destructive": "#d7002d",         # oklch(0.54 0.24 20)
    "success": "#00791b",             # oklch(0.50 0.16 145)
}

DARK: dict[str, str] = {
    "background": "#0a0917",          # oklch(0.15 0.03 285)
    "card": "#121123",                # oklch(0.19 0.035 285)
    "foreground": "#e0f3f4",          # oklch(0.95 0.02 200)
    "primary": "#00edee",             # oklch(0.85 0.16 195)
    "primary_foreground": "#0a0917",  # oklch(0.15 0.03 285)
    "muted_foreground": "#89abb4",    # oklch(0.72 0.04 215)
    "border": "#2b2a46",              # oklch(0.30 0.05 285)
    "destructive": "#ff426d",         # oklch(0.70 0.24 15)
    "success": "#8aec41",             # oklch(0.85 0.22 135)
}

__all__ = ["DARK", "LIGHT"]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_mail_palette.py -p no:cacheprovider -p no:warnings
```
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/mail/ backend/tests/test_mail_palette.py
git commit -m "feat(mail): the app palette, transcoded for email

oklch() works in no mail client, so these are hex copies. The test re-runs the
conversion to catch a typo; nothing catches drift from globals.css, which is
why the module says so.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Templates and the embedded logo

The base template owns the table layout and every cross-client workaround, so
there is one place to fix when a client misbehaves. Rendering produces both an
HTML and a plain-text body — HTML-only mail is a spam signal.

**Files:**
- Create: `backend/app/services/mail/templates.py`
- Create: `backend/app/services/mail/templates/base.html.j2`
- Create: `backend/app/services/mail/templates/test_email.html.j2`
- Create: `backend/app/services/mail/templates/test_email.txt.j2`
- Create: `backend/app/services/mail/assets/logo-email.png`
- Test: `backend/tests/test_mail_templates.py`

**Interfaces:**
- Consumes: `LIGHT`, `DARK` from `app.services.mail.palette`.
- Produces:
  - `APP_NAME: str = "MegooPM"` — the product name used in subjects and bodies.
  - `LOGO_CID: str = "megoopm-logo"` — the Content-ID the sender attaches under.
  - `LOGO_PATH: Path` — the on-disk asset the sender reads.
  - `@dataclass(frozen=True) RenderedEmail: subject: str; html: str; text: str`
  - `render(name: str, *, subject: str, **context: object) -> RenderedEmail` —
    `name` is a template stem such as `"test_email"`.

- [ ] **Step 1: Produce the logo asset**

The committed `frontend/public/logo.png` is 512×512 and 180 KB — roughly thirty
times the bytes needed for something displayed at 48px. Downscale it once. This
uses Pillow in a throwaway container so **Pillow does not become a project
dependency**:

```bash
export MSYS_NO_PATHCONV=1
mkdir -p backend/app/services/mail/assets
docker run --rm -v "C:/Projects/megoopm:/repo" -w /repo python:3.12-slim sh -c "\
  pip install -q pillow && python -c \"
from PIL import Image
img = Image.open('frontend/public/logo.png').convert('RGBA')
img.resize((96, 96), Image.LANCZOS).save(
    'backend/app/services/mail/assets/logo-email.png', optimize=True
)
\""
ls -l backend/app/services/mail/assets/logo-email.png
```

Expected: a file well under 20 KB. If it is larger than 20 KB, stop and say so —
a heavier asset means the source has unexpected content and the size claim in
the spec is wrong.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_mail_templates.py`:

```python
"""Rendering tests. These open no socket and touch no database."""

from __future__ import annotations

import pytest

from app.services.mail.palette import DARK, LIGHT
from app.services.mail.templates import LOGO_CID, LOGO_PATH, render


def test_renders_both_a_html_and_a_text_body() -> None:
    # HTML-only mail is a spam signal, and a text client showing an empty body
    # is worse than a plain one showing the message.
    email = render("test_email", subject="Test", app_name="MegooPM")
    assert email.html.strip()
    assert email.text.strip()


def test_subject_is_carried_through() -> None:
    email = render("test_email", subject="MegooPM test email", app_name="MegooPM")
    assert email.subject == "MegooPM test email"


def test_light_colours_are_inlined_as_hex() -> None:
    # Inline, because a <style> block is stripped by Gmail in most contexts.
    email = render("test_email", subject="Test", app_name="MegooPM")
    assert LIGHT["primary"] in email.html
    assert LIGHT["background"] in email.html
    assert "oklch(" not in email.html


def test_dark_overrides_live_in_a_prefers_color_scheme_block() -> None:
    # Honoured by Apple Mail, iOS Mail and Outlook.com; ignored by Gmail, which
    # inverts the light design itself.
    email = render("test_email", subject="Test", app_name="MegooPM")
    assert "@media (prefers-color-scheme: dark)" in email.html
    assert DARK["background"] in email.html


def test_logo_is_referenced_by_content_id_not_a_url() -> None:
    # Remote images are blocked until the reader opts in, and a self-hosted
    # instance on an internal network is unreachable from their mail client.
    email = render("test_email", subject="Test", app_name="MegooPM")
    assert f'src="cid:{LOGO_CID}"' in email.html
    assert "http://" not in email.html.split("<body")[0]


def test_logo_carries_alt_text() -> None:
    # When images are blocked, the header still has to say who sent this.
    email = render("test_email", subject="Test", app_name="MegooPM")
    assert 'alt="MegooPM"' in email.html


def test_logo_asset_exists_and_is_small() -> None:
    assert LOGO_PATH.is_file()
    assert LOGO_PATH.stat().st_size < 20_000


def test_context_is_escaped_in_the_html_body() -> None:
    # A display name is attacker-influenced in later projects; autoescape must
    # be on, or an invite email becomes an HTML injection.
    email = render("test_email", subject="Test", app_name="<script>x</script>")
    assert "<script>x</script>" not in email.html
    assert "&lt;script&gt;" in email.html


def test_text_body_is_not_escaped() -> None:
    # Escaping in plain text renders "&lt;" to the reader as literal characters.
    email = render("test_email", subject="Test", app_name="A & B")
    assert "A & B" in email.text
    assert "&amp;" not in email.text


def test_a_subject_containing_a_newline_is_refused() -> None:
    # A newline in a subject lets an attacker append arbitrary headers — a Bcc
    # of their choosing — to every message the system sends.
    with pytest.raises(ValueError, match="newline"):
        render("test_email", subject="Test\r\nBcc: attacker@example.com", app_name="MegooPM")


def test_an_unknown_template_fails_loudly() -> None:
    with pytest.raises(Exception):
        render("no_such_template", subject="Test", app_name="MegooPM")
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_mail_templates.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.mail.templates'`.

- [ ] **Step 4: Write the renderer**

Create `backend/app/services/mail/templates.py`:

```python
"""Render a named email to an HTML + plain-text pair.

Two Jinja environments, deliberately: the HTML one autoescapes, the text one
must not. Escaping in plain text shows the reader a literal `&amp;`.

This module opens no socket and reads no database, so its tests are fast and
its failures are unambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.services.mail.palette import DARK, LIGHT

_TEMPLATE_DIR = Path(__file__).parent / "templates"

#: The product name as it appears in email. The backend `Settings` has no
#: `app_name`, and inventing a config field for a constant nobody varies would
#: be a knob with one position.
APP_NAME = "MegooPM"

#: Content-ID the logo is attached under; the templates reference `cid:` + this.
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
    append a `Bcc:` of their choosing to every message.
    """
    if "\r" in subject or "\n" in subject:
        raise ValueError("email subject must not contain a newline")
    return subject


def render(name: str, *, subject: str, **context: object) -> RenderedEmail:
    """Render `<name>.html.j2` and `<name>.txt.j2` with the shared context."""
    shared = {"light": LIGHT, "dark": DARK, "logo_cid": LOGO_CID, **context}
    return RenderedEmail(
        subject=_reject_newlines(subject),
        html=_html_env.get_template(f"{name}.html.j2").render(**shared),
        text=_text_env.get_template(f"{name}.txt.j2").render(**shared),
    )


__all__ = ["APP_NAME", "LOGO_CID", "LOGO_PATH", "RenderedEmail", "render"]
```

- [ ] **Step 5: Write the base layout**

Create `backend/app/services/mail/templates/base.html.j2`:

```jinja
{# Every cross-client workaround lives in this file.

   Tables, not flexbox: Outlook renders with the Word engine, which supports
   neither flex nor grid. Widths in px, not %, for the same reason.

   Colours are inlined; the <style> block below carries ONLY the dark-mode
   overrides, because Gmail strips <style> in most contexts and applies its own
   inversion instead. A light design that Gmail inverts reads acceptably, which
   is why light is the base and dark is the override. #}
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{{ self.subject_text() }}</title>
<style>
  @media (prefers-color-scheme: dark) {
    .m-page  { background: {{ dark.background }} !important; }
    .m-card  { background: {{ dark.card }} !important;
               border-color: {{ dark.border }} !important; }
    .m-text  { color: {{ dark.foreground }} !important; }
    .m-muted { color: {{ dark.muted_foreground }} !important; }
    .m-btn   { background: {{ dark.primary }} !important;
               color: {{ dark.primary_foreground }} !important; }
  }
</style>
</head>
<body class="m-page" style="margin:0;padding:0;background:{{ light.background }};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       class="m-page" style="background:{{ light.background }};">
  <tr>
    <td align="center" style="padding:32px 16px;">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0"
             class="m-card"
             style="width:560px;max-width:100%;background:{{ light.card }};
                    border:1px solid {{ light.border }};border-radius:12px;">
        <tr>
          <td align="center" style="padding:28px 32px 8px 32px;">
            {# cid:, not a URL: remote images are blocked until the reader opts
               in, and a self-hosted instance is unreachable from their client. #}
            <img src="cid:{{ logo_cid }}" alt="{{ app_name }}" width="48" height="48"
                 style="display:block;border:0;width:48px;height:48px;">
          </td>
        </tr>
        <tr>
          <td class="m-text"
              style="padding:8px 32px 28px 32px;color:{{ light.foreground }};
                     font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
                     font-size:15px;line-height:1.6;">
            {% block body %}{% endblock %}
          </td>
        </tr>
      </table>
      <table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0"
             style="width:560px;max-width:100%;">
        <tr>
          <td class="m-muted" align="center"
              style="padding:16px 32px;color:{{ light.muted_foreground }};
                     font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
                     font-size:12px;line-height:1.5;">
            Sent by {{ app_name }}.
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>
```

- [ ] **Step 6: Write the test-email templates**

Create `backend/app/services/mail/templates/test_email.html.j2`:

```jinja
{% extends "base.html.j2" %}
{% block subject_text %}{{ app_name }} test email{% endblock %}
{% block body %}
<p style="margin:0 0 16px 0;font-size:18px;font-weight:600;">Your mail server works.</p>
<p style="margin:0 0 16px 0;">
  This message was sent from {{ app_name }} to confirm the SMTP settings you just
  saved. Nothing else was changed.
</p>
<p class="m-muted" style="margin:0;color:{{ light.muted_foreground }};font-size:13px;">
  If you did not ask for this, someone with administrator access to
  {{ app_name }} did.
</p>
{% endblock %}
```

Create `backend/app/services/mail/templates/test_email.txt.j2`:

```jinja
Your mail server works.

This message was sent from {{ app_name }} to confirm the SMTP settings you just
saved. Nothing else was changed.

If you did not ask for this, someone with administrator access to {{ app_name }}
did.

--
Sent by {{ app_name }}.
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_mail_templates.py -p no:cacheprovider -p no:warnings
```
Expected: PASS, 11 tests.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/mail/ backend/tests/test_mail_templates.py
git commit -m "feat(mail): themed templates with an embedded logo

Tables and px widths because Outlook renders with the Word engine. Colours
inlined with a <style> block carrying only the dark overrides, because Gmail
strips <style> and inverts the light design itself.

Two Jinja environments: the HTML one autoescapes, the text one must not —
escaping in plain text shows the reader a literal &amp;.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: SMTP columns, migration and schemas

The database refuses a half-configured row as well as the API, in the same shape
as the existing `llm_needs_model` constraint.

**Files:**
- Modify: `backend/app/models/instance_settings.py`
- Create: `backend/alembic/versions/0024_smtp_settings.py`
- Modify: `backend/app/models/enums.py`
- Modify: `backend/app/schemas/instance_settings.py`
- Test: `backend/tests/test_smtp_schema.py`

**Interfaces:**
- Consumes: `validate_redirect_url` is *not* reused — see Step 5.
- Produces:
  - `SmtpSecurity(StrEnum)`: `starttls`, `ssl`, `none`
  - Columns `smtp_enabled`, `smtp_host`, `smtp_port`, `smtp_security`,
    `smtp_username`, `smtp_password_enc`, `smtp_from`, `smtp_from_name`, `app_url`
  - `SmtpSettingsUpdate` with field `smtp_password: str | None` (the plaintext
    in, distinguished keep/replace/clear by `model_fields_set`)
  - `MailTestRequest(to: EmailStr | None = None)`
  - `MailTestResult(ok: bool, detail: str = "", latency_ms: int = 0)`
  - `InstanceSettingsRead` gains all the above plus `smtp_password_set: bool`

- [ ] **Step 1: Write the failing schema tests**

Create `backend/tests/test_smtp_schema.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.enums import SmtpSecurity
from app.schemas.instance_settings import MailTestRequest, SmtpSettingsUpdate


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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_smtp_schema.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `ImportError: cannot import name 'SmtpSecurity'`.

- [ ] **Step 3: Start the test stack if it is not running**

The app imports `fcntl`, so pytest cannot run natively on Windows:

```bash
export MSYS_NO_PATHCONV=1
docker network create megoopm-testnet
docker run -d --name megoopm-testdb --network megoopm-testnet \
  -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm -e POSTGRES_DB=megoopm postgres:16-alpine
docker run -d --name megoopm-test --user root --network megoopm-testnet \
  -v "C:/Projects/megoopm/backend:/src" -w /src \
  -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
  -e DATABASE_URL="postgresql+asyncpg://megoopm:megoopm@megoopm-testdb:5432/megoopm" \
  --entrypoint sleep megoopm-backend infinity
docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" \
  "aiosqlite>=0.20" "ruff>=0.6" "maxminddb"
```

Run pytest **without** `-q` — `pyproject.toml` already sets it, and `-qq`
swallows the summary line.

- [ ] **Step 4: Add the enum**

In `backend/app/models/enums.py`, beside the existing enums:

```python
class SmtpSecurity(enum.StrEnum):
    """How the SMTP connection is secured."""

    #: Connect in the clear, then upgrade with STARTTLS. Port 587.
    starttls = "starttls"
    #: TLS from the first byte ("SMTPS"). Port 465.
    ssl = "ssl"
    #: No transport security. For a trusted local relay only.
    none = "none"
```

Add `"SmtpSecurity"` to that module's `__all__`.

- [ ] **Step 5: Add the schemas**

In `backend/app/schemas/instance_settings.py`, add near the top:

```python
def validate_app_url(value: str) -> str:
    """Accept only a plain absolute http(s) URL.

    Deliberately not `validate_redirect_url`: that one also bans quotes,
    backslash, ';' and '$' because its output is written into an nginx
    directive. This value never reaches a config file, and over-restricting it
    would reject legitimate URLs for a rule that does not apply.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError("app URL must not be empty")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in stripped):
        raise ValueError("app URL must not contain control characters")
    parsed = urlsplit(stripped)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError("app URL must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("app URL must include a host")
    return stripped.rstrip("/")


def reject_newlines(value: str, field: str) -> str:
    """Refuse a value that could inject an email header.

    A CR or LF ends the current header and begins another, letting an attacker
    append a `Bcc:` of their choosing to every message the system sends.
    """
    if "\r" in value or "\n" in value:
        raise ValueError(f"{field} must not contain a newline")
    return value
```

Add `from pydantic import EmailStr` to the imports and `SmtpSecurity` to the
enum import. Then add the three models:

```python
class SmtpSettingsUpdate(BaseModel):
    """Set the SMTP group. Carries the whole card; the password is the exception.

    `smtp_enabled` is required for the same reason `default_site_mode` is on its
    sibling: "enabled needs a host" cannot be checked against a payload that
    omits it, and a schema never sees the stored row.

    `smtp_password` is never returned, so a client has nothing to send back.
    Absent keeps the stored password; a string replaces it; an explicit `null`
    clears it — distinguished with `model_fields_set`, which is why the service
    is handed `model_dump(exclude_unset=True)`.
    """

    smtp_enabled: bool
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: SmtpSecurity = SmtpSecurity.starttls
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_from_name: str | None = None
    app_url: str | None = None

    @field_validator(
        "smtp_host", "smtp_username", "smtp_password", "smtp_from", "smtp_from_name", "app_url"
    )
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """An empty input box means "not set", not "the empty string"."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("smtp_from")
    @classmethod
    def _clean_from(cls, value: str | None) -> str | None:
        return None if value is None else reject_newlines(value, "smtp_from")

    @field_validator("smtp_from_name")
    @classmethod
    def _clean_from_name(cls, value: str | None) -> str | None:
        return None if value is None else reject_newlines(value, "smtp_from_name")

    @field_validator("app_url")
    @classmethod
    def _clean_app_url(cls, value: str | None) -> str | None:
        return None if value is None else validate_app_url(value)

    @model_validator(mode="after")
    def _coherent(self) -> SmtpSettingsUpdate:
        """Mirror the database CHECK constraint, with a usable message."""
        if self.smtp_enabled and not self.smtp_host:
            raise ValueError("smtp_host is required when smtp_enabled is true")
        if self.smtp_enabled and not self.smtp_from:
            raise ValueError("smtp_from is required when smtp_enabled is true")
        return self


class MailTestRequest(BaseModel):
    """Where to send the test. Omitted means the requesting admin's own address."""

    to: EmailStr | None = None


class MailTestResult(BaseModel):
    """The send's outcome. `ok: false` still returns HTTP 200 — see the route."""

    ok: bool
    detail: str = ""
    latency_ms: int = 0
```

Add these fields to `InstanceSettingsRead` and to `from_row`:

```python
    smtp_enabled: bool
    smtp_host: str | None
    smtp_port: int
    smtp_security: SmtpSecurity
    smtp_username: str | None
    smtp_password_set: bool
    smtp_from: str | None
    smtp_from_name: str | None
    app_url: str | None
```

```python
            smtp_enabled=row.smtp_enabled,
            smtp_host=row.smtp_host,
            smtp_port=row.smtp_port,
            smtp_security=row.smtp_security,
            smtp_username=row.smtp_username,
            smtp_password_set=row.smtp_password_enc is not None,
            smtp_from=row.smtp_from,
            smtp_from_name=row.smtp_from_name,
            app_url=row.app_url,
```

Extend the module's `__all__` with `"MailTestRequest"`, `"MailTestResult"`,
`"SmtpSettingsUpdate"`, `"reject_newlines"`, `"validate_app_url"`.

- [ ] **Step 6: Add the columns**

In `backend/app/models/instance_settings.py`, import `SmtpSecurity` from
`app.models.enums`, add the constraint to `__table_args__`:

```python
        CheckConstraint(
            "smtp_enabled = false OR smtp_host IS NOT NULL",
            name="smtp_needs_host",
        ),
```

and append the columns:

```python
    # --- Outbound email -------------------------------------------------
    # Off by default: an upgrade must never start a reverse proxy's admin
    # backend talking to a mail server nobody configured.
    smtp_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    smtp_host: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_port: Mapped[int] = mapped_column(
        Integer, nullable=False, default=587, server_default="587"
    )
    smtp_security: Mapped[SmtpSecurity] = mapped_column(
        Enum(
            SmtpSecurity,
            name="smtp_security",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=SmtpSecurity.starttls,
        server_default=SmtpSecurity.starttls.value,
    )
    smtp_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fernet token (app.core.crypto), never plaintext — as llm_api_key_enc.
    smtp_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_from_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # This instance's public URL. Unused in P1 — stored here so the operator
    # sets it in the same sitting as the mail server. Password reset and
    # invitations build links with it; passkeys derive the RP ID from it.
    app_url: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 7: Write the migration**

Create `backend/alembic/versions/0024_smtp_settings.py`:

```python
"""Outbound email settings on the instance-settings singleton

Nine columns: the SMTP connection, the Fernet-encrypted password, the From
identity, and this instance's public URL.

Seeded off. Enabling by migration would make the backend start talking to a
mail server nobody configured because an upgrade shipped.

Revision ID: 0024_smtp_settings
Revises: 0023_visitor_day
Create Date: 2026-09-03 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024_smtp_settings"
down_revision: str | None = "0023_visitor_day"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# op.add_column does NOT emit CREATE TYPE for an enum — only create_table does.
# The type is therefore created and dropped by hand here.
_SECURITY = sa.Enum("starttls", "ssl", "none", name="smtp_security")


def upgrade() -> None:
    bind = op.get_bind()
    _SECURITY.create(bind, checkfirst=True)
    op.add_column(
        "instance_settings",
        sa.Column("smtp_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("instance_settings", sa.Column("smtp_host", sa.Text(), nullable=True))
    op.add_column(
        "instance_settings",
        sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="587"),
    )
    op.add_column(
        "instance_settings",
        sa.Column("smtp_security", _SECURITY, nullable=False, server_default="starttls"),
    )
    op.add_column("instance_settings", sa.Column("smtp_username", sa.Text(), nullable=True))
    op.add_column("instance_settings", sa.Column("smtp_password_enc", sa.Text(), nullable=True))
    op.add_column("instance_settings", sa.Column("smtp_from", sa.Text(), nullable=True))
    op.add_column("instance_settings", sa.Column("smtp_from_name", sa.Text(), nullable=True))
    op.add_column("instance_settings", sa.Column("app_url", sa.Text(), nullable=True))
    # Bare name: the ck_%(table_name)s_%(constraint_name)s convention is applied
    # by alembic, so an expanded name would be double-prefixed.
    op.create_check_constraint(
        "smtp_needs_host",
        "instance_settings",
        "smtp_enabled = false OR smtp_host IS NOT NULL",
    )


def downgrade() -> None:
    # The constraint goes first: dropping a column it references would fail.
    op.drop_constraint(
        op.f("ck_instance_settings_smtp_needs_host"), "instance_settings", type_="check"
    )
    op.drop_column("instance_settings", "app_url")
    op.drop_column("instance_settings", "smtp_from_name")
    op.drop_column("instance_settings", "smtp_from")
    op.drop_column("instance_settings", "smtp_password_enc")
    op.drop_column("instance_settings", "smtp_username")
    op.drop_column("instance_settings", "smtp_security")
    op.drop_column("instance_settings", "smtp_port")
    op.drop_column("instance_settings", "smtp_host")
    op.drop_column("instance_settings", "smtp_enabled")
    _SECURITY.drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 8: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_smtp_schema.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
```
Expected: the schema tests pass, and the whole suite still passes except
`tests/test_openapi.py::test_committed_openapi_is_in_sync`, which Task 5
regenerates. If any *other* test fails, stop.

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/ backend/app/schemas/instance_settings.py \
        backend/alembic/versions/0024_smtp_settings.py backend/tests/test_smtp_schema.py
git commit -m "feat(settings): SMTP columns, schemas and migration

The database refuses a half-configured row as well as the API, in the shape of
the existing llm_needs_model constraint.

app_url gets its own validator rather than reusing validate_redirect_url: that
one also bans quotes, backslash, ';' and '\$' because its output lands in an
nginx directive. This value never reaches a config file, and the extra rules
would reject legitimate URLs.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The mailer

`config.py` turns a settings row into something the sender can use; `sender.py`
builds the MIME message and talks SMTP. Neither renders a template.

**Files:**
- Create: `backend/app/services/mail/config.py`
- Create: `backend/app/services/mail/sender.py`
- Modify: `backend/app/services/instance_settings.py`
- Test: `backend/tests/test_mail_sender.py`

**Interfaces:**
- Consumes: `RenderedEmail`, `LOGO_CID`, `LOGO_PATH` from
  `app.services.mail.templates`; `SmtpSecurity` from `app.models.enums`;
  `decrypt_secret`/`encrypt_secret` from `app.core.crypto`.
- Produces:
  - `MailNotConfigured(RuntimeError)`
  - `@dataclass(frozen=True) MailConfig: host, port, security, username, password, from_address, from_name`
  - `mail_config_from_row(row) -> MailConfig` in `app/services/instance_settings.py`
  - `update_smtp_settings(db, changes) -> InstanceSettings` in the same module
  - `send_email(config: MailConfig, *, to: str, email: RenderedEmail, timeout: float = 15.0) -> None`
  - `build_message(config, *, to, email) -> EmailMessage` — exposed so tests can
    inspect the MIME without connecting to anything.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_mail_sender.py`:

```python
"""Sender tests. No fake SMTP server, so no dev dependency: `smtplib.SMTP` and
`smtplib.SMTP_SSL` are replaced with recorders."""

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

    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent: Any = None
        self.quit_called = False
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
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
    logo = [p for p in message.walk() if p.get("Content-ID") == f"<{LOGO_CID}>"][0]
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_mail_sender.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.mail.config'`.

- [ ] **Step 3: Write the config module**

Create `backend/app/services/mail/config.py`:

```python
"""What one send needs, and the error for when it is missing.

Holds no session and no ORM row, so the sender is trivial to fake in a test.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import SmtpSecurity


class MailNotConfigured(RuntimeError):
    """No SMTP host is configured, so nothing can be sent."""


@dataclass(frozen=True, slots=True)
class MailConfig:
    """A resolved SMTP configuration with the password already decrypted."""

    host: str | None
    port: int
    security: SmtpSecurity
    username: str | None
    password: str | None
    from_address: str | None
    from_name: str | None


__all__ = ["MailConfig", "MailNotConfigured"]
```

- [ ] **Step 4: Write the sender**

Create `backend/app/services/mail/sender.py`:

```python
"""Build the MIME message and hand it to SMTP.

Stdlib `smtplib` rather than `aiosmtplib`: the same code has to run from an
async route and (from P2 onward) a sync Celery task. `asyncio.to_thread` bridges
sync-into-async in one line; the reverse means running an event loop inside a
worker. It also keeps this project at zero new dependencies.
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
    # this order is what makes the part `multipart/alternative`.
    message.set_content(email.text)
    message.add_alternative(email.html, subtype="html")

    # The logo joins the HTML part, making it multipart/related — the structure
    # a client needs to resolve `cid:` without fetching anything remote.
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


def send_email(
    config: MailConfig, *, to: str, email: RenderedEmail, timeout: float = 15.0
) -> None:
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
```

- [ ] **Step 5: Add the service helpers**

In `backend/app/services/instance_settings.py`, import `MailConfig` from
`app.services.mail.config` and `SmtpSecurity` from `app.models.enums`, then add
beside the LLM helpers:

```python
async def update_smtp_settings(db: AsyncSession, changes: dict[str, Any]) -> InstanceSettings:
    """Apply an SMTP settings payload, encrypting the password on the way in.

    `changes` must come from `model_dump(exclude_unset=True)`: the presence or
    absence of `smtp_password` is the signal for keep-vs-replace-vs-clear, and a
    plain dump would flatten "absent" into `None` and wipe a working password on
    every save.
    """
    row = await get_instance_settings(db)

    row.smtp_enabled = changes["smtp_enabled"]
    row.smtp_host = changes.get("smtp_host")
    row.smtp_port = changes.get("smtp_port", 587)
    row.smtp_security = changes.get("smtp_security", SmtpSecurity.starttls)
    row.smtp_username = changes.get("smtp_username")
    row.smtp_from = changes.get("smtp_from")
    row.smtp_from_name = changes.get("smtp_from_name")
    row.app_url = changes.get("app_url")

    if "smtp_password" in changes:
        password = changes["smtp_password"]
        row.smtp_password_enc = encrypt_secret(password) if password else None

    await db.commit()
    await db.refresh(row)
    return row


def mail_config_from_row(row: InstanceSettings) -> MailConfig:
    """Decrypt the stored password into a config the sender can use."""
    return MailConfig(
        host=row.smtp_host,
        port=row.smtp_port,
        security=row.smtp_security,
        username=row.smtp_username,
        password=decrypt_secret(row.smtp_password_enc) if row.smtp_password_enc else None,
        from_address=row.smtp_from,
        from_name=row.smtp_from_name,
    )
```

Extend that module's `__all__` with `"mail_config_from_row"` and
`"update_smtp_settings"`.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_mail_sender.py -p no:cacheprovider -p no:warnings
```
Expected: PASS, 14 tests.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/mail/ backend/app/services/instance_settings.py \
        backend/tests/test_mail_sender.py
git commit -m "feat(mail): MIME assembly and SMTP delivery

Stdlib smtplib, not aiosmtplib: the same code runs from an async route and,
from P2, a sync Celery task. to_thread bridges one direction in a line; the
other means an event loop inside a worker.

The logo is added to the HTML part rather than the message, which is what makes
that part multipart/related — the structure a client needs to resolve cid:
without fetching anything remote.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The routes

`PATCH /settings/smtp` and `POST /settings/smtp/test`, in the shape of the LLM
pair beside them. The test send is synchronous: the operator is standing there
and needs the real SMTP error.

**Files:**
- Modify: `backend/app/api/routes/settings.py`
- Modify: `backend/openapi.json` (regenerated)
- Test: `backend/tests/test_smtp_api.py`

**Interfaces:**
- Consumes: `SmtpSettingsUpdate`, `MailTestRequest`, `MailTestResult`,
  `InstanceSettingsRead` from `app.schemas.instance_settings`;
  `update_smtp_settings`, `mail_config_from_row` from
  `app.services.instance_settings`; `send_email` from `app.services.mail.sender`;
  `render` from `app.services.mail.templates`; `MailNotConfigured`.
- Produces: the two routes, plus `smtp_password_set` on every settings response.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_smtp_api.py`:

```python
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.services.mail import sender as sender_module

HEADERS = "Authorization"


def _payload(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "smtp_enabled": True,
        "smtp_host": "mail.example.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "smtp_username": "user",
        "smtp_password": "secret",
        "smtp_from": "megoopm@example.com",
        "smtp_from_name": "MegooPM",
        "app_url": "https://pm.example.com",
    }
    base.update(over)
    return base


async def test_saving_smtp_settings_returns_them_without_the_password(
    db_client: AsyncClient, admin_token: str
) -> None:
    resp = await db_client.patch(
        "/api/v1/settings/smtp",
        headers={HEADERS: f"Bearer {admin_token}"},
        json=_payload(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["smtp_host"] == "mail.example.com"
    assert body["smtp_password_set"] is True
    # The value itself is never returned, so a compromised session cannot read
    # it back out.
    assert "smtp_password" not in body
    assert "secret" not in resp.text


async def test_omitting_the_password_keeps_the_stored_one(
    db_client: AsyncClient, admin_token: str
) -> None:
    hdr = {HEADERS: f"Bearer {admin_token}"}
    await db_client.patch("/api/v1/settings/smtp", headers=hdr, json=_payload())

    without = _payload()
    del without["smtp_password"]
    resp = await db_client.patch("/api/v1/settings/smtp", headers=hdr, json=without)

    assert resp.json()["smtp_password_set"] is True


async def test_an_explicit_null_clears_the_stored_password(
    db_client: AsyncClient, admin_token: str
) -> None:
    hdr = {HEADERS: f"Bearer {admin_token}"}
    await db_client.patch("/api/v1/settings/smtp", headers=hdr, json=_payload())

    resp = await db_client.patch(
        "/api/v1/settings/smtp", headers=hdr, json=_payload(smtp_password=None)
    )

    assert resp.json()["smtp_password_set"] is False


async def test_enabling_without_a_host_is_rejected(
    db_client: AsyncClient, admin_token: str
) -> None:
    resp = await db_client.patch(
        "/api/v1/settings/smtp",
        headers={HEADERS: f"Bearer {admin_token}"},
        json=_payload(smtp_host=None),
    )
    assert resp.status_code == 422


async def test_settings_routes_are_admin_only(
    db_client: AsyncClient, member_token: str
) -> None:
    resp = await db_client.patch(
        "/api/v1/settings/smtp",
        headers={HEADERS: f"Bearer {member_token}"},
        json=_payload(),
    )
    assert resp.status_code == 403


async def test_a_successful_test_send_reports_ok(
    db_client: AsyncClient, admin_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: dict[str, object] = {}

    def fake_send(config, *, to, email, timeout=15.0):
        sent["to"] = to
        sent["subject"] = email.subject

    monkeypatch.setattr(sender_module, "send_email", fake_send)
    hdr = {HEADERS: f"Bearer {admin_token}"}
    await db_client.patch("/api/v1/settings/smtp", headers=hdr, json=_payload())

    resp = await db_client.post(
        "/api/v1/settings/smtp/test", headers=hdr, json={"to": "ops@example.com"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert sent["to"] == "ops@example.com"


async def test_the_test_send_defaults_to_the_requesting_admin(
    db_client: AsyncClient, admin_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # "Send it to me" is the common case and should need no typing.
    sent: dict[str, object] = {}
    monkeypatch.setattr(
        sender_module,
        "send_email",
        lambda config, *, to, email, timeout=15.0: sent.update(to=to),
    )
    hdr = {HEADERS: f"Bearer {admin_token}"}
    await db_client.patch("/api/v1/settings/smtp", headers=hdr, json=_payload())

    resp = await db_client.post("/api/v1/settings/smtp/test", headers=hdr, json={})

    assert resp.status_code == 200, resp.text
    assert "@" in str(sent["to"])


async def test_a_failed_send_returns_200_with_ok_false(
    db_client: AsyncClient, admin_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The API call succeeded; the mail server did not. An error status would
    # make a working endpoint indistinguishable from a broken one in monitoring.
    def boom(config, *, to, email, timeout=15.0):
        raise OSError("Connection refused")

    monkeypatch.setattr(sender_module, "send_email", boom)
    hdr = {HEADERS: f"Bearer {admin_token}"}
    await db_client.patch("/api/v1/settings/smtp", headers=hdr, json=_payload())

    resp = await db_client.post("/api/v1/settings/smtp/test", headers=hdr, json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "Connection refused" in body["detail"]


async def test_testing_with_nothing_configured_reports_it(
    db_client: AsyncClient, admin_token: str
) -> None:
    resp = await db_client.post(
        "/api/v1/settings/smtp/test",
        headers={HEADERS: f"Bearer {admin_token}"},
        json={},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "configured" in body["detail"].lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_smtp_api.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — 404 on `/api/v1/settings/smtp`.

- [ ] **Step 3: Add the routes**

In `backend/app/api/routes/settings.py`, extend the schema import with
`MailTestRequest`, `MailTestResult`, `SmtpSettingsUpdate`, and add:

```python
import time

from app.services.mail import sender as mail_sender
from app.services.mail.config import MailNotConfigured
from app.services.mail.templates import APP_NAME, render as render_email
```

Then, after the LLM routes:

```python
@router.patch("/smtp", response_model=InstanceSettingsRead)
async def update_smtp_settings(
    body: SmtpSettingsUpdate, admin: AdminUser, db: SessionDep
) -> InstanceSettingsRead:
    """Configure outbound email. Admin-only.

    ``exclude_unset`` is load-bearing: it is what tells the service the
    difference between "the client did not send a password" and "the client
    cleared the password".
    """
    changes = body.model_dump(exclude_unset=True)
    row = await settings_service.update_smtp_settings(db, changes)
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.update,
        object_type="instance_settings",
        object_id=row.id,
        # Field names and non-secret values only — never the password.
        meta={
            "smtp_enabled": row.smtp_enabled,
            "smtp_host": row.smtp_host,
            "smtp_password_changed": "smtp_password" in changes,
        },
    )
    await db.commit()
    return InstanceSettingsRead.from_row(row)


@router.post("/smtp/test", response_model=MailTestResult)
async def send_test_email(
    body: MailTestRequest, admin: AdminUser, db: SessionDep
) -> MailTestResult:
    """Send one themed test message. Admin-only.

    Synchronous on purpose. The operator is on the Settings page waiting, and
    needs the actual SMTP error — "authentication failed", "connection refused"
    — not a task id to go and poll. Real notifications (P2 onward) go through
    Celery instead, so a slow mail server never fails a user-facing action.

    A failed send returns **200 with ``ok: false``**, not a 4xx or 5xx: the API
    call succeeded, the mail server did not. An error status would make a
    working endpoint indistinguishable from a broken one in monitoring.
    """
    row = await settings_service.get_instance_settings(db)
    config = settings_service.mail_config_from_row(row)
    recipient = body.to or admin.email
    email = render_email("test_email", subject=f"{APP_NAME} test email", app_name=APP_NAME)

    started = time.perf_counter()
    try:
        # to_thread because smtplib blocks: without it a slow mail server would
        # stall the whole event loop, not just this request.
        await asyncio.to_thread(
            mail_sender.send_email, config, to=recipient, email=email
        )
    except MailNotConfigured as exc:
        return MailTestResult(ok=False, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - any SMTP failure is a result here
        return MailTestResult(
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    return MailTestResult(
        ok=True,
        detail=f"Sent to {recipient}.",
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
```

Add `import asyncio` at the top. `record_audit`, `AuditAction`, `AdminUser` and
`settings_service` are already imported by this module; nothing else is needed.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_smtp_api.py -p no:cacheprovider -p no:warnings
```
Expected: PASS, 9 tests.

- [ ] **Step 5: Regenerate the OpenAPI document and run everything**

```bash
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
```
Expected: the whole suite passes, including
`test_openapi.py::test_committed_openapi_is_in_sync`, and ruff is clean on the
touched files. `ruff format --check` reports pre-existing failures on files this
task did not touch — check only your own.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/settings.py backend/tests/test_smtp_api.py backend/openapi.json
git commit -m "feat(settings): SMTP configuration and a test send

The test send is synchronous because the operator is waiting and needs the real
SMTP error, not a task id. It runs in a thread: smtplib blocks, and without
to_thread a slow mail server would stall the event loop rather than one request.

A failed send is 200 with ok:false. The API call succeeded; the mail server did
not, and an error status would make a working endpoint indistinguishable from a
broken one in monitoring.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The Settings card

A card in the shape of the LLM one beside it, with the same password convention:
blank means "keep the stored one", and the card reports whether one exists
rather than what it is.

**Files:**
- Modify: `frontend/src/lib/api/resources/settings.ts`
- Modify: `frontend/src/components/settings/lib.ts`
- Create: `frontend/src/components/settings/smtp-card.tsx`
- Modify: `frontend/src/components/settings/settings-view.tsx`
- Modify: `frontend/src/lib/api/generated/schema.ts` (regenerated)
- Test: `frontend/src/components/settings/lib.test.ts` (append)
- Test: `frontend/src/components/settings/smtp-card.test.tsx`

**Interfaces:**
- Consumes: `instanceSettings.updateSmtp`, `instanceSettings.testSmtp`.
- Produces:
  - `SmtpFormState` — `{ enabled, host, port, security, username, password, passwordIsSet, passwordCleared, from, fromName, appUrl }`
  - `smtpStateFromSettings(settings) -> SmtpFormState`
  - `validateSmtpForm(state) -> string | null`
  - `buildSmtpPayload(state) -> SmtpSettingsUpdate`

- [ ] **Step 1: Regenerate the types**

```bash
cd frontend && npm run gen:api
```

- [ ] **Step 2: Add the API calls**

In `frontend/src/lib/api/resources/settings.ts`, add the types and calls:

```ts
export type SmtpSettingsUpdate = Schemas["SmtpSettingsUpdate"];
export type SmtpSecurity = Schemas["SmtpSecurity"];
export type MailTestRequest = Schemas["MailTestRequest"];
export type MailTestResult = Schemas["MailTestResult"];
```

```ts
  updateSmtp: (body: SmtpSettingsUpdate) => api.patch<InstanceSettings>(`${BASE}/smtp`, body),
  /**
   * Sends one real message using the *stored* settings, so save first. A
   * failure comes back as `ok: false` with HTTP 200 — the API call succeeded,
   * the mail server did not.
   */
  testSmtp: (body: MailTestRequest) => api.post<MailTestResult>(`${BASE}/smtp/test`, body),
```

Re-export the four types from `frontend/src/lib/api/index.ts` beside the LLM ones.

- [ ] **Step 3: Write the failing helper tests**

Append to `frontend/src/components/settings/lib.test.ts`:

```ts
describe("smtpStateFromSettings", () => {
  const settings = {
    smtp_enabled: true,
    smtp_host: "mail.example.com",
    smtp_port: 587,
    smtp_security: "starttls",
    smtp_username: "user",
    smtp_password_set: true,
    smtp_from: "megoopm@example.com",
    smtp_from_name: "MegooPM",
    app_url: "https://pm.example.com",
  } as unknown as InstanceSettings;

  it("starts the password field empty even when one is stored", () => {
    // The password is never returned, so there is nothing to prefill.
    const state = smtpStateFromSettings(settings);
    expect(state.password).toBe("");
    expect(state.passwordIsSet).toBe(true);
  });

  it("carries the rest of the configuration through", () => {
    const state = smtpStateFromSettings(settings);
    expect(state.host).toBe("mail.example.com");
    expect(state.port).toBe("587");
    expect(state.security).toBe("starttls");
  });
});

describe("validateSmtpForm", () => {
  function state(over: Partial<SmtpFormState> = {}): SmtpFormState {
    return {
      enabled: true,
      host: "mail.example.com",
      port: "587",
      security: "starttls",
      username: "",
      password: "",
      passwordIsSet: false,
      passwordCleared: false,
      from: "megoopm@example.com",
      fromName: "",
      appUrl: "",
      ...over,
    };
  }

  it("accepts a complete configuration", () => {
    expect(validateSmtpForm(state())).toBeNull();
  });

  it("refuses enabling without a host", () => {
    expect(validateSmtpForm(state({ host: "" }))).toMatch(/host/i);
  });

  it("refuses enabling without a from address", () => {
    expect(validateSmtpForm(state({ from: "" }))).toMatch(/from/i);
  });

  it("refuses a port outside the valid range", () => {
    expect(validateSmtpForm(state({ port: "70000" }))).toMatch(/port/i);
  });

  it("asks for nothing while delivery is switched off", () => {
    expect(validateSmtpForm(state({ enabled: false, host: "", from: "" }))).toBeNull();
  });
});

describe("buildSmtpPayload", () => {
  function state(over: Partial<SmtpFormState> = {}): SmtpFormState {
    return {
      enabled: true,
      host: "mail.example.com",
      port: "587",
      security: "starttls",
      username: "user",
      password: "",
      passwordIsSet: true,
      passwordCleared: false,
      from: "megoopm@example.com",
      fromName: "MegooPM",
      appUrl: "https://pm.example.com",
      ...over,
    };
  }

  it("omits the password entirely when the field was left blank", () => {
    // Sending null would wipe a working password on every save.
    expect("smtp_password" in buildSmtpPayload(state())).toBe(false);
  });

  it("sends a typed password", () => {
    expect(buildSmtpPayload(state({ password: "hunter2" })).smtp_password).toBe("hunter2");
  });

  it("sends an explicit null when the stored password was removed", () => {
    const payload = buildSmtpPayload(state({ passwordCleared: true, passwordIsSet: false }));
    expect("smtp_password" in payload).toBe(true);
    expect(payload.smtp_password).toBeNull();
  });

  it("sends the port as a number", () => {
    expect(buildSmtpPayload(state()).smtp_port).toBe(587);
  });
});
```

Add the new names to that file's import from `@/components/settings/lib`.

- [ ] **Step 4: Run them to verify they fail**

```bash
cd frontend && npx vitest run src/components/settings/lib.test.ts
```
Expected: FAIL — `smtpStateFromSettings is not exported`.

- [ ] **Step 5: Write the helpers**

Append to `frontend/src/components/settings/lib.ts`:

```ts
export type SmtpFormState = {
  enabled: boolean;
  host: string;
  port: string;
  security: SmtpSecurity;
  username: string;
  /** Always starts empty: the stored password is never returned. */
  password: string;
  passwordIsSet: boolean;
  /** True once the operator removed the stored password, so we send null. */
  passwordCleared: boolean;
  from: string;
  fromName: string;
  appUrl: string;
};

export function smtpStateFromSettings(settings: InstanceSettings): SmtpFormState {
  return {
    enabled: settings.smtp_enabled,
    host: settings.smtp_host ?? "",
    port: String(settings.smtp_port ?? 587),
    security: settings.smtp_security ?? "starttls",
    username: settings.smtp_username ?? "",
    password: "",
    passwordIsSet: settings.smtp_password_set,
    passwordCleared: false,
    from: settings.smtp_from ?? "",
    fromName: settings.smtp_from_name ?? "",
    appUrl: settings.app_url ?? "",
  };
}

export function validateSmtpForm(state: SmtpFormState): string | null {
  const port = Number(state.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    return "Port must be a whole number between 1 and 65535.";
  }
  // Only when delivery is on: a switched-off card should not nag for fields
  // nothing is going to use.
  if (!state.enabled) return null;
  if (!state.host.trim()) return "A host is required to send mail.";
  if (!state.from.trim()) return "A from address is required to send mail.";
  return null;
}

export function buildSmtpPayload(state: SmtpFormState): SmtpSettingsUpdate {
  const payload: SmtpSettingsUpdate = {
    smtp_enabled: state.enabled,
    smtp_host: state.host.trim() || null,
    smtp_port: Number(state.port),
    smtp_security: state.security,
    smtp_username: state.username.trim() || null,
    smtp_from: state.from.trim() || null,
    smtp_from_name: state.fromName.trim() || null,
    app_url: state.appUrl.trim() || null,
  };
  // Three states, not two. Absent keeps the stored password; a string replaces
  // it; an explicit null clears it. Always sending the key would wipe a working
  // password every time the card is saved.
  if (state.password) payload.smtp_password = state.password;
  else if (state.passwordCleared) payload.smtp_password = null;
  return payload;
}
```

Import `SmtpSecurity` and `SmtpSettingsUpdate` from `@/lib/api` at the top of
that file.

- [ ] **Step 6: Run them to verify they pass**

```bash
cd frontend && npx vitest run src/components/settings/lib.test.ts
```
Expected: PASS.

- [ ] **Step 7: Write the failing card tests**

Create `frontend/src/components/settings/smtp-card.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { instanceSettings, type InstanceSettings } from "@/lib/api";
import { SmtpCard } from "@/components/settings/smtp-card";

function makeSettings(over: Partial<InstanceSettings> = {}): InstanceSettings {
  return {
    default_site_mode: "not_found",
    default_site_redirect_url: null,
    default_site_page_id: null,
    crowdsec_ban_mode: "megoopm",
    crowdsec_ban_page_id: null,
    llm_enabled: false,
    llm_model: null,
    llm_api_base: null,
    llm_api_key_set: false,
    smtp_enabled: false,
    smtp_host: null,
    smtp_port: 587,
    smtp_security: "starttls",
    smtp_username: null,
    smtp_password_set: false,
    smtp_from: null,
    smtp_from_name: null,
    app_url: null,
    updated_at: "2026-09-03T00:00:00Z",
    ...over,
  } as InstanceSettings;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SmtpCard", () => {
  it("says no password is stored on a fresh instance", () => {
    render(<SmtpCard settings={makeSettings()} onSaved={() => {}} />);
    expect(screen.getByText(/no password stored/i)).toBeInTheDocument();
  });

  it("reports a stored password without showing it", () => {
    render(
      <SmtpCard
        settings={makeSettings({ smtp_password_set: true, smtp_host: "mail.example.com" })}
        onSaved={() => {}}
      />,
    );
    expect(screen.getByText(/a password is stored/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("refuses to save an enabled card with no host", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(instanceSettings, "updateSmtp");
    render(<SmtpCard settings={makeSettings()} onSaved={() => {}} />);

    await user.click(screen.getByRole("switch", { name: /send email/i }));
    await user.click(screen.getByRole("button", { name: /save email settings/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/host/i);
    expect(update).not.toHaveBeenCalled();
  });

  it("shows the failure detail when a test send fails", async () => {
    // The whole point of the button: the operator sees the real SMTP error.
    const user = userEvent.setup();
    vi.spyOn(instanceSettings, "testSmtp").mockResolvedValue({
      ok: false,
      detail: "SMTPAuthenticationError: bad credentials",
      latency_ms: 12,
    });
    render(
      <SmtpCard
        settings={makeSettings({ smtp_enabled: true, smtp_host: "mail.example.com" })}
        onSaved={() => {}}
      />,
    );

    await user.click(screen.getByRole("button", { name: /send test email/i }));

    expect(await screen.findByText(/bad credentials/i)).toBeInTheDocument();
  });

  it("confirms a successful test send", async () => {
    const user = userEvent.setup();
    vi.spyOn(instanceSettings, "testSmtp").mockResolvedValue({
      ok: true,
      detail: "Sent to ops@example.com.",
      latency_ms: 340,
    });
    render(
      <SmtpCard
        settings={makeSettings({ smtp_enabled: true, smtp_host: "mail.example.com" })}
        onSaved={() => {}}
      />,
    );

    await user.click(screen.getByRole("button", { name: /send test email/i }));

    expect(await screen.findByText(/sent to ops@example\.com/i)).toBeInTheDocument();
  });

  it("saves the whole card and reports the new settings upward", async () => {
    const user = userEvent.setup();
    const saved = makeSettings({ smtp_enabled: true, smtp_host: "mail.example.com" });
    const update = vi.spyOn(instanceSettings, "updateSmtp").mockResolvedValue(saved);
    const onSaved = vi.fn();
    render(
      <SmtpCard
        settings={makeSettings({ smtp_enabled: true, smtp_host: "mail.example.com",
                                smtp_from: "megoopm@example.com" })}
        onSaved={onSaved}
      />,
    );

    await user.click(screen.getByRole("button", { name: /save email settings/i }));

    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(onSaved).toHaveBeenCalledWith(saved);
  });
});
```

- [ ] **Step 8: Run them to verify they fail**

```bash
cd frontend && npx vitest run src/components/settings/smtp-card.test.tsx
```
Expected: FAIL — `Failed to resolve import "@/components/settings/smtp-card"`.

- [ ] **Step 9: Write the card**

Create `frontend/src/components/settings/smtp-card.tsx`:

```tsx
"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import {
  instanceSettings,
  type InstanceSettings,
  type MailTestResult,
  type SmtpSecurity,
} from "@/lib/api";
import {
  buildSmtpPayload,
  describeError,
  smtpStateFromSettings,
  validateSmtpForm,
  type SmtpFormState,
} from "@/components/settings/lib";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const SECURITY_LABELS: Record<SmtpSecurity, string> = {
  starttls: "STARTTLS (usually port 587)",
  ssl: "TLS from connect (usually port 465)",
  none: "None — trusted local relay only",
};

/**
 * Configure outbound email.
 *
 * Owns its own state and save, like the cards beside it. The password is the
 * awkward part: it is never returned, so the field starts empty and the card
 * reports whether one is stored rather than what it is.
 */
export function SmtpCard({
  settings,
  onSaved,
}: {
  settings: InstanceSettings;
  onSaved: (settings: InstanceSettings) => void;
}) {
  const [form, setForm] = useState<SmtpFormState>(() => smtpStateFromSettings(settings));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<MailTestResult | null>(null);

  function patch(changes: Partial<SmtpFormState>) {
    setForm((current) => ({ ...current, ...changes }));
  }

  async function handleSave() {
    const problem = validateSmtpForm(form);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const updated = await instanceSettings.updateSmtp(buildSmtpPayload(form));
      setForm(smtpStateFromSettings(updated));
      toast.success("Email settings saved");
      onSaved(updated);
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setError(null);
    setResult(null);
    setTesting(true);
    try {
      // Deliberately no recipient: the backend sends it to the signed-in admin,
      // which is what "does my mail server work" almost always means.
      setResult(await instanceSettings.testSmtp({}));
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setTesting(false);
    }
  }

  return (
    <section className="space-y-4 rounded-xl border p-4">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold">Email</h3>
        <p className="text-muted-foreground text-sm">
          The mail server MegooPM sends from. Save, then send yourself a test.
        </p>
      </div>

      <div className="flex items-center gap-2">
        <Switch
          id="smtp-enabled"
          checked={form.enabled}
          onCheckedChange={(next) => patch({ enabled: next })}
          aria-label="Send email"
          disabled={saving}
        />
        <Label htmlFor="smtp-enabled">Send email</Label>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="smtp-host">Host</Label>
          <Input
            id="smtp-host"
            value={form.host}
            onChange={(e) => patch({ host: e.target.value })}
            placeholder="smtp.example.com"
            disabled={saving}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="smtp-port">Port</Label>
          <Input
            id="smtp-port"
            inputMode="numeric"
            value={form.port}
            onChange={(e) => patch({ port: e.target.value })}
            disabled={saving}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="smtp-security">Security</Label>
        <Select
          value={form.security}
          onValueChange={(value) => patch({ security: value as SmtpSecurity })}
        >
          <SelectTrigger id="smtp-security" disabled={saving}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(Object.keys(SECURITY_LABELS) as SmtpSecurity[]).map((value) => (
              <SelectItem key={value} value={value}>
                {SECURITY_LABELS[value]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="smtp-username">Username</Label>
          <Input
            id="smtp-username"
            value={form.username}
            onChange={(e) => patch({ username: e.target.value })}
            placeholder="optional"
            disabled={saving}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="smtp-password">Password</Label>
          <div className="flex gap-2">
            <Input
              id="smtp-password"
              type="password"
              aria-label="Password"
              value={form.password}
              onChange={(e) => patch({ password: e.target.value, passwordCleared: false })}
              placeholder={form.passwordIsSet ? "leave blank to keep" : "optional"}
              disabled={saving}
              className="flex-1"
            />
            {form.passwordIsSet ? (
              <Button
                variant="outline"
                size="sm"
                disabled={saving}
                onClick={() =>
                  patch({ password: "", passwordIsSet: false, passwordCleared: true })
                }
              >
                Remove
              </Button>
            ) : null}
          </div>
          <p className="text-muted-foreground text-xs">
            {form.passwordIsSet ? "A password is stored." : "No password stored."}
          </p>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="smtp-from">From address</Label>
          <Input
            id="smtp-from"
            value={form.from}
            onChange={(e) => patch({ from: e.target.value })}
            placeholder="megoopm@example.com"
            disabled={saving}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="smtp-from-name">From name</Label>
          <Input
            id="smtp-from-name"
            value={form.fromName}
            onChange={(e) => patch({ fromName: e.target.value })}
            placeholder="MegooPM"
            disabled={saving}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="smtp-app-url">This instance&apos;s public URL</Label>
        <Input
          id="smtp-app-url"
          value={form.appUrl}
          onChange={(e) => patch({ appUrl: e.target.value })}
          placeholder="https://pm.example.com"
          disabled={saving}
        />
        <p className="text-muted-foreground text-xs">
          Not used yet. Password-reset and invitation links will be built from it.
        </p>
      </div>

      {error ? (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      ) : null}

      {result ? (
        result.ok ? (
          <p className="border-success/30 bg-success/5 rounded-lg border p-3 text-sm">
            <span className="font-medium">Sent.</span> {result.detail} ({result.latency_ms} ms)
          </p>
        ) : (
          <p role="alert" className="text-destructive text-sm">
            {result.detail}
          </p>
        )
      ) : null}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={handleTest} disabled={testing || saving}>
          {testing ? <Loader2 className="animate-spin" /> : null}
          Send test email
        </Button>
        <Button onClick={handleSave} disabled={saving || testing}>
          {saving ? "Saving…" : "Save email settings"}
        </Button>
      </div>
    </section>
  );
}
```

- [ ] **Step 10: Mount it**

In `frontend/src/components/settings/settings-view.tsx`, add the import beside
the others and render it after the LLM card:

```tsx
import { SmtpCard } from "@/components/settings/smtp-card";
```

```tsx
      {row ? <SmtpCard settings={row} onSaved={setRow} /> : null}
```

- [ ] **Step 11: Run everything**

```bash
cd frontend && npx vitest run src/components/settings && npm run typecheck && npm run lint
cd frontend && npm test
```
Expected: all green.

- [ ] **Step 12: Commit and tear down the test stack**

```bash
git add frontend/src/components/settings frontend/src/lib/api
git commit -m "feat(settings): the email card

Same password convention as the LLM card beside it: the field starts empty
because the value is never returned, and the card reports whether one is stored
rather than what it is. Three payload states, not two — absent keeps it, a
string replaces it, an explicit null clears it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

```bash
docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet
```

---

## Manual verification

The suite proves the markup is what we intended. Only a real send proves it
looks right, and every production bug this project has hit was found by running
the real thing.

With the stack up and SMTP configured against a real server:

- [ ] Send a test to a **Gmail** account. Open it in light mode, then switch
      Gmail to dark. Confirm the auto-inverted version is readable — Gmail
      ignores the dark block entirely.
- [ ] Send to an **Apple Mail / iOS Mail** account in dark mode. This is the
      client that honours `prefers-color-scheme`; the palette should be the dark
      one from `palette.py`.
- [ ] Send to **Outlook desktop** if you have it. It renders with the Word
      engine — confirm the table layout holds and the card is not full-bleed.
- [ ] In every client, confirm the **logo appears without clicking "show
      images"**. If it does not, the CID attachment is wrong.
- [ ] View the message source and confirm a **plain-text part is present and
      readable**.
- [ ] Save a password, reload the Settings page, and confirm the field is empty
      and the card says a password is stored.
- [ ] Type a deliberately wrong password and send a test. The card should show
      the SMTP authentication error, not a generic failure.
- [ ] Point the host at an unreachable address and send a test. It should fail
      within the timeout rather than hanging the page.
