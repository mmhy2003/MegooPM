# Default Site Over TLS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the operator's chosen default site for HTTPS requests to names a certificate covers but no enabled host claims — most obviously, a host that was just disabled.

**Architecture:** For each active certificate, the loader computes the names it covers that no enabled TLS host claims, and the renderer emits one `listen 443 ssl` server block per certificate that `include`s the *existing* default-site fragment. The name arithmetic is extracted into a pure module so it is unit-testable without a database; the loader only feeds it rows.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 (async), Jinja2 (`StrictUndefined`), pytest, nginx/OpenResty.

**Spec:** `docs/superpowers/specs/2026-09-02-default-site-tls-design.md`

## Global Constraints

- **Only `active` certificates participate.** A `pending` certificate has no files on disk; referencing one makes `nginx -t` fail, which rolls back the *entire* apply for the whole instance.
- **A host claims `:443` if and only if `host.certificate is not None`** — that is exactly what `server.conf.j2`, `redirect.conf.j2` and `dead.conf.j2` branch on to emit their 443 block.
- **Output must be byte-identical across nodes** for the same state: sort names within a block, and emit blocks ordered by certificate id. Two nodes rendering different text makes the engine see a spurious change and reload every node for nothing.
- **Every emitted block carries `# cert-material {id}:{fingerprint}`.** Renewal rewrites certificate files in place, leaving paths and therefore the config byte-identical; without this line the engine's idempotency check skips the reload and nodes keep serving the old certificate from memory. This bug has already been fixed once for host blocks.
- **No change to `infra/nginx/nginx.conf`** and no new directory. These files render into `conf.d/` via `render_config`, which is already a reconciliation target.
- Run backend tests in a Linux container — the app imports `fcntl`. Start it once, with Postgres attached so the DB-gated suites do not silently skip:

```bash
export MSYS_NO_PATHCONV=1
docker network create megoopm-testnet
docker run -d --name megoopm-testdb --network megoopm-testnet \
  -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm -e POSTGRES_DB=megoopm postgres:16-alpine
docker run -d --name megoopm-test --user root --network megoopm-testnet \
  -v "C:/Projects/megoopm/backend:/src" -w /src \
  -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
  -e DATABASE_URL="postgresql+asyncpg://megoopm:megoopm@megoopm-testdb:5432/megoopm" \
  --entrypoint sleep megoopm-backend infinity
docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" "ruff>=0.6"
```

  Do NOT mount the working tree over `/app`: it shadows the image's entrypoint with the host's CRLF copy and the container dies on `bash\r`. Tear down with `docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet`.

---

### Task 1: The spec type and the pure name planner

**Files:**
- Modify: `backend/app/services/nginx/state.py`
- Create: `backend/app/services/nginx/default_tls.py`
- Test: `backend/tests/test_nginx_default_tls.py` (create)

**Interfaces:**
- Consumes: `CertificateSpec(id, fullchain_path, privkey_path, fingerprint)` from `state.py`; `_certificate_spec(certificate, certs_dir) -> CertificateSpec` from `loader.py`.
- Produces:
  - `DefaultTlsSpec(certificate: CertificateSpec, server_names: tuple[str, ...])` in `state.py`
  - `DesiredState.default_tls: tuple[DefaultTlsSpec, ...] = ()`
  - `plan_default_tls(certificates: Sequence[Certificate], claimed_names: Collection[str], certs_dir: str) -> tuple[DefaultTlsSpec, ...]` in `default_tls.py`

- [x] **Step 1: Add the spec type**

In `backend/app/services/nginx/state.py`, directly after the `DefaultSiteSpec` class:

```python
@dataclass(frozen=True, slots=True)
class DefaultTlsSpec:
    """The default site served over TLS for names one certificate covers.

    ``server_names`` are the names that certificate holds which no enabled host
    claims on :443 — a disabled host's own name lands here, which is the whole
    point. Sorted, so two nodes render identical text.
    """

    certificate: CertificateSpec
    server_names: tuple[str, ...]
```

Then add the field to `DesiredState`, after `default_site`:

