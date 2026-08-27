# DNS-01 providers for Let's Encrypt certificates — design

Date: 2026-08-27 · Status: approved design, awaiting implementation plan

## Goal

Make the **DNS-01** option in the new-certificate dialog actually work, with a
dropdown of DNS providers big enough to cover real deployments (≈85 providers)
and credentials that are saved once, encrypted at rest, and reused by every
certificate and renewal that needs them. DNS-01 is what enables wildcard
certificates and certificates for hosts that are not publicly reachable on :80.

Today `AcmeIssuer` supports DNS-01 in principle but the only `DnsProvider` is a
placeholder that raises "no DNS provider is configured", and the challenge is
answered immediately after the TXT record is requested, with no propagation
wait — so even a working provider would fail validation most of the time.

## Non-goals

- Delegated sub-zones: the zone a TXT record is written to is always the
  registered domain of the record name (`example.co.uk` for
  `_acme-challenge.www.example.co.uk`). A per-certificate zone override is a
  later addition.
- CNAME-alias validation (`_acme-challenge` CNAME'd into another zone).
- Per-provider TTL tuning; adding new providers by hand (the catalog is
  generated from the library).
- Any change to HTTP-01, custom uploads, self-signed issuance, or renewal
  scheduling.

## Decisions taken during brainstorming

| Decision | Choice |
|---|---|
| Provider breadth | Large catalog (NPM-style, 40+) |
| Engine | **dns-lexicon** as a library plugged into the existing `DnsProvider` protocol (not lego subprocess, not certbot plugins) |
| Credentials | Reusable **named** credentials (saved once, picked from a dropdown), not inline per certificate |
| Extras | `dns-lexicon[route53]` + `dnspython` — not `[full]` (keeps OCI/SoftLayer/QCloud/Gransy SDKs out of the image; those four providers are simply absent from the catalog) |
| Propagation | Verify the TXT record on the zone's authoritative nameservers before answering the ACME challenge |
| Credential probe | Keep a `verify` endpoint that writes and removes a probe TXT record |
| UI home | "DNS providers" tab on the Certificates page; credential picker inside the certificate dialog |

## Backend — provider engine

### Dependencies

`pyproject.toml`: `dns-lexicon[route53]>=3.25` and `dnspython>=2.6`. The
backend image installs them through its existing `pip install .`. Verified
facts about dns-lexicon 3.25.x (May 2026): Python 3.10–3.14; ≈90 providers;
programmatic API `with Client(config) as ops: ops.create_record("TXT", name,
value)` / `ops.delete_record(None, "TXT", name, value)`; config dict shape
`{"provider_name": "<id>", "domain": "<zone>", "<id>": {<options>}}`; provider
discovery via `lexicon._private.discovery.find_providers() -> dict[str, bool]`
(name → available) and `load_provider_module(name)`; every provider declares
its options in `Provider.configure_parser(parser: ArgumentParser)`.

### New package `app/services/certs/dns_providers/`

**`catalog.py`**

```
@dataclass(frozen=True) class DnsProviderField: name, label, help, secret: bool
@dataclass(frozen=True) class DnsProviderInfo: id, label, description, fields: tuple[DnsProviderField, ...]
list_providers() -> list[DnsProviderInfo]        # cached; sorted by label
get_provider(provider_id) -> DnsProviderInfo     # raises UnknownDnsProviderError
```

Built once at first use: for every `find_providers()` entry that is available
(and is not the dev-only `localzone`), instantiate an `ArgumentParser`, call
`Provider.configure_parser(parser)`, and turn each `--option-name` action into
a field (`name="option_name"`, `help=action.help`, `label` = humanised name).
`secret` is true when the name starts with `auth_` or contains `token`,
`secret`, `password` or `key`. `description` is `parser.description`. Labels
use an override map (`cloudflare → Cloudflare`, `route53 → AWS Route 53`,
`digitalocean → DigitalOcean`, `godaddy → GoDaddy`, `googleclouddns → Google
Cloud DNS`, `powerdns → PowerDNS`, `namecheap → Namecheap`, `ovh → OVH`,
`rfc2136 → RFC 2136 (dynamic update)`, `henet → Hurricane Electric`, …) with
title-case fallback.

**`lexicon_provider.py`**

```
class DnsProviderError(RuntimeError)           # message never contains credential values
class LexiconDnsProvider:                      # implements DnsProvider
    __init__(self, provider_id: str, options: dict[str, str], *, client_factory=Client)
    set_txt_record(name, value) -> None
    remove_txt_record(name, value) -> None
zone_for(name: str) -> str                     # registered domain via tldextract (offline PSL snapshot)
```

`set_txt_record` builds `{"provider_name": id, "domain": zone_for(name), id:
options}` and calls `create_record("TXT", f"{name.rstrip('.')}.", value)`;
`remove_txt_record` calls `delete_record(None, "TXT", ...)`. Any exception
from lexicon is re-raised as `DnsProviderError(f"{provider_id}: {message}")`
after replacing every option value that is at least 4 characters long with
`***`.

**`propagation.py`**

```
class PropagationTimeoutError(RuntimeError)
wait_for_txt(name, value, *, timeout_seconds, interval_seconds, resolver=None, sleep=time.sleep) -> None
authoritative_nameservers(zone, resolver) -> list[str]   # NS names → A/AAAA addresses
```

Polls every authoritative nameserver of `zone_for(name)` for a TXT record at
`name` whose value equals `value`; returns when all of them agree, raises
`PropagationTimeoutError` when `timeout_seconds` elapse. The `resolver` is a
`dns.resolver.Resolver`-shaped object so tests inject a fake with scripted
answers; `sleep` is injectable so tests don't wait.

### Changes to existing code

- `app/core/config.py`: `acme_dns_propagation_timeout_seconds: int = 120`,
  `acme_dns_propagation_interval_seconds: int = 5`.
- `AcmeIssuer.__init__` gains `propagation_check: Callable[[str, str], None] |
  None = None`. In `_provision_challenge`'s DNS-01 branch, after
  `provider.set_txt_record(name, validation)` and after appending the cleanup
  entry, it calls `self._propagation_check(name, validation)` when set. A
  propagation failure propagates like any issuance error, and `finally` still
  removes the TXT record.
- `issuance.build_issuer(...)` passes `propagation_check` = `wait_for_txt`
  bound to the two settings whenever the challenge is DNS-01 (tests replace it).
- `tasks/certs.py::_issue_async`: when `cert.meta.get("challenge") == "dns-01"`,
  call `dns_credentials.build_provider_for(session, cert)`, which loads the
  credential referenced by `meta["dns_credential_id"]`, decrypts it, and returns
  a `LexiconDnsProvider`; the result is passed as `dns_provider=` to
  `build_issuer`. A missing/deleted credential raises
  `DnsProviderNotConfigured("DNS credential <id> no longer exists")`, which
  lands in `meta.last_error` through the existing failure path. `build_issuer`
  and `issue_for_certificate` stay DB-agnostic.

## Backend — data model and API

### Table `dns_provider_credentials` (migration `0008_dns_provider_credentials`)

| Column | Type | Notes |
|---|---|---|
| `id` | bigint PK | `IdMixin` |
| `name` | varchar(255), unique | human label, e.g. "Cloudflare — prod token" |
| `provider` | varchar(64) | lexicon provider id; validated against the catalog on write |
| `options` | JSONB, default `{}` | the provider's **non-secret** fields in the clear |
| `secrets_enc` | text | Fernet token (`app.core.crypto.encrypt_secret`) of a JSON object holding the **secret** fields |
| `created_at`, `updated_at` | timestamptz | `TimestampMixin` |

Model `DnsProviderCredential` in `app/models/dns_credential.py`, registered in
`app/models/__init__.py`. Purely additive migration; reversible.

### Certificate link

`Certificate.meta["dns_credential_id"]: int` and `meta["dns_provider"]: str`
(the provider id, for display). Deliberately no foreign key: `meta` is JSONB and
certificate history must survive credential deletion; the reference is resolved
at issuance/renewal time, so rotating a credential's token applies to every
certificate using it with no further action.

### Service `app/services/certs/dns_credentials.py`

```
class CredentialInUseError(Exception): certificate_names: list[str]
class UnknownDnsProviderError(ValueError)
async list_credentials(db) -> list[DnsProviderCredential]
async get_credential(db, credential_id) -> DnsProviderCredential | None
async create_credential(db, *, name, provider, options: dict[str, str]) -> DnsProviderCredential
async update_credential(db, credential, *, name=None, options=None) -> DnsProviderCredential
async delete_credential(db, credential) -> None            # raises CredentialInUseError
async certificates_using(db, credential_id) -> list[Certificate]   # meta->>'dns_credential_id' = str(id)
split_options(provider_id, options) -> tuple[dict, dict]   # (public, secret) per catalog field flags; unknown field → ValueError
decrypted_options(credential) -> dict[str, str]            # public ∪ decrypted secrets
async build_provider_for(db, certificate) -> LexiconDnsProvider
async verify_credential(credential, domain) -> None        # set + remove probe TXT `_megoopm-verify.<domain>`; raises DnsProviderError
```

`update_credential` merges: non-secret `options` replace the stored ones as
given; a secret field that is supplied replaces the stored value, one that is
omitted (or blank) is kept. Creating requires at least one secret field.

### Endpoints (all admin-only; audit `object_type="dns_credential"`, secrets never in `meta`)

| Method | Path | Request → Response |
|---|---|---|
| `GET` | `/api/v1/dns-providers` | → `DnsProviderInfoRead[]` = `{ id, label, description, fields: [{ name, label, help, secret }] }` |
| `GET` | `/api/v1/dns-credentials` | → `DnsCredentialRead[]` = `{ id, name, provider, provider_label, options, secret_fields: [names], in_use_by: [{ id, name }], created_at, updated_at }` |
| `POST` | `/api/v1/dns-credentials` | `DnsCredentialCreate { name, provider, options: {field: value} }` → `201 DnsCredentialRead`; `422` unknown provider / unknown field / no secret field; `409` duplicate name |
| `PATCH` | `/api/v1/dns-credentials/{id}` | `DnsCredentialUpdate { name?, options? }` → `200`; `404`; `409` duplicate name; `422` unknown field |
| `POST` | `/api/v1/dns-credentials/{id}/verify` | `DnsCredentialVerify { domain }` → `200 { "ok": true }`; `400 { "detail": "<provider>: <scrubbed error>" }`; `404`. Runs the blocking provider call via `fastapi.concurrency.run_in_threadpool`, capped at 30 s (`asyncio.wait_for`) → `400 "…: timed out"` |
| `DELETE` | `/api/v1/dns-credentials/{id}` | → `204`; `404`; `409 { "detail": "Still used by: prod-wildcard, api-cert" }` |

Audit actions: `create`, `update` (`meta.changes` limited to `name` and the
names of fields that changed — never values), `delete` (`meta.name`,
`meta.provider`).

### Certificate request changes

- `LetsEncryptCertificateCreate` gains `dns_credential_id: int | None = None`.
  Route-level rule: `challenge == "dns-01"` **requires** it (`422 "DNS-01
  requires dns_credential_id"`); it must reference an existing credential
  (`422 "Unknown DNS credential"`); for HTTP-01 it must be `null`/absent.
- `create_letsencrypt_certificate(...)` stores `meta["dns_credential_id"]` and
  `meta["dns_provider"]` alongside the existing `challenge`/`account_email`.
- `CertificateRead` gains `challenge: str | None` and `dns_provider: str |
  None` (derived from `meta`; `null` for custom/self-signed).

## Frontend

### Certificates page

`certificates-view.tsx` wraps its content in `Tabs` with two panels:

- **Certificates** — the existing table plus a "Challenge" cell: `HTTP-01`,
  `DNS-01 · <provider label>`, or `—`.
- **DNS providers** — new `components/dns-providers/dns-credentials-view.tsx`:
  standard load/skeleton/error/empty pattern; columns Name · Provider ·
  Credentials set (secret field names as badges) · Used by (count, title lists
  the certificate names) · Actions (edit, verify, delete). Header button **New
  credentials**.

### Dialogs (`components/dns-providers/`)

- `dns-credential-dialog.tsx` — create/edit, remounted per target via `key`.
  Fields: name; provider `Select` (catalog sorted by label; read-only in edit
  mode; the provider `description` renders under it once chosen); then one
  input per catalog field — `type="password"` for secret fields (edit-mode
  placeholder "unchanged — leave blank to keep"), text otherwise — each with
  its `help`. Submits `buildOptionsPayload(fields, values, { editing })`.
  Errors via `describeError` in the dialog alert.
- `verify-credential-dialog.tsx` — asks for a domain in that zone, calls
  `verify`, shows success or the scrubbed provider error inline.
- Delete reuses `ConfirmDeleteDialog`; a 409 surfaces as the toast.

### Certificate dialog

When Challenge = DNS-01: a **DNS credentials** `Select` listing saved
credentials as "name · Provider label" appears under the challenge select; it
is required client-side ("Choose DNS provider credentials for DNS-01"); empty
state text: "No DNS provider credentials yet — add one under Certificates →
DNS providers". The request includes `dns_credential_id` (or `null` for
HTTP-01). The credential list is fetched when the dialog opens.

