"""What every MegooPM-branded page must be true of.

These pages are served when something is already wrong — no host matched, a
backend is down, a visitor is banned — so the rules are about surviving that
moment, not about looks.
"""

from __future__ import annotations

import re

import pytest
from app.services.nginx.renderer import _env

PAGES = ["congratulations.html.j2", "banned.html.j2"]


def _render(name: str) -> str:
    return _env().get_template(name).render()


def _source(name: str) -> str:
    env = _env()
    return env.loader.get_source(env, name)[0]


@pytest.mark.parametrize("name", PAGES)
def test_a_page_makes_no_external_request(name: str) -> None:
    # The moment this page renders is the moment the network is least likely
    # to work. A webfont link or a CDN image would degrade exactly then.
    html = _render(name)
    assert "http://" not in html
    assert "https://" not in html
    assert "//fonts." not in html


@pytest.mark.parametrize("name", PAGES)
def test_a_page_carries_the_logo_inline(name: str) -> None:
    html = _render(name)
    assert "data:image/png;base64," in html
    # The wordmark alone was what these had before; the logo replaces it.
    assert "<img" in html


@pytest.mark.parametrize("name", PAGES)
def test_every_oklch_has_a_hex_fallback(name: str) -> None:
    # Chrome 111 / Safari 15.4 / Firefox 113 (2023) and older take the hex.
    # Declaration order is what makes that work, so check pairs, not counts.
    html = _render(name)
    for match in re.finditer(r"(--[\w-]+):\s*oklch\(", html):
        prop = match.group(1)
        before = html[: match.start()]
        assert re.search(
            rf"{prop}:\s*#[0-9a-fA-F]{{3,8}};\s*$", before.rstrip("\n") + "\n", re.M
        ), f"{prop} in {name} has no hex fallback immediately before its oklch()"


@pytest.mark.parametrize("name", PAGES)
def test_a_page_has_a_dark_treatment(name: str) -> None:
    assert "prefers-color-scheme: dark" in _render(name)


@pytest.mark.parametrize("name", PAGES)
def test_the_pages_share_one_palette(name: str) -> None:
    # Three files each carrying their own copy is three places to change a
    # colour and two chances to forget one.
    assert '{% include "_palette.css.j2" %}' in _source(name)
