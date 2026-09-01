# MegooPM Core Domain Data Model (MEG-15)

The schema backbone for the product: an Nginx-Proxy-Manager-equivalent domain
plus a first-class **upstream pool** abstraction (the key differentiator) and an
append-only audit log. Defined as SQLAlchemy 2.0 models under `app/models/` and
shipped by Alembic migration `0003_core_domain`.

## Entities

| Table | Purpose |
| --- | --- |
| `upstreams` | A named pool of backends with a load-balancing method and a `context` (`http` / `stream` / `both`) saying where it may be attached. |
| `upstream_backends` | N `server` entries per pool (the differentiator). |
| `proxy_hosts` | Reverse-proxy entry points; forward to one upstream pool. |
| `proxy_host_locations` | Extra `location ^~ <path>` routes of a proxy host to other pools. |
| `redirection_hosts` | Issue HTTP redirects for a set of domains. |
| `dead_hosts` | Park domains and always return 404. |
| `streams` | Raw TCP/UDP port forwarding, to a single host:port or an upstream pool. |
| `certificates` | Managed/uploaded TLS certs (letsencrypt/custom/self_signed). |
| `access_lists` | Reusable authorization policy for proxy hosts. |
| `access_list_auth` | Basic-auth users (hashed) within an access list. |
| `access_list_clients` | IP/CIDR allow/deny rules within an access list. |
| `custom_pages` | Named HTML documents authored in the app; images embedded as base64 `data:` URIs, so a page has no side-car assets. |
| `instance_settings` | Instance-wide settings. Exactly one row (`id=1`), seeded by its migration so readers never handle "no row yet". Holds the default site and the LLM integration config. |
| `audit_log` | Append-only record of domain mutations. |

## The upstream-pool relationship (differentiator)

```
proxy_hosts.upstream_id ──▶ upstreams.id ──◀ upstream_backends.upstream_id (N)
```

A proxy host references exactly **one** upstream pool; a pool holds **N**
backends and a `lb_method`. A pool may be shared by several proxy hosts. Stock
NPM forwards to a single host/port — MegooPM forwards to a load-balanced pool.

## Enum types (native Postgres ENUMs)

- `load_balance_method`: `round_robin`, `least_conn`, `ip_hash`, `hash`, `random`
- `certificate_provider`: `letsencrypt`, `custom`, `self_signed`
- `http_scheme`: `http`, `https` (proxy → upstream)
- `redirect_scheme`: `auto`, `http`, `https`
- `access_list_directive`: `allow`, `deny`
- `audit_action`: `create`, `update`, `delete`, `enable`, `disable`
- `default_site_mode`: `congratulations`, `not_found`, `no_response`, `redirect`, `custom_page`

The migration drops these types explicitly on downgrade (Alembic does not drop
implicitly-created enum types), so a downgrade/re-upgrade cycle is clean.

## Foreign keys & cascade rules

| From → To | On delete | Rationale |
| --- | --- | --- |
| `upstream_backends.upstream_id` → `upstreams.id` | **CASCADE** | Backends have no meaning without their pool. |
| `proxy_hosts.upstream_id` → `upstreams.id` | **RESTRICT** | A pool in use by a proxy host cannot be deleted. Nullable: the host may target a single backend instead. |
| `proxy_hosts.certificate_id` → `certificates.id` | **SET NULL** | Removing a cert must not delete the host. |
| `proxy_hosts.access_list_id` → `access_lists.id` | **SET NULL** | Removing a policy must not delete the host. |
| `proxy_host_locations.proxy_host_id` → `proxy_hosts.id` | **CASCADE** | Locations belong to their host. |
| `proxy_host_locations.upstream_id` → `upstreams.id` | **RESTRICT** | A pool used by a location cannot be deleted. Nullable, as above. |
| `redirection_hosts.certificate_id` → `certificates.id` | **SET NULL** | As above. |
| `dead_hosts.certificate_id` → `certificates.id` | **SET NULL** | As above. |
| `streams.certificate_id` → `certificates.id` | **SET NULL** | As above. |
| `streams.upstream_id` → `upstreams.id` | **RESTRICT** | A pool in use by a stream cannot be deleted. |
| `access_list_auth.access_list_id` → `access_lists.id` | **CASCADE** | Auth users belong to their list. |
| `access_list_clients.access_list_id` → `access_lists.id` | **CASCADE** | Client rules belong to their list. |
| `instance_settings.default_site_page_id` → `custom_pages.id` | **RESTRICT** | Deliberately unlike the `SET NULL` rows above. Detaching a guard from one host is visible and recoverable; silently changing what *every* unmatched visitor sees is neither, so the delete is refused (409) instead. |

`audit_log.object_id` is a loose reference (no FK) so history survives deletion
of the referenced row.

## Constraints

- `upstream_backends`: unique `(upstream_id, host, port)`; `port` in 1–65535;
  `weight`, `max_fails`, `fail_timeout_seconds` ≥ 0.
- `access_list_auth`: unique `(access_list_id, username)`.
- `streams`: `incoming_port` unique; `incoming_port`/`forward_port` in 1–65535
  (`forward_port` may be NULL); at least one of `tcp_forwarding`/`udp_forwarding`
  must be true; and exactly one target — either `forward_host` + `forward_port`,
  or `upstream_id`, never both and never neither.
- `proxy_hosts` and `proxy_host_locations`: exactly one forward target —
  either `forward_host` + `forward_port`, or `upstream_id`, never both and
  never neither; `forward_port` in 1–65535 when set.
- `redirection_hosts`: `forward_http_code` in 300–308.
- `custom_pages`: `name` unique.
- `instance_settings`: `default_site_mode = 'redirect'` requires
  `default_site_redirect_url`; `= 'custom_page'` requires `default_site_page_id`.
  A half-configured row would render nginx config that says nothing, so the
  database refuses it as well as the API.
  `llm_enabled = true` requires `llm_model`, for the same reason. No constraint
  requires an API key — a local model (Ollama, LM Studio, vLLM) legitimately
  has none, and demanding one would lock those deployments out. The key is a
  Fernet token in `llm_api_key_enc`, never plaintext.

## Conventions

Every table has a `BigInteger` surrogate `id` and `created_at`/`updated_at`
timestamps (`audit_log` is append-only: `created_at` only; `instance_settings`
is a singleton whose `id` is a non-autoincrement `Integer` always equal to 1). Constraint and index
names follow the naming convention in `app/db/base.py`.

## Verification

- `alembic upgrade head` applies cleanly from scratch (`0001 → 0002_users →
  0003_core_domain`); `alembic downgrade` then re-`upgrade` is clean.
- `alembic check` reports no drift between models and migration.
- `tests/test_domain_model.py` covers the N-backend pool relationship plus
  CASCADE / RESTRICT / SET NULL and check-constraint behavior against Postgres.
