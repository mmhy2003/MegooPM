"""Tests for the document tools the model drives.

Pure: no LLM, no database. A bug in the staging engine silently corrupts an
operator's page, so this is where the coverage goes.
"""

from __future__ import annotations

from app.services.page_tools import EditDocument

DOC = "\n".join(
    [
        "<!doctype html>",
        "<html>",
        "  <body>",
        "    <main>",
        "      <h1>Access denied</h1>",
        "      <p>Nothing to see.</p>",
        "    </main>",
        "  </body>",
        "</html>",
    ]
)


# --- Numbering -------------------------------------------------------------


def test_numbering_is_one_based_and_separated_from_content() -> None:
    """The model has to tell a line number from the markup at a glance."""
    out = EditDocument(DOC).numbered()
    first = out.splitlines()[0]
    assert first.strip().startswith("1 |")
    assert first.rstrip().endswith("<!doctype html>")
    assert out.splitlines()[4].strip().startswith("5 |")


# --- grep ------------------------------------------------------------------


def test_grep_finds_a_line_and_shows_context() -> None:
    out = EditDocument(DOC).grep("<h1")
    assert "1 match" in out
    assert "<h1>Access denied</h1>" in out
    # Two lines of context either side.
    assert "<main>" in out
    assert "<p>Nothing to see.</p>" in out


def test_grep_reports_no_matches_rather_than_returning_nothing() -> None:
    """An empty string would read to the model as a broken tool."""
    out = EditDocument(DOC).grep("<footer")
    assert "no matches" in out.lower()


def test_grep_is_case_sensitive_by_default_and_optional_otherwise() -> None:
    doc = EditDocument(DOC)
    assert "no matches" in doc.grep("ACCESS DENIED").lower()
    assert "Access denied" in doc.grep("ACCESS DENIED", ignore_case=True)


def test_grep_finds_every_occurrence() -> None:
    doc = EditDocument("a\nb\na\nc\na")
    assert "3 matches" in doc.grep("a")


def test_grep_clamps_context_at_the_document_edges() -> None:
    """Line 1 has no lines above it; the window must not run negative."""
    out = EditDocument(DOC).grep("<!doctype")
    assert "<!doctype html>" in out
    # The first numbered line shown must be 1, not 0 or -1.
    numbered = [line for line in out.splitlines() if "|" in line]
    assert numbered[0].split("|")[0].strip() == "1"


def test_grep_treats_the_pattern_as_a_literal_not_a_regex() -> None:
    """A model-supplied regex against 200 KB with no timeout is a hung worker."""
    doc = EditDocument("cost is $5.00\nprice: 500")
    out = doc.grep("$5.00")
    assert "1 match" in out
    assert "cost is $5.00" in out


def test_grep_caps_a_pattern_that_matches_everything() -> None:
    doc = EditDocument("\n".join(f"<div>{i}</div>" for i in range(200)))
    out = doc.grep("<div")
    assert "200 matches" in out
    assert "showing" in out.lower()  # says it truncated
    assert len(out.splitlines()) < 200


# --- read_lines ------------------------------------------------------------


def test_read_lines_returns_an_inclusive_numbered_range() -> None:
    out = EditDocument(DOC).read_lines(5, 6)
    assert "<h1>Access denied</h1>" in out
    assert "<p>Nothing to see.</p>" in out
    assert "<main>" not in out


def test_read_lines_refuses_a_range_past_the_end() -> None:
    out = EditDocument(DOC).read_lines(5, 99)
    assert "error" in out.lower()
    assert "9" in out  # says how many lines there are


def test_read_lines_refuses_an_inverted_range() -> None:
    assert "error" in EditDocument(DOC).read_lines(6, 5).lower()


# --- replace_lines: staging ------------------------------------------------


def test_replace_lines_stages_without_mutating() -> None:
    """Mutating now would shift every later line under the model's feet."""
    doc = EditDocument(DOC)
    doc.replace_lines(5, 5, "      <h2>Access denied</h2>")
    # The document the model is reading is unchanged.
    assert "<h1>Access denied</h1>" in doc.read_lines(5, 5)
    assert len(doc.staged) == 1


def test_replace_lines_says_numbers_still_refer_to_the_original() -> None:
    """The model must never be left inferring this."""
    out = EditDocument(DOC).replace_lines(5, 5, "x")
    assert "original" in out.lower()


def test_replace_lines_refuses_an_out_of_range_target() -> None:
    doc = EditDocument(DOC)
    assert "error" in doc.replace_lines(1, 99, "x").lower()
    assert doc.staged == ()


def test_replace_lines_refuses_an_overlap() -> None:
    """Two edits to the same lines produce markup nobody intended."""
    doc = EditDocument(DOC)
    doc.replace_lines(4, 7, "  <main>new</main>")
    out = doc.replace_lines(5, 5, "something else")
    assert "overlap" in out.lower()
    assert len(doc.staged) == 1


def test_adjacent_ranges_are_not_an_overlap() -> None:
    doc = EditDocument(DOC)
    doc.replace_lines(5, 5, "a")
    doc.replace_lines(6, 6, "b")
    assert len(doc.staged) == 2


# --- apply -----------------------------------------------------------------


def test_apply_returns_the_document_unchanged_when_nothing_is_staged() -> None:
    html, changes = EditDocument(DOC).apply()
    assert html == DOC
    assert changes == ()


def test_apply_performs_a_single_replacement() -> None:
    doc = EditDocument(DOC)
    doc.replace_lines(5, 5, '      <h1 style="font-size:3rem">Access denied</h1>')
    html, changes = doc.apply()
    assert "font-size:3rem" in html
    assert "<h1>Access denied</h1>" not in html
    assert len(changes) == 1
    assert changes[0].start == 5
    assert changes[0].before == "      <h1>Access denied</h1>"


def test_apply_handles_edits_that_change_the_line_count() -> None:
    """The bug this whole design exists to avoid: an early edit shifting a later one."""
    doc = EditDocument(DOC)
    # Line 5 becomes three lines...
    doc.replace_lines(5, 5, "      <h1>A</h1>\n      <h2>B</h2>\n      <h3>C</h3>")
    # ...and line 8 must still mean line 8 of the ORIGINAL document.
    doc.replace_lines(8, 8, "  </body><!-- end -->")
    html, changes = doc.apply()
    lines = html.split("\n")
    assert "<h1>A</h1>" in lines[4]
    assert "<h3>C</h3>" in lines[6]
    assert "<!-- end -->" in html
    # The original line 8 was "  </body>", not something shifted into place.
    assert changes[1].before == "  </body>"


def test_apply_returns_changes_in_document_order() -> None:
    """Applied bottom-up, reported top-down — the operator reads a page downward."""
    doc = EditDocument(DOC)
    doc.replace_lines(8, 8, "z")
    doc.replace_lines(5, 5, "a")
    _, changes = doc.apply()
    assert [c.start for c in changes] == [5, 8]


def test_a_replacement_with_empty_text_deletes_the_lines() -> None:
    doc = EditDocument(DOC)
    doc.replace_lines(6, 6, "")
    html, _ = doc.apply()
    assert "<p>Nothing to see.</p>" not in html


def test_a_replacement_that_re_emits_the_line_inserts_after_it() -> None:
    doc = EditDocument(DOC)
    doc.replace_lines(5, 5, "      <h1>Access denied</h1>\n      <p>support@example.com</p>")
    html, _ = doc.apply()
    assert "<h1>Access denied</h1>" in html
    assert "support@example.com" in html
