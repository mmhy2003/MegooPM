# Targeted Page Edits via a Grep Tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the model an IDE-like toolset — literal `grep`, `read_lines`, `replace_lines` — over the page it is editing, so it changes only the lines it names instead of regenerating the document.

**Architecture:** A pure `EditDocument` owns the line-numbered text and a list of *staged* edits; the tools never mutate, so line numbers stay valid for the whole conversation and edits apply bottom-up at the end. `app/services/llm.py` gains a tool-capable entry point that normalises litellm's response into a plain `LlmTurn`, keeping provider shapes in the one module that already owns them. `assist_page` becomes an orchestrator: generate, tool loop, or fall back to today's rewrite.

**Tech Stack:** FastAPI, Pydantic v2, `litellm` tool calling via `app/services/llm.py`; Next.js 16 + React 19 + vitest on the frontend.

**Spec:** `docs/superpowers/specs/2026-09-01-page-edit-tools-design.md`

## Global Constraints

- **`grep` matches literal substrings. Never compile a model-supplied regex.** It is untrusted input run against a document up to 200 KB, Python's `re` has no timeout, and one catastrophic-backtracking pattern hangs the worker executing it.
- **`replace_lines` stages; it never mutates.** Line numbers refer to the **original** document for the entire loop, every tool result says so, and staged edits apply **bottom-up** at the end. Applying as they arrive would shift every later line while the model went on addressing the numbering it was shown — silent corruption that reads as the model misbehaving.
- **Overlapping staged ranges are refused**, not merged.
- **Tool results are strings returned to the model, including errors.** A bad range is not an exception; it is a message the model can read and correct itself from.
- **Ranges are 1-based and inclusive on both ends.** `replace_lines(13, 13, …)` replaces exactly line 13. Insertion re-emits the original line plus the new one; deletion passes empty text.
- **`MAX_TOOL_TURNS = 8`.** If the cap is hit with edits staged, they are **applied** and `truncated` is set — discarding work the operator paid for is worse, and every staged edit was individually validated.
- **`litellm` is NEVER imported at module scope.** `app/services/llm.py` is the only module that touches it and imports it inside its functions; 3.49s to load against 0.84s for the whole app. `tests/test_llm_service.py` pins this — do not break it.
- **Provider error text is never returned verbatim** — `scrub_secrets` already handles it inside `LlmError`. The new tool path must scrub identically.
- **Backend tests only run on Linux** (`app` imports `fcntl`) and most need a reachable Postgres. Use the runner below; never run `pytest` on the Windows host.
- **Run pytest WITHOUT `-q`** — `pyproject.toml` already sets it, and `-qq` hides the pass count.
- **`ruff format --check .` reports ~32 pre-existing unformatted files.** Only format files you create; never reformat a file you did not otherwise touch.
- **Line endings must be LF.** After editing run `git ls-files --eol <files>`; anything `w/crlf` gets `sed -i 's/\r$//'`.
- **Never commit a provider-shaped credential, even a fake one.** GitHub push protection reads `sk_live_…` as a Stripe key and blocks the push — that already cost one rewritten history. Fixtures stay obviously synthetic (`sk-EXAMPLE-not-a-real-credential-1`).
- **Schema changes need two regenerations:** `docker exec megoopm-test python -m scripts.export_openapi`, then `cd frontend && npm run gen:api`.
- **vitest does not typecheck** — run `npm run typecheck` separately. Frontend commands run from `frontend/`.
- Commits go **directly to `main`**, the operator's established preference.

### One deliberate deviation from the spec

**The rewrite fallback triggers on an `LlmError` anywhere in the loop, not only on the first turn.** The spec describes the fallback as covering "the provider rejects `tools`", which is a first-turn failure. Distinguishing that from a transient mid-loop error is not reliably possible — both surface as the same scrubbed `LlmError`. So any `LlmError` during the loop falls through to the rewrite; if the provider is genuinely broken the rewrite raises the same error and the operator sees it, which is the correct outcome either way.

### Running the backend tests

```bash
export MSYS_NO_PATHCONV=1
docker network create megoopm-testnet
docker run -d --name megoopm-testdb --network megoopm-testnet \
  -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm -e POSTGRES_DB=megoopm postgres:16-alpine
docker run -d --name megoopm-test --network megoopm-testnet --user root \
  -v "C:/Projects/megoopm/backend:/src" -w /src \
  -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
  -e DATABASE_URL="postgresql+asyncpg://megoopm:megoopm@megoopm-testdb:5432/megoopm" \
  --entrypoint sleep megoopm-backend infinity
docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" "ruff>=0.6"
```

Per run: `docker exec megoopm-test python -m pytest tests/<file> -p no:cacheprovider -p no:warnings`

Teardown: `docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet`

---

## File Structure

**Created:**

| file | responsibility |
| --- | --- |
| `backend/app/services/page_tools.py` | `EditDocument`: numbering, literal grep, reads, staging, bottom-up apply |
| `backend/tests/test_page_tools.py` | the tools and the staging engine — pure, and where the risk is |

**Modified:**

