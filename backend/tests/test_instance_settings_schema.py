"""Schema tests for instance settings.

The redirect URL is operator input that lands verbatim inside a generated nginx
config file, so its validator gets a case per rejected class rather than one
happy-path test. ``nginx -t`` and the engine's rollback protect against a config
that fails to *parse*; they do nothing about one that parses fine and does
something else.
"""

from __future__ import annotations

import pytest
from app.schemas.instance_settings import InstanceSettingsUpdate, validate_redirect_url
from pydantic import ValidationError


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com",
        "https://example.com/path?q=1",
        "https://example.com:8443/deep/path",
    ],
)
def test_accepts_plain_absolute_urls(url: str) -> None:
    assert validate_redirect_url(url) == url


def test_trims_surrounding_whitespace() -> None:
    assert validate_redirect_url("  https://example.com  ") == "https://example.com"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "file:///etc/passwd",
        "/relative/path",
        "javascript:alert(1)",
        "https://",
    ],
)
def test_rejects_targets_that_are_not_absolute_http_urls(url: str) -> None:
    with pytest.raises(ValueError):
        validate_redirect_url(url)


@pytest.mark.parametrize(
    "url",
    [
        'https://example.com" ; return 200 "pwned',  # quote + directive break
        "https://example.com';",  # single quote
        "https://example.com\\",  # backslash
        "https://example.com;",  # directive terminator
        "https://example.com$request_uri",  # nginx variable
        "https://example.com\nlocation / { return 200; }",  # newline
        "https://example.com\rX",  # carriage return
        "https://example.com\tX",  # tab
    ],
)
def test_rejects_nginx_config_injection(url: str) -> None:
    """These parse as URLs; the point is that they must never reach the config."""
    with pytest.raises(ValueError):
        validate_redirect_url(url)


def test_newline_is_caught_before_urlsplit_can_strip_it() -> None:
    """urlsplit removes CR/LF/TAB per WHATWG, so parsing first would pass this."""
    from urllib.parse import urlsplit

    hostile = "https://example.com\nreturn 200;"
    assert urlsplit(hostile).scheme == "https"  # parses clean — the trap
    with pytest.raises(ValueError):
        validate_redirect_url(hostile)


def test_redirect_mode_requires_a_url() -> None:
    with pytest.raises(ValidationError):
        InstanceSettingsUpdate(default_site_mode="redirect")


def test_custom_page_mode_requires_a_page() -> None:
    with pytest.raises(ValidationError):
        InstanceSettingsUpdate(default_site_mode="custom_page")


@pytest.mark.parametrize("mode", ["congratulations", "not_found", "no_response"])
def test_simple_modes_need_nothing_else(mode: str) -> None:
    assert InstanceSettingsUpdate(default_site_mode=mode).default_site_mode == mode


def test_mode_is_required() -> None:
    """A partial patch cannot be checked for coherence without the stored row."""
    with pytest.raises(ValidationError):
        InstanceSettingsUpdate(default_site_redirect_url="https://example.com")
