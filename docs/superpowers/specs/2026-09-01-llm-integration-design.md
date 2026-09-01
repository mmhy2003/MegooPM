# LLM integration settings — design

## Goal

Let an operator point MegooPM at an LLM from the Settings page — model, API
key, optional base URL — and prove the connection works before relying on it.
Calls go through the `litellm` Python package, so any of its 100+ providers can
be named with a single model string.

This is the foundation the AI features will sit on. It ships with one consumer:
a connection test.

## Non-goals

- **AI-assisted editing in Custom Pages.** The reason this exists, and its own
  design round. It is a harder problem than it looks — what the model is shown,
  streaming, and how an operator reviews a rewrite of a page they spent time on.
  This spec deliberately stops at "a working, tested LLM client the app can
  call", so that design starts from solid ground.
- **Streaming.** The connection test does not need it. The editor will, and
  will add it against the same service.
- **Cost or token accounting.** litellm can report usage; nothing here records
  it. Add it when something spends enough to be worth counting.
- **Per-request model override.** One model for the instance. If a cheap/smart
  split turns out to be wanted, it is two more columns, not a redesign.
- **A provider dropdown.** litellm's model string already names the provider,
  and any curated list of model names rots within months.

## Decisions taken during brainstorming

**litellm, at a measured cost of 190 MB.** The operator was shown the number —
`litellm` plus `openai`, `tokenizers`, `huggingface_hub`, `hf_xet`, `tiktoken`
and `aiohttp` nearly double a 207 MB site-packages — and the lighter
alternative (a direct `httpx` POST to `/chat/completions`, which covers every
OpenAI-compatible provider for zero new dependencies) and chose litellm for its
natively-authenticated providers and unified error handling.

**One config on the settings singleton**, not a table of named providers. The
request was for "a setting in the settings page", and litellm's single model
string already encodes the provider, so there is no separate provider field to
manage.

**Opt-in, off by default.** This makes a reverse proxy's admin backend open
outbound connections to a third party. That should never happen because an
upgrade shipped.

## Two findings that shape the implementation

Both were measured, not assumed.

### litellm costs 3.49 seconds to import

Against 0.84s to import the entire application. Imported at module level that
is a **4x startup penalty on the API process, the Celery worker and beat** —
paid on every boot whether or not anyone has enabled the feature.

So `litellm` is imported **inside the functions that use it**, never at module
scope. A test asserts this holds: it imports `app.main` and then checks
`litellm` is absent from `sys.modules`. That guard matters because the
regression is invisible — someone adds a convenient top-level
`import litellm` for a type annotation and every process gets slower with
nothing failing.

### litellm has telemetry on by default

`litellm.telemetry` is `True` out of the box. For a security-adjacent product
whose whole job is controlling what reaches the network, a dependency phoning
home unasked is not acceptable. The service module sets `litellm.telemetry =
False` before any call, alongside `suppress_debug_info = True` to keep provider
chatter out of the application log.

## Backend — data model

Migration `0020_llm_settings` adds four columns to the existing
`instance_settings` singleton.

| column | type | notes |
| --- | --- | --- |
| `llm_enabled` | `Boolean` not null | default and server default `false` |
| `llm_model` | `Text` null | litellm model string, e.g. `gpt-4o`, `anthropic/claude-sonnet-4`, `ollama/llama3` |
| `llm_api_base` | `Text` null | for local runners and gateways |
| `llm_api_key_enc` | `Text` null | Fernet token, never plaintext |

One CHECK, `llm_needs_model`:
`llm_enabled = false OR llm_model IS NOT NULL`. Enabling with no model would
leave the feature switched on and inert, which is worse than refusing.

**No constraint requires a key.** Ollama, LM Studio and vLLM need none, and
demanding one would lock out exactly the deployments most likely to want a
local model. `llm_api_base` is likewise optional — it is only needed when the
endpoint is not the provider's default.

## Backend — secrets

The key is encrypted with the existing `app.core.crypto` Fernet helper, the
same one behind `CrowdSecCredential` and `DnsProviderCredential`. Nothing new
to key or rotate.