| file | change |
| --- | --- |
| `backend/app/services/llm.py` | `ToolCall`, `LlmTurn`, `complete_with_tools` |
| `backend/tests/test_llm_service.py` | tool forwarding and normalisation |
| `backend/app/services/page_assist.py` | tool schemas, the loop, the orchestrator, `AssistResult` |
| `backend/tests/test_page_assist.py` | the loop against a scripted model |
| `backend/app/schemas/custom_page.py` | `mode`, `truncated`, `changes` on the response |
| `backend/app/api/routes/custom_pages.py` | pass the new fields through |
| `backend/openapi.json` | regenerated |
| `backend/tests/test_custom_pages_api.py` | the new response shape |
| `frontend/src/components/custom-pages/custom-page-editor-view.tsx` | render `changes` and the rewrite notice |
| `frontend/src/components/custom-pages/custom-page-editor-view.test.tsx` | those cases |
| `frontend/src/lib/api/generated/schema.ts` | regenerated |

---

### Task 1: The document and its tools

**Files:**
- Create: `backend/app/services/page_tools.py`, `backend/tests/test_page_tools.py`

**Interfaces:**
- Produces: `StagedEdit(start: int, end: int, before: str, after: str)` (frozen dataclass); `EditDocument(html: str)` with `.numbered() -> str`, `.grep(pattern, *, ignore_case=False) -> str`, `.read_lines(start, end) -> str`, `.replace_lines(start, end, text) -> str`, `.apply() -> tuple[str, tuple[StagedEdit, ...]]`, `.staged -> tuple[StagedEdit, ...]`; constants `GREP_CONTEXT_LINES = 2`, `MAX_GREP_MATCHES = 50`.

Everything here is pure and carries the whole risk of the feature: a bug in
staging silently corrupts someone's page. It gets the most tests.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_page_tools.py`:

```python
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
    doc.replace_lines(5, 5, "      <h1 style=\"font-size:3rem\">Access denied</h1>")
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_page_tools.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.page_tools'`.

- [ ] **Step 3: Write the module**

Create `backend/app/services/page_tools.py`:

```python
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

        for edit in self._staged:
            if start <= edit.end and edit.start <= end:
                return (
                    f"Error: lines {start}-{end} overlap a staged edit covering "
                    f"{edit.start}-{edit.end}. Revise one of them instead of "
                    "staging both."
                )

        before = "\n".join(self._lines[start - 1 : end])
        self._staged.append(StagedEdit(start=start, end=end, before=before, after=text))
        return (
            f"Staged: lines {start}-{end} will be replaced. {_ORIGINAL_NOTE} "
            f"{len(self._staged)} edit(s) staged so far."
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker exec megoopm-test python -m pytest tests/test_page_tools.py -p no:cacheprovider -p no:warnings`
Expected: PASS.

- [ ] **Step 5: Lint, check line endings, commit**

```bash
docker exec megoopm-test ruff check app tests
docker exec megoopm-test ruff format --check app/services/page_tools.py tests/test_page_tools.py
git ls-files --eol backend/app/services/page_tools.py backend/tests/test_page_tools.py
git add backend/app/services/page_tools.py backend/tests/test_page_tools.py
git commit -m "feat(custom-pages): add the document tools a model edits pages with"
```

---

### Task 2: Tool calling in the LLM seam

**Files:**
- Modify: `backend/app/services/llm.py`, `backend/tests/test_llm_service.py`

**Interfaces:**
- Produces: `ToolCall(id: str, name: str, arguments: str)`; `LlmTurn(content: str, tool_calls: tuple[ToolCall, ...], message: dict)`; `async complete_with_tools(config, *, messages: list[dict], tools: list[dict], timeout: float = 60.0) -> LlmTurn`.
- `message` is the assistant turn as a plain dict, ready to append back into the conversation — the caller must never need a litellm object to continue a loop.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_llm_service.py`. First extend the fake so it can
answer with tool calls — add these classes beside `_Response`:

```python
class _FnCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.type = "function"
        self.function = _FnCall(name, arguments)


class _ToolMessage:
    def __init__(self, calls) -> None:
        self.content = None
        self.tool_calls = calls

    def model_dump(self) -> dict:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.function.name, "arguments": c.function.arguments},
                }
                for c in self.tool_calls
            ],
        }


class _ToolChoice:
    def __init__(self, calls) -> None:
        self.message = _ToolMessage(calls)


class _ToolResponse:
    def __init__(self, calls) -> None:
        self.choices = [_ToolChoice(calls)]
```

Then the tests:

```python
# --- complete_with_tools ---------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search the document.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    }
]


async def test_complete_with_tools_forwards_the_tool_definitions(fake_litellm) -> None:
    await complete_with_tools(
        LlmConfig(model="gpt-4o", api_key="sk-EXAMPLE-not-a-real-credential-1"),
        messages=[{"role": "user", "content": "hi"}],
        tools=TOOLS,
    )
    call = fake_litellm.calls[0]
    assert call["tools"] == TOOLS
    assert call["messages"] == [{"role": "user", "content": "hi"}]
    assert call["api_key"] == "sk-EXAMPLE-not-a-real-credential-1"


async def test_complete_with_tools_returns_plain_text_when_no_tool_is_called(
    fake_litellm,
) -> None:
    fake_litellm.reply = "all done"
    turn = await complete_with_tools(
        LlmConfig(model="gpt-4o"), messages=[{"role": "user", "content": "hi"}], tools=TOOLS
    )
    assert turn.content == "all done"
    assert turn.tool_calls == ()


