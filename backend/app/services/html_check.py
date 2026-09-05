"""A conservative structural check for a custom page's HTML.

Answers one question: is the element nesting coherent? Not whether the page is
valid HTML5 — that is a far larger claim, and one nobody here needs.

The bias is deliberate: **report only what is unambiguously wrong.** This runs
inside the assist loop, so a false positive sends the model "repairing" correct
markup and leaves the page worse than if nothing had checked it. HTML5 lets
whole families of end tags be omitted and void elements never close, so the
silent cases below are as much the contract as the loud ones.

Pure stdlib on purpose. The parser ships with Python, so the backend image
needs no new dependency to gain this.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

#: Elements that never have an end tag. Closing one is not required, and the
#: parser reports them through ``handle_startendtag`` or as a plain start tag.
VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: Elements whose end tag HTML5 allows you to omit. Hand-written pages omit
#: these constantly — `<li>one<li>two` is correct markup — so an unclosed one
#: is never reported, and one of these on the stack never blocks a parent from
#: closing.
OPTIONAL_END = frozenset(
    {
        "body",
        "colgroup",
        "dd",
        "dt",
        "head",
        "html",
        "li",
        "optgroup",
        "option",
        "p",
        "rp",
        "rt",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
    }
)


@dataclass(frozen=True, slots=True)
class Problem:
    """One structural fault, with the line to look at."""

    line: int
    message: str


class _StructureParser(HTMLParser):
    """Tracks open elements and notes the nesting faults it can be sure of.

    ``convert_charrefs`` is left on: entities are text, and this checks
    structure. Script and style bodies are handled by HTMLParser's own CDATA
    mode, so `if (a < b)` is never mistaken for a tag.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[Problem] = []
        self._stack: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in VOID_ELEMENTS:
            return
        self._stack.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag: str, attrs) -> None:
        # `<div />` closes itself; nothing to track either way.
        return

    def handle_endtag(self, tag: str) -> None:
        line = self.getpos()[0]
        if tag in VOID_ELEMENTS:
            # `</br>` is meaningless but harmless, and browsers ignore it.
            return

        for depth in range(len(self._stack) - 1, -1, -1):
            if self._stack[depth][0] != tag:
                continue
            # Everything above the match is being closed implicitly. That is
            # only a fault for elements whose end tag is required.
            for open_tag, open_line in self._stack[depth + 1 :]:
                if open_tag not in OPTIONAL_END:
                    self.problems.append(
                        Problem(
                            line=open_line,
                            message=(
                                f"<{open_tag}> opened on line {open_line} is still open "
                                f"where </{tag}> closes on line {line}."
                            ),
                        )
                    )
            del self._stack[depth:]
            return

        # Nothing on the stack matches: the end tag closes nothing.
        if tag not in OPTIONAL_END:
            self.problems.append(
                Problem(line=line, message=f"</{tag}> on line {line} closes nothing that is open.")
            )

    def finish(self) -> list[Problem]:
        for tag, line in self._stack:
            if tag not in OPTIONAL_END:
                self.problems.append(
                    Problem(line=line, message=f"<{tag}> opened on line {line} is never closed.")
                )
        return sorted(self.problems, key=lambda p: (p.line, p.message))


def check_html(html: str) -> list[Problem]:
    """Structural faults in ``html``, or an empty list when it is coherent."""
    parser = _StructureParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # pragma: no cover - HTMLParser is lenient by design
        # A parser that cannot finish tells us nothing trustworthy about the
        # document, and guessing would be exactly the false positive this
        # module exists to avoid.
        return []
    return parser.finish()


def describe_problems(problems: list[Problem], html: str | None = None) -> str:
    """The faults as instructions for whoever has to fix them.

    Pass ``html`` to quote each offending line. Inside the assist loop that
    quote is the point: the line numbers here are the document's *edited*
    numbering, which has drifted from the numbering the editing tools use, so
    the text is what makes a fault findable with grep.
    """
    if not problems:
        return "No structural problems found."
    source = html.split("\n") if html is not None else []
    lines = [f"Found {len(problems)} structural problem(s):"]
    for problem in problems:
        lines.append(f"- {problem.message}")
        if 0 < problem.line <= len(source):
            lines.append(f"    that line reads: {source[problem.line - 1].strip()}")
    return "\n".join(lines)


__all__ = ["OPTIONAL_END", "VOID_ELEMENTS", "Problem", "check_html", "describe_problems"]
