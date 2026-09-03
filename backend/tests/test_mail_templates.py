"""Rendering tests. These open no socket and touch no database."""

from __future__ import annotations

import pytest
from app.services.mail.palette import DARK, LIGHT
from app.services.mail.templates import LOGO_CID, LOGO_PATH, render
from jinja2 import TemplateNotFound


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
    # The specific exception, not a blind one: this must fail because the
    # template is missing, not because some later line happened to raise.
    with pytest.raises(TemplateNotFound):
        render("no_such_template", subject="Test", app_name="MegooPM")


# --- password reset -------------------------------------------------------


def test_reset_email_carries_the_link_in_both_bodies() -> None:
    email = render(
        "password_reset",
        subject="Reset",
        app_name="MegooPM",
        reset_url="https://pm.example.com/reset-password?token=abc",
        ttl_minutes=60,
    )
    assert "https://pm.example.com/reset-password?token=abc" in email.html
    assert "https://pm.example.com/reset-password?token=abc" in email.text


def test_reset_email_states_the_expiry() -> None:
    email = render(
        "password_reset",
        subject="Reset",
        app_name="MegooPM",
        reset_url="https://x/r?token=abc",
        ttl_minutes=60,
    )
    assert "60 minutes" in email.text


def test_reset_url_is_not_html_escaped_into_a_broken_link() -> None:
    # `&` in a query string must survive; `&amp;` inside an href is fine for a
    # browser, but the *text* body has no parser and must be raw.
    url = "https://x/r?token=abc&x=1"
    email = render(
        "password_reset", subject="Reset", app_name="MegooPM", reset_url=url, ttl_minutes=60
    )
    assert url in email.text


def test_changed_notice_has_no_link() -> None:
    # It exists to tell a victim someone else completed a reset. A link in it
    # would make it phishable.
    email = render("password_changed", subject="Changed", app_name="MegooPM")
    assert "href=" not in email.html
    assert "http" not in email.text


# --- invitation -----------------------------------------------------------


def _invitation(**over):
    context = {
        "app_name": "MegooPM",
        "inviter_name": "Mohamed Hammad",
        "accept_url": "https://pm.example.com/accept-invite?token=abc",
        "ttl_days": 7,
    }
    context.update(over)
    return render("invitation", subject="You're invited", **context)


def test_invitation_carries_the_link_in_both_bodies() -> None:
    email = _invitation()
    assert "https://pm.example.com/accept-invite?token=abc" in email.html
    assert "https://pm.example.com/accept-invite?token=abc" in email.text


def test_invitation_names_who_sent_it() -> None:
    # "You've been invited to MegooPM" with no human attached is what phishing
    # looks like.
    email = _invitation(inviter_name="Sara Ali")
    assert "Sara Ali" in email.html
    assert "Sara Ali" in email.text


def test_invitation_states_the_expiry_in_days() -> None:
    email = _invitation(ttl_days=7)
    assert "7 days" in email.text


def test_inviter_name_is_escaped_in_the_html_body() -> None:
    # The inviter is an admin, but an admin's display name is still user input.
    email = _invitation(inviter_name="<b>x</b>")
    assert "<b>x</b>" not in email.html
    assert "&lt;b&gt;" in email.html