```python
    default_tls: tuple[DefaultTlsSpec, ...] = field(default_factory=tuple)
```

And extend the `DesiredState` docstring with a sentence:

```
    ``default_tls`` renders one ``:443`` server block per certificate, serving
    the default site for names that certificate covers but no enabled host
    claims — the HTTPS counterpart to the ``:80`` ``default_server``.
```

- [x] **Step 2: Write the failing tests**

Create `backend/tests/test_nginx_default_tls.py`:

```python
"""Tests for the default-site-over-TLS name arithmetic.

Pure: no database, no nginx. This is where the risk lives — a mistake here
either leaves a disabled host pointing at a stranger's site or steals a name
from a working host — so it takes the bulk of the coverage.
"""

from __future__ import annotations

from app.models.certificate import Certificate
from app.models.enums import CertificateProvider, CertificateStatus
from app.services.nginx.default_tls import plan_default_tls

CERTS_DIR = "/data/certs"


def _cert(**kw) -> Certificate:
    """An in-memory Certificate. Declarative models need no session."""
    base = {
        "id": 1,
        "name": "cert",
        "provider": CertificateProvider.letsencrypt,
        "status": CertificateStatus.active,
        "domain_names": ["example.com", "*.example.com"],
        "expires_on": None,
        "meta": {},
    }
    base.update(kw)
    cert = Certificate(**base)
    return cert


def test_a_name_no_host_claims_is_covered() -> None:
    """The reported bug: a disabled host's name must reach the default site."""
    specs = plan_default_tls([_cert(domain_names=["disabled.example.com"])], set(), CERTS_DIR)
    assert len(specs) == 1
    assert specs[0].server_names == ("disabled.example.com",)


def test_a_name_an_enabled_tls_host_claims_is_never_taken() -> None:
    """Stealing a working host's name would be worse than the bug being fixed."""
    specs = plan_default_tls(
        [_cert(domain_names=["live.example.com"])], {"live.example.com"}, CERTS_DIR
    )
    assert specs == ()


def test_the_certificate_paths_and_fingerprint_are_carried() -> None:
    specs = plan_default_tls([_cert(id=7, domain_names=["a.example.com"])], set(), CERTS_DIR)
    cert = specs[0].certificate
    assert cert.id == 7
    assert cert.fullchain_path == "/data/certs/7/fullchain.pem"
    assert cert.privkey_path == "/data/certs/7/privkey.pem"
    assert cert.fingerprint  # non-empty: renewal must change the rendered text


def test_only_unclaimed_names_of_a_partly_claimed_certificate_are_used() -> None:
    specs = plan_default_tls(
        [_cert(domain_names=["live.example.com", "disabled.example.com"])],
        {"live.example.com"},
        CERTS_DIR,
    )
    assert specs[0].server_names == ("disabled.example.com",)


def test_a_certificate_with_every_name_claimed_produces_nothing() -> None:
    """No block at all, rather than an empty server_name nginx would reject."""
    specs = plan_default_tls(
        [_cert(domain_names=["a.example.com", "b.example.com"])],
        {"a.example.com", "b.example.com"},
        CERTS_DIR,
    )
    assert specs == ()


def test_pending_failed_and_expired_certificates_contribute_nothing() -> None:
    """Their files may not exist; referencing one fails nginx -t and rolls back
    the entire apply for the whole instance."""
    for status in (
        CertificateStatus.pending,
        CertificateStatus.failed,
        CertificateStatus.expired,
    ):
        specs = plan_default_tls(
            [_cert(status=status, domain_names=["a.example.com"])], set(), CERTS_DIR
        )
        assert specs == (), status


def test_the_identical_name_in_two_certificates_lands_in_one_block() -> None:
    """Two blocks declaring one name leaves nginx picking arbitrarily — the very
    bug this feature removes."""
    specs = plan_default_tls(
        [
            _cert(id=2, domain_names=["shared.example.com"]),
            _cert(id=1, domain_names=["shared.example.com"]),
        ],
        set(),
        CERTS_DIR,
    )
    assert len(specs) == 1
    assert specs[0].certificate.id == 1  # lowest id wins, deterministically
    assert specs[0].server_names == ("shared.example.com",)


def test_an_exact_name_and_a_wildcard_in_different_certificates_both_survive() -> None:
    """They are different strings, so they do not collide. nginx prefers the
    exact one at match time; ranking them here would risk diverging from it."""
    specs = plan_default_tls(
        [
            _cert(id=1, domain_names=["a.example.com"]),
            _cert(id=2, domain_names=["*.example.com"]),
        ],
        set(),
        CERTS_DIR,
    )
    assert [s.server_names for s in specs] == [("a.example.com",), ("*.example.com",)]


def test_names_are_sorted_and_blocks_ordered_by_certificate_id() -> None:
    """Byte-identical output across nodes, or the engine reloads for nothing."""
    specs = plan_default_tls(
        [
            _cert(id=5, domain_names=["z.example.com", "a.example.com"]),
            _cert(id=2, domain_names=["m.other.com"]),
        ],
        set(),
        CERTS_DIR,
    )
    assert [s.certificate.id for s in specs] == [2, 5]
    assert specs[1].server_names == ("a.example.com", "z.example.com")


def test_a_certificate_with_no_names_is_skipped() -> None:
    assert plan_default_tls([_cert(domain_names=[])], set(), CERTS_DIR) == ()
```