### API client and helpers

`lib/api/resources/dns-providers.ts`: `dnsProviders.catalog()`,
`dnsCredentials.list() / create(body) / update(id, body) / verify(id, body) /
remove(id)`; types `DnsProviderInfo`, `DnsProviderField`, `DnsCredential`,
`DnsCredentialCreate`, `DnsCredentialUpdate` from the regenerated schema;
exported from `lib/api/index.ts`.

`components/dns-providers/lib.ts` (pure, unit-tested): `credentialLabel(cred)`
→ "name · Provider label"; `emptyValues(fields)`; `buildOptionsPayload(fields,
values, { editing })` — trims values, drops blank secret fields only when
`editing`, keeps blank non-secret fields out entirely; `fieldLabel(name)`
("auth_token" → "Auth token").

## Error handling

- Provider/API failures never include credential values: `LexiconDnsProvider`
  scrubs option values from messages; audit `meta` carries field names only.
- Issuance failures (provider error, propagation timeout, missing credential)
  follow the existing path: `status=failed`, `meta.last_error`, Celery task
  result `error`, visible in the certificates table.
- The TXT record is always cleaned up after an issuance attempt, including
  when propagation verification fails.
- `verify` is bounded to 30 s so a hung provider cannot tie up a request.

## Testing

### Backend (pytest, Linux container)