async def test_complete_with_tools_normalises_tool_calls(fake_litellm) -> None:
    """The caller must never need a litellm object to keep a loop going."""
    fake_litellm.response = _ToolResponse(
        [_ToolCall("call_1", "grep", '{"pattern": "<h1"}')]
    )
    turn = await complete_with_tools(
        LlmConfig(model="gpt-4o"), messages=[{"role": "user", "content": "hi"}], tools=TOOLS
    )
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].id == "call_1"
    assert turn.tool_calls[0].name == "grep"
    assert turn.tool_calls[0].arguments == '{"pattern": "<h1"}'
    # And the assistant turn comes back as a plain dict, ready to append.
    assert turn.message["role"] == "assistant"
    assert isinstance(turn.message, dict)


async def test_complete_with_tools_disables_telemetry(fake_litellm) -> None:
    await complete_with_tools(
        LlmConfig(model="gpt-4o"), messages=[{"role": "user", "content": "hi"}], tools=TOOLS
    )
    assert fake_litellm.telemetry is False


async def test_complete_with_tools_scrubs_provider_failures(fake_litellm) -> None:
    key = "sk-EXAMPLE-not-a-real-credential-1"
    fake_litellm.raises = RuntimeError(f"401 using {key}")
    with pytest.raises(LlmError) as excinfo:
        await complete_with_tools(
            LlmConfig(model="gpt-4o", api_key=key),
            messages=[{"role": "user", "content": "hi"}],
            tools=TOOLS,
        )
    assert key not in str(excinfo.value)
```

Extend the fake's `_acompletion` so a test can supply a whole response object,
replacing its current body with:

```python
    async def _acompletion(**kwargs):
        module.calls.append(kwargs)
        if getattr(module, "raises", None) is not None:
            raise module.raises
        canned = getattr(module, "response", None)
        if canned is not None:
            return canned
        return _Response(getattr(module, "reply", "OK"))
```

and add `complete_with_tools` to the module's import from `app.services.llm`.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_llm_service.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — `ImportError: cannot import name 'complete_with_tools'`.

- [ ] **Step 3: Add the tool-capable entry point**

In `backend/app/services/llm.py`, add the two dataclasses beside `LlmCheckResult`:

```python
@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool the model asked to run. ``arguments`` is raw JSON, unparsed."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class LlmTurn:
    """One assistant turn, normalised so callers never touch a litellm object.

    ``message`` is the turn as a plain dict, ready to append back into the
    conversation — a tool loop has to replay it verbatim on the next call.
    """

    content: str
    tool_calls: tuple[ToolCall, ...]
    message: dict
```

and the function after `complete`:

```python
async def complete_with_tools(
    config: LlmConfig,
    *,
    messages: list[dict],
    tools: list[dict],
    timeout: float = 60.0,
) -> LlmTurn:
    """One turn of a tool-calling conversation.

    Takes and returns whole messages rather than a prompt, because a tool loop
    has to carry the conversation forward. Everything litellm-shaped stops here:
    the caller gets a :class:`LlmTurn` of plain data.

    Raises :class:`LlmError` — already scrubbed — for anything the provider or
    the transport does wrong, including refusing ``tools`` outright.
    """
    if not config.model:
        raise LlmNotConfiguredError("No model configured")

    litellm = _litellm()
    try:
        response = await litellm.acompletion(
            model=config.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            api_key=config.api_key or None,
            api_base=config.api_base or None,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — every provider raises its own type
        raise LlmError(scrub_secrets(str(exc), key=config.api_key)) from None

    message = response.choices[0].message
    raw_calls = getattr(message, "tool_calls", None) or ()
    calls = tuple(
        ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments)
        for c in raw_calls
    )
    # `.model_dump()` on a provider object, or the object itself if it is
    # already a mapping — either way the caller gets something JSON-shaped.
    dumped = message.model_dump() if hasattr(message, "model_dump") else dict(message)
    return LlmTurn(content=(message.content or "").strip(), tool_calls=calls, message=dumped)
```

Add `"LlmTurn"`, `"ToolCall"` and `"complete_with_tools"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker exec megoopm-test python -m pytest tests/test_llm_service.py -p no:cacheprovider -p no:warnings`
Expected: PASS, including the existing lazy-import guard.

- [ ] **Step 5: Confirm the guard still holds, lint, commit**

```bash
docker exec megoopm-test python -c "import app.main, sys; print('litellm imported:', 'litellm' in sys.modules)"
docker exec megoopm-test ruff check app tests
docker exec megoopm-test ruff format --check app/services/llm.py tests/test_llm_service.py
git ls-files --eol backend/app/services/llm.py backend/tests/test_llm_service.py
git add backend/app/services/llm.py backend/tests/test_llm_service.py
git commit -m "feat(llm): add a tool-calling turn that normalises provider shapes"
```

Expected on the first line: `False`.

---

### Task 3: The tool loop

**Files:**
- Modify: `backend/app/services/page_assist.py`, `backend/tests/test_page_assist.py`

**Interfaces:**
- Consumes: `EditDocument`, `StagedEdit` (Task 1); `complete_with_tools`, `LlmTurn`, `ToolCall` (Task 2); the existing `complete`, `strip_document_fences`, `SYSTEM_PROMPT`.
- Produces: `MAX_TOOL_TURNS = 8`; `TOOL_SCHEMAS: list[dict]`; `TOOL_SYSTEM_PROMPT: str`; `AssistResult(html: str, mode: str, truncated: bool, changes: tuple[StagedEdit, ...])`; `async assist_page(config, *, instruction, html, timeout=ASSIST_TIMEOUT_SECONDS) -> AssistResult` — **note the return type changes from `str`**.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_page_assist.py`:

