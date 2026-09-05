"""What counts as broken markup, and — just as important — what does not.

A checker that cries wolf is worse than none: the model would "repair" correct
HTML and make the page worse. The quiet cases below are the ones that keep it
honest, and they outnumber the loud ones deliberately.
"""

from __future__ import annotations

import pytest
from app.services.html_check import check_html

# --- what must be reported ----------------------------------------------------


def test_an_unclosed_element_is_reported() -> None:
    problems = check_html("<div><span>text</div>")
    assert problems
    assert any("span" in p.message for p in problems)


def test_a_stray_end_tag_is_reported() -> None:
    problems = check_html("<div>text</div></section>")
    assert any("section" in p.message for p in problems)


def test_an_element_left_open_at_the_end_is_reported() -> None:
    problems = check_html("<html><body><div>text</body></html>")
    assert any("div" in p.message for p in problems)


def test_a_problem_carries_the_line_it_is_on() -> None:
    # "Something is wrong somewhere" is not actionable inside a tool loop.
    problems = check_html("<html>\n<body>\n<div>text\n</body>\n</html>")
    assert problems
    assert problems[0].line >= 1


def test_the_report_reads_as_instructions_not_a_dump() -> None:
    from app.services.html_check import describe_problems

    text = describe_problems(check_html("<div><span>x</div>"))
    assert "span" in text
    assert "line" in text.lower()


# --- what must stay quiet -----------------------------------------------------


@pytest.mark.parametrize(
    "html",
    [
        "<br>",
        "<hr>",
        '<img src="x.png">',
        '<meta charset="utf-8">',
        '<link rel="stylesheet" href="x.css">',
        '<input type="text">',
        "<br/>",
        '<img src="x.png" />',
    ],
)
def test_a_void_element_never_needs_closing(html: str) -> None:
    assert check_html(f"<div>{html}</div>") == []


@pytest.mark.parametrize(
    "html",
    [
        "<p>one<p>two",
        "<ul><li>one<li>two</ul>",
        "<table><tr><td>a<td>b</table>",
        "<select><option>a<option>b</select>",
    ],
)
def test_an_omitted_end_tag_html5_allows_is_not_an_error(html: str) -> None:
    # HTML5 closes these implicitly. Reporting them would be the most common
    # false positive by far, since hand-written pages omit them constantly.
    assert check_html(html) == []


def test_a_script_body_is_not_parsed_as_markup() -> None:
    # `if (a < b)` is not an open tag, and a template string full of angle
    # brackets is not unbalanced markup.
    html = "<html><body><script>if (a < b) { x('<div>'); }</script></body></html>"
    assert check_html(html) == []


def test_a_style_body_is_not_parsed_as_markup() -> None:
    html = "<html><body><style>a > b { color: red }</style></body></html>"
    assert check_html(html) == []


def test_a_comment_is_not_markup() -> None:
    assert check_html("<div><!-- <span> not real --></div>") == []


def test_a_doctype_and_a_full_page_pass_clean() -> None:
    html = "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "  <head>",
            '    <meta charset="utf-8">',
            "    <title>Hi</title>",
            "  </head>",
            "  <body>",
            "    <h1>Hi</h1>",
            '    <img src="data:image/png;base64,MEGOOPM_IMAGE_1" alt="">',
            "  </body>",
            "</html>",
        ]
    )
    assert check_html(html) == []


def test_a_fragment_with_no_html_element_is_fine() -> None:
    # Custom pages are whole documents, but an assist may work on a fragment.
    assert check_html("<h1>Just a heading</h1>") == []


def test_an_empty_document_is_not_an_error() -> None:
    assert check_html("") == []


def test_unknown_elements_are_left_alone() -> None:
    # A web component is not a syntax error.
    assert check_html("<my-widget><span>x</span></my-widget>") == []