- `test_dns_catalog.py` — non-empty; `cloudflare` present with `auth_token`
  secret and `zone_id` non-secret; `localzone` absent; label overrides and
  title-case fallback.
- `test_lexicon_provider.py` — fake `client_factory` capturing the config
  dict and calls: `{provider_name, domain: zone, <id>: options}`;
  `zone_for("_acme-challenge.www.example.co.uk") == "example.co.uk"`; TXT name
  sent with a trailing dot; provider exceptions become `DnsProviderError`
  with option values scrubbed.
- `test_propagation.py` — fake resolver: value present after N polls → returns
  (with injected `sleep` counting calls); never → `PropagationTimeoutError`;
  one of two nameservers lagging → keeps polling until both agree.
- `test_certs_issuance.py` (extend) — DNS-01 with a fake provider and a
  recording `propagation_check`: order set → propagate → answer; cleanup runs
  when propagation raises.
- `test_dns_credentials_service.py` — `split_options`; encrypt/decrypt
  round-trip; `secrets_enc` never contains plaintext; update keeps omitted
  secrets and replaces supplied ones; delete in use raises with names;
  `certificates_using`; `build_provider_for` with and without a credential.
- `test_dns_credentials_api.py` — 401/403; CRUD; 422 unknown provider / field /
  no secret; 409 duplicate name; reads expose `secret_fields` names only;
  `verify` 200 and 400 with a monkeypatched provider; delete 409 in use, 204
  after; audit rows; Let's Encrypt request with `dns-01` and no credential →
  422, with one → `meta.dns_credential_id`/`dns_provider` stored, HTTP-01 with
  a credential → 422.
