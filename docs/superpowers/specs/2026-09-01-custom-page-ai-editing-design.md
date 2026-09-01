# AI-assisted editing in the Custom Pages editor — design

## Goal

Let an operator type an instruction in the Custom Pages editor — "make the
heading bigger", "add a support email under the message", "a 503 maintenance
page, dark, with a status link" — and get a revised or newly written HTML
document back, using the LLM configured in Settings.

One prompt box serves both: with an empty or starter document it writes a page,
with real content it edits the one in front of you.

## Non-goals

- **Streaming.** A full generation takes 20–60 seconds and streaming would make
  that feel like progress rather than a hang, but it costs an SSE route, a
  streaming service variant and frontend stream handling — and it forces the
  live preview to freeze mid-generation, since a half-written document renders
  as broken markup. Adding it later is a second service function and one route,
  not a redesign.
- **A diff view.** The strongest review, and rejected deliberately: see
  **Decisions**.
- **Per-request model choice.** One model for the instance, from Part A.
- **Cost or token display.** Nothing counts spend yet.
- **Multi-turn conversation.** One instruction, one result. Refining means
  typing another instruction against the new document, which is the same thing
  with less machinery.

## Decisions taken during brainstorming

**One prompt box for both generating and editing.** The elision machinery below
is needed for the edit case regardless, so supporting generation costs only
prompt wording.

**The result replaces the document, with an explicit Revert** — rather than a
diff with accept/reject. A diff is the better review and was offered: it shows
exactly what changed instead of asking the operator to compare two renderings.
It was declined because it needs a diff renderer as a new dependency, and
because the editor already puts a live preview beside the source, so a replaced
document is visible immediately. `Revert AI edit` holds the exact prior text,
and CodeMirror's own undo still works alongside it.

**No streaming** (see Non-goals). The operator chose the simpler path, reusing
`complete()` from Part A unchanged.

## The image round trip

This is the part of the design that has to be right, because it is what makes
the feature possible at all.

A custom page may hold up to 2 MiB, and images live inside it as base64 `data:`
URIs. Base64 is roughly one token per three characters, so sending a page whole:

| page | tokens |
| --- | --- |
| the congratulations page, ~5 KB | ~1.4k — fine |
| the same page plus one 200 KB screenshot | ~70k — most of a context window, for a blob the model cannot use |
| a page near the 2 MiB cap | ~500k — impossible |

So each data URI is replaced by a placeholder before the request and restored
after:

```
in the editor    <img src="data:image/png;base64,iVBORw0KG…184 KB…">
sent             <img src="data:image/png;base64,MEGOOPM_IMAGE_1">
returned         <img src="data:image/png;base64,MEGOOPM_IMAGE_1">
applied          <img src="data:image/png;base64,iVBORw0KG…184 KB…">
```

**The placeholder stays syntactically a data URI**, mime type included, rather
than something like `[image 1]`. A model shown a malformed `src` attribute tends
to repair it; one shown a well-formed URI it does not understand leaves it
alone.

**Elision happens in the browser, not on the server.** The alternative — send
the whole document and let the backend elide — moves up to 2 MiB up and the same
back down to change a heading, which defeats the entire point. Doing it client
side also means the base64 never leaves the browser on this path.

### When the model does not cooperate

| what came back | what happens |
| --- | --- |
| every placeholder intact | each is restored to its original URI |
| a placeholder missing | that image was removed. Legitimate — the instruction may have asked for it — so the result stands and a note says how many were removed |
| a placeholder that was never sent | the literal text is left in place, so the preview shows a visibly broken image, and a warning says the result referenced an image that is not in the page |

Leaving an unknown placeholder alone is deliberate. Stripping the `<img>` would
be a silent structural edit nobody asked for; a broken image in the preview is
immediately visible and immediately fixable.

Both notes are produced **in the browser, by the restoration step**. The server
never sees the placeholder map, so it cannot know an image was dropped or
invented — which is why the response carries only `html` and the warnings are
computed where the mapping lives.

### The size guard

Even elided, a document can be too large — 2 MiB of pure markup is unusual but
possible. Anything over **200 KB** after elision is refused before the request
is made, with a message saying so. The backend enforces the same cap, so a
client that skips its own check (or sends an un-elided document by mistake) gets
a 422 rather than a provider error or a surprising bill.

## Output hygiene

