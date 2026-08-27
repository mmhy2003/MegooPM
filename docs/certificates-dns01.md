# DNS-01 certificates and DNS provider credentials

DNS-01 lets Let's Encrypt validate a domain through a `_acme-challenge` TXT
record instead of an HTTP fetch — required for wildcard certificates and for
hosts that are not reachable on port 80.

## How an issuance works

1. The operator saves **DNS provider credentials** once (Certificates → DNS
   providers) and picks them in the new-certificate dialog when Challenge =
   DNS-01. The certificate stores `meta.dns_credential_id`.
2. The issuance task (`app/tasks/certs.py`) resolves that id into a
   `LexiconDnsProvider` — dns-lexicon drives the provider's API.
3. For every domain in the order the provider publishes
   `_acme-challenge.<domain>` TXT = validation value in the domain's zone (the
   registered domain, e.g. `example.co.uk`).
4. **Propagation check**: the zone's authoritative nameservers are polled until
   all of them serve the record (`ACME_DNS_PROPAGATION_TIMEOUT_SECONDS`, default
   120 s, every `ACME_DNS_PROPAGATION_INTERVAL_SECONDS`, default 5 s). Only then
   is the ACME challenge answered.
5. The TXT record is removed afterwards — also when validation fails.

Renewals repeat the same steps, re-reading the credential every time, so
rotating a token in one credential set applies to every certificate using it.

## The provider catalog

`GET /api/v1/dns-providers` is generated at runtime by introspecting
dns-lexicon: every provider whose optional dependencies are installed, with
the option names its `configure_parser` declares. A field is **secret** when its
name starts with `auth_` or contains `token`, `secret`, `password` or `key`.
The image installs `dns-lexicon[route53]`; providers gated behind other extras
(`oci`, `softlayer`, `qcloud`, `gransy`) are absent from the catalog — add the
extra in `backend/pyproject.toml` to enable one. `localzone` is excluded (dev only).

## Credential storage

`dns_provider_credentials` keeps non-secret options (zone ids, server URLs) in
`options` (JSONB) and the secret fields as **one Fernet token** in
`secrets_enc`, keyed off `SECRET_KEY` like the CrowdSec credentials. Reads
return only the *names* of secret fields; audit rows record field names, never
values; provider errors are scrubbed of credential values before they reach
`last_error`, the API, or logs. Rotating `SECRET_KEY` invalidates stored
credentials — re-enter them.

## Verify

`POST /api/v1/dns-credentials/{id}/verify` (the shield icon in the table)
writes and removes `_megoopm-verify.<domain>` with the real provider, capped at
30 s. Use it right after saving credentials.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/dns-providers` | catalog |
| `GET` / `POST` | `/api/v1/dns-credentials` | list / create (422 unknown provider or field, or no secret; 409 duplicate name) |
| `PATCH` | `/api/v1/dns-credentials/{id}` | rename / replace options; blank secret keeps its value |
| `POST` | `/api/v1/dns-credentials/{id}/verify` | probe record; 400 with a scrubbed provider error |
| `DELETE` | `/api/v1/dns-credentials/{id}` | 409 while certificates reference it |

`POST /api/v1/certificates/letsencrypt` takes `dns_credential_id` — required
for `"challenge": "dns-01"`, rejected for HTTP-01.

## Not supported (yet)

Delegated sub-zones (the zone is always the registered domain), CNAME-alias
validation, per-provider TTL tuning.
