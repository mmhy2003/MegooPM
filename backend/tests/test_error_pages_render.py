"""The eight documents and the fragment that points nginx at them."""

from __future__ import annotations

import pytest
from app.models.error_page import ERROR_CODES
from app.services.nginx.renderer import ERRORS_CONF, error_html, render_default_site
from app.services.nginx.state import DesiredState, ErrorPageSpec


def test_all_eight_documents_are_written_with_nothing_configured() -> None:
    # Always written, so an `error_page` directive can never point at a file
    # that is not there — which nginx answers with its own bare page.
    files = render_default_site(DesiredState())
    for code in ERROR_CODES:
        assert error_html(code) in files, code


def test_a_document_names_its_own_code_and_copy() -> None:
    files = render_default_site(DesiredState())
    page = files[error_html(502)]
    assert "502" in page
    assert "Bad gateway" in page
    assert "The site behind this address didn&rsquo;t respond correctly." in page
    # And not another code's.
    assert "Gateway timeout" not in page


def test_a_document_never_names_the_instance() -> None:
    # Reachable by anyone: naming a backend tells a prober how this is built.
    page = render_default_site(DesiredState())[error_html(504)]
    for leak in ("upstream", "proxy_pass", "megoopm_upstream", "server_name"):
        assert leak not in page


def test_a_custom_page_replaces_exactly_one_document() -> None:
    state = DesiredState(error_pages=(ErrorPageSpec(code=404, html="<h1>mine</h1>"),))
    files = render_default_site(state)
    assert files[error_html(404)] == "<h1>mine</h1>"
    assert "Bad gateway" in files[error_html(502)]


def test_an_empty_document_falls_back_to_the_branded_page() -> None:
    # The row was edited outside the API and its page is gone. An empty error
    # page is worse than a generic one.
    state = DesiredState(error_pages=(ErrorPageSpec(code=404, html=""),))
    assert "There&rsquo;s nothing at this address." in render_default_site(state)[error_html(404)]


def test_the_fragment_wires_every_code() -> None:
    fragment = render_default_site(DesiredState())[ERRORS_CONF]
    for code in ERROR_CODES:
        assert f"error_page {code} /{error_html(code)};" in fragment
        assert f"location = /{error_html(code)} {{" in fragment
    # Unreachable by a direct request: served only as the result of an error.
    assert fragment.count("internal;") == len(ERROR_CODES)


def test_the_fragment_is_not_parsed_as_a_top_level_conf() -> None:
    # The base config includes `.../*.conf`; this file must not match, or
    # nginx parses `error_page` at the http level and refuses to start.
    assert not ERRORS_CONF.endswith(".conf")
    assert ERRORS_CONF.endswith(".inc")


@pytest.mark.parametrize("code", ERROR_CODES)
def test_every_document_is_self_contained(code: int) -> None:
    page = render_default_site(DesiredState())[error_html(code)]
    assert "data:image/png;base64," in page
    assert "http://" not in page and "https://" not in page
