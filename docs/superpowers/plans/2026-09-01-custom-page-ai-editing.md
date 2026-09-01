# AI-Assisted Page Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator type an instruction in the Custom Pages editor and get a revised — or newly written — HTML document back from the LLM configured in Settings.

**Architecture:** The browser swaps each base64 `data:` URI for a placeholder before the request and swaps it back after, so an image-heavy page costs ~2k tokens instead of ~70k and the base64 never leaves the browser. A stateless `POST /custom-pages/assist` builds the prompt, calls Part A's `complete()`, and strips the markdown fences models insist on adding. The result replaces the document; a Revert button holds the exact prior text.

**Tech Stack:** FastAPI, Pydantic v2, `litellm` via `app/services/llm.py`; Next.js 16 + React 19 + CodeMirror 6 + vitest on the frontend.

**Spec:** `docs/superpowers/specs/2026-09-01-custom-page-ai-editing-design.md`

## Global Constraints

- **Elision happens in the browser, never on the server.** Server-side elision would move up to 2 MiB up and the same back down to change a heading. The server receives an already-elided document and never sees the placeholder map.
- **The placeholder stays syntactically a data URI** — `data:image/png;base64,MEGOOPM_IMAGE_1`, mime preserved. A model shown a malformed `src` repairs it; one shown a well-formed URI it does not understand leaves it alone.
- **`MAX_ASSIST_BYTES` is 200 KB**, checked client-side for a friendly message *and* server-side for enforcement. The duplication is deliberate — the server cannot trust a client that skipped its own check.
- **Warnings are computed in the browser**, by the restoration step. The server never sees the placeholder map, so the response carries only `html`.
- **`litellm` is NEVER imported at module scope** anywhere. `app/services/llm.py` is the only module that touches it and imports it inside its functions; it costs 3.49s to load against 0.84s for the whole app. `tests/test_llm_service.py` already pins this — do not break it.
- **Provider error text is never returned verbatim.** `app/services/llm.py` already scrubs it into `LlmError`; surface that message, never a raw exception.
- **Backend tests only run on Linux** (`app` imports `fcntl`) and most need a reachable Postgres. Use the containerised runner below; never run `pytest` on the Windows host.
- **Run pytest WITHOUT `-q`** — `pyproject.toml` already sets it, and `-qq` hides the pass count.
- **`ruff format --check .` reports ~32 pre-existing unformatted files.** Only format files you create; never reformat a file you did not otherwise touch.
- **Line endings must be LF.** After editing run `git ls-files --eol <files>`; anything `w/crlf` gets `sed -i 's/\r$//'`.
- **Never commit a provider-shaped credential, even a fake one.** GitHub push protection reads `sk_live_…` as a Stripe key and blocks the push — that already cost one rewritten history. Test fixtures stay obviously synthetic (`sk-EXAMPLE-not-a-real-credential-1`).
- **Schema changes need two regenerations:** `docker exec megoopm-test python -m scripts.export_openapi`, then `cd frontend && npm run gen:api`.
- **vitest does not typecheck** — run `npm run typecheck` separately. Frontend commands run from `frontend/`.
- Commits go **directly to `main`**, the operator's established preference for this repo.

### One deliberate deviation from the spec

**A provider failure returns 502, not 422.** The spec says only that failures "surface as the scrubbed `LlmError` message". 422 would be wrong: the request was well-formed and the client can do nothing about it. 502 says the upstream failed, which is what happened, and keeps it distinguishable in logs from the 422s that mean the operator sent something invalid.

This is the opposite of Part A's probe, which returns **200 with `ok: false`** on a provider failure — deliberately, because reporting on the connection *is* that endpoint's job, so a failed probe is a successful answer. Here there is no document to return, so the request genuinely failed.

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
| `backend/app/services/page_assist.py` | the system prompt, the call, fence/preamble stripping |
| `backend/tests/test_page_assist.py` | stripping across the shapes models emit; prompt shape |
| `frontend/src/components/custom-pages/ai-prompt-bar.tsx` | the prompt row, presentational |
| `frontend/src/components/custom-pages/ai-prompt-bar.test.tsx` | its tests |

**Modified:**

| file | change |
| --- | --- |
| `backend/app/schemas/custom_page.py` | `PageAssistRequest`, `PageAssistResponse` |
| `backend/app/api/routes/custom_pages.py` | the `assist` route |
| `backend/openapi.json` | regenerated |
| `backend/tests/test_custom_pages_api.py` | the route's tests |
| `frontend/src/components/custom-pages/lib.ts` | `elideImages`, `restoreImages`, `MAX_ASSIST_BYTES` |
| `frontend/src/components/custom-pages/lib.test.ts` | their tests |
| `frontend/src/lib/api/resources/custom-pages.ts` | `assist` |
| `frontend/src/lib/api/index.ts` | export the new types |
| `frontend/src/components/custom-pages/custom-page-editor-view.tsx` | mount the bar, hold `htmlBeforeAi`, fetch settings |
| `frontend/src/components/custom-pages/custom-page-editor-view.test.tsx` | the new flow |
| `frontend/src/lib/api/generated/schema.ts` | regenerated |

---

### Task 1: The image round trip

**Files:**
- Modify: `frontend/src/components/custom-pages/lib.ts`, `frontend/src/components/custom-pages/lib.test.ts`

**Interfaces:**
- Produces: `MAX_ASSIST_BYTES = 200 * 1024`; `type ElidedDocument = { html: string; images: string[] }`; `type RestoredDocument = { html: string; warnings: string[] }`; `elideImages(html: string): ElidedDocument`; `restoreImages(html: string, images: string[]): RestoredDocument`; `isOverAssistCap(html: string): boolean`.

This carries the most risk in the feature, and it is pure, so it gets the most tests. A bug here silently destroys someone's embedded images.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/custom-pages/lib.test.ts`:

```typescript
import {
  MAX_ASSIST_BYTES,
  elideImages,
  isOverAssistCap,
  restoreImages,
} from "@/components/custom-pages/lib";

const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg";
const JPEG = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQ";

describe("elideImages", () => {
  it("leaves a document with no images untouched", () => {
    const html = "<!doctype html><html><body><h1>hi</h1></body></html>";
    expect(elideImages(html)).toEqual({ html, images: [] });
  });

  it("replaces each data URI with a placeholder that is still a data URI", () => {
    const { html, images } = elideImages(`<img src="${PNG}">`);
    // Still a well-formed URI: a model shown a malformed src repairs it.
    expect(html).toBe('<img src="data:image/png;base64,MEGOOPM_IMAGE_1">');
    expect(images).toEqual([PNG]);
  });

  it("preserves each image's own mime type", () => {
    const { html } = elideImages(`<img src="${PNG}"><img src="${JPEG}">`);
    expect(html).toContain("data:image/png;base64,MEGOOPM_IMAGE_1");
    expect(html).toContain("data:image/jpeg;base64,MEGOOPM_IMAGE_2");
  });

  it("shrinks the document by the weight of the images", () => {
    const big = `data:image/png;base64,${"A".repeat(100_000)}`;
    const { html } = elideImages(`<img src="${big}">`);
    expect(html.length).toBeLessThan(100);
  });
});

describe("restoreImages", () => {
  it("round-trips a document byte for byte", () => {
    const original = `<body><img src="${PNG}"><p>x</p><img src="${JPEG}"></body>`;
    const { html, images } = elideImages(original);
    const restored = restoreImages(html, images);
    expect(restored.html).toBe(original);
    expect(restored.warnings).toEqual([]);
  });

  it("round-trips a document with no images", () => {
    const original = "<p>nothing here</p>";
    const { html, images } = elideImages(original);
    expect(restoreImages(html, images).html).toBe(original);
  });

  it("accepts a dropped placeholder and says how many went", () => {
    // The instruction may legitimately have asked for the image to go.
    const { images } = elideImages(`<img src="${PNG}"><img src="${JPEG}">`);
    const returned = '<img src="data:image/jpeg;base64,MEGOOPM_IMAGE_2">';
    const restored = restoreImages(returned, images);
    expect(restored.html).toBe(`<img src="${JPEG}">`);
    expect(restored.warnings).toEqual(["1 image was removed from the page."]);
  });

  it("pluralises the removal note", () => {
    const { images } = elideImages(`<img src="${PNG}"><img src="${JPEG}">`);
    const restored = restoreImages("<p>gone</p>", images);
    expect(restored.warnings).toEqual(["2 images were removed from the page."]);
  });

  it("leaves a placeholder it never sent, so the break is visible", () => {
    // Stripping the <img> would be a silent structural edit nobody asked for.
    const { images } = elideImages(`<img src="${PNG}"`);
    const returned = '<img src="data:image/png;base64,MEGOOPM_IMAGE_9">';
    const restored = restoreImages(returned, images);
    expect(restored.html).toContain("MEGOOPM_IMAGE_9");
    expect(restored.warnings).toContain(
      "The result referenced 1 image that isn't in your page.",
    );
  });

  it("reports both problems at once", () => {
    const { images } = elideImages(`<img src="${PNG}"><img src="${JPEG}">`);
    const returned = '<img src="data:image/png;base64,MEGOOPM_IMAGE_7">';
    expect(restoreImages(returned, images).warnings).toHaveLength(2);
  });
});

describe("isOverAssistCap", () => {
  it("is false at the cap and true past it", () => {
    expect(isOverAssistCap("x".repeat(MAX_ASSIST_BYTES))).toBe(false);
    expect(isOverAssistCap("x".repeat(MAX_ASSIST_BYTES + 1))).toBe(true);
  });

  it("measures encoded bytes, not characters", () => {
    expect(isOverAssistCap("é".repeat(MAX_ASSIST_BYTES))).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/custom-pages/lib.test.ts`
Expected: FAIL — `elideImages` is not exported.

- [ ] **Step 3: Write the helpers**

Append to `frontend/src/components/custom-pages/lib.ts`:

```typescript
/* -------------------------------------------------------------------------- */
/* The image round trip                                                        */
/* -------------------------------------------------------------------------- */

/**
 * The most an elided document may be before it is sent to a model. 2 MiB of
 * pure markup is unusual but possible, and there is no point paying for it.
 * The backend enforces the same number — this copy exists only so the operator
 * gets a sentence instead of a 422.
 */
export const MAX_ASSIST_BYTES = 200 * 1024;

/** Matches a whole data URI and captures its mime type. */
const DATA_URI_WITH_MIME = /data:([\w.+-]+\/[\w.+-]+);base64,[A-Za-z0-9+/=]+/g;

/** Matches a placeholder the model handed back, capturing its 1-based index. */
const PLACEHOLDER_URI = /data:[\w.+-]+\/[\w.+-]+;base64,MEGOOPM_IMAGE_(\d+)/g;

export type ElidedDocument = {
  /** The document with every data URI replaced by a placeholder. */
  html: string;
  /** The originals, in order; index `i` backs `MEGOOPM_IMAGE_{i + 1}`. */
  images: string[];
};

export type RestoredDocument = {
  html: string;
  /** Notes for the operator about images the model dropped or invented. */
  warnings: string[];
};

/**
 * Swap every embedded image for a placeholder before sending to a model.
 *
 * Base64 runs about a token per three characters, so one 200 KB screenshot
 * inside a page costs roughly 70k tokens of context for a blob the model cannot
 * read. The placeholder deliberately stays a *well-formed* data URI, mime type
 * and all: a model shown a malformed `src` attribute tends to repair it, while
 * one shown a URI it simply does not understand leaves it alone.
 */
export function elideImages(html: string): ElidedDocument {
  const images: string[] = [];
  const elided = html.replace(DATA_URI_WITH_MIME, (match, mime: string) => {
    images.push(match);
    return `data:${mime};base64,MEGOOPM_IMAGE_${images.length}`;
  });
  return { html: elided, images };
}

/** "1 image" / "2 images", so the notes below read like English. */
function countImages(n: number): string {
  return n === 1 ? "1 image" : `${n} images`;
}

/**
 * Put the real images back, and report what the model did with them.
 *
 * A *missing* placeholder means the model removed that image — legitimate, the
 * instruction may have asked for exactly that — so the result stands and the
 * operator is told how many went.
 *
 * A placeholder that was never sent is left in place on purpose. Stripping the
 * surrounding `<img>` would be a silent structural edit nobody asked for; a
 * broken image in the live preview is immediately visible and immediately
 * fixable.
 */
export function restoreImages(html: string, images: string[]): RestoredDocument {
  const seen = new Set<number>();
  let unknown = 0;

  const restored = html.replace(PLACEHOLDER_URI, (match, index: string) => {
    const position = Number(index);
    const original = images[position - 1];
    if (original === undefined) {
      unknown += 1;
      return match;
    }
    seen.add(position);
    return original;
  });

  const warnings: string[] = [];
  const dropped = images.length - seen.size;
  if (dropped > 0) {
    warnings.push(`${countImages(dropped)} ${dropped === 1 ? "was" : "were"} removed from the page.`);
  }
  if (unknown > 0) {
    warnings.push(`The result referenced ${countImages(unknown)} that isn't in your page.`);
  }
  return { html: restored, warnings };
}