- `test_tasks.py` (extend) — issuance task passes a `LexiconDnsProvider` to
  `build_issuer` when `meta.dns_credential_id` is set; missing credential →
  `status=failed` with a clear `last_error`.
- Migration: `alembic upgrade head` and `alembic check` green.

### Frontend (vitest + testing-library)

- `dns-providers/lib.test.ts` — labels; `buildOptionsPayload` drops blank
  secrets only when editing, never sends blank non-secrets.
- `dns-credentials-view.test.tsx` — rows render with provider label and secret
  badges; delete confirmation refetches; verify dialog wiring (dialogs mocked).
- `certificate-dialog.test.tsx` — DNS-01 reveals the credential select;
  submitting without one shows the error and makes no API call; with one, the
  payload includes `dns_credential_id`; HTTP-01 sends `null`.

### Gates (unchanged)

Backend `ruff check`, `ruff format --check` on touched files, `alembic check`,
`pytest`. Frontend `lint`, `typecheck`, `test`, `build`, generated-types drift.

## Contract and docs

- After the backend lands: `python -m scripts.export_openapi` → `npm run
  gen:api`; commit both generated files.
- New `docs/certificates-dns01.md`: how DNS-01 works here (flow diagram in
  text), the generated catalog and what "secret" means, credential storage and
  Fernet encryption (and that rotating `SECRET_KEY` invalidates stored
  credentials), propagation settings, the verify probe, non-goals.