**The key is never returned.** `InstanceSettingsRead` exposes
`llm_api_key_set: bool` and no ciphertext, so a compromised browser session
cannot read it back out.

Writes follow the omit-to-keep rule already used for access-list passwords,
because a client editing settings has no key to send back:

| `llm_api_key` in the payload | effect |
| --- | --- |
| absent | keep the stored key |
| a string | encrypt and replace |
| explicit `null` | clear it |

The audit entry records *that* the key changed — never its value, and never the
ciphertext.

## Backend — the service

`app/services/llm.py`, deliberately narrow so the editor round can build on it
without reaching into litellm directly:

```python
class LlmNotConfiguredError(Exception): ...
class LlmError(Exception): ...          # provider/transport failure, scrubbed

@dataclass(frozen=True, slots=True)
class LlmConfig:
    model: str
    api_key: str | None = None
    api_base: str | None = None

@dataclass(frozen=True, slots=True)
class LlmCheckResult:
    ok: bool
    model: str
    reply: str = ""
    error: str = ""
    latency_ms: int = 0

async def load_config(db) -> LlmConfig          # raises LlmNotConfiguredError
async def complete(config, *, prompt, system=None, max_tokens=None,
                   timeout=60.0) -> str
async def check_connection(config, *, timeout=30.0) -> LlmCheckResult
```

`complete` and `check_connection` take a config rather than a session, so the
whole surface is testable without a database and the editor can pass overrides.

**Credentials are passed explicitly** on every call. When no key is stored,
litellm falls back to its own environment-variable resolution — which is what
lets a keyless local runner work, but does mean an ambient `OPENAI_API_KEY`
could supply one the operator never entered. That is surfaced in the field's
helper text rather than suppressed, because suppressing it would break the
local-model case.

### Error scrubbing

Provider exceptions are not returned verbatim. Some SDKs include the request —
headers included — in their error text, so a raw message can carry the API key
straight into a browser. `LlmError` messages pass through a scrubber that
replaces any occurrence of the configured key, and anything matching a
long bearer-token shape, with `***`. A test feeds it an error string containing
the key and asserts the key is gone.

## Backend — API

### The existing PATCH is restructured

`PATCH /api/v1/settings` currently requires `default_site_mode`, by design:
coherence ("redirect needs a URL") cannot be checked against a payload that
omits the mode. That reasoning is sound for one settings group and does not
generalise — as written, changing the LLM model would force resending the
default-site mode.

So settings gets **one PATCH per settings group**:

| route | |
| --- | --- |
| `GET /api/v1/settings` | everything; unchanged |
| `PATCH /api/v1/settings/default-site` | renamed from the bare `PATCH` |
| `PATCH /api/v1/settings/llm` | new |
| `POST /api/v1/settings/llm/test` | run a probe completion |

`LlmSettingsUpdate` carries **the whole group** — `llm_enabled` required,
`llm_model` and `llm_api_base` present but nullable — for the same reason
`default_site_mode` is required on its sibling: "enabled needs a model" cannot
be checked against a payload that omits `llm_enabled`, and a schema never sees
the stored row. The key is the one exception, and has to be: it is the only
field a client cannot read back, so it follows omit-to-keep instead.

The rename is a breaking change to an endpoint added hours earlier that nothing
outside this repo consumes; the cost is one frontend call site. Both PATCHes are
admin-only and audited via `record_audit` — **neither enqueues an nginx
reload**, since no rendered configuration references any of this.

### The test endpoint

Body is all-optional `{model?, api_base?, api_key?}`; anything omitted falls
back to the stored value, so the same endpoint serves "check what is saved" and
"check what I just typed, before I commit it".

It sends a minimal completion — one user message asking for the single word
`OK`, capped at 16 output tokens — because that is the only thing that proves
the whole path: credentials, base URL, model name, and the provider actually
answering.