```python
# --- The tool loop ---------------------------------------------------------

import json  # noqa: E402

from app.services.llm import LlmError, LlmTurn, ToolCall  # noqa: E402
from app.services.page_assist import (  # noqa: E402
    MAX_TOOL_TURNS,
    AssistResult,
)

PAGE = "\n".join(
    ["<!doctype html>", "<html>", "  <body>", "    <h1>Old</h1>", "  </body>", "</html>"]
)


def _tool_turn(name: str, **arguments) -> LlmTurn:
    """An assistant turn that asks for one tool call."""
    return LlmTurn(
        content="",
        tool_calls=(ToolCall(id="c1", name=name, arguments=json.dumps(arguments)),),
        message={"role": "assistant", "content": None},
    )


def _done_turn(text: str = "Done.") -> LlmTurn:
    return LlmTurn(content=text, tool_calls=(), message={"role": "assistant", "content": text})


@pytest.fixture
def scripted(monkeypatch):
    """Drive the loop with a fixed sequence of model turns."""
    import app.services.page_assist as page_assist

    state: dict = {"turns": [], "seen": []}

    async def _with_tools(config, *, messages, tools, timeout=60.0):
        state["seen"].append({"messages": list(messages), "tools": tools})
        return state["turns"].pop(0)

    monkeypatch.setattr(page_assist, "complete_with_tools", _with_tools)
    return state


async def test_the_loop_edits_the_document_it_was_given(scripted) -> None:
    scripted["turns"] = [
        _tool_turn("grep", pattern="<h1"),
        _tool_turn("replace_lines", start=4, end=4, text="    <h1>New</h1>"),
        _done_turn(),
    ]
    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="rename it", html=PAGE)

    assert isinstance(result, AssistResult)
    assert result.mode == "tools"
    assert result.truncated is False
    assert "<h1>New</h1>" in result.html
    assert "<h1>Old</h1>" not in result.html
    assert len(result.changes) == 1
    assert result.changes[0].before == "    <h1>Old</h1>"


async def test_the_opening_message_carries_the_numbered_document(scripted) -> None:
    scripted["turns"] = [_done_turn()]
    await assist_page(LlmConfig(model="gpt-4o"), instruction="look", html=PAGE)
    opening = scripted["seen"][0]["messages"][-1]["content"]
    assert "rename" not in opening
    assert "1 |" in opening
    assert "<!doctype html>" in opening


async def test_tool_results_come_back_as_tool_messages(scripted) -> None:
    """A provider will reject a conversation that replies to a call any other way."""
    scripted["turns"] = [_tool_turn("grep", pattern="<h1"), _done_turn()]
    await assist_page(LlmConfig(model="gpt-4o"), instruction="look", html=PAGE)

    second_call = scripted["seen"][1]["messages"]
    tool_messages = [m for m in second_call if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "c1"
    assert "<h1>Old</h1>" in tool_messages[0]["content"]


async def test_an_unknown_tool_is_reported_to_the_model_not_raised(scripted) -> None:
    scripted["turns"] = [_tool_turn("delete_everything"), _done_turn()]
    await assist_page(LlmConfig(model="gpt-4o"), instruction="look", html=PAGE)
    tool_message = [m for m in scripted["seen"][1]["messages"] if m.get("role") == "tool"][0]
    assert "unknown tool" in tool_message["content"].lower()


async def test_malformed_tool_arguments_are_reported_to_the_model(scripted) -> None:
    bad = LlmTurn(
        content="",
        tool_calls=(ToolCall(id="c1", name="grep", arguments="{not json"),),
        message={"role": "assistant", "content": None},
    )
    scripted["turns"] = [bad, _done_turn()]
    await assist_page(LlmConfig(model="gpt-4o"), instruction="look", html=PAGE)
    tool_message = [m for m in scripted["seen"][1]["messages"] if m.get("role") == "tool"][0]
    assert "error" in tool_message["content"].lower()


async def test_the_turn_cap_stops_a_model_that_never_finishes(scripted) -> None:
    """Applied, not discarded — the operator paid for the work that got done."""
    scripted["turns"] = [
        _tool_turn("replace_lines", start=4, end=4, text="    <h1>New</h1>")
    ] + [_tool_turn("grep", pattern="x") for _ in range(MAX_TOOL_TURNS)]

    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="loop", html=PAGE)
    assert result.truncated is True
    assert result.mode == "tools"
    assert "<h1>New</h1>" in result.html
    assert len(scripted["seen"]) == MAX_TOOL_TURNS


# --- Generation and fallback -----------------------------------------------


async def test_an_empty_document_generates_rather_than_editing(captured) -> None:
    """There is nothing to grep; the model writes a page."""
    calls, _ = captured
    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="a 503 page", html="")
    assert result.mode == "generate"
    assert result.changes == ()
    assert calls  # the plain completion path ran


async def test_a_model_that_never_calls_a_tool_falls_back_to_a_rewrite(
    scripted, captured
) -> None:
    calls, reply = captured
    reply["value"] = "<!doctype html>\n<html><body>rewritten</body></html>"
    scripted["turns"] = [_done_turn("I would change the heading.")]

    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="edit", html=PAGE)
    assert result.mode == "rewrite"
    assert "rewritten" in result.html
    assert result.changes == ()


async def test_a_provider_that_rejects_tools_falls_back_to_a_rewrite(
    captured, monkeypatch
) -> None:
    """MiniMax-style: tools may not be supported, and the feature must still work."""
    import app.services.page_assist as page_assist

    _, reply = captured
    reply["value"] = "<!doctype html>\n<html><body>rewritten</body></html>"

    async def _boom(config, *, messages, tools, timeout=60.0):
        raise LlmError("tools is not supported by this model")

    monkeypatch.setattr(page_assist, "complete_with_tools", _boom)

    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="edit", html=PAGE)
    assert result.mode == "rewrite"
    assert "rewritten" in result.html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_page_assist.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — `ImportError: cannot import name 'MAX_TOOL_TURNS'`.

- [ ] **Step 3: Write the loop**

In `backend/app/services/page_assist.py`, extend the imports:

```python
import json
from dataclasses import dataclass, field