- `README.md`: one line pointing at DNS-01/wildcards and the DNS providers
  tab. `.env.example`: the two propagation settings with defaults.

## Live verification (compose stack, no real provider credentials)

1. Rebuild; `GET /api/v1/dns-providers` lists ≈85 providers including
   `cloudflare` and `route53`, excluding `localzone`/`oci`.
2. Create a Cloudflare credential with a bogus token; the read shows
   `secret_fields: ["auth_token"]` and no token value; the DB row's
   `secrets_enc` is a Fernet token.
3. `verify` with `example.com` → `400` whose detail starts with `cloudflare:`
   and does not contain the bogus token.
4. Request a DNS-01 certificate for `*.example.com` with it → task ends
   `failed`; `last_error` is scrubbed; the certificates table shows
   `DNS-01 · Cloudflare` and the failed status.
5. `DELETE` the credential → 409 naming the certificate; delete the
   certificate; `DELETE` again → 204.
6. UI: DNS providers tab lists/edits/deletes; certificate dialog shows the
   credential select for DNS-01; headless screenshot of the tab.

A real issuance requires the operator's own provider token and a domain they
control — done once after merge.

## Files touched

Backend: `pyproject.toml`; `app/core/config.py`;
`app/services/certs/acme_client.py`; `app/services/certs/issuance.py`;
`app/services/certs/service.py`; `app/services/certs/dns_providers/{__init__,
catalog, lexicon_provider, propagation}.py` (new);
`app/services/certs/dns_credentials.py` (new); `app/models/dns_credential.py`
(new) + `app/models/__init__.py`; `alembic/versions/0008_dns_provider_credentials.py`
(new); `app/schemas/dns_credential.py` (new); `app/schemas/certificate.py`;
`app/api/routes/dns_credentials.py` (new) + `app/api/router.py`;
`app/api/routes/certificates.py`; `app/tasks/certs.py`; tests listed above;
`openapi.json` (regenerated).

Frontend: `src/lib/api/resources/dns-providers.ts` (new) + `src/lib/api/index.ts`;
`src/components/dns-providers/{lib,dns-credentials-view,dns-credential-dialog,verify-credential-dialog}.ts|tsx`
(new, + tests); `src/components/certificates/certificates-view.tsx`;
`src/components/certificates/certificate-dialog.tsx` (+ new test);
`src/lib/api/generated/schema.ts` (regenerated).

Docs: `docs/certificates-dns01.md` (new), `README.md`, `.env.example`.
