# Targeted page edits via a grep tool — design

## Goal

Replace the full-document rewrite in the Custom Pages AI editor with targeted
edits. The model is given an IDE-like toolset — `grep`, `read_lines`,
`replace_lines` — over the document it is editing, so it changes only the lines
it names instead of regenerating the page.

Generating a page from nothing is unchanged: there is nothing to edit, so the
model writes a whole document.

## Why

Today "make the heading bigger" on a 10 KB page sends 10 KB up and regenerates
10 KB back. That costs two things:

- **Latency and spend.** Output tokens are the slow, expensive ones — roughly
  3k of them whether one word changed or the page was rewritten. This is most
  of why generations run into proxy timeouts.
- **Fidelity.** The model rewrites every line, so it can quietly reflow CSS,
  drop a comment or rename a class that nobody asked it to touch. Nothing
  detects that. `Revert AI edit` is an undo, not a review: it says *that*
  something changed, never *what*.

The second is the one that prompted this. A tool-driven edit cannot touch a line
it did not name.

## Non-goals

- **Streaming.** Still deferred. A tool loop is a sequence of discrete calls, so
  streaming would apply to the final message only.
- **Per-change accept/reject.** Changes are applied and listed; `Revert AI edit`
  remains the escape hatch, as decided in the previous round.
- **Multi-turn conversation with the operator.** One instruction, one loop.
- **Editing anything but the page in the editor.** The tools are scoped to a
  single in-memory document. They are not a filesystem.

## Decisions taken during brainstorming

**A grep tool loop, chosen over two simpler options.** The operator was shown
both alternatives and the measurements behind them:

| approach | round trips | provider needs | matching failure mode |
| --- | --- | --- | --- |
| SEARCH/REPLACE blocks | 1 (+1 retry) | none | model must reproduce text exactly |
| line-numbered edits | 1 | none | model miscounts; catchable with a checksum |
| **grep tool loop** | **2–8** | **tool calling** | none — the model reads before it writes |

They chose the tool loop knowing it costs extra round trips and depends on
provider support. This design therefore carries a fallback (below) rather than
treating that dependency as safe.

**Verified before designing:** litellm forwards `tools` to a custom
OpenAI-compatible endpoint and parses `tool_calls` back — probed against a stub
with `model="openai/MiniMax-M3"`, tools arrived at the provider and the call came
back parsed. So the mechanism works. What is *not* verified is whether MiniMax
itself honours `tools`; `litellm.supports_function_calling` returns `False` for
it, but that is an empty registry entry for a custom endpoint, not evidence.

## The tools

Three, deliberately small. Each operates on one in-memory document.

```python
grep(pattern: str, ignore_case: bool = False) -> str
read_lines(start: int, end: int) -> str
replace_lines(start: int, end: int, text: str) -> str
```

`grep` returns matching lines with their numbers and two lines of context either
side; `read_lines` returns a numbered range; `replace_lines` stages an edit and
confirms it.

**Ranges are 1-based and inclusive on both ends**, so `replace_lines(13, 13, …)`
replaces exactly line 13. `replace_lines` is the only mutation, which keeps the
staging model simple: an **insertion** is a replacement that re-emits the
original line plus the new one, and a **deletion** is a replacement with empty
text. Adding `insert_at` and `delete_lines` would triple the overlap rules for
no capability the model does not already have.

### grep matches literal substrings, not regex

A model-supplied regex is an untrusted pattern executed against a document up to
200 KB. Python's `re` has no timeout, so a single catastrophic-backtracking
pattern hangs the worker that runs it — a denial of service reachable by any
model that emits a bad pattern, deliberately or otherwise.

Literal substring matching removes the entire class. `<h1`, `font-size`,
`support@` are what the model actually needs; nothing about this task requires
alternation or quantifiers. `ignore_case` covers the one real ergonomic gap.

## Staging, and the line-numbering trap

**`replace_lines` stages an edit. It does not apply one.**

This is the bug this design most has to avoid. If edits applied as they arrived,
the first one would change every line number after it — while the model, still
reasoning from the numbering it was shown, went on addressing lines that had
moved. The corruption would be silent and would look like the model
misbehaving.

So:

- Line numbers **always** refer to the original document, for the whole loop.
- Every tool result says so explicitly, so the model is never left inferring it.
- Staged edits are applied **bottom-up** at the end, so an earlier edit cannot
  shift a later one.
- **Overlapping ranges are refused**, not merged: two edits to the same lines
  produce markup neither the model nor the operator intended.

A rejected `replace_lines` returns its reason as the tool result — out of range,
inverted range, overlaps a staged edit — so the model can correct itself within
the loop rather than failing the whole request.

## The loop

The document is sent once, line-numbered, in the first user message. Then:

```
for turn in range(MAX_TURNS):        # MAX_TURNS = 8
    reply = acompletion(messages, tools=TOOLS)
    if reply has tool_calls:
        run each, append its result as a tool message
        continue
    break                            # the model is done
apply staged edits bottom-up
```

Eight turns is the hard cap that stops a confused model wandering. At 5–20
seconds a turn it fits inside the 240s assist budget, which itself sits below
the proxy's 300s.

**If the cap is reached with edits already staged, they are applied** and the
response sets `truncated: true`. Discarding them would throw away work the
operator paid for, and every staged edit was individually validated; the
plausible failure is a change the model had not finished, which the live preview
shows and `Revert AI edit` undoes. The editor says the loop was cut short so
that is never a silent outcome.

**On the economics, plainly:** because the document stays in context every turn,
input grows as output shrinks and the *token* saving largely cancels out. What
this buys is precision — the model can only change lines it names. That is the
fidelity problem, and it is the reason to do this.

## The fallback

If the provider rejects `tools`, or answers with prose and never calls one, the
backend **falls back to the existing full-document rewrite** — the current
`assist_page` path, unchanged.

MiniMax's tool support is unverified, and shipping a design where one unknown
breaks the feature outright is not acceptable. The response records which path
ran in `mode`, so the behaviour is never a mystery, and the editor shows it.

## API

`POST /api/v1/custom-pages/assist` keeps its request shape —
`{instruction, html}`, with `html` already elided in the browser. The response
grows:

```
{
  "html":  str,                              # the finished document
  "mode":  "tools" | "rewrite" | "generate", # which path produced it
  "truncated": bool,                         # the turn cap cut the loop short
  "changes": [                               # empty unless mode is "tools"
    {"start": int, "end": int, "before": str, "after": str}
  ]
}
```

Three modes, not two: a **generation** from an empty document produces a whole
page legitimately, and reporting that as `rewrite` would make the editor say the
fallback had fired when it had not.

Everything else about the endpoint is unchanged: admin-only, 422 when LLM
features are off or the input is invalid, 502 on a provider failure with the
scrubbed message, and the same audit entry recording the instruction and result
size but never the document.

## Frontend

Unchanged except for what it does with `changes`: the edits are applied
immediately, the live preview re-renders, and the blocks are listed above the
editor so the operator can read what moved. `Revert AI edit` is untouched.

When `mode` is `rewrite`, the list is replaced by one line saying the whole page
was rewritten — otherwise a fallback would look like an edit that changed
nothing.

Image elision is unaffected: it happens before line numbering, and placeholders
are single-line, so numbering is stable.

## Testing

**The document tools and the staging engine are pure**, carry all the risk, and
get the bulk of the coverage: a grep hit, a miss, several hits, `ignore_case`,
context clamped at the first and last line, `read_lines` out of range and
inverted, a staged edit, two overlapping stages refused, three stages applied
bottom-up in the right order, and a replacement that changes the line count not
disturbing the others.

**The loop** runs against a scripted fake model — a `grep`, then a
`replace_lines`, then a plain message — asserting the tool results come back as
`role: "tool"` messages, that the turn cap stops an endless caller, and that a
provider which never calls a tool triggers the rewrite fallback with
`mode: "rewrite"`.

**Not covered by automated tests:** whether a real model drives these tools
sensibly, and whether MiniMax supports tool calling at all. Both are a manual
pass against a configured provider, and the second determines which path an
operator actually gets.

## Files

**Backend**

- `app/services/page_tools.py` (new) — `grep`, `read_lines`, `replace_lines`,
  the staging engine, line numbering
- `app/services/page_assist.py` — the tool loop, the tool schemas, the fallback
- `app/schemas/custom_page.py` — `mode` and `changes` on the response
- `app/api/routes/custom_pages.py` — pass the new fields through
- `tests/test_page_tools.py` (new), `tests/test_page_assist.py` (extended)

**Frontend**

- `src/components/custom-pages/lib.ts` — nothing new; elision is unchanged
- `src/components/custom-pages/custom-page-editor-view.tsx` — render `changes`
  and the `rewrite` notice
- its test — the changes list, and the rewrite case

## Open risks

**Tool support is unverified on the operator's provider.** The fallback means
the feature still works either way, but if MiniMax cannot call tools, the
operator gets today's behaviour plus a notice — and the work in this spec buys
them nothing until they point at a model that can. A five-minute probe against a
real key settles it and should happen before implementation starts.

**A model can stage edits that are individually valid and jointly wrong** —
three non-overlapping replacements that together produce invalid markup. Nothing
here validates the resulting HTML. The live preview and `Revert AI edit` are the
protection, which is the same protection the current rewrite path has.