from app.services.llm import LlmConfig, LlmError, complete, complete_with_tools
from app.services.page_tools import EditDocument, StagedEdit
```

and add, after `SYSTEM_PROMPT`:

```python
# Each turn is a round trip. Eight is the ceiling that stops a confused model
# wandering, and at 5-20s a turn it still fits inside ASSIST_TIMEOUT_SECONDS.
MAX_TOOL_TURNS = 8

TOOL_SYSTEM_PROMPT = """You edit single-file HTML pages for MegooPM, a \
reverse-proxy manager, using the tools provided.

How to work:
- The document is shown to you with line numbers. Use grep to locate text and \
read_lines to see more around it.
- Use replace_lines to change what needs changing. Change as little as \
possible; leave every line the instruction does not require you to touch.
- Line numbers ALWAYS refer to the document as first shown to you. They never \
shift, however many edits you stage.
- To insert, replace a line with itself plus the new content. To delete, \
replace with empty text.
- grep matches literal text, not regular expressions.
- When the instruction is satisfied, reply with a short sentence and no \
further tool calls.

The page is served directly by nginx and must stay entirely self-contained: no \
external stylesheets, scripts, fonts or images. Any src value of the form \
data:<type>;base64,MEGOOPM_IMAGE_<number> is a placeholder for an image already \
in the page — reproduce it exactly."""

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Find lines containing a literal substring. Returns matching "
                "lines with their numbers and surrounding context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Literal text to find."},
                    "ignore_case": {"type": "boolean", "default": False},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_lines",
            "description": "Read an inclusive range of numbered lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                },
                "required": ["start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_lines",
            "description": (
                "Stage a replacement of an inclusive line range. Line numbers "
                "refer to the original document and never shift."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                    "text": {"type": "string", "description": "The replacement lines."},
                },
                "required": ["start", "end", "text"],
            },
        },
    },
]


@dataclass(frozen=True, slots=True)
class AssistResult:
    """What one assist produced, and by which route.

    ``mode`` is ``tools`` for a targeted edit, ``generate`` for a page written
    from nothing, and ``rewrite`` when the tool path could not be used and the
    whole document was regenerated instead.
    """

    html: str
    mode: str
    truncated: bool = False
    changes: tuple[StagedEdit, ...] = field(default_factory=tuple)


def _run_tool(doc: EditDocument, call) -> str:
    """Dispatch one tool call. Every failure is a message, never an exception —
    the model reads the result and can correct itself inside the loop."""
    try:
        args = json.loads(call.arguments or "{}")
    except ValueError:
        return "Error: arguments were not valid JSON. Send a JSON object."

    if call.name == "grep":
        return doc.grep(str(args.get("pattern", "")), ignore_case=bool(args.get("ignore_case")))
    if call.name == "read_lines":
        return doc.read_lines(int(args.get("start", 0)), int(args.get("end", 0)))
    if call.name == "replace_lines":
        return doc.replace_lines(
            int(args.get("start", 0)), int(args.get("end", 0)), str(args.get("text", ""))
        )
    return f"Error: unknown tool {call.name!r}. Available: grep, read_lines, replace_lines."
```

Then replace `assist_page` with the orchestrator plus the loop:

```python
async def _rewrite(config: LlmConfig, instruction: str, html: str, timeout: float) -> str:
    """The original whole-document path, still used to generate and to fall back."""
    raw = await complete(
        config,
        prompt=build_user_message(instruction, html),
        system=SYSTEM_PROMPT,
        timeout=timeout,
    )
    return strip_document_fences(raw)