- [x] **Step 3: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider tests/test_nginx_default_tls.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.nginx.default_tls'`.

- [x] **Step 4: Write the implementation**

Create `backend/app/services/nginx/default_tls.py`:

```python
"""Which names get the default site over TLS, and on which certificate.

Pure: no database, no I/O. The loader supplies rows and the set of names
already claimed; everything decided here is decided from those two inputs,
which is what makes the whole feature testable without Postgres or nginx.

A name is covered when some *active* certificate holds it and no enabled host
claims it on :443. Disabling a host stops it claiming its name, so the name
falls to the default site — the case this exists for.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from app.models.certificate import Certificate
from app.models.enums import CertificateStatus

from .state import DefaultTlsSpec


def plan_default_tls(
    certificates: Sequence[Certificate],
    claimed_names: Collection[str],
    certs_dir: str,
) -> tuple[DefaultTlsSpec, ...]:
    """Build one spec per certificate that still covers at least one name."""
    # Imported here: loader imports this module, so a module-level import back
    # into it would be circular. Reused rather than rebuilt so the on-disk path
    # format lives in exactly one place.
    from .loader import _certificate_spec

    claimed = set(claimed_names)

    # name -> id of the certificate that will serve it. Two certificates listing
    # the identical name would otherwise emit two blocks declaring it, leaving
    # nginx to pick one arbitrarily; the lowest id wins, deterministically.
    #
    # Only *identical* strings need arbitration. An exact name in one
    # certificate and a wildcard covering it in another are different strings,
    # so both are emitted and nginx prefers the exact one at match time.
    owner: dict[str, int] = {}
    by_id: dict[int, Certificate] = {}

    for certificate in certificates:
        if certificate.status is not CertificateStatus.active:
            continue
        by_id[certificate.id] = certificate
        for name in certificate.domain_names or ():
            if name in claimed:
                continue
            current = owner.get(name)
            if current is None or certificate.id < current:
                owner[name] = certificate.id

    names_for: dict[int, list[str]] = {}
    for name, cert_id in owner.items():
        names_for.setdefault(cert_id, []).append(name)

    return tuple(
        DefaultTlsSpec(
            certificate=_certificate_spec(by_id[cert_id], certs_dir),
            server_names=tuple(sorted(names_for[cert_id])),
        )
        for cert_id in sorted(names_for)
    )


__all__ = ["plan_default_tls"]
```