/** Whether an elided document is too large to send. */
export function isOverAssistCap(html: string): boolean {
  return htmlByteLength(html) > MAX_ASSIST_BYTES;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/custom-pages/lib.test.ts`
Expected: PASS.

If the pluralisation test fails on wording, the assertion is the contract — make the code match the test, not the other way round.

- [ ] **Step 5: Typecheck, lint, commit**

```bash
cd frontend && npm run typecheck && npm run lint
git ls-files --eol frontend/src/components/custom-pages/lib.ts frontend/src/components/custom-pages/lib.test.ts
git add frontend/src/components/custom-pages
git commit -m "feat(custom-pages): swap embedded images for placeholders around an LLM call"
```

---

### Task 2: The prompt and output hygiene

**Files:**
- Create: `backend/app/services/page_assist.py`, `backend/tests/test_page_assist.py`

**Interfaces:**
- Consumes: `LlmConfig`, `complete` from `app/services/llm.py`.
- Produces: `MAX_INSTRUCTION_CHARS = 2000`; `MAX_ASSIST_HTML_BYTES = 200 * 1024`; `SYSTEM_PROMPT: str`; `strip_document_fences(text: str) -> str`; `build_user_message(instruction: str, html: str) -> str`; `async assist_page(config, *, instruction, html, timeout=120.0) -> str`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_page_assist.py`:

```python
"""Tests for AI page assistance — the prompt and the output hygiene.

litellm is never imported here: `complete` is patched, so these stay fast and
have nothing to do with any provider.
"""

from __future__ import annotations

import pytest
from app.services.llm import LlmConfig
from app.services.page_assist import (
    SYSTEM_PROMPT,
    assist_page,
    build_user_message,
    strip_document_fences,
)

DOC = "<!doctype html>\n<html><body><h1>hi</h1></body></html>"


# --- Output hygiene --------------------------------------------------------


def test_passes_a_clean_document_through() -> None:
    assert strip_document_fences(DOC) == DOC


@pytest.mark.parametrize("tag", ["", "html", "HTML"])
def test_strips_a_markdown_fence(tag: str) -> None:
    """Models add these constantly, whatever the system prompt says."""
    assert strip_document_fences(f"```{tag}\n{DOC}\n```") == DOC


def test_strips_prose_before_the_document() -> None:
    fenced = f"Sure! Here is the updated page:\n\n{DOC}"
    assert strip_document_fences(fenced) == DOC


def test_strips_prose_after_the_document() -> None:
    assert strip_document_fences(f"{DOC}\n\nLet me know if you want changes!") == DOC


def test_strips_a_fence_and_prose_together() -> None:
    messy = f"Here you go:\n\n```html\n{DOC}\n```\n\nHope that helps."
    assert strip_document_fences(messy) == DOC


def test_finds_a_document_that_starts_with_doctype_in_any_case() -> None:
    doc = "<!DOCTYPE html>\n<html></html>"
    assert strip_document_fences(f"note\n{doc}") == doc


def test_returns_a_fragment_unchanged_rather_than_rejecting_it() -> None:
    """An instruction may legitimately have asked for a fragment, and the
    operator can see and revert whatever comes back."""
    assert strip_document_fences("```html\n<p>just a paragraph</p>\n```") == (
        "<p>just a paragraph</p>"
    )


# --- The prompt ------------------------------------------------------------


def test_the_system_prompt_states_the_rules_that_matter() -> None:
    lowered = SYSTEM_PROMPT.lower()
    assert "megoopm_image" in lowered  # placeholders must survive
    assert "no external" in lowered or "self-contained" in lowered
    assert "markdown" in lowered or "fence" in lowered


def test_the_user_message_carries_the_instruction_and_the_document() -> None:
    message = build_user_message("make the heading bigger", DOC)
    assert "make the heading bigger" in message
    assert DOC in message


def test_the_user_message_says_so_when_there_is_no_document() -> None:
    message = build_user_message("a 503 maintenance page", "   ")
    assert "a 503 maintenance page" in message
    assert "no current document" in message.lower()


# --- assist_page -----------------------------------------------------------


@pytest.fixture
def captured(monkeypatch):
    """Replace the LLM round trip: record what it was asked, control what it says."""
    import app.services.page_assist as page_assist

    calls: list[dict] = []
    reply = {"value": DOC}

    async def _complete(config, *, prompt, system=None, max_tokens=None, timeout=60.0):
        calls.append({"config": config, "prompt": prompt, "system": system, "timeout": timeout})
        return reply["value"]

    monkeypatch.setattr(page_assist, "complete", _complete)
    return calls, reply


async def test_assist_page_returns_the_document(captured) -> None:
    calls, _ = captured
    out = await assist_page(
        LlmConfig(model="gpt-4o", api_key="sk-EXAMPLE-not-a-real-credential-1"),
        instruction="make it blue",
        html=DOC,
    )
    assert out == DOC
    assert calls[0]["system"] == SYSTEM_PROMPT
    assert "make it blue" in calls[0]["prompt"]


async def test_assist_page_strips_what_the_model_wrapped(captured) -> None:
    calls, reply = captured
    reply["value"] = f"```html\n{DOC}\n```"
    out = await assist_page(
        LlmConfig(model="gpt-4o"), instruction="tidy it", html=DOC
    )
    assert out == DOC


async def test_assist_page_allows_a_long_generation(captured) -> None:
    """A full page from a strong model takes 20-60s; the default must not cut it off."""
    calls, _ = captured
    await assist_page(LlmConfig(model="gpt-4o"), instruction="write one", html="")
    assert calls[0]["timeout"] >= 120.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_page_assist.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.page_assist'`.

- [ ] **Step 3: Write the service**

Create `backend/app/services/page_assist.py`:

```python
"""Ask a language model to write or revise a custom page.

Two jobs, both awkward enough to deserve their own module:

**The prompt.** A custom page is served straight off nginx, possibly on a box
with no egress, so a generated page must be self-contained — the same rule the
bundled congratulations page follows. And the browser has replaced every
embedded image with a ``MEGOOPM_IMAGE_n`` placeholder before sending, so those
tokens must come back untouched or the images cannot be restored.

**Output hygiene.** Models wrap code in markdown fences constantly, whatever
they are told, and add a sentence of preamble on top. Left alone that renders as
literal backticks on the served page, so :func:`strip_document_fences` removes
it. That function is where most of this module's tests live, because it is the
difference between a working page and a visibly broken one.

This module does not import litellm. :func:`~app.services.llm.complete` is the
only door to a provider, and it imports litellm inside its own functions.
"""

from __future__ import annotations

import re

from app.services.llm import LlmConfig, complete

# An instruction is a sentence or two. An unbounded field is just an unmetered
# path into a paid API.
MAX_INSTRUCTION_CHARS = 2000

# The most an *elided* document may be. The browser checks this too, so the
# operator gets a sentence rather than a 422; this copy is the enforcement.
MAX_ASSIST_HTML_BYTES = 200 * 1024

# A generation from a strong model runs 20-60 seconds, and the editor shows a
# spinner with a cancel button throughout. Cutting it off at the usual 60 would
# fail the exact requests this feature exists for.
ASSIST_TIMEOUT_SECONDS = 120.0

SYSTEM_PROMPT = """You edit and write single-file HTML pages for MegooPM, a \
reverse-proxy manager. These pages are served directly by nginx.

Rules:
- Reply with the HTML document and nothing else. No explanation, no commentary.
- Never wrap the output in markdown code fences.
- The document must be entirely self-contained: no external stylesheets, \
scripts, fonts or images, and no network requests of any kind. The page may be \
served on a machine with no internet access.
- Any src value of the form data:<type>;base64,MEGOOPM_IMAGE_<number> is a \
placeholder for an image already in the page. Reproduce it exactly, character \
for character, unless the instruction asks for that image to be removed. Never \
invent a new MEGOOPM_IMAGE placeholder.
- Keep the document's existing structure and content except where the \
instruction asks otherwise."""

_FENCE_OPEN = re.compile(r"\A\s*```[A-Za-z0-9_+-]*[ \t]*\r?\n")
_FENCE_CLOSE = re.compile(r"\r?\n\s*```\s*\Z")
_DOC_START = re.compile(r"<!doctype\b|<html\b", re.IGNORECASE)


def strip_document_fences(text: str) -> str:
    """Remove the markdown fences and prose models wrap around HTML.

    If no ``<!doctype`` or ``<html`` is found the fence-stripped text is
    returned as it is, rather than rejected: a model that answers with a
    fragment has produced something the operator can see in both the editor and
    the preview and can revert with one click — and an instruction may
    legitimately have asked for a fragment.
    """
    out = text.strip()
    out = _FENCE_OPEN.sub("", out)
    out = _FENCE_CLOSE.sub("", out)
    out = out.strip()

    start = _DOC_START.search(out)
    if start is not None:
        out = out[start.start() :]

    # Trailing "Let me know if you want changes!" is the same problem as a
    # leading preamble, and just as easy to cut.
    end = out.lower().rfind("</html>")
    if end != -1:
        out = out[: end + len("</html>")]

    return out.strip()


def build_user_message(instruction: str, html: str) -> str:
    """The instruction plus the document, or a note that there is none."""
    if html.strip():
        return f"Instruction:\n{instruction}\n\nCurrent document:\n{html}"
    return (
        f"Instruction:\n{instruction}\n\n"
        "There is no current document. Write a complete one from scratch."
    )


async def assist_page(
    config: LlmConfig,
    *,
    instruction: str,
    html: str,
    timeout: float = ASSIST_TIMEOUT_SECONDS,
) -> str:
    """Run one page edit or generation and return a cleaned HTML document.

    Raises :class:`~app.services.llm.LlmError` — already scrubbed — for anything
    the provider or the transport does wrong.
    """
    raw = await complete(
        config,
        prompt=build_user_message(instruction, html),
        system=SYSTEM_PROMPT,
        timeout=timeout,
    )
    return strip_document_fences(raw)


__all__ = [
    "ASSIST_TIMEOUT_SECONDS",
    "MAX_ASSIST_HTML_BYTES",
    "MAX_INSTRUCTION_CHARS",
    "SYSTEM_PROMPT",
    "assist_page",
    "build_user_message",
    "strip_document_fences",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker exec megoopm-test python -m pytest tests/test_page_assist.py -p no:cacheprovider -p no:warnings`
Expected: PASS.

- [ ] **Step 5: Confirm the lazy-import guard still holds**

```bash
docker exec megoopm-test python -c "import app.main, sys; print('litellm imported:', 'litellm' in sys.modules)"
```
Expected: `False`. This module imports `app.services.llm` at module scope, which is safe *because* that module defers litellm — this check is what proves the chain did not break.

- [ ] **Step 6: Lint, check line endings, commit**

```bash
docker exec megoopm-test ruff check app tests
docker exec megoopm-test ruff format --check app/services/page_assist.py tests/test_page_assist.py
git ls-files --eol backend/app/services/page_assist.py backend/tests/test_page_assist.py
git add backend/app/services/page_assist.py backend/tests/test_page_assist.py
git commit -m "feat(custom-pages): prompt and output hygiene for AI page assistance"
```

---

### Task 3: The assist route

**Files:**
- Modify: `backend/app/schemas/custom_page.py`, `backend/app/api/routes/custom_pages.py`, `backend/openapi.json`
- Test: `backend/tests/test_custom_pages_api.py` (extend)

**Interfaces:**
- Consumes: `assist_page`, `MAX_INSTRUCTION_CHARS`, `MAX_ASSIST_HTML_BYTES` (Task 2); `llm_config_from_row` from `app/services/instance_settings.py`; `LlmError` from `app/services/llm.py`.
- Produces: `PageAssistRequest(instruction, html="")`, `PageAssistResponse(html)`; `POST /api/v1/custom-pages/assist`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_custom_pages_api.py`:

```python
# --- AI assistance ---------------------------------------------------------

ASSIST_DOC = "<!doctype html>\n<html><body><h1>done</h1></body></html>"


async def _enable_llm(client: AsyncClient, auth) -> None:
    resp = await client.patch(
        "/api/v1/settings/llm",
        headers=auth,
        json={
            "llm_enabled": True,
            "llm_model": "gpt-4o",
            "llm_api_key": "sk-EXAMPLE-not-a-real-credential-1",
        },
    )
    assert resp.status_code == 200, resp.text


@pytest.fixture
def stub_assist(monkeypatch):
    """Replace the model round trip; these tests are about the route."""
    import app.api.routes.custom_pages as routes

    seen: list[dict] = []

    async def _assist(config, *, instruction, html, timeout=120.0):
        seen.append({"config": config, "instruction": instruction, "html": html})
        return ASSIST_DOC

    monkeypatch.setattr(routes, "assist_page", _assist)
    return seen


async def test_assist_returns_a_document(client: AsyncClient, auth, stub_assist) -> None:
    await _enable_llm(client, auth)
    resp = await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "make it blue", "html": "<p>x</p>"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"html": ASSIST_DOC}
    assert stub_assist[0]["instruction"] == "make it blue"
    assert stub_assist[0]["html"] == "<p>x</p>"
    assert stub_assist[0]["config"].model == "gpt-4o"


async def test_assist_works_with_no_document(client: AsyncClient, auth, stub_assist) -> None:
    """Generating from nothing is the same endpoint."""
    await _enable_llm(client, auth)
    resp = await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "a 503 maintenance page"},
    )
    assert resp.status_code == 200, resp.text
    assert stub_assist[0]["html"] == ""


async def test_assist_is_refused_while_llm_is_off(client: AsyncClient, auth) -> None:
    """Unlike the settings probe, this is feature code and the flag gates it."""
    resp = await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "make it blue", "html": "<p>x</p>"},
    )
    assert resp.status_code == 422, resp.text
    assert "settings" in resp.text.lower()


async def test_assist_rejects_an_empty_instruction(client: AsyncClient, auth) -> None:
    await _enable_llm(client, auth)
    for instruction in ("", "   "):
        resp = await client.post(
            "/api/v1/custom-pages/assist",
            headers=auth,
            json={"instruction": instruction, "html": "<p>x</p>"},
        )
        assert resp.status_code == 422, resp.text


async def test_assist_rejects_an_overlong_instruction(client: AsyncClient, auth) -> None:
    """An unbounded field is an unmetered path into a paid API."""
    await _enable_llm(client, auth)
    resp = await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "x" * 2001, "html": "<p>x</p>"},
    )
    assert resp.status_code == 422, resp.text


async def test_assist_rejects_an_un_elided_document(client: AsyncClient, auth) -> None:
    """The client elides before sending; this catches one that did not."""
    await _enable_llm(client, auth)
    resp = await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "tidy", "html": "x" * (200 * 1024 + 1)},
    )
    assert resp.status_code == 422, resp.text


async def test_a_provider_failure_is_502_not_422(
    client: AsyncClient, auth, monkeypatch
) -> None:
    """The request was fine and the client can do nothing about it; the
    upstream failed. 422 would blur that in the logs."""
    import app.api.routes.custom_pages as routes
    from app.services.llm import LlmError

    async def _boom(config, *, instruction, html, timeout=120.0):
        raise LlmError("provider said no")

    monkeypatch.setattr(routes, "assist_page", _boom)

    await _enable_llm(client, auth)
    resp = await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "make it blue", "html": "<p>x</p>"},
    )
    assert resp.status_code == 502, resp.text
    assert "provider said no" in resp.text


async def test_assist_does_not_record_the_document(
    client: AsyncClient, auth, stub_assist
) -> None:
    """The audit log keeps the instruction and a size, never the page."""
    await _enable_llm(client, auth)
    await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "make it blue", "html": "<p>secret marker</p>"},
    )
    entries = await client.get("/api/v1/audit-log", headers=auth)
    assert entries.status_code == 200, entries.text
    assert "secret marker" not in entries.text
    assert "make it blue" in entries.text


async def test_assist_requires_authentication(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/custom-pages/assist", json={"instruction": "x", "html": ""}
    )
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_custom_pages_api.py -k assist -p no:cacheprovider -p no:warnings`
Expected: FAIL — the route 404s, and the `stub_assist` fixture cannot patch a name the module does not have.

- [ ] **Step 3: Add the schemas**

In `backend/app/schemas/custom_page.py`, add before `__all__`:

```python
class PageAssistRequest(BaseModel):
    """An instruction plus the document to work on.

    ``html`` arrives **already elided** — the browser has swapped every embedded
    image for a ``MEGOOPM_IMAGE_n`` placeholder, because sending the base64
    would cost ~70k tokens per screenshot and move megabytes over the wire. The
    size cap is enforcement for a client that skipped its own check; it is not
    the primary guard.
    """

    instruction: str = Field(min_length=1, max_length=MAX_INSTRUCTION_CHARS)
    html: str = Field(default="")

    @field_validator("instruction")
    @classmethod
    def _clean_instruction(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("instruction must not be empty")
        return stripped

    @field_validator("html")
    @classmethod
    def _check_size(cls, value: str) -> str:
        encoded = len(value.encode("utf-8"))
        if encoded > MAX_ASSIST_HTML_BYTES:
            raise ValueError(
                f"html is {encoded} bytes; the maximum for AI editing is "
                f"{MAX_ASSIST_HTML_BYTES}. Embedded images should be replaced "
                "with placeholders before sending."
            )
        return value


class PageAssistResponse(BaseModel):
    """The cleaned document. Placeholders are restored by the browser."""

    html: str
```

Import the two limits at the top of the module:

```python
from app.services.page_assist import MAX_ASSIST_HTML_BYTES, MAX_INSTRUCTION_CHARS
```

and add `"PageAssistRequest"` and `"PageAssistResponse"` to `__all__`.

- [ ] **Step 4: Add the route**

In `backend/app/api/routes/custom_pages.py`, extend the imports:

```python
from app.schemas.custom_page import (
    CustomPageCreate,
    CustomPageRead,
    CustomPageSummary,
    CustomPageUpdate,
    PageAssistRequest,
    PageAssistResponse,
)
from app.services import custom_page as custom_page_service
from app.services import instance_settings as settings_service
from app.services.audit import record_audit
from app.services.llm import LlmError, LlmNotConfiguredError
from app.services.page_assist import assist_page
```

and add the route **above** `@router.get("/{page_id}")`, so the static segment is
declared before the dynamic one:

```python
@router.post("/assist", response_model=PageAssistResponse)
async def assist_custom_page(
    body: PageAssistRequest, admin: AdminUser, db: SessionDep
) -> PageAssistResponse:
    """Write or revise a page with the configured model. Admin-only.

    Stateless: it takes the document rather than a page id, so it works on a
    page that has never been saved.

    ``html`` arrives already elided and the response is re-hydrated in the
    browser, which is why nothing here knows about images.

    A provider failure is **502**, not 422: the request was well-formed and the
    client can do nothing about it, so blurring it into the 4xx that mean
    "you sent something invalid" would lose that distinction in the logs. This
    is the opposite of the settings probe, which reports a provider failure as
    200 with ``ok: false`` — there, reporting on the connection *is* the job.
    """
    row = await settings_service.get_instance_settings(db)
    if not row.llm_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enable LLM features in Settings before using AI editing",
        )

    config = settings_service.llm_config_from_row(row)
    try:
        html = await assist_page(config, instruction=body.instruction, html=body.html)
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
        result_bytes=len(html.encode("utf-8")),
    )
    return PageAssistResponse(html=html)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker exec megoopm-test python -m pytest tests/test_custom_pages_api.py -p no:cacheprovider -p no:warnings`
Expected: PASS.

- [ ] **Step 6: Regenerate OpenAPI, run the full suite, commit**

```bash
export MSYS_NO_PATHCONV=1
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
docker exec megoopm-test python -c "import app.main, sys; print('litellm imported:', 'litellm' in sys.modules)"
git ls-files --eol backend/app/schemas/custom_page.py backend/app/api/routes/custom_pages.py backend/openapi.json backend/tests/test_custom_pages_api.py
git add backend/app backend/openapi.json backend/tests
git commit -m "feat(custom-pages): POST /custom-pages/assist writes and revises pages"
```

The `litellm imported: False` check matters here: this route now reaches
`page_assist` → `llm`, and if any of them acquires a module-scope litellm import
every process pays 3.49s at boot.

---

### Task 4: The client and the prompt bar

**Files:**
- Create: `frontend/src/components/custom-pages/ai-prompt-bar.tsx`, `frontend/src/components/custom-pages/ai-prompt-bar.test.tsx`
- Modify: `frontend/src/lib/api/resources/custom-pages.ts`, `frontend/src/lib/api/index.ts`, `frontend/src/lib/api/generated/schema.ts`

**Interfaces:**
- Produces: `customPages.assist(body: PageAssistRequest, options?: ApiRequestOptions)`; types `PageAssistRequest`, `PageAssistResponse`; `<AiPromptBar enabled busy elapsedSeconds onSubmit onCancel />`.

The bar is presentational — it owns the instruction text and nothing else. The
editor owns the request, the abort and the document, so this stays testable on
its own.

- [ ] **Step 1: Regenerate the API types**

```bash
cd frontend && npm run gen:api
grep -n "PageAssistRequest\|PageAssistResponse" src/lib/api/generated/schema.ts | head
```
Expected: both appear.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/components/custom-pages/ai-prompt-bar.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AiPromptBar } from "@/components/custom-pages/ai-prompt-bar";

afterEach(cleanup);

describe("AiPromptBar", () => {
  it("submits the instruction", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<AiPromptBar enabled busy={false} elapsedSeconds={0} onSubmit={onSubmit} onCancel={() => {}} />);

    await user.type(screen.getByLabelText("Instruction"), "make the heading bigger");
    await user.click(screen.getByRole("button", { name: "Generate" }));

    expect(onSubmit).toHaveBeenCalledWith("make the heading bigger");
  });

  it("submits on Enter, since it is a one-line instruction", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<AiPromptBar enabled busy={false} elapsedSeconds={0} onSubmit={onSubmit} onCancel={() => {}} />);

    await user.type(screen.getByLabelText("Instruction"), "tidy it{Enter}");
    expect(onSubmit).toHaveBeenCalledWith("tidy it");
  });

  it("refuses to submit nothing", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<AiPromptBar enabled busy={false} elapsedSeconds={0} onSubmit={onSubmit} onCancel={() => {}} />);

    await user.type(screen.getByLabelText("Instruction"), "   ");
    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("shows elapsed time and a cancel while busy", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(<AiPromptBar enabled busy elapsedSeconds={14} onSubmit={() => {}} onCancel={onCancel} />);

    expect(screen.getByText(/14s/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Generate" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("points at Settings when LLM features are off", () => {
    render(<AiPromptBar enabled={false} busy={false} elapsedSeconds={0} onSubmit={() => {}} onCancel={() => {}} />);

    expect(screen.getByText(/enable llm features/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /settings/i })).toHaveAttribute("href", "/settings");
    expect(screen.queryByLabelText("Instruction")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/custom-pages/ai-prompt-bar.test.tsx`
Expected: FAIL — `Cannot find module '@/components/custom-pages/ai-prompt-bar'`.

- [ ] **Step 4: Add the client method**

In `frontend/src/lib/api/resources/custom-pages.ts`, add the types and the
method:

```typescript
export type PageAssistRequest = Schemas["PageAssistRequest"];
export type PageAssistResponse = Schemas["PageAssistResponse"];
```

```typescript
  /**
   * Write or revise a page with the configured model. Stateless — pass the
   * document, get one back — so it works on a page that has never been saved.
   *
   * `html` must be **elided**: swap embedded images for placeholders with
   * `elideImages` first, or a page with one screenshot in it costs ~70k tokens
   * and is rejected at 200 KB. Pass `{ signal }` to make Cancel work.
   */
  assist: (body: PageAssistRequest, options?: ApiRequestOptions) =>
    api.post<PageAssistResponse>(`${BASE}/assist`, body, options),
```

`ApiRequestOptions` extends `RequestInit`, so `signal` reaches `fetch` with no
change to the client. Import the type:
`import type { ApiRequestOptions } from "@/lib/api/client";`

In `frontend/src/lib/api/index.ts`, add `PageAssistRequest` and
`PageAssistResponse` to the custom-pages type exports.

- [ ] **Step 5: Write the bar**

Create `frontend/src/components/custom-pages/ai-prompt-bar.tsx`:

```tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * The instruction row above the editor split.
 *
 * Presentational: it owns the text and nothing else. The editor owns the
 * request, the abort and the document, so this can be tested without any of
 * them.
 *
 * When LLM features are off the row is still rendered, pointing at Settings.
 * Hiding it would mean nobody discovers the feature exists.
 */
export function AiPromptBar({
  enabled,
  busy,
  elapsedSeconds,
  onSubmit,
  onCancel,
}: {
  enabled: boolean;
  busy: boolean;
  elapsedSeconds: number;
  onSubmit: (instruction: string) => void;
  onCancel: () => void;
}) {
  const [instruction, setInstruction] = useState("");

  function submit() {
    const trimmed = instruction.trim();
    if (!trimmed || busy) return;
    onSubmit(trimmed);
  }

  if (!enabled) {
    return (
      <section className="flex items-center gap-2 rounded-xl border border-dashed p-3 text-sm text-muted-foreground">
        <Sparkles className="size-4 shrink-0" />
        <span>
          Enable LLM features in{" "}
          <Link href="/settings" className="underline underline-offset-2">
            Settings
          </Link>{" "}
          to write and edit pages with AI.
        </span>
      </section>
    );
  }

  return (
    <section className="flex items-end gap-2 rounded-xl border p-3">
      <div className="flex-1 space-y-1.5">
        <Label htmlFor="ai-instruction">Instruction</Label>
        <Input
          id="ai-instruction"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            // One line of instruction; Enter is the obvious way to send it.
            if (e.key === "Enter") {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="make the heading bigger and add a support email"
          disabled={busy}
        />
      </div>
      {busy ? (
        <div className="flex items-center gap-2 pb-0.5">
          <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            {elapsedSeconds}s
          </span>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      ) : (
        <Button onClick={submit} className="mb-0.5">
          <Sparkles /> Generate
        </Button>
      )}
    </section>
  );
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/custom-pages/ai-prompt-bar.test.tsx`
Expected: PASS.

- [ ] **Step 7: Typecheck, lint, commit**

```bash
cd frontend && npm run typecheck && npm run lint
git ls-files --eol frontend/src/components/custom-pages/ai-prompt-bar.tsx frontend/src/components/custom-pages/ai-prompt-bar.test.tsx frontend/src/lib/api/resources/custom-pages.ts frontend/src/lib/api/index.ts
git add frontend/src
git commit -m "feat(custom-pages): add the AI instruction bar and its client method"
```

---

### Task 5: Wire it into the editor

**Files:**
- Modify: `frontend/src/components/custom-pages/custom-page-editor-view.tsx`, `frontend/src/components/custom-pages/custom-page-editor-view.test.tsx`

**Interfaces:**
- Consumes: `elideImages`, `restoreImages`, `isOverAssistCap`, `MAX_ASSIST_BYTES`, `formatBytes` (Task 1); `customPages.assist` and `<AiPromptBar>` (Task 4); `instanceSettings.get` from Part A.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/custom-pages/custom-page-editor-view.test.tsx`. Add
`instanceSettings` to the `@/lib/api` import, and add these to the existing
`beforeEach`:

```tsx
    vi.spyOn(customPages, "assist").mockResolvedValue({ html: AI_DOC });
    vi.spyOn(instanceSettings, "get").mockResolvedValue({
      default_site_mode: "not_found",
      default_site_redirect_url: null,
      default_site_page_id: null,
      llm_enabled: true,
      llm_model: "gpt-4o",
      llm_api_base: null,
      llm_api_key_set: true,
      updated_at: "2026-09-01T00:00:00Z",
    });
```

Then the tests:

```tsx
const AI_DOC = "<!doctype html>\n<html><body><h1>AI wrote this</h1></body></html>";
const IMG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg";

/** The bar lives behind a toggle so the default layout is unchanged. */
async function openAi(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Ask AI" }));
}

async function generate(user: ReturnType<typeof userEvent.setup>, instruction: string) {
  await openAi(user);
  await user.type(await screen.findByLabelText("Instruction"), instruction);
  await user.click(screen.getByRole("button", { name: "Generate" }));
}

it("sends the instruction and applies the result", async () => {
  const user = userEvent.setup();
  render(<CustomPageEditorView pageId={7} />);
  await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(HTML));

  await generate(user, "make it blue");

  await waitFor(() => expect(customPages.assist).toHaveBeenCalledTimes(1));
  expect(vi.mocked(customPages.assist).mock.calls[0][0]).toMatchObject({
    instruction: "make it blue",
    html: HTML,
  });
  await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(AI_DOC));
});

it("elides images before sending and restores them after", async () => {
  const withImage = `<body><img src="${IMG}"></body>`;
  vi.mocked(customPages.get).mockResolvedValue(makePage({ html: withImage }));
  vi.mocked(customPages.assist).mockResolvedValue({
    html: '<body><h1>hi</h1><img src="data:image/png;base64,MEGOOPM_IMAGE_1"></body>',
  });

  const user = userEvent.setup();
  render(<CustomPageEditorView pageId={7} />);
  await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(withImage));

  await generate(user, "add a heading");

  await waitFor(() => expect(customPages.assist).toHaveBeenCalledTimes(1));
  // The base64 never leaves the browser.
  const sent = vi.mocked(customPages.assist).mock.calls[0][0].html;
  expect(sent).not.toContain("iVBORw0KGgo");
  expect(sent).toContain("MEGOOPM_IMAGE_1");
  // ...and it comes back.
  await waitFor(() =>
    expect(screen.getByLabelText("HTML")).toHaveValue(
      `<body><h1>hi</h1><img src="${IMG}"></body>`,
    ),
  );
});

it("reverts to the document from before the AI edit", async () => {
  const user = userEvent.setup();
  render(<CustomPageEditorView pageId={7} />);
  await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(HTML));

  await generate(user, "make it blue");
  await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(AI_DOC));

  await user.click(screen.getByRole("button", { name: "Revert AI edit" }));
  expect(screen.getByLabelText("HTML")).toHaveValue(HTML);
  expect(screen.queryByRole("button", { name: "Revert AI edit" })).not.toBeInTheDocument();
});

it("offers no revert until an AI edit has happened", async () => {
  render(<CustomPageEditorView pageId={7} />);
  await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(HTML));
  expect(screen.queryByRole("button", { name: "Revert AI edit" })).not.toBeInTheDocument();
});

it("leaves the document alone when the model call fails", async () => {
  vi.mocked(customPages.assist).mockRejectedValueOnce(new Error("provider said no"));
  const user = userEvent.setup();
  render(<CustomPageEditorView pageId={7} />);
  await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(HTML));

  await generate(user, "make it blue");

  expect(await screen.findByRole("alert")).toHaveTextContent("provider said no");
  expect(screen.getByLabelText("HTML")).toHaveValue(HTML);
});

it("refuses to send a document that is too large even elided", async () => {
  vi.mocked(customPages.get).mockResolvedValue(
    makePage({ html: "x".repeat(200 * 1024 + 1) }),
  );
  const user = userEvent.setup();
  render(<CustomPageEditorView pageId={7} />);
  await generate(user, "tidy it");

  expect(await screen.findByRole("alert")).toHaveTextContent(/too large/i);
  expect(customPages.assist).not.toHaveBeenCalled();
});

it("points at Settings when LLM features are off", async () => {
  vi.mocked(instanceSettings.get).mockResolvedValue({
    default_site_mode: "not_found",
    default_site_redirect_url: null,
    default_site_page_id: null,
    llm_enabled: false,
    llm_model: null,
    llm_api_base: null,
    llm_api_key_set: false,
    updated_at: "2026-09-01T00:00:00Z",
  });
  const user = userEvent.setup();
  render(<CustomPageEditorView pageId={7} />);
  await openAi(user);

  expect(await screen.findByText(/enable llm features/i)).toBeInTheDocument();
  expect(screen.queryByLabelText("Instruction")).not.toBeInTheDocument();
});

it("keeps the bar out of the way until it is asked for", async () => {
  render(<CustomPageEditorView pageId={7} />);
  await waitFor(() => expect(screen.getByLabelText("HTML")).toHaveValue(HTML));
  // Default layout is unchanged for anyone not using the feature.
  expect(screen.queryByLabelText("Instruction")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Ask AI" })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/custom-pages/custom-page-editor-view.test.tsx`
Expected: FAIL — there is no Instruction field.

- [ ] **Step 3: Add the state and the handler**

In `frontend/src/components/custom-pages/custom-page-editor-view.tsx`, extend the
imports:

```tsx
import { customPages, instanceSettings, type CustomPage } from "@/lib/api";
import {
  MAX_ASSIST_BYTES,
  STARTER_HTML,
  describeError,
  describeImageSize,
  elideImages,
  formatBytes,
  htmlByteLength,
  imgTagFor,
  isOverAssistCap,
  isOverPageCap,
  restoreImages,
} from "@/components/custom-pages/lib";
import { AiPromptBar } from "@/components/custom-pages/ai-prompt-bar";
```

Add state beside the existing declarations:

```tsx
  const [llmEnabled, setLlmEnabled] = useState(false);
  // The bar is opt-in: the editor is already vertically tight, so the default
  // layout stays exactly as it was for anyone not using AI.
  const [promptOpen, setPromptOpen] = useState(false);
  // The document as it was immediately before the last AI edit. `null` means
  // there is nothing to revert to.
  const [htmlBeforeAi, setHtmlBeforeAi] = useState<string | null>(null);
  const [assisting, setAssisting] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const assistAbort = useRef<AbortController | null>(null);
```

In `load`, fetch the settings alongside the page. Both are needed to render, and
a settings failure must not block editing:

```tsx
      const [page, settings] = await Promise.all([
        customPages.get(pageId),
        instanceSettings.get().catch(() => null),
      ]);
      setForm(formFrom(page));
      setSaved(formFrom(page));
      setLlmEnabled(settings?.llm_enabled ?? false);
```

For create mode (`pageId === null`) `load` returns early, so add a second effect
that fetches only the settings:

```tsx
  // Create mode returns from `load` before fetching anything, but the prompt
  // bar still needs to know whether the feature is on.
  useEffect(() => {
    void (async () => {
      if (pageId !== null) return;
      const settings = await instanceSettings.get().catch(() => null);
      setLlmEnabled(settings?.llm_enabled ?? false);
    })();
  }, [pageId]);
```

The elapsed counter, ticking only while a request is in flight:

```tsx
  useEffect(() => {
    if (!assisting) return;
    const started = Date.now();
    setElapsedSeconds(0);
    const timer = setInterval(
      () => setElapsedSeconds(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => clearInterval(timer);
  }, [assisting]);
```

And the handler:

```tsx
  async function handleAssist(instruction: string) {
    // Swap embedded images for placeholders first: one 200 KB screenshot is
    // ~70k tokens of base64 the model cannot read, and it never needs to leave
    // the browser.
    const { html: elided, images } = elideImages(form.html);
    if (isOverAssistCap(elided)) {
      setError(
        `This page is too large for AI editing — ${formatBytes(htmlByteLength(elided))} ` +
          `without its images, against a limit of ${formatBytes(MAX_ASSIST_BYTES)}.`,
      );
      return;
    }

    setError(null);
    setAssisting(true);
    const controller = new AbortController();
    assistAbort.current = controller;
    try {
      const result = await customPages.assist(
        { instruction, html: elided },
        { signal: controller.signal },
      );
      const restored = restoreImages(result.html, images);
      setHtmlBeforeAi(form.html);
      patch({ html: restored.html });
      for (const warning of restored.warnings) toast.warning(warning);
      toast.success("Page updated");
    } catch (err) {
      // An aborted request is the operator pressing Cancel, not a failure.
      if (controller.signal.aborted) return;
      const described = describeError(err);
      setError(described.message);
      toast.error(described.message);
    } finally {
      assistAbort.current = null;
      setAssisting(false);
    }
  }

  function handleCancelAssist() {
    assistAbort.current?.abort();
    assistAbort.current = null;
    setAssisting(false);
  }

  function handleRevertAi() {
    if (htmlBeforeAi === null) return;
    patch({ html: htmlBeforeAi });
    setHtmlBeforeAi(null);
  }
```

- [ ] **Step 4: Render the bar and the revert**

Add the toggle to the HTML pane's toolbar, immediately before the *Insert image*
button:

```tsx
            <Button
              variant="outline"
              size="sm"
              disabled={saving || loading}
              onClick={() => setPromptOpen((open) => !open)}
            >
              <Sparkles /> Ask AI
            </Button>
```

Import `Sparkles` from `lucide-react` alongside the existing icons.

Then render the bar directly above the `grid` that holds the split, only when
the toggle is on — the editor is already vertically tight, and the spec's point
is that the default layout is unchanged for anyone not using the feature:

```tsx
      {promptOpen ? (
        <AiPromptBar
          enabled={llmEnabled}
          busy={assisting}
          elapsedSeconds={elapsedSeconds}
          onSubmit={(instruction) => void handleAssist(instruction)}
          onCancel={handleCancelAssist}
        />
      ) : null}
```

The bar renders its own "enable LLM features in Settings" state, so the toggle
stays clickable when the feature is off — that note is how the feature is
discovered at all.

and the revert button in the header row, immediately before the Save button:

```tsx
        {htmlBeforeAi !== null ? (
          <Button variant="outline" onClick={handleRevertAi} disabled={saving || assisting}>
            <Undo2 /> Revert AI edit
          </Button>
        ) : null}
```

Import `Undo2` from `lucide-react` alongside the existing icons.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/custom-pages/`
Expected: PASS.

If a query is ambiguous, check whether two controls now share an accessible name
and rename the *control* — two "Save changes" buttons on the Settings page was a
real UI wart, not a test problem, and this editor now has more buttons than it did.

- [ ] **Step 6: Full frontend verification**

```bash
cd frontend && npx vitest run && npm run typecheck && npm run lint && npm run build
```

- [ ] **Step 7: Check it in the real app**

```bash
docker compose up -d --build
```

With a working provider configured in Settings:

- Open a page, type "make the heading bigger", Generate. The document changes and
  the preview re-renders.
- **Revert AI edit** puts the original back exactly.
- Insert an image, then ask for an unrelated change. The image must survive —
  open devtools and confirm the request body contains `MEGOOPM_IMAGE_1` and
  **not** the base64.
- Ask for something slow and press **Cancel** mid-flight; the document must be
  untouched.
- Turn LLM features off in Settings, reload the editor: the bar shows the
  Settings link instead of the input.
- `docker compose logs backend | grep -i "sk-"` finds nothing.

- [ ] **Step 8: Line endings and commit**

```bash
git ls-files --eol frontend/src/components/custom-pages/custom-page-editor-view.tsx frontend/src/components/custom-pages/custom-page-editor-view.test.tsx
git add frontend/src/components/custom-pages
git commit -m "feat(custom-pages): write and revise pages with AI from the editor"
```

---

## Done when

- Every task's steps are checked off.
- `docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings` — all pass, no new skips.
- `docker exec megoopm-test ruff check app tests alembic` — clean.
- `docker exec megoopm-test python -c "import app.main, sys; print('litellm' in sys.modules)"` prints `False`.
- `cd frontend && npx vitest run && npm run typecheck && npm run lint && npm run build` — all pass.
- `git ls-files --eol` shows no `w/crlf` on any changed file.
- The manual pass in Task 5 Step 7 has been walked against a real provider,
  including the image round trip and the cancel.
- Test containers torn down: `docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet`.

## Follow-up, not in this plan

Streaming, a diff view, per-request model choice, and cost display — all
recorded as non-goals in the spec, and none of them a redesign of what this
builds.
