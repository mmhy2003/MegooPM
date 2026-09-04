"""Rendering tests for the default site.

Pure: a DesiredState in, a {filename: contents} map out, no database and no
filesystem, so the whole mode matrix is covered without infrastructure.
"""

from __future__ import annotations

import re

from app.services.nginx.renderer import (
    DEFAULT_SITE_BODY,
    DEFAULT_SITE_CONF,
    DEFAULT_SITE_HTML,
    render_default_site,
)
from app.services.nginx.state import DefaultSiteSpec, DesiredState


def _state(**kw) -> DesiredState:
    return DesiredState(default_site=DefaultSiteSpec(**kw))


def test_no_setting_writes_no_default_site_file() -> None:
    """With no file, nginx matches no location and returns 404 — today's behaviour.

    The directory is no longer empty in that case: the error documents are
    written unconditionally, because a directive pointing at a missing file
    gets nginx's own bare page. This is about the default site's own files.
    """
    files = render_default_site(DesiredState())
    assert DEFAULT_SITE_CONF not in files
    assert DEFAULT_SITE_BODY not in files
    assert DEFAULT_SITE_HTML not in files


def test_not_found_returns_404() -> None:
    files = render_default_site(_state(mode="not_found"))
    assert {DEFAULT_SITE_CONF, DEFAULT_SITE_BODY} <= set(files)
    assert "return 404;" in files[DEFAULT_SITE_CONF]


def test_no_response_returns_444() -> None:
    files = render_default_site(_state(mode="no_response"))
    assert "return 444;" in files[DEFAULT_SITE_CONF]


def test_redirect_emits_a_quoted_target() -> None:
    files = render_default_site(_state(mode="redirect", redirect_url="https://example.com/moved"))
    assert 'return 301 "https://example.com/moved";' in files[DEFAULT_SITE_CONF]


def test_custom_page_writes_the_document_verbatim() -> None:
    html = "<!doctype html><html><body>banned</body></html>"
    files = render_default_site(_state(mode="custom_page", html=html))
    assert {DEFAULT_SITE_CONF, DEFAULT_SITE_BODY, DEFAULT_SITE_HTML} <= set(files)
    assert files[DEFAULT_SITE_HTML] == html
    assert "try_files /megoopm-default.html =404;" in files[DEFAULT_SITE_CONF]


def test_congratulations_ships_the_bundled_page() -> None:
    files = render_default_site(_state(mode="congratulations"))
    assert {DEFAULT_SITE_CONF, DEFAULT_SITE_BODY, DEFAULT_SITE_HTML} <= set(files)
    page = files[DEFAULT_SITE_HTML]
    assert page.startswith("<!doctype html>")
    assert "MegooPM" in page
    # Jinja must not have mangled the CSS braces.
    assert "{{" not in page and "{%" not in page


def test_the_two_document_modes_share_one_conf() -> None:
    """They differ only in file content, so a divergence here is a bug."""
    a = render_default_site(_state(mode="congratulations"))[DEFAULT_SITE_CONF]
    b = render_default_site(_state(mode="custom_page", html="<p>x</p>"))[DEFAULT_SITE_CONF]
    assert a.replace("congratulations", "MODE") == b.replace("custom_page", "MODE")


def test_congratulations_page_makes_no_external_requests() -> None:
    """It is what you see when nothing works; it must not need the network."""
    page = render_default_site(_state(mode="congratulations"))[DEFAULT_SITE_HTML]
    for token in ("http://", "https://", "//fonts.", "<script"):
        assert token not in page, token
    # The logo is an <img>, but an inline one: the rule is about fetching,
    # not about the tag, so every src here must already be in the document.
    for src in re.findall(r'src="([^"]*)"', page):
        assert src.startswith("data:"), src


def test_congratulations_page_supports_both_colour_schemes() -> None:
    page = render_default_site(_state(mode="congratulations"))[DEFAULT_SITE_HTML]
    assert "prefers-color-scheme: dark" in page


def test_every_oklch_has_a_hex_fallback_directly_above_it() -> None:
    """Browsers predating oklch (pre-2023) must still get the right colour.

    Asserts the actual invariant rather than counting occurrences: each
    ``--var: oklch(...)`` declaration is immediately preceded by a
    ``--var: #...`` for the same variable, so a fallback cannot go missing or
    drift onto the wrong property.
    """
    page = render_default_site(_state(mode="congratulations"))[DEFAULT_SITE_HTML]
    lines = [line.strip() for line in page.splitlines()]

    declarations = [
        (i, line.split(":", 1)[0])
        for i, line in enumerate(lines)
        if line.startswith("--") and "oklch(" in line
    ]
    assert declarations, "the page declares no oklch colours at all"

    for index, variable in declarations:
        previous = lines[index - 1]
        assert previous.startswith(f"{variable}:"), (variable, previous)
        assert "#" in previous, (variable, previous)


def test_filenames_are_sorted() -> None:
    files = render_default_site(_state(mode="congratulations"))
    assert list(files) == sorted(files)