- [x] **Step 5: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider tests/test_nginx_default_tls.py -q
```

Expected: PASS, 10 tests.

- [x] **Step 6: Commit**

```bash
git add backend/app/services/nginx/state.py backend/app/services/nginx/default_tls.py backend/tests/test_nginx_default_tls.py
git commit -m "feat(nginx): decide which names get the default site over TLS"
```

---

### Task 2: Render the TLS default block

**Files:**
- Create: `backend/app/templates/nginx/default_tls.conf.j2`
- Modify: `backend/app/services/nginx/renderer.py`
- Test: `backend/tests/test_nginx_render.py`

**Interfaces:**
- Consumes: `DefaultTlsSpec(certificate, server_names)` and `DesiredState.default_tls` from Task 1.
- Produces: files named `megoopm-default-tls-{cert_id}.conf` in the mapping returned by `render_config(state)`.

- [x] **Step 1: Write the failing tests**

Append to `backend/tests/test_nginx_render.py`. Add `DefaultTlsSpec` to the existing `from app.services.nginx.state import (...)` block first.

```python
def _default_tls(**kw) -> DefaultTlsSpec:
    base = {
        "certificate": CertificateSpec(
            id=3,
            fullchain_path="/data/certs/3/fullchain.pem",
            privkey_path="/data/certs/3/privkey.pem",
            fingerprint="abc123",
        ),
        "server_names": ("disabled.example.com", "*.example.com"),
    }
    base.update(kw)
    return DefaultTlsSpec(**base)


def test_default_tls_block_is_named_per_certificate() -> None:
    files = render_config(DesiredState(default_tls=(_default_tls(),)))
    assert set(files) == {"megoopm-default-tls-3.conf"}


def test_default_tls_block_serves_the_names_on_443_with_the_certificate() -> None:
    conf = render_config(DesiredState(default_tls=(_default_tls(),)))[
        "megoopm-default-tls-3.conf"
    ]
    assert "listen 443 ssl;" in conf
    assert "server_name disabled.example.com *.example.com;" in conf
    assert "ssl_certificate /data/certs/3/fullchain.pem;" in conf
    assert "ssl_certificate_key /data/certs/3/privkey.pem;" in conf


def test_default_tls_block_includes_the_existing_default_site_fragment() -> None:
    """Reusing the fragment is what makes the Settings choice apply to HTTPS."""
    conf = render_config(DesiredState(default_tls=(_default_tls(),)))[
        "megoopm-default-tls-3.conf"
    ]
    assert "include" in conf
    assert "*.conf;" in conf


def test_default_tls_block_records_the_certificate_material() -> None:
    """Renewal rewrites the files in place; without this the rendered config is
    unchanged and no node reloads onto the new certificate."""
    conf = render_config(DesiredState(default_tls=(_default_tls(),)))[
        "megoopm-default-tls-3.conf"
    ]
    assert "# cert-material 3:abc123" in conf
    other = render_config(
        DesiredState(
            default_tls=(
                _default_tls(
                    certificate=CertificateSpec(
                        id=3,
                        fullchain_path="/data/certs/3/fullchain.pem",
                        privkey_path="/data/certs/3/privkey.pem",
                        fingerprint="def456",
                    )
                ),
            )
        )
    )["megoopm-default-tls-3.conf"]
    assert conf != other


def test_default_tls_block_has_a_root_so_nothing_falls_through_to_openresty() -> None:
    """Without it an unmatched request is served OpenResty's welcome page."""
    conf = render_config(DesiredState(default_tls=(_default_tls(),)))[
        "megoopm-default-tls-3.conf"
    ]
    assert "root /var/empty/megoopm;" in conf


def test_no_default_tls_blocks_when_there_are_none() -> None:
    assert render_config(DesiredState()) == {}
```

- [x] **Step 2: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider tests/test_nginx_render.py -q -k default_tls
```

Expected: FAIL — `ImportError: cannot import name 'DefaultTlsSpec'` is already resolved by Task 1, so expect `KeyError: 'megoopm-default-tls-3.conf'`.

- [x] **Step 3: Write the template**

Create `backend/app/templates/nginx/default_tls.conf.j2`:

```jinja
{# The default site over TLS, for names this certificate covers that no enabled
   host claims on :443 — a host that was just disabled, most obviously.

   The base config declares `default_server` on :80 only. With nothing default
   on :443, nginx answers an unmatched HTTPS request from the FIRST server block
   it loaded for that port — an unrelated host, chosen by filename order. This
   block removes that for every name a certificate covers. #}
# Managed by MegooPM — do not edit by hand.
server {
    listen 443 ssl;
    server_name {{ server_names }};

    # Without this a request matching no location falls through to OpenResty's
    # compiled-in root and is served its welcome page.
    root /var/empty/megoopm;

    # Renewal rewrites the files below in place, so without this line the
    # rendered config would be unchanged and no node would reload onto the new
    # certificate. See CertificateSpec.fingerprint.
    # cert-material {{ spec.certificate.id }}:{{ spec.certificate.fingerprint }}
    ssl_certificate {{ spec.certificate.fullchain_path }};
    ssl_certificate_key {{ spec.certificate.privkey_path }};

    # The default site the operator chose in Settings, written by the same
    # feature that serves it on :80 — a bare `location /`, so the mode matrix
    # is not duplicated here. Absent when no default site is configured, in
    # which case no location matches and nginx answers 404.
    include {{ default_dir }}/*.conf;
}
```

- [x] **Step 4: Wire it into the renderer**

In `backend/app/services/nginx/renderer.py`, add the render helper next to `_render_dead_host`:

```python
def _render_default_tls(spec: DefaultTlsSpec) -> str:
    return _env().get_template("default_tls.conf.j2").render(
        spec=spec,
        server_names=" ".join(spec.server_names),
        default_dir=settings.nginx_default_dir,
    )
```

Add `DefaultTlsSpec` to the `from .state import (...)` block at the top of the file. Then, inside `render_config`, after the `dead_hosts` loop and before the `return`:

```python
    for tls in state.default_tls:
        files[f"megoopm-default-tls-{tls.certificate.id}.conf"] = _render_default_tls(tls)
```

- [x] **Step 5: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider tests/test_nginx_render.py -q
```

Expected: PASS, the whole render module including the six new tests.

- [x] **Step 6: Commit**

```bash
git add backend/app/templates/nginx/default_tls.conf.j2 backend/app/services/nginx/renderer.py backend/tests/test_nginx_render.py
git commit -m "feat(nginx): render a :443 default-site block per certificate"
```

---

### Task 3: Wire the loader

**Files:**
- Modify: `backend/app/services/nginx/loader.py`
- Test: `backend/tests/test_nginx_default_tls.py`

**Interfaces:**
- Consumes: `plan_default_tls(certificates, claimed_names, certs_dir)` from Task 1; `DesiredState.default_tls` from Task 1.
- Produces: `load_desired_state` returns a state whose `default_tls` is populated. No signature change.

- [x] **Step 1: Write the failing test for the claimed-name rule**

The set of claimed names is derived from the specs already built, so it can be tested without a database. Append to `backend/tests/test_nginx_default_tls.py`:

```python
from app.services.nginx.default_tls import claimed_tls_names
from app.services.nginx.state import (
    CertificateSpec,
    DeadHostSpec,
    DesiredState,
    ProxyHostSpec,
    RedirectionHostSpec,
)

_CERT = CertificateSpec(
    id=1,
    fullchain_path="/data/certs/1/fullchain.pem",
    privkey_path="/data/certs/1/privkey.pem",
    fingerprint="f",
)


def test_a_host_with_a_certificate_claims_its_names() -> None:
    state = DesiredState(
        proxy_hosts=(
            ProxyHostSpec(
                id=1, domain_names=("live.example.com",), upstream_id=1, certificate=_CERT
            ),
        )
    )
    assert claimed_tls_names(state) == {"live.example.com"}


def test_a_host_without_a_certificate_claims_nothing() -> None:
    """It renders no :443 block at all, so HTTPS to it currently reaches a
    stranger's site. Leaving its name unclaimed is what fixes that."""
    state = DesiredState(
        proxy_hosts=(ProxyHostSpec(id=1, domain_names=("plain.example.com",), upstream_id=1),)
    )
    assert claimed_tls_names(state) == set()