async def _edit_with_tools(
    config: LlmConfig, *, instruction: str, html: str, timeout: float
) -> AssistResult:
    """Drive the model through the document tools. Raises LlmError like any call."""
    doc = EditDocument(html)
    messages: list[dict] = [
        {"role": "system", "content": TOOL_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Instruction:\n{instruction}\n\nDocument:\n{doc.numbered()}",
        },
    ]

    truncated = True
    for _ in range(MAX_TOOL_TURNS):
        turn = await complete_with_tools(
            config, messages=messages, tools=TOOL_SCHEMAS, timeout=timeout
        )
        messages.append(turn.message)
        if not turn.tool_calls:
            truncated = False
            break
        for call in turn.tool_calls:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": _run_tool(doc, call),
                }
            )

    new_html, changes = doc.apply()
    return AssistResult(html=new_html, mode="tools", truncated=truncated, changes=changes)


async def assist_page(
    config: LlmConfig,
    *,
    instruction: str,
    html: str,
    timeout: float = ASSIST_TIMEOUT_SECONDS,
) -> AssistResult:
    """Edit the page with tools, or write one, or fall back to a rewrite.

    The fallback fires on an :class:`LlmError` *anywhere* in the loop, not only
    on the first turn. A provider that refuses ``tools`` and one that fails
    mid-conversation surface as the same scrubbed error, and telling them apart
    is not reliably possible — so both retry as a rewrite. If the provider is
    genuinely broken the rewrite raises the same error and the operator sees it.
    """
    if not html.strip():
        return AssistResult(html=await _rewrite(config, instruction, html, timeout), mode="generate")

    try:
        result = await _edit_with_tools(
            config, instruction=instruction, html=html, timeout=timeout
        )
    except LlmError:
        return AssistResult(html=await _rewrite(config, instruction, html, timeout), mode="rewrite")

    if not result.changes:
        # The model answered in prose and never touched a tool. Nothing changed,
        # so give the operator the rewrite rather than a no-op.
        return AssistResult(html=await _rewrite(config, instruction, html, timeout), mode="rewrite")
    return result
```

Add `"MAX_TOOL_TURNS"`, `"TOOL_SCHEMAS"`, `"TOOL_SYSTEM_PROMPT"` and
`"AssistResult"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker exec megoopm-test python -m pytest tests/test_page_assist.py -p no:cacheprovider -p no:warnings`
Expected: PASS.

The pre-existing `test_assist_page_returns_the_document` and
`test_assist_page_strips_what_the_model_wrapped` assert a `str` return. Update
both to read `result.html` and assert `result.mode == "generate"`, passing
`html=""` so they exercise the generation path they were always about.

- [ ] **Step 5: Lint, check line endings, commit**

```bash
docker exec megoopm-test ruff check app tests
docker exec megoopm-test ruff format --check app/services/page_assist.py tests/test_page_assist.py
git ls-files --eol backend/app/services/page_assist.py backend/tests/test_page_assist.py
git add backend/app/services/page_assist.py backend/tests/test_page_assist.py
git commit -m "feat(custom-pages): drive page edits through a tool loop"
```

---

### Task 4: The API response

**Files:**
- Modify: `backend/app/schemas/custom_page.py`, `backend/app/api/routes/custom_pages.py`, `backend/openapi.json`
- Test: `backend/tests/test_custom_pages_api.py` (extend)

**Interfaces:**
- Consumes: `AssistResult` (Task 3).
- Produces: `PageEditChange(start, end, before, after)`; `PageAssistResponse(html, mode, truncated, changes)`.

- [ ] **Step 1: Write the failing test**

In `backend/tests/test_custom_pages_api.py`, replace the `stub_assist` fixture
body so it returns an `AssistResult`, and add the new assertions:

```python
@pytest.fixture
def stub_assist(monkeypatch):
    """Replace the model round trip; these tests are about the route."""
    import app.api.routes.custom_pages as routes
    from app.services.page_assist import AssistResult
    from app.services.page_tools import StagedEdit

    seen: list[dict] = []

    async def _assist(config, *, instruction, html, timeout=240.0):
        seen.append({"config": config, "instruction": instruction, "html": html})
        return AssistResult(
            html=ASSIST_DOC,
            mode="tools",
            truncated=False,
            changes=(StagedEdit(start=4, end=4, before="<h1>Old</h1>", after="<h1>New</h1>"),),
        )

    monkeypatch.setattr(routes, "assist_page", _assist)
    return seen


async def test_assist_reports_what_changed(client: AsyncClient, auth, stub_assist) -> None:
    await _enable_llm(client, auth)
    resp = await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "rename the heading", "html": "<p>x</p>"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["html"] == ASSIST_DOC
    assert body["mode"] == "tools"
    assert body["truncated"] is False
    assert body["changes"] == [
        {"start": 4, "end": 4, "before": "<h1>Old</h1>", "after": "<h1>New</h1>"}
    ]


async def test_assist_reports_a_fallback_rewrite(
    client: AsyncClient, auth, monkeypatch
) -> None:
    """A rewrite must not look like an edit that changed nothing."""
    import app.api.routes.custom_pages as routes
    from app.services.page_assist import AssistResult

    async def _assist(config, *, instruction, html, timeout=240.0):
        return AssistResult(html=ASSIST_DOC, mode="rewrite")

    monkeypatch.setattr(routes, "assist_page", _assist)

    await _enable_llm(client, auth)
    resp = await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "edit", "html": "<p>x</p>"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "rewrite"
    assert resp.json()["changes"] == []