Models wrap code in ```` ```html ```` fences constantly, whatever the system
prompt says, and frequently add a sentence of preamble. Left alone that renders
as literal backticks on the served page.

The backend therefore strips, in order: a leading fence with optional language
tag, a trailing fence, and any prose before the first `<!doctype` or `<html`. It
is a small pure function with its own tests, because it is the difference
between a working page and a visibly broken one.

**If no `<!doctype` or `<html` is found, the fence-stripped text is returned as
it is** rather than rejected. A model that answers with a fragment, or with
prose, has produced something the operator can see in both the editor and the
preview and can revert with one click. Refusing would also reject the legitimate
case where an instruction asked for a fragment.

## Backend — API

`POST /api/v1/custom-pages/assist`, admin-only and **stateless**: it takes the
document rather than a page id, so it works on a page that has never been saved.

```
request   { "instruction": str, "html": str }
response  { "html": str }
```

- **422 when `llm_enabled` is false.** The Settings flag gates feature code, and
  this is feature code — unlike Part A's probe, which deliberately ignores it so
  a configuration can be proved before being switched on.
- **422 when `html` exceeds 200 KB**, matching the client guard.
- **422 when `instruction` is empty or exceeds 2000 characters.** An
  instruction is a sentence or two; an unbounded field is just an unmetered
  path into a paid API.
- Provider failures surface as the scrubbed `LlmError` message from Part A. No
  new error handling: that module is already the only door to a provider.
- Audited via `record_audit`, no nginx reload — nothing here writes a page. The
  audit entry records the instruction and the resulting size, never the
  document.

The route sits under `/custom-pages` because that is its only consumer, and
`POST /assist` cannot be shadowed by the `GET /{page_id}` beside it.

### The prompt

A system prompt establishing: return one complete HTML document and nothing
else; no markdown fences; the document must be self-contained with **no external
requests** — the same rule the congratulations page follows, because a custom
page is served straight off nginx and may be on a machine with no egress; and
any `MEGOOPM_IMAGE_n` token must be reproduced verbatim.

The user message carries the instruction and the current document, or states
that the document is empty when generating from nothing.

## Frontend — the editor

An **Ask AI** button joins the HTML pane's toolbar, beside *Insert image*. It
toggles a prompt row above the split, so the default layout is unchanged for
anyone not using the feature.

```
┌─ Ask AI ────────────────────────────────────────────┐
│ make the heading bigger and add a support email      │
│                              [ Cancel ] [ Generate ] │
└──────────────────────────────────────────────────────┘
┌─ HTML ──────────── 4.2 KB ──┬─ Preview ─────────────┐
│  1 <!doctype html>          │                       │
│  2 <html>                   │    Access denied      │
│  …                          │    support@…          │
└─────────────────────────────┴───────────────────────┘
  ↻ Revert AI edit                        [ Save ]
```

While generating: a spinner with elapsed seconds, and **Cancel** aborts the
request. `ApiRequestOptions` extends `RequestInit`, so an `AbortSignal` passes
straight through the existing client with no change to it.

On success the document is replaced, the preview updates on its usual debounce,
and **Revert AI edit** appears holding the exact prior text. It stays until the
next AI edit.

**When LLM features are off**, the button is visible but inert and opens a
one-line note linking to Settings. Hiding it would mean nobody discovers the
feature exists. The editor learns this from `instanceSettings.get()`, fetched
alongside the page.

## Testing

**Frontend** — the elision helpers are pure and carry the most risk, so they get
the most coverage: a round trip that returns the original document byte for
byte; a document with several images; one with none; a returned document missing
a placeholder; one carrying a placeholder that was never sent; and the size
guard at and over its limit.

Then the editor flow: generate applies the result, Revert restores the prior
text exactly, Cancel aborts without touching the document, a provider failure
surfaces the message and leaves the document alone, and the disabled state
renders the Settings link instead of firing a request.

**Backend** — fence and preamble stripping across the shapes models actually
emit; the prompt carries the instruction and the document; 422 for disabled,
oversize and empty-instruction; a provider failure surfacing the scrubbed
message; admin-only.

**Not covered by automated tests:** that a real model returns usable HTML for a
real instruction. That is a manual pass against a configured provider.

## Files

**Backend**

- `app/schemas/custom_page.py` — `PageAssistRequest`, `PageAssistResponse`
- `app/services/page_assist.py` (new) — the prompt, the call, fence stripping
- `app/api/routes/custom_pages.py` — the `assist` route
- `tests/test_page_assist.py` (new)

**Frontend**

- `src/lib/api/resources/custom-pages.ts` — `assist(body, options?)`
- `src/components/custom-pages/lib.ts` — `elideImages`, `restoreImages`,
  `MAX_ASSIST_BYTES`, and the warning text
- `src/components/custom-pages/lib.test.ts` — their tests
- `src/components/custom-pages/ai-prompt-bar.tsx` (new) + test
- `src/components/custom-pages/custom-page-editor-view.tsx` — mount the bar,
  hold `htmlBeforeAi`, fetch the settings row
- its test — the new flow

## Open risks

**Prompt injection is noted, not defended against.** Page HTML goes into the
prompt, so its content can steer the model. Pages are authored by admins and
this endpoint is admin-only, so nothing crosses a privilege boundary — the
author and the operator are the same person. That stops being true the day pages
can be imported from anywhere else, and this paragraph is the marker for that
day.

**Model output is not sanitised as HTML.** It is written into a page the
operator then previews and explicitly saves, and an admin can already type
arbitrary HTML into the editor by hand, so the model adds no capability the
operator lacks. The preview iframe's existing `sandbox="allow-scripts"` without
`allow-same-origin` is what keeps generated script off the admin origin.
