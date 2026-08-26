# MegooPM Core Domain Data Model (MEG-15)

The schema backbone for the product: an Nginx-Proxy-Manager-equivalent domain
plus a first-class **upstream pool** abstraction (the key differentiator) and an
append-only audit log. Defined as SQLAlchemy 2.0 models under `app/models/` and
shipped by Alembic migration `0003_core_domain`.

## Entities

| Table | Purpose |
| --- | --- |
| `upstreams` | A named pool of backends with a load-balancing method. |
| `upstream_backends` | N `server` entries per pool (the differentiator). |
| `proxy_hosts` | Reverse-proxy entry points; forward to one upstream pool. |
| `redirection_hosts` | Issue HTTP redirects for a set of domains. |
| `dead_hosts` | Park domains and always return 404. |
| `streams` | Raw TCP/UDP port forwarding. |
| `certificates` | Managed/uploaded TLS certs (letsencrypt/custom/self_signed). |
| `access_lists` | Reusable authorization policy for proxy hosts. |
| `access_list_auth` | Basic-auth users (hashed) within an access list. |
| `access_list_clients` | IP/CIDR allow/deny rules within an access list. |
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

The migration drops these types explicitly on downgrade (Alembic does not drop
implicitly-created enum types), so a downgrade/re-upgrade cycle is clean.

## Foreign keys & cascade rules

| From → To | On delete | Rationale |
| --- | --- | --- |
| `upstream_backends.upstream_id` → `upstreams.id` | **CASCADE** | Backends have no meaning without their pool. |
| `proxy_hosts.upstream_id` → `upstreams.id` | **RESTRICT** | A pool in use by a proxy host cannot be deleted. |
| `proxy_hosts.certificate_id` → `certificates.id` | **SET NULL** | Removing a cert must not delete the host. |
| `proxy_hosts.access_list_id` → `access_lists.id` | **SET NULL** | Removing a policy must not delete the host. |
| `redirection_hosts.certificate_id` → `certificates.id` | **SET NULL** | As above. |
| `dead_hosts.certificate_id` → `certificates.id` | **SET NULL** | As above. |
| `streams.certificate_id` → `certificates.id` | **SET NULL** | As above. |
| `access_list_auth.access_list_id` → `access_lists.id` | **CASCADE** | Auth users belong to their list. |
| `access_list_clients.access_list_id` → `access_lists.id` | **CASCADE** | Client rules belong to their list. |

`audit_log.object_id` is a loose reference (no FK) so history survives deletion
of the referenced row.

## Constraints

- `upstream_backends`: unique `(upstream_id, host, port)`; `port` in 1–65535;
  `weight`, `max_fails`, `fail_timeout_seconds` ≥ 0.
- `access_list_auth`: unique `(access_list_id, username)`.
- `streams`: `incoming_port` unique; `incoming_port`/`forward_port` in 1–65535;
  at least one of `tcp_forwarding`/`udp_forwarding` must be true.
- `redirection_hosts`: `forward_http_code` in 300–308.

## Conventions

Every table has a `BigInteger` surrogate `id` and `created_at`/`updated_at`
timestamps (`audit_log` is append-only: `created_at` only). Constraint and index
names follow the naming convention in `app/db/base.py`.

## Verification

- `alembic upgrade head` applies cleanly from scratch (`0001 → 0002_users →
  0003_core_domain`); `alembic downgrade` then re-`upgrade` is clean.
- `alembic check` reports no drift between models and migration.
- `tests/test_domain_model.py` covers the N-backend pool relationship plus
  CASCADE / RESTRICT / SET NULL and check-constraint behavior against Postgres.
