"""The document a model edits, and the three tools it drives it with.

Pure: no LLM, no database, no I/O. Everything the model can do to a page happens
here, which is what makes the whole feature testable without a provider.

**Nothing mutates until :meth:`EditDocument.apply`.** ``replace_lines`` stages an
edit and returns; line numbers refer to the *original* document for the entire
conversation. Applying edits as they arrived would shift every later line while
the model went on addressing the numbering it was shown, and the resulting
corruption would be silent and would read as the model misbehaving. Staged edits
are applied bottom-up at the end, so an earlier edit cannot move a later one.

**grep matches literal substrings, never a regex.** The pattern is untrusted
input from a model, run against a document up to 200 KB, and Python's ``re`` has
no timeout — one catastrophic-backtracking pattern hangs the worker executing
it. Nothing about editing HTML needs alternation or quantifiers.
"""

from __future__ import annotations

from dataclasses import dataclass

# Lines shown either side of a grep hit, so the model can see where it landed.
GREP_CONTEXT_LINES = 2
# A pattern like "<" would otherwise return the whole document back to itself.
MAX_GREP_MATCHES = 50

_ORIGINAL_NOTE = "Line numbers still refer to the original document."


@dataclass(frozen=True, slots=True)
class StagedEdit:
    """One pending replacement. ``start``/``end`` are 1-based and inclusive."""

    start: int
    end: int
    before: str
    after: str


def _number(index: int, line: str) -> str:
    """``   13 | <content>`` — the separator keeps numbers out of the markup."""
    return f"{index:>5} | {line}"


class EditDocument:
    """A page under edit, plus the staged changes a model has asked for."""

    def __init__(self, html: str) -> None:
        self._lines = html.split("\n")
        self._staged: list[StagedEdit] = []

    @property
    def staged(self) -> tuple[StagedEdit, ...]:
        return tuple(self._staged)

    def numbered(self) -> str:
        """The whole document, numbered, for the opening message."""
        return "\n".join(_number(i, line) for i, line in enumerate(self._lines, 1))

    # --- tools -------------------------------------------------------------

    def grep(self, pattern: str, *, ignore_case: bool = False) -> str:
        """Literal substring search, with context. Returns a message for the model."""
        if not pattern:
            return "Error: pattern must not be empty."

        needle = pattern.lower() if ignore_case else pattern
        hits = [
            i
            for i, line in enumerate(self._lines, 1)
            if needle in (line.lower() if ignore_case else line)
        ]
        if not hits:
            return f"No matches for {pattern!r}."

        shown = hits[:MAX_GREP_MATCHES]
        header = f"{len(hits)} match{'es' if len(hits) != 1 else ''} for {pattern!r}."
        if len(shown) < len(hits):
            header += f" Showing the first {len(shown)}; narrow the pattern to see the rest."

        # Merge overlapping context windows so neighbouring hits read as one block.
        windows: list[tuple[int, int]] = []
        for hit in shown:
            start = max(1, hit - GREP_CONTEXT_LINES)
            end = min(len(self._lines), hit + GREP_CONTEXT_LINES)
            if windows and start <= windows[-1][1] + 1:
                windows[-1] = (windows[-1][0], max(windows[-1][1], end))
            else:
                windows.append((start, end))

        blocks = [
            "\n".join(_number(i, self._lines[i - 1]) for i in range(start, end + 1))
            for start, end in windows
        ]
        return f"{header}\n\n" + "\n--\n".join(blocks)

    def read_lines(self, start: int, end: int) -> str:
        """An inclusive numbered range, or an error the model can act on."""
        problem = self._check_range(start, end)
        if problem:
            return problem
        return "\n".join(_number(i, self._lines[i - 1]) for i in range(start, end + 1))

    def replace_lines(self, start: int, end: int, text: str) -> str:
        """Stage a replacement. Does not modify the document — see the module doc."""
        problem = self._check_range(start, end)
        if problem:
            return problem

        before = "\n".join(self._lines[start - 1 : end])

        for index, edit in enumerate(self._staged):
            if edit.start == start and edit.end == end:
                # The same range again is a correction, not a collision. Without
                # this the model cannot fix an edit it got wrong: the overlap
                # error below would tell it to revise with no way to do so.
                self._staged[index] = StagedEdit(start=start, end=end, before=before, after=text)
                return f"Revised the staged edit for lines {start}-{end}, which replace:\n{before}"
            if start <= edit.end and edit.start <= end:
                return (
                    f"Error: lines {start}-{end} overlap a staged edit covering "
                    f"{edit.start}-{edit.end}. Restage the whole of "
                    f"{edit.start}-{edit.end} instead of staging both."
                )

        self._staged.append(StagedEdit(start=start, end=end, before=before, after=text))
        return (
            # Echo what is being replaced: a model that aimed at the wrong range
            # can see it here, rather than discovering it in a mangled document.
            f"Staged: lines {start}-{end} will be replaced. They currently read:\n"
            f"{before}\n{_ORIGINAL_NOTE} {len(self._staged)} edit(s) staged so far."
        )

    # --- apply -------------------------------------------------------------

    def apply(self) -> tuple[str, tuple[StagedEdit, ...]]:
        """Apply every staged edit and return the new document plus what changed.

        Bottom-up, so an earlier replacement cannot shift the target of a later
        one. The returned edits are in document order, because that is the order
        an operator reads a page in.
        """
        lines = list(self._lines)
        for edit in sorted(self._staged, key=lambda e: e.start, reverse=True):
            replacement = edit.after.split("\n") if edit.after else []
            lines[edit.start - 1 : edit.end] = replacement
        ordered = tuple(sorted(self._staged, key=lambda e: e.start))
        return "\n".join(lines), ordered

    # --- internals ---------------------------------------------------------

    def _check_range(self, start: int, end: int) -> str | None:
        total = len(self._lines)
        if start < 1 or end < 1:
            return f"Error: line numbers start at 1. The document has {total} lines."
        if start > end:
            return f"Error: start ({start}) is after end ({end})."
        if end > total:
            return f"Error: line {end} is past the end. The document has {total} lines."
        return None


__all__ = ["GREP_CONTEXT_LINES", "MAX_GREP_MATCHES", "EditDocument", "StagedEdit"]