```

The existing `test_assist_returns_a_document` asserts
`resp.json() == {"html": ASSIST_DOC}`. Change it to
`assert resp.json()["html"] == ASSIST_DOC`, since the body now carries more.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_custom_pages_api.py -k assist -p no:cacheprovider -p no:warnings`
Expected: FAIL — the response has no `mode`.

- [ ] **Step 3: Extend the schema**

In `backend/app/schemas/custom_page.py`, replace `PageAssistResponse`:

```python
class PageEditChange(BaseModel):
    """One line range the model replaced, so the operator can read what moved."""

    start: int
    end: int
    before: str
    after: str


class PageAssistResponse(BaseModel):
    """The cleaned document, and how it was produced.

    ``mode`` distinguishes a targeted edit (``tools``) from a page written from
    nothing (``generate``) and from a whole-document regeneration used because
    the tool path was unavailable (``rewrite``). Without that a fallback would
    look to the operator like an edit that changed nothing.

    Placeholders are restored by the browser, so ``html`` is still elided here.
    """

    html: str
    mode: str
    truncated: bool = False
    changes: list[PageEditChange] = Field(default_factory=list)
```

Add `"PageEditChange"` to `__all__`.

- [ ] **Step 4: Pass the fields through**

In `backend/app/api/routes/custom_pages.py`, import `PageEditChange` alongside
the other schemas and replace the tail of `assist_custom_page`:

```python
    try:
        result = await assist_page(config, instruction=body.instruction, html=body.html)
    except LlmNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    except LlmError as exc:
        # Already scrubbed of credentials by app/services/llm.py.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None

    await _audit(
        db,
        actor=admin.email,
        action=AuditAction.update,
        page_id=None,
        # The instruction and a size — never the document, which is the
        # operator's content and can be megabytes.
        instruction=body.instruction[:200],
        result_bytes=len(result.html.encode("utf-8")),
        mode=result.mode,
        edits=len(result.changes),
    )
    return PageAssistResponse(
        html=result.html,
        mode=result.mode,
        truncated=result.truncated,
        changes=[
            PageEditChange(start=c.start, end=c.end, before=c.before, after=c.after)
            for c in result.changes
        ],
    )
```

- [ ] **Step 5: Run tests, regenerate OpenAPI, commit**

```bash
export MSYS_NO_PATHCONV=1
docker exec megoopm-test python -m pytest tests/test_custom_pages_api.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
docker exec megoopm-test python -c "import app.main, sys; print('litellm imported:', 'litellm' in sys.modules)"
git ls-files --eol backend/app/schemas/custom_page.py backend/app/api/routes/custom_pages.py backend/openapi.json backend/tests/test_custom_pages_api.py
git add backend/app backend/openapi.json backend/tests
git commit -m "feat(custom-pages): report which edits the model made, and by which route"
```

---

### Task 5: Show the operator what changed

**Files:**
- Modify: `frontend/src/components/custom-pages/custom-page-editor-view.tsx`, `frontend/src/components/custom-pages/custom-page-editor-view.test.tsx`, `frontend/src/lib/api/generated/schema.ts`

**Interfaces:**
- Consumes: the response's `mode`, `truncated` and `changes` (Task 4).

- [ ] **Step 1: Regenerate the API types**

```bash
cd frontend && npm run gen:api
grep -n "PageEditChange" src/lib/api/generated/schema.ts | head -3
```
Expected: it appears.

- [ ] **Step 2: Write the failing test**

In `custom-page-editor-view.test.tsx`, the AI `beforeEach` currently mocks
`assist` with `{ html: AI_DOC }`. Change it to the full shape and add the tests:

```tsx
    vi.spyOn(customPages, "assist").mockResolvedValue({
      html: AI_DOC,
      mode: "tools",
      truncated: false,
      changes: [
        { start: 4, end: 4, before: "    <h1>Old</h1>", after: "    <h1>New</h1>" },
      ],
    });
```

```tsx
  it("lists the lines the model changed", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(HTML));

    await generate(user, "rename the heading");

    expect(await screen.findByText(/1 change/i)).toBeInTheDocument();
    expect(screen.getByText(/line 4/i)).toBeInTheDocument();
    expect(screen.getByText("    <h1>Old</h1>")).toBeInTheDocument();
    expect(screen.getByText("    <h1>New</h1>")).toBeInTheDocument();
  });

  it("says the page was rewritten rather than showing an empty change list", async () => {
    // Otherwise a fallback looks like an edit that changed nothing.
    vi.mocked(customPages.assist).mockResolvedValue({
      html: AI_DOC,
      mode: "rewrite",
      truncated: false,
      changes: [],
    });
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(HTML));

    await generate(user, "make it dark");

    expect(await screen.findByText(/rewrote the whole page/i)).toBeInTheDocument();
    expect(screen.queryByText(/1 change/i)).not.toBeInTheDocument();
  });

  it("warns when the model ran out of turns", async () => {
    vi.mocked(customPages.assist).mockResolvedValue({
      html: AI_DOC,
      mode: "tools",
      truncated: true,
      changes: [
        { start: 4, end: 4, before: "    <h1>Old</h1>", after: "    <h1>New</h1>" },
      ],
    });
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(HTML));

    await generate(user, "do a lot");

    expect(await screen.findByText(/stopped early/i)).toBeInTheDocument();
  });

  it("clears the change list on revert", async () => {
    const user = userEvent.setup();
    render(<CustomPageEditorView pageId={7} />);
    await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(HTML));

    await generate(user, "rename the heading");
    await screen.findByText(/1 change/i);

    await user.click(screen.getByRole("button", { name: "Revert AI edit" }));
    expect(screen.queryByText(/1 change/i)).not.toBeInTheDocument();
  });
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/custom-pages/custom-page-editor-view.test.tsx`
Expected: FAIL — no change list is rendered.