def test_redirection_and_dead_hosts_claim_their_names_too() -> None:
    """They render :443 blocks from their own templates on the same condition."""
    state = DesiredState(
        redirection_hosts=(
            RedirectionHostSpec(
                id=1,
                domain_names=("r.example.com",),
                forward_domain_name="x.example.com",
                certificate=_CERT,
            ),
        ),
        dead_hosts=(DeadHostSpec(id=1, domain_names=("d.example.com",), certificate=_CERT),),
    )
    assert claimed_tls_names(state) == {"r.example.com", "d.example.com"}
```


- [x] **Step 2: Run the test to verify it fails**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider tests/test_nginx_default_tls.py -q -k claimed
```

Expected: FAIL — `ImportError: cannot import name 'claimed_tls_names'`.

- [x] **Step 3: Implement `claimed_tls_names`**

Add to `backend/app/services/nginx/default_tls.py`, importing `DesiredState` from `.state`:

```python
def claimed_tls_names(state: DesiredState) -> set[str]:
    """Names an enabled host already answers for on :443.

    A host renders a 443 block if and only if it has a certificate — exactly
    what ``server.conf.j2``, ``redirect.conf.j2`` and ``dead.conf.j2`` branch
    on. Deriving the set from the same field means the two cannot drift.

    A duplicate ``server_name`` only conflicts within one listen port, so a
    host with no certificate is deliberately NOT counted: it has nothing on
    :443 today, which is precisely why HTTPS to it lands on a stranger's site.
    """
    hosts = (*state.proxy_hosts, *state.redirection_hosts, *state.dead_hosts)
    return {
        name for host in hosts if host.certificate is not None for name in host.domain_names
    }
```

- [x] **Step 4: Run the test to verify it passes**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider tests/test_nginx_default_tls.py -q
```

Expected: PASS.

- [x] **Step 5: Load the certificates and populate the state**

In `backend/app/services/nginx/loader.py`, add the import:

```python
from .default_tls import claimed_tls_names, plan_default_tls
```

Add a loader helper next to `_load_default_site`:

```python
async def _load_certificates(session: AsyncSession) -> tuple[Certificate, ...]:
    """Every active certificate. Status gates it because a pending row's files
    are not on disk yet, and referencing one fails ``nginx -t``, which rolls
    back the entire apply for the instance."""
    rows = await session.scalars(
        select(Certificate).where(Certificate.status == CertificateStatus.active)
    )
    return tuple(rows)
```

Add the imports it needs at the top of `loader.py`: `from app.models.certificate import Certificate` and `CertificateStatus` from `app.models.enums` (check whether the module already imports from `app.models.enums` and extend that line rather than adding a second).

Then in `load_desired_state`, replace the `return DesiredState(...)` block with:

```python
    state = DesiredState(
        proxy_hosts=tuple(host_specs),
        http_upstreams=upstream_specs,
        redirection_hosts=redirection_specs,
        dead_hosts=dead_specs,
        streams=stream_specs,
        stream_upstreams=stream_upstream_specs,
        default_site=default_site,
    )
    # Built from the finished state so the claimed-name set comes from exactly
    # the specs that render :443 blocks.
    certificates = await _load_certificates(session)
    return replace(
        state,
        default_tls=plan_default_tls(certificates, claimed_tls_names(state), certs_dir),
    )