**The probe deliberately ignores `llm_enabled`.** It builds its config from the
stored row overlaid with the request's overrides and calls `check_connection`
directly, rather than going through `load_config`, whose disabled check exists
to stop *feature* code running when the operator has turned the feature off.
Requiring the feature to be enabled before it can be tested would invert the
order an operator actually works in: configure, prove it works, then switch it
on. A model is still required — with none there is nothing to probe — and that
returns 422.

**A failed probe returns 200 with `ok: false`,** not a 4xx or 5xx. The API call
succeeded; it is the upstream that did not. Returning an error status would
make a working endpoint indistinguishable from a broken one in logs and
monitoring.

## Frontend

A second card on the Settings page, below Default site:

```
┌─ LLM Integration ───────────────────────────────┐
│ Let MegooPM call a language model. Off by       │
│ default — this opens outbound connections.      │
│                                                  │
│  [  ] Enable LLM features                        │
│                                                  │
│  Model      [ gpt-4o                          ]  │
│             Provider is part of the name, e.g.   │
│             anthropic/claude-sonnet-4            │
│                                                  │
│  API key    [ ••••••••••  (set)               ]  │
│             Leave blank for a local model that   │
│             needs no key.                        │
│                                                  │
│  API base   [ (optional)                      ]  │
│                                                  │
│         [ Test connection ]   [ Save changes ]   │
└──────────────────────────────────────────────────┘
```

The Test result renders inline: the model's reply and round-trip time on
success, the scrubbed error on failure. Test sends whatever is currently in the
form, so a key can be checked before it is saved.

The key input starts empty with a "set" / "not set" indicator, mirroring how the
Custom Pages editor handles a password it can never read back. Clearing a set
key is an explicit action, not something that happens by leaving a field blank.

Form logic lives in the existing `components/settings/lib.ts` beside the
default-site helpers, so the branching stays testable without mounting the card.

## Testing

**Backend**

- Migration against a fresh database: columns, the CHECK, downgrade round trip.
- Schema: the key never appears in read output; omit-keeps / null-clears /
  string-replaces; enabling without a model is 422.
- Service with litellm mocked: a successful completion; a timeout; a provider
  exception becoming `LlmError`; `LlmNotConfiguredError` when disabled or
  modelless.
- **The scrubber**, fed an error string containing the configured key.
- **`litellm` absent from `sys.modules` after importing `app.main`** — the
  startup-cost guard.
- Routes: both PATCHes; the probe's 200-with-`ok: false` on failure; the audit
  entry carries no key material; admin-only.

**Frontend**

- `lib.ts` helpers unit-tested directly.
- The card: enable gates the fields; Test posts form values; the result renders
  both ways; a set key shows as set and is not sent unless changed.

**Not covered by automated tests:** that a real provider answers. The probe is
that check, run by hand against a real key.

## Files

**Backend**

- `alembic/versions/0020_llm_settings.py` (new)
- `app/models/instance_settings.py` — four columns, one CHECK
- `app/schemas/instance_settings.py` — `LlmSettingsUpdate`, `LlmTestRequest`,
  `LlmTestResult`; `llm_api_key_set` on read
- `app/services/llm.py` (new) — the client seam and the scrubber
- `app/services/instance_settings.py` — `update_llm_settings`
- `app/api/routes/settings.py` — the split PATCHes and the probe
- `pyproject.toml` — `litellm>=1.99`
- `tests/test_llm_service.py`, `tests/test_settings_api.py` (extended)

**Frontend**

- `src/lib/api/resources/settings.ts` — `updateDefaultSite`, `updateLlm`,
  `testLlm`
- `src/components/settings/lib.ts` — LLM form state and validation
- `src/components/settings/llm-card.tsx` (new) + test
- `src/components/settings/settings-view.tsx` — mount the card, adopt the
  renamed default-site call

## Open risk

`litellm` is pinned only as `>=1.99`. Its release cadence is fast and its
surface has moved before. If a minor release breaks `acompletion`'s keyword
arguments, every LLM feature fails at once — but the narrow service module is
the only thing that touches it, so the blast radius is one file. Pin exactly if
that turns out to bite.