- [ ] **Step 4: Render the summary**

In `custom-page-editor-view.tsx`, add state beside `htmlBeforeAi`:

```tsx
  // What the last AI edit did, for the operator to read. `null` means no AI
  // edit has happened since the last revert.
  const [lastEdit, setLastEdit] = useState<
    { mode: string; truncated: boolean; changes: PageEditChange[] } | null
  >(null);
```

Import the type: `import { customPages, instanceSettings, type CustomPage, type PageEditChange } from "@/lib/api";`
(and export `PageEditChange` from `src/lib/api/resources/custom-pages.ts` and
`src/lib/api/index.ts` alongside `PageAssistResponse`).

In `handleAssist`, after `patch({ html: restored.html })`:

```tsx
      setLastEdit({
        mode: result.mode,
        truncated: result.truncated,
        changes: result.changes,
      });
```

In `handleRevertAi`, add `setLastEdit(null);`.

Render it directly below the prompt bar:

```tsx
      {lastEdit ? (
        <section className="space-y-2 rounded-xl border p-3 text-sm">
          {lastEdit.mode === "tools" ? (
            <p className="font-medium">
              {lastEdit.changes.length} change
              {lastEdit.changes.length === 1 ? "" : "s"} applied
            </p>
          ) : (
            <p className="font-medium">Rewrote the whole page</p>
          )}
          {lastEdit.truncated ? (
            <p className="text-xs text-warning">
              The model stopped early after reaching its step limit — check the
              result before saving.
            </p>
          ) : null}
          {lastEdit.changes.map((change) => (
            <div key={`${change.start}-${change.end}`} className="space-y-0.5">
              <p className="text-xs text-muted-foreground">
                line {change.start}
                {change.end !== change.start ? `–${change.end}` : ""}
              </p>
              <pre className="overflow-x-auto rounded bg-destructive/10 p-1.5 font-mono text-xs">
                {change.before}
              </pre>
              <pre className="overflow-x-auto rounded bg-success/10 p-1.5 font-mono text-xs">
                {change.after}
              </pre>
            </div>
          ))}
        </section>
      ) : null}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/custom-pages/`
Expected: PASS.

If a query is ambiguous, check whether two elements now share an accessible
name and rename the *element* — that has been a real UI wart twice in this
codebase, not a test problem.

- [ ] **Step 6: Full frontend verification**

```bash
cd frontend && npx vitest run && npm run typecheck && npm run lint && npm run build
```

- [ ] **Step 7: Check it against a real model**

```bash
docker compose up -d --build
```

With MiniMax configured in Settings:

- Open a page with a heading and ask "make the heading bigger". Expect
  `1 change applied`, the before/after lines shown, and the preview updated.
- Confirm in `docker compose logs backend` that the request finished in fewer
  turns than the cap.
- Insert an image, then ask for an unrelated change. The image must survive and
  must not appear in the request body — check devtools for `MEGOOPM_IMAGE_1`
  and no base64.
- Ask for something sweeping ("convert this to dark mode"). Either many changes
  or a rewrite is correct; what matters is that the summary says which.
- **Revert AI edit** restores the document exactly and clears the change list.
- Point Settings at a model without tool support and repeat the first step:
  expect `Rewrote the whole page` rather than an error.

- [ ] **Step 8: Line endings and commit**

```bash
git ls-files --eol frontend/src/components/custom-pages/custom-page-editor-view.tsx frontend/src/components/custom-pages/custom-page-editor-view.test.tsx frontend/src/lib/api/resources/custom-pages.ts frontend/src/lib/api/index.ts
git add frontend/src
git commit -m "feat(custom-pages): show which lines the AI edit changed"
```

---

## Done when

- Every task's steps are checked off.
- `docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings` — all pass, no new skips.
- `docker exec megoopm-test ruff check app tests alembic` — clean.
- `docker exec megoopm-test python -c "import app.main, sys; print('litellm' in sys.modules)"` prints `False`.
- `cd frontend && npx vitest run && npm run typecheck && npm run lint && npm run build` — all pass.
- `git ls-files --eol` shows no `w/crlf` on any changed file.
- The manual pass in Task 5 Step 7 has been walked against MiniMax, including
  the image round trip and the no-tool-support fallback.
- Test containers torn down: `docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet`.

## Follow-up, not in this plan

Streaming, per-change accept/reject, and multi-turn refinement with the
operator — all recorded as non-goals in the spec.