```

Add `from dataclasses import replace` to the imports.

- [x] **Step 6: Run the whole backend suite**

```bash
docker exec megoopm-test sh -c "python -m pytest -p no:cacheprovider && ruff check app tests alembic"
```

Expected: PASS, with no new skips, and ruff clean.

- [x] **Step 7: Commit**

```bash
git add backend/app/services/nginx/loader.py backend/app/services/nginx/default_tls.py backend/tests/test_nginx_default_tls.py
git commit -m "feat(nginx): serve the default site over TLS for unclaimed names"
```

---

### Task 4: Prove it against real nginx

**Files:**
- None modified. This task verifies behaviour no string assertion can.

**Interfaces:**
- Consumes: the rendered output of Tasks 2 and 3.

- [x] **Step 1: Render a sample config to a scratch directory**

Write the two files by hand into a scratch `conf.d`, matching what Task 2 renders — one enabled host with an exact name, one default-TLS block whose filename sorts LAST, so it cannot win by configuration order:

```bash
mkdir -p /tmp/probe/conf.d /tmp/probe/default
openssl req -x509 -newkey rsa:2048 -nodes -keyout /tmp/probe/k.pem -out /tmp/probe/c.pem -days 1 -subj "/CN=probe"
cat > /tmp/probe/conf.d/megoopm-proxy-1.conf <<'EOF'
server {
    listen 443 ssl;
    server_name aaa.example.com;
    ssl_certificate /certs/c.pem; ssl_certificate_key /certs/k.pem;
    location / { return 200 "ENABLED HOST\n"; }
}
EOF
cat > /tmp/probe/conf.d/megoopm-default-tls-3.conf <<'EOF'
server {
    listen 443 ssl;
    server_name *.example.com;
    root /var/empty/megoopm;
    ssl_certificate /certs/c.pem; ssl_certificate_key /certs/k.pem;
    include /data/nginx/default/*.conf;
}
EOF
cat > /tmp/probe/default/megoopm-default.conf <<'EOF'
location / { return 200 "DEFAULT SITE\n"; }
EOF
```

- [x] **Step 2: Run nginx and query both names**

```bash
docker run --rm --entrypoint sh \
  -v "/c/Projects/megoopm/infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v "/tmp/probe/conf.d:/data/nginx/conf.d:ro" \
  -v "/tmp/probe/default:/data/nginx/default:ro" \
  -v "/tmp/probe/c.pem:/certs/c.pem:ro" -v "/tmp/probe/k.pem:/certs/k.pem:ro" \
  megoopm-nginx:latest -c '
mkdir -p /var/empty/megoopm
echo "access_log /dev/null;" > /etc/nginx/logging.conf
openresty -p /usr/local/openresty/nginx -c /etc/nginx/nginx.conf
sleep 1
for h in aaa.example.com disabled.example.com; do
  printf "%-24s -> %s\n" "$h" "$(curl -sk --resolve $h:443:127.0.0.1 https://$h/ | tr -d "\r\n")"
done
'
```

Expected, exactly:

```
aaa.example.com          -> ENABLED HOST
disabled.example.com     -> DEFAULT SITE
```

If `aaa.example.com` returns `DEFAULT SITE`, the catch-all is stealing a working host's name — stop and fix before going further; that is worse than the bug being fixed.

- [x] **Step 3: Record the result in the spec**

Append the observed output to the "Behaviour" table's surrounding prose in `docs/superpowers/specs/2026-09-02-default-site-tls-design.md` if it differs from what is already recorded there. If it matches, change nothing.

- [x] **Step 4: Commit any spec correction**

```bash
git add docs/superpowers/specs/2026-09-02-default-site-tls-design.md
git commit -m "docs(nginx): record the verified TLS default-site behaviour"
```

---

## Manual verification

Not covered by any automated test, and worth doing once against the running instance:

1. Disable a proxy host that has a certificate.
2. Visit it over HTTPS in a browser.
3. Expect: the default site chosen in Settings, **with no certificate warning**.
4. Re-enable it and confirm the host itself answers again.

The absence of a warning is the point of using the operator's own certificate rather than a self-signed one, and only a browser can confirm it.


---

## Executed 2026-09-02

All four tasks complete. **708 passed, 41 skipped**; ruff clean.

Two things the plan did not anticipate:

- **The wiring needed its own test.** The plan tested `plan_default_tls` and
  `claimed_tls_names` separately but never that `load_desired_state` joins
  them, so `tests/test_nginx_default_tls_pg.py` was added. It failed on first
  run and was worth having.
- **A host whose pool has no backends is covered too.** The loader drops such a
  host rather than emit a block with nothing to forward to, so it claims no
  name and has nothing on `:443` — the same position a disabled host is in.
  Discovered through a fixture that forgot the backend, and now pinned by a
  test.

Task 4 was run against config rendered by the real code rather than
hand-written, with the catch-all file sorting *first* so it could not win by
load order:

| request | result |
| --- | --- |
| `aaa.example.com` (enabled) | 502 from its own block, reaching for its upstream |
| `disabled.example.com` | 301 to the default site |
| `deep.sub.example.com` | 301 to the default site |
