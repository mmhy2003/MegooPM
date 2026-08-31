# CrowdSec Whitelists Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator author CrowdSec IP/CIDR whitelists from the Security page, render them into a YAML parser file the CrowdSec container reads, and reload CrowdSec by restarting it over the docker socket.

**Architecture:** Whitelists live in Postgres. A pure renderer turns enabled rows into one multi-document YAML file written **in place** onto the shared `/data` volume, which the CrowdSec container sees through a single-file bind mount. A Celery task routed to the control-plane node's queue writes the file, restarts the container over the docker socket via `httpx`'s unix-socket transport, polls LAPI health, and restores the previous bytes if CrowdSec does not come back.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 + Alembic, Celery, Jinja2, httpx, PyYAML; Next.js + base-ui + vitest on the frontend.

**Spec:** `docs/superpowers/specs/2026-08-31-crowdsec-whitelists-design.md`

## Global Constraints

- **Backend tests only run on Linux** — `app` imports `fcntl`. Every backend test command in this plan means the containerised runner in "Running the backend tests" below. Never run `pytest` directly on the host.
- **`ARRAY` columns are Postgres-only.** Any test that inserts a `CrowdSecWhitelist` row must be Postgres-gated with the `_pg` module pattern (Task 1). The SQLite `@compiles` shim fixes DDL only; the bind still fails with `type 'list' is not supported`.
- **`op.drop_constraint` / `op.create_check_constraint` take the BARE name.** The `ck_%(table_name)s_%(constraint_name)s` convention is applied by Alembic on top; passing an expanded name double-prefixes it.
- **The whitelist file is written in place — `open("r+")`, seek, write, truncate. NEVER write-temp-then-rename.** A rename swaps the inode and the container reads the old content forever with no error anywhere.
- **Line endings must be LF.** After any edit run `git ls-files --eol <file>`; anything showing `w/crlf` must be rewritten with `newline="\n"`.
- **Schema changes need two regenerations:** `python -m scripts.export_openapi`, then `cd frontend && npm run gen:api`.
- **vitest does not typecheck** — always run `npx tsc --noEmit` separately.
- Frontend commands run from `frontend/`: `npx vitest run`, `npx eslint src`, `npx tsc --noEmit`.
- Work on branch `feat/crowdsec-whitelists` (already created, holds the spec commit). Never commit to `main`.

### Two deliberate deviations from the spec

1. **Table names are pluralised** — `crowdsec_whitelists`, not the spec's `crowdsec_whitelist` — to match `upstreams` / `streams` / `certificates`. The singleton state table stays singular: `crowdsec_whitelist_apply`.
2. **The dialog's YAML preview is rendered by the backend** via `POST /crowdsec/whitelists/preview`, not re-implemented in TypeScript. The spec promises the preview shows "the exact YAML that will be rendered"; a second renderer in TS would drift and make that claim false.

### Running the backend tests

```bash
MSYS_NO_PATHCONV=1 docker run --rm --user root -v "C:/Projects/megoopm/backend:/src:ro" \
  --entrypoint sh megoopm-backend:latest -c '
  cp -r /src /work && cd /work && pip install -q --no-input "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" "ruff>=0.6" >/dev/null 2>&1
  python -m ruff check . && python -m pytest -q -p no:warnings'
```

For Postgres-gated modules add `--network host -e DATABASE_URL=postgresql+asyncpg://megoopm:megoopm@127.0.0.1:<port>/megoopm`.

---

## File Structure

**Created:**

| file | responsibility |
| --- | --- |
| `backend/alembic/versions/0016_crowdsec_whitelists.py` | both tables |
| `backend/app/models/crowdsec_whitelist.py` | `CrowdSecWhitelist`, `CrowdSecWhitelistApply` |
| `backend/app/schemas/crowdsec_whitelist.py` | request/response models |
| `backend/app/services/crowdsec/whitelists.py` | slug, validation, render, digest, file I/O |
| `backend/app/templates/crowdsec/whitelist.yaml.j2` | the YAML document template |
| `backend/app/services/crowdsec/reload.py` | docker-socket container restart |
| `backend/app/services/crowdsec/apply_state.py` | read/record `crowdsec_whitelist_apply` |
| `backend/app/tasks/crowdsec.py` | `apply_crowdsec_whitelists` |
| `backend/tests/test_crowdsec_whitelists.py` | renderer, slug, validation, file I/O |
| `backend/tests/test_crowdsec_whitelists_pg.py` | model round-trip + constraint |
| `backend/tests/test_crowdsec_reload.py` | docker client + task + rollback |
| `backend/tests/test_crowdsec_whitelists_api.py` | routes |
| `frontend/src/components/security/whitelists-table.tsx` | list + toggle + delete |
| `frontend/src/components/security/whitelist-dialog.tsx` | create/edit + preview |
| `frontend/src/components/security/whitelist-status-banner.tsx` | apply status + retry |
| three matching `*.test.tsx` files | |

**Modified:** `backend/app/core/config.py`, `backend/app/core/celery_app.py`, `backend/app/api/routes/crowdsec.py`, `backend/app/models/__init__.py`, `docker-compose.ha.yml`, `backend/tests/test_compose_config.py`, `frontend/src/components/security/security-view.tsx`, `.env.example`, `docs/crowdsec.md`.

---

### Task 1: Tables and models

**Files:**
- Create: `backend/alembic/versions/0016_crowdsec_whitelists.py`
- Create: `backend/app/models/crowdsec_whitelist.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_crowdsec_whitelists_pg.py`

**Interfaces:**
- Produces: `CrowdSecWhitelist(id, name, reason, description, ips, cidrs, enabled, created_at, updated_at)`; `CrowdSecWhitelistApply(id, applied_digest, applied_at, ok, error)`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_crowdsec_whitelists_pg.py`:

```python
"""CrowdSec whitelist persistence against Postgres (skipped if unavailable).

``ips``/``cidrs`` are Postgres ``ARRAY`` columns, so the SQLite test engine
cannot exercise them: the ``@compiles`` shim fixes DDL only and the bind still
fails with "type 'list' is not supported". Runs in one rolled-back transaction.
"""

from __future__ import annotations

import pytest
from app.core.config import settings
from app.models.crowdsec_whitelist import CrowdSecWhitelist
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

pytestmark = pytest.mark.asyncio


async def _pg_available() -> bool:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT id FROM crowdsec_whitelists LIMIT 0")
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


@pytest.fixture
async def session():
    if not await _pg_available():
        pytest.skip("Postgres with migration 0016 not available")
    engine = create_async_engine(settings.database_url)
    conn = await engine.connect()
    trans = await conn.begin()
    try:
        yield AsyncSession(bind=conn, expire_on_commit=False)
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def test_round_trips_ip_and_cidr_arrays(session: AsyncSession) -> None:
    row = CrowdSecWhitelist(
        name="internal backends",
        reason="internal backends trip appsec generic rules",
        ips=["10.10.0.14"],
        cidrs=["10.10.0.0/24"],
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    assert row.ips == ["10.10.0.14"]
    assert row.cidrs == ["10.10.0.0/24"]
    assert row.enabled is True


async def test_rejects_a_whitelist_matching_nothing(session: AsyncSession) -> None:
    # A whitelist with no ips and no cidrs silently matches nothing; the DB
    # refuses it so a caller bypassing the API cannot create one.
    session.add(CrowdSecWhitelist(name="empty", reason="nothing", ips=[], cidrs=[]))
    with pytest.raises(IntegrityError):
        await session.flush()
```

- [ ] **Step 2: Run it and confirm it fails**

Run the containerised suite with `--network host -e DATABASE_URL=...` (see Global Constraints), filtered:
`python -m pytest -q tests/test_crowdsec_whitelists_pg.py`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.crowdsec_whitelist'`.

- [ ] **Step 3: Write the migration**

`backend/alembic/versions/0016_crowdsec_whitelists.py`:

```python
"""CrowdSec whitelists authored in the UI, plus their apply state

``crowdsec_whitelists`` holds one row per rendered YAML document.
``crowdsec_whitelist_apply`` is a single row (id=1) recording whether the last
render actually reached CrowdSec — the apply is asynchronous and can fail after
the API has already returned 200, and without this a failed reload is invisible.

Revision ID: 0016_crowdsec_whitelists
Revises: 0015_location_forward_target
Create Date: 2026-08-31 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_crowdsec_whitelists"
down_revision: str | None = "0015_location_forward_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Bare name: the metadata naming convention adds the ck_<table>_ prefix.
_NOT_EMPTY_CK = "not_empty"


def upgrade() -> None:
    op.create_table(
        "crowdsec_whitelists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "ips", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column(
            "cidrs", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_unique_constraint(
        op.f("uq_crowdsec_whitelists_name"), "crowdsec_whitelists", ["name"]
    )
    op.create_check_constraint(
        _NOT_EMPTY_CK,
        "crowdsec_whitelists",
        "cardinality(ips) + cardinality(cidrs) > 0",
    )

    op.create_table(
        "crowdsec_whitelist_apply",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("applied_digest", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.Text(), nullable=True),
    )
    # Seed the singleton so readers never have to handle "no row yet".
    op.execute("INSERT INTO crowdsec_whitelist_apply (id, ok) VALUES (1, true)")


def downgrade() -> None:
    op.drop_table("crowdsec_whitelist_apply")
    op.drop_constraint(_NOT_EMPTY_CK, "crowdsec_whitelists", type_="check")
    op.drop_table("crowdsec_whitelists")
```

- [ ] **Step 4: Write the models**

`backend/app/models/crowdsec_whitelist.py`:

```python
"""CrowdSec whitelists and the state of their last apply.

A :class:`CrowdSecWhitelist` becomes one YAML document in the parser file the
CrowdSec container reads, dropping matching events before they can become
alerts or decisions. See ``docs/crowdsec.md``.

:class:`CrowdSecWhitelistApply` is a single row (``id=1``) recording whether the
last render reached CrowdSec. The apply runs in a Celery task on the
control-plane node, so it can fail long after the API returned 200; without this
row the UI would show a whitelist that is not in force.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, Text, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


class CrowdSecWhitelist(IdMixin, TimestampMixin, Base):
    """One whitelist document: a reason plus the IPs and CIDRs it exempts."""

    __tablename__ = "crowdsec_whitelists"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    ips: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    cidrs: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    __table_args__ = (
        # A whitelist matching nothing is always a mistake, and an empty
        # `whitelist:` block would render without complaint.
        CheckConstraint("cardinality(ips) + cardinality(cidrs) > 0", name="not_empty"),
    )


class CrowdSecWhitelistApply(Base):
    """Singleton (``id=1``) describing the last apply attempt."""

    __tablename__ = "crowdsec_whitelist_apply"

    id: Mapped[int] = mapped_column(primary_key=True)
    applied_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Then add to `backend/app/models/__init__.py` alongside the existing exports:

```python
from app.models.crowdsec_whitelist import CrowdSecWhitelist, CrowdSecWhitelistApply
```

and add both names to `__all__`.

- [ ] **Step 5: Apply the migration and re-run the test**

```bash
docker compose exec backend alembic upgrade head
```
Then re-run Step 2's command. Expected: 2 passed.

- [ ] **Step 6: Verify line endings and commit**

```bash
git ls-files --eol backend/alembic/versions/0016_crowdsec_whitelists.py backend/app/models/crowdsec_whitelist.py
git add backend/alembic backend/app/models backend/tests/test_crowdsec_whitelists_pg.py
git commit -m "feat(crowdsec): whitelist tables and models"
```

---

### Task 2: Slugification and entry validation

**Files:**
- Create: `backend/app/services/crowdsec/whitelists.py`
- Test: `backend/tests/test_crowdsec_whitelists.py`

**Interfaces:**
- Produces: `slugify(name: str) -> str`; `WhitelistValidationError(ValueError)`; `validate_entries(ips: Sequence[str], cidrs: Sequence[str]) -> None`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_crowdsec_whitelists.py`:

```python
"""Whitelist slugification, validation, rendering and file I/O (no DB)."""

from __future__ import annotations

import pytest
from app.services.crowdsec.whitelists import (
    WhitelistValidationError,
    slugify,
    validate_entries,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Internal Backends", "internal-backends"),
        ("  spaced  out  ", "spaced-out"),
        ("MiXeD_Case.99", "mixed-case-99"),
        ("already-slug", "already-slug"),
    ],
)
def test_slugify_normalises_names(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


def test_slugify_rejects_a_name_with_nothing_to_slug() -> None:
    # CrowdSec needs a unique non-empty `name:`; "!!!" would render `megoopm/wl-`.
    with pytest.raises(WhitelistValidationError, match="at least one letter or digit"):
        slugify("!!!")


def test_validate_entries_accepts_ipv4_ipv6_and_cidr() -> None:
    validate_entries(["10.10.0.14", "2001:db8::1"], ["10.10.0.0/24", "2001:db8::/32"])


def test_validate_entries_names_the_bad_ip() -> None:
    with pytest.raises(WhitelistValidationError, match="10.10.0.999"):
        validate_entries(["10.10.0.999"], [])


def test_validate_entries_names_the_bad_cidr() -> None:
    with pytest.raises(WhitelistValidationError, match="10.10.0.0/99"):
        validate_entries([], ["10.10.0.0/99"])


def test_validate_entries_accepts_a_host_bit_cidr() -> None:
    # 10.10.0.14/24 has host bits set; operators write these constantly and
    # CrowdSec accepts them, so strict=False is deliberate.
    validate_entries([], ["10.10.0.14/24"])
```

- [ ] **Step 2: Run it and confirm it fails**

`python -m pytest -q tests/test_crowdsec_whitelists.py`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.crowdsec.whitelists'`.

- [ ] **Step 3: Implement**

`backend/app/services/crowdsec/whitelists.py`:

```python
"""Render CrowdSec whitelist rows into the parser YAML file CrowdSec reads.

The file lives on the shared ``/data`` volume and the CrowdSec container sees it
through a single-file bind mount at
``/etc/crowdsec/parsers/s02-enrich/99-megoopm-whitelist.yaml``. See
``docs/crowdsec.md`` for the deployment side.

Everything here is pure except :func:`write_whitelist_file` and
:func:`read_whitelist_file`, so the renderer is testable without a database or a
container.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence


class WhitelistValidationError(ValueError):
    """A whitelist would render an invalid or meaningless CrowdSec document."""


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Normalise an operator-supplied name into a CrowdSec-safe slug.

    Rendered as ``megoopm/wl-<slug>``. CrowdSec requires ``name:`` to be unique
    across every loaded parser, so the prefix keeps us clear of the hub.
    """
    slug = _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")
    if not slug:
        raise WhitelistValidationError(
            f"Whitelist name {name!r} must contain at least one letter or digit."
        )
    return slug


def validate_entries(ips: Sequence[str], cidrs: Sequence[str]) -> None:
    """Raise if any entry is not a valid IP address or network.

    ``strict=False`` on networks: operators routinely write ``10.10.0.14/24``
    with host bits set, and CrowdSec accepts it.
    """
    for ip in ips:
        try:
            ipaddress.ip_address(ip.strip())
        except ValueError as exc:
            raise WhitelistValidationError(f"{ip!r} is not a valid IP address.") from exc
    for cidr in cidrs:
        try:
            ipaddress.ip_network(cidr.strip(), strict=False)
        except ValueError as exc:
            raise WhitelistValidationError(f"{cidr!r} is not a valid CIDR range.") from exc
```

- [ ] **Step 4: Run the test to verify it passes**

`python -m pytest -q tests/test_crowdsec_whitelists.py` — Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/crowdsec/whitelists.py backend/tests/test_crowdsec_whitelists.py
git commit -m "feat(crowdsec): whitelist name slugification and entry validation"
```

---

### Task 3: Renderer, digest, and the in-place writer

**Files:**
- Create: `backend/app/templates/crowdsec/whitelist.yaml.j2`
- Modify: `backend/app/services/crowdsec/whitelists.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_crowdsec_whitelists.py`

**Interfaces:**
- Consumes: `slugify`, `validate_entries`, `WhitelistValidationError` (Task 2).
- Produces: `WhitelistDoc(name, reason, description, ips, cidrs)` frozen dataclass; `render_whitelists(docs: Sequence[WhitelistDoc]) -> str`; `content_digest(content: str) -> str`; `write_whitelist_file(path: Path, content: str) -> None`; `read_whitelist_file(path: Path) -> str`; settings `crowdsec_whitelist_path`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_crowdsec_whitelists.py`:

```python
import yaml
from app.services.crowdsec.whitelists import (
    WhitelistDoc,
    content_digest,
    read_whitelist_file,
    render_whitelists,
    write_whitelist_file,
)

DOC = WhitelistDoc(
    name="Internal Backends",
    reason="internal backends trip appsec generic rules",
    description="Internal backend pool",
    ips=["10.10.0.14"],
    cidrs=["10.10.0.0/24"],
)


def test_renders_one_valid_crowdsec_document() -> None:
    docs = list(yaml.safe_load_all(render_whitelists([DOC])))
    assert len(docs) == 1
    assert docs[0]["name"] == "megoopm/wl-internal-backends"
    assert docs[0]["description"] == "Internal backend pool"
    assert docs[0]["whitelist"]["reason"] == "internal backends trip appsec generic rules"
    assert docs[0]["whitelist"]["ip"] == ["10.10.0.14"]
    assert docs[0]["whitelist"]["cidr"] == ["10.10.0.0/24"]


def test_renders_one_document_per_whitelist() -> None:
    second = WhitelistDoc(
        name="Monitoring", reason="prometheus scrape", description="",
        ips=["10.10.0.99"], cidrs=[],
    )
    docs = [d for d in yaml.safe_load_all(render_whitelists([DOC, second])) if d]
    assert [d["name"] for d in docs] == ["megoopm/wl-internal-backends", "megoopm/wl-monitoring"]


def test_no_whitelists_renders_a_parseable_placeholder() -> None:
    # The path is a bind-mount source: it must never be empty-but-invalid, and
    # must never be deleted.
    out = render_whitelists([])
    assert out.strip().startswith("#")
    assert [d for d in yaml.safe_load_all(out) if d] == []


def test_render_is_byte_stable() -> None:
    # The digest of this output decides whether CrowdSec is restarted at all,
    # so unstable rendering would restart the cluster's WAF on every save.
    assert render_whitelists([DOC]) == render_whitelists([DOC])


def test_reason_containing_yaml_metacharacters_stays_one_scalar() -> None:
    tricky = WhitelistDoc(
        name="odd", reason='he said: "no" # really', description="", ips=["1.2.3.4"], cidrs=[]
    )
    doc = next(d for d in yaml.safe_load_all(render_whitelists([tricky])) if d)
    assert doc["whitelist"]["reason"] == 'he said: "no" # really'


def test_digest_changes_with_content() -> None:
    assert content_digest("a") != content_digest("b")
    assert content_digest("a") == content_digest("a")


def test_write_keeps_the_same_inode(tmp_path) -> None:
    # THE trap. The CrowdSec container resolves this path to an inode when it
    # starts; a write-then-rename would leave it reading the old content
    # forever, with no error in any log.
    path = tmp_path / "megoopm.yaml"
    path.write_text("# seed\n", encoding="utf-8")
    before = path.stat().st_ino

    write_whitelist_file(path, render_whitelists([DOC]))

    assert path.stat().st_ino == before
    assert "megoopm/wl-internal-backends" in read_whitelist_file(path)


def test_write_truncates_a_longer_previous_file(tmp_path) -> None:
    path = tmp_path / "megoopm.yaml"
    path.write_text("x" * 5000, encoding="utf-8")
    write_whitelist_file(path, "# short\n")
    assert read_whitelist_file(path) == "# short\n"


def test_write_creates_the_file_when_absent(tmp_path) -> None:
    path = tmp_path / "nested" / "megoopm.yaml"
    write_whitelist_file(path, "# new\n")
    assert read_whitelist_file(path) == "# new\n"
```

- [ ] **Step 2: Run it and confirm it fails**

`python -m pytest -q tests/test_crowdsec_whitelists.py`

Expected: FAIL — `ImportError: cannot import name 'WhitelistDoc'`.

- [ ] **Step 3: Write the template**

`backend/app/templates/crowdsec/whitelist.yaml.j2`:

```jinja
{#- Renders every enabled MegooPM whitelist as one multi-document parser file.

    CrowdSec reads `---`-separated documents in a single parser file as separate
    nodes. Scalars go through `tojson` because JSON is a subset of YAML: it
    quotes and escapes correctly whatever the operator typed, so a reason
    containing `:` or `#` cannot break the file — and a broken parser file stops
    CrowdSec starting, which with APPSEC_FAILURE_ACTION=deny is an outage.

    Whitespace is controlled so identical input renders byte-identical output;
    the digest of this file decides whether CrowdSec is restarted. -#}
# Managed by MegooPM — do not edit by hand.
{% for doc in docs -%}
---
name: {{ ("megoopm/wl-" ~ doc.slug) | tojson }}
description: {{ doc.description | tojson }}
whitelist:
  reason: {{ doc.reason | tojson }}
{% if doc.ips -%}
  ip:
{% for ip in doc.ips -%}
    - {{ ip | tojson }}
{% endfor -%}
{% endif -%}
{% if doc.cidrs -%}
  cidr:
{% for cidr in doc.cidrs -%}
    - {{ cidr | tojson }}
{% endfor -%}
{% endif -%}
{% endfor -%}
```

- [ ] **Step 4: Implement the renderer and file I/O**

Append to `backend/app/services/crowdsec/whitelists.py`:

```python
import hashlib
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

_env = Environment(
    loader=PackageLoader("app", "templates"),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


@dataclass(frozen=True, slots=True)
class WhitelistDoc:
    """One whitelist, decoupled from the ORM so the renderer needs no database."""

    name: str
    reason: str
    description: str
    ips: tuple[str, ...] | list[str]
    cidrs: tuple[str, ...] | list[str]

    @property
    def slug(self) -> str:
        return slugify(self.name)


def render_whitelists(docs: Sequence[WhitelistDoc]) -> str:
    """Render every doc into one multi-document YAML file.

    Validates as it goes: this output is the thing that can stop CrowdSec from
    starting, so an invalid entry must never reach the file.
    """
    for doc in docs:
        validate_entries(doc.ips, doc.cidrs)
        slugify(doc.name)
    return _env.get_template("crowdsec/whitelist.yaml.j2").render(docs=docs)


def content_digest(content: str) -> str:
    """sha256 of rendered content; decides whether a reload is needed at all."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def read_whitelist_file(path: Path) -> str:
    """Current file content, or the empty string when it does not exist yet."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def write_whitelist_file(path: Path, content: str) -> None:
    """Write in place, preserving the inode. NEVER write-temp-then-rename.

    The CrowdSec container sees this path through a single-file bind mount,
    resolved to an inode when the container starts. A rename would swap the
    inode and the container would keep reading the old content for the rest of
    its life, with no error in any log — the whole feature would silently do
    nothing. Truncate-and-write is correct here precisely because atomic
    replacement is correct everywhere else.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "r+" if path.exists() else "w"
    with path.open(mode, encoding="utf-8") as fh:
        fh.seek(0)
        fh.write(content)
        fh.truncate()
```

- [ ] **Step 5: Add the settings key**

In `backend/app/core/config.py`, beside the other `crowdsec_*` settings (near line 169):

```python
    # Rendered whitelist parser file on the shared volume. The CrowdSec
    # container bind-mounts this single file into its parsers directory.
    crowdsec_whitelist_path: str = "/data/crowdsec/whitelists/megoopm.yaml"
```

- [ ] **Step 6: Run the tests**

`python -m pytest -q tests/test_crowdsec_whitelists.py` — Expected: 16 passed.

- [ ] **Step 7: Commit**

```bash
git ls-files --eol backend/app/templates/crowdsec/whitelist.yaml.j2
git add backend/app/templates backend/app/services/crowdsec/whitelists.py backend/app/core/config.py backend/tests/test_crowdsec_whitelists.py
git commit -m "feat(crowdsec): render whitelists to a multi-document parser file"
```

---

### Task 4: Compose mount and boot seed

**Files:**
- Modify: `docker-compose.ha.yml`
- Modify: `docker-compose.yml`
- Modify: `backend/tests/test_compose_config.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: the file path from Task 3 (`/data/crowdsec/whitelists/megoopm.yaml`).
- Produces: a CrowdSec container that loads the file at boot.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_compose_config.py`:

```python
import yaml as _yaml

HA_COMPOSE = REPO_ROOT / "docker-compose.ha.yml"


def _service(compose_path, name):
    return _yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"][name]


def test_crowdsec_mounts_the_whitelist_file_not_a_directory():
    """s02-enrich already holds hub parsers (geoip enrichment among them).

    Bind-mounting a *directory* over it would mask them and silently break
    enrichment, so the mount must target a single file.
    """
    mounts = _service(HA_COMPOSE, "crowdsec")["volumes"]
    target = "/etc/crowdsec/parsers/s02-enrich/99-megoopm-whitelist.yaml"
    match = [m for m in mounts if isinstance(m, str) and target in m]
    assert match, f"no whitelist mount found in {mounts}"
    assert match[0].endswith(":ro")
    assert match[0].split(":")[0].endswith("/crowdsec/whitelists/megoopm.yaml")


def test_data_init_seeds_the_whitelist_file():
    """Docker creates a DIRECTORY when a bind-mount source is missing.

    CrowdSec then fails to parse it and refuses to start, and with
    APPSEC_FAILURE_ACTION=deny that is a full outage on first boot.
    """
    command = " ".join(_service(HA_COMPOSE, "data-init")["command"])
    assert "/data/crowdsec/whitelists" in command
    assert "megoopm.yaml" in command


def test_worker_can_reach_the_docker_socket():
    mounts = _service(HA_COMPOSE, "worker")["volumes"]
    assert any("/var/run/docker.sock" in m for m in mounts)


def test_api_backend_cannot_reach_the_docker_socket():
    """The socket is root on the host. It stays off the internet-facing process."""
    mounts = _service(HA_COMPOSE, "backend")["volumes"]
    assert not any("/var/run/docker.sock" in m for m in mounts)
```

- [ ] **Step 2: Run it and confirm it fails**

`python -m pytest -q tests/test_compose_config.py`

Expected: FAIL — `no whitelist mount found in [...]`.

- [ ] **Step 3: Edit `docker-compose.ha.yml`**

Under `crowdsec:` → `volumes:`, after the existing `acquis.d` mounts:

```yaml
      # Whitelists authored in the UI. A single-FILE mount: s02-enrich also
      # holds hub-installed parsers, and mounting a directory over it would
      # mask them. data-init seeds the file so Docker never creates a
      # directory here (CrowdSec would then refuse to start).
      - ${SHARED_DATA_PATH:?}/crowdsec/whitelists/megoopm.yaml:/etc/crowdsec/parsers/s02-enrich/99-megoopm-whitelist.yaml:ro
```

Under `data-init:` → `command:`, extend the existing inline shell (keep it one `sh -c` string):

```yaml
      - "mkdir -p /data/nginx/conf.d/stream /data/certs/_acme-challenge /data/crowdsec/whitelists && { [ -f /data/crowdsec/whitelists/megoopm.yaml ] || printf '# Managed by MegooPM — no whitelists defined.\\n' > /data/crowdsec/whitelists/megoopm.yaml; } && chown -R 1000:1000 /data || { echo 'data-init: cannot prepare /data (SHARED_DATA_PATH) for uid 1000' >&2; exit 1; }"
```

Under `worker:` → `volumes:`:

```yaml
      # Reload path: the worker restarts the CrowdSec container to make it
      # re-read the whitelist parser file. Mounted here and NOT on `backend`
      # — the socket is root on the host and backend takes internet traffic.
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

Apply the same three edits to `docker-compose.yml` for the single-node file.

- [ ] **Step 4: Add the new env keys to `.env.example`**

```bash
# Node whose worker holds the docker socket and can restart CrowdSec. Leave
# blank to disable whitelist reloads (whitelists then save but never apply).
CROWDSEC_CONTROL_NODE_ID=
# Container name the reload task restarts. `docker ps --format '{{.Names}}'`.
CROWDSEC_CONTAINER_NAME=megoopm-crowdsec-1
```

- [ ] **Step 5: Run the compose tests**

`python -m pytest -q tests/test_compose_config.py` — Expected: all pass (the `docker compose config` tests skip inside the backend container; run them on a host with docker to confirm the YAML is valid).

Also verify the YAML parses and the stack comes up:

```bash
docker compose -f docker-compose.ha.yml config >/dev/null
docker compose up -d crowdsec && docker compose logs crowdsec | grep -i "whitelist\|error"
docker compose exec crowdsec cat /etc/crowdsec/parsers/s02-enrich/99-megoopm-whitelist.yaml
```

Expected: the placeholder comment, and CrowdSec healthy.

- [ ] **Step 6: Commit**

```bash
git ls-files --eol docker-compose.ha.yml docker-compose.yml .env.example
git add docker-compose.ha.yml docker-compose.yml .env.example backend/tests/test_compose_config.py
git commit -m "feat(crowdsec): mount the whitelist parser file and seed it at boot"
```

---

### Task 5: Docker-socket container restart

**Files:**
- Create: `backend/app/services/crowdsec/reload.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_crowdsec_reload.py`

**Interfaces:**
- Produces: `CrowdSecReloadError(RuntimeError)`; `restart_container(name: str, *, socket_path: str, timeout_seconds: float, transport: httpx.BaseTransport | None = None) -> None`; settings `docker_socket_path`, `crowdsec_container_name`, `crowdsec_control_node_id`, `crowdsec_reload_health_timeout_seconds`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_crowdsec_reload.py`:

```python
"""Restarting the CrowdSec container over the docker socket."""

from __future__ import annotations

import httpx
import pytest
from app.services.crowdsec.reload import CrowdSecReloadError, restart_container


def _transport(handler):
    return httpx.MockTransport(handler)


def test_posts_a_restart_for_the_named_container() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        return httpx.Response(204)

    restart_container(
        "megoopm-crowdsec-1",
        socket_path="/var/run/docker.sock",
        timeout_seconds=30,
        transport=_transport(handler),
    )
    assert seen["method"] == "POST"
    assert seen["path"].endswith("/containers/megoopm-crowdsec-1/restart")


def test_missing_container_names_what_it_tried() -> None:
    # A wrong CROWDSEC_CONTAINER_NAME is the likeliest misconfiguration, so the
    # error has to say which name failed rather than "404".
    handler = lambda request: httpx.Response(404, json={"message": "No such container"})
    with pytest.raises(CrowdSecReloadError, match="megoopm-crowdsec-1"):
        restart_container(
            "megoopm-crowdsec-1",
            socket_path="/var/run/docker.sock",
            timeout_seconds=30,
            transport=_transport(handler),
        )


def test_socket_error_names_the_socket_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Permission denied")

    with pytest.raises(CrowdSecReloadError, match="/var/run/docker.sock"):
        restart_container(
            "megoopm-crowdsec-1",
            socket_path="/var/run/docker.sock",
            timeout_seconds=30,
            transport=_transport(handler),
        )
```

- [ ] **Step 2: Run it and confirm it fails**

`python -m pytest -q tests/test_crowdsec_reload.py`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.crowdsec.reload'`.

- [ ] **Step 3: Implement**

`backend/app/services/crowdsec/reload.py`:

```python
"""Restart the CrowdSec container so it re-reads its parser files.

CrowdSec loads parsers at startup and exposes no reload endpoint, and LAPI has
no route for parser configuration, so a restart is the only channel. We talk to
the docker daemon over its unix socket with ``httpx`` rather than adding the
docker SDK — ``httpx`` is already a dependency and one endpoint is all we need.

The socket is mounted on the **worker** only. It is root-equivalent on the host,
and the API process takes internet traffic.
"""

from __future__ import annotations

import httpx

# Pinned API version: the daemon accepts any version it supports, and pinning
# keeps the path stable if the host's docker is upgraded.
_DOCKER_API = "v1.43"


class CrowdSecReloadError(RuntimeError):
    """The CrowdSec container could not be restarted."""


def restart_container(
    name: str,
    *,
    socket_path: str,
    timeout_seconds: float,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Restart ``name`` via the docker socket. Raises on anything but success.

    Every error names the container and the socket, because the two likely
    misconfigurations — a wrong container name and an unmounted or
    unreadable socket — are indistinguishable from the symptom alone.
    """
    client_transport = transport or httpx.HTTPTransport(uds=socket_path)
    where = f"container {name!r} via {socket_path}"
    try:
        with httpx.Client(
            transport=client_transport,
            base_url="http://docker",
            timeout=timeout_seconds,
        ) as client:
            resp = client.post(
                f"/{_DOCKER_API}/containers/{name}/restart", params={"t": 10}
            )
    except httpx.HTTPError as exc:
        detail = str(exc) or "no detail"
        raise CrowdSecReloadError(
            f"Could not reach the docker daemon to restart {where}: "
            f"{type(exc).__name__} — {detail}"
        ) from exc

    # 204 = restarting; 304 = already in the requested state.
    if resp.status_code not in (httpx.codes.NO_CONTENT, httpx.codes.NOT_MODIFIED):
        raise CrowdSecReloadError(
            f"Docker refused to restart {where}: HTTP {resp.status_code} — "
            f"{resp.text.strip() or 'no body'}"
        )
```

- [ ] **Step 4: Add the settings**

In `backend/app/core/config.py`, beside `crowdsec_whitelist_path`:

```python
    # Reload path. The CrowdSec container runs only on the control-plane node
    # (compose profile "control-plane"), but workers run everywhere — this
    # names the node whose worker holds the docker socket. Unset disables
    # reloads: whitelists save but are reported as not applied, never silently.
    crowdsec_control_node_id: str | None = None
    crowdsec_container_name: str = "megoopm-crowdsec-1"
    docker_socket_path: str = "/var/run/docker.sock"
    # How long to wait for LAPI to answer again after a restart before giving
    # up and rolling the file back.
    crowdsec_reload_health_timeout_seconds: int = 60
```

- [ ] **Step 5: Run the tests**

`python -m pytest -q tests/test_crowdsec_reload.py` — Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/crowdsec/reload.py backend/app/core/config.py backend/tests/test_crowdsec_reload.py
git commit -m "feat(crowdsec): restart the CrowdSec container over the docker socket"
```

---

### Task 6: Apply state and the reload task

**Files:**
- Create: `backend/app/services/crowdsec/apply_state.py`
- Create: `backend/app/tasks/crowdsec.py`
- Modify: `backend/app/core/celery_app.py`
- Test: `backend/tests/test_crowdsec_reload.py`

**Interfaces:**
- Consumes: `render_whitelists`, `content_digest`, `read_whitelist_file`, `write_whitelist_file`, `WhitelistDoc` (Task 3); `restart_container`, `CrowdSecReloadError` (Task 5); `CrowdSecClient.ping()`.
- Produces: `read_apply_state(conn) -> ApplyState`; `record_apply(conn, *, digest, ok, error) -> None`; Celery task `app.tasks.crowdsec.apply_crowdsec_whitelists`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_crowdsec_reload.py`:

```python
from pathlib import Path

from app.services.crowdsec.whitelists import WhitelistDoc, render_whitelists
from app.tasks.crowdsec import apply_whitelists_to_disk

DOC = WhitelistDoc(
    name="internal", reason="internal backends", description="",
    ips=["10.10.0.14"], cidrs=[],
)


class _Recorder:
    """Stands in for the restart + health-check pair."""

    def __init__(self, *, healthy: bool) -> None:
        self.restarts = 0
        self._healthy = healthy

    def restart(self) -> None:
        self.restarts += 1

    def healthy(self) -> bool:
        return self._healthy


def test_writes_renders_and_restarts(tmp_path: Path) -> None:
    path = tmp_path / "megoopm.yaml"
    path.write_text("# seed\n", encoding="utf-8")
    rec = _Recorder(healthy=True)

    result = apply_whitelists_to_disk(
        [DOC], path=path, applied_digest=None, restart=rec.restart, healthy=rec.healthy
    )

    assert result.ok is True
    assert rec.restarts == 1
    assert "megoopm/wl-internal" in path.read_text(encoding="utf-8")
    assert result.digest == content_digest(render_whitelists([DOC]))


def test_unchanged_content_does_not_restart_crowdsec(tmp_path: Path) -> None:
    # Restarting is a few seconds of fail-closed denial on every protected
    # host; a save that changes nothing must not cost that.
    path = tmp_path / "megoopm.yaml"
    content = render_whitelists([DOC])
    path.write_text(content, encoding="utf-8")
    rec = _Recorder(healthy=True)

    result = apply_whitelists_to_disk(
        [DOC], path=path, applied_digest=content_digest(content),
        restart=rec.restart, healthy=rec.healthy,
    )

    assert result.ok is True
    assert rec.restarts == 0


def test_rolls_back_when_crowdsec_does_not_come_back(tmp_path: Path) -> None:
    # A parser file CrowdSec cannot load stops it starting, and with
    # APPSEC_FAILURE_ACTION=deny that denies every request on every protected
    # host indefinitely. Rollback bounds it to the health timeout.
    path = tmp_path / "megoopm.yaml"
    previous = "# Managed by MegooPM — no whitelists defined.\n"
    path.write_text(previous, encoding="utf-8")
    rec = _Recorder(healthy=False)

    result = apply_whitelists_to_disk(
        [DOC], path=path, applied_digest=None, restart=rec.restart, healthy=rec.healthy
    )

    assert result.ok is False
    assert "did not come back" in result.error
    assert path.read_text(encoding="utf-8") == previous
    assert rec.restarts == 2  # once for the new file, once to restore


def test_rollback_preserves_the_inode(tmp_path: Path) -> None:
    path = tmp_path / "megoopm.yaml"
    path.write_text("# seed\n", encoding="utf-8")
    before = path.stat().st_ino
    rec = _Recorder(healthy=False)

    apply_whitelists_to_disk(
        [DOC], path=path, applied_digest=None, restart=rec.restart, healthy=rec.healthy
    )

    assert path.stat().st_ino == before


def test_invalid_entry_never_reaches_the_file(tmp_path: Path) -> None:
    path = tmp_path / "megoopm.yaml"
    path.write_text("# seed\n", encoding="utf-8")
    bad = WhitelistDoc(
        name="bad", reason="typo", description="", ips=["10.10.0.999"], cidrs=[]
    )
    rec = _Recorder(healthy=True)

    result = apply_whitelists_to_disk(
        [bad], path=path, applied_digest=None, restart=rec.restart, healthy=rec.healthy
    )

    assert result.ok is False
    assert "10.10.0.999" in result.error
    assert path.read_text(encoding="utf-8") == "# seed\n"
    assert rec.restarts == 0
```

Add `content_digest` to the imports at the top of the file.

- [ ] **Step 2: Run it and confirm it fails**

`python -m pytest -q tests/test_crowdsec_reload.py`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.tasks.crowdsec'`.

- [ ] **Step 3: Write the apply-state helpers**

`backend/app/services/crowdsec/apply_state.py`:

```python
"""Read and record whether the last whitelist render actually reached CrowdSec.

A single row (``id=1``), seeded by migration 0016. The apply runs in a Celery
task on the control-plane node and can fail long after the API returned 200; the
UI reads this to avoid showing a whitelist as active when it is not.

Synchronous, like the cluster helpers — Celery tasks are sync and use
``app.services.cluster.sync_engine``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, func, select, update

from app.models.crowdsec_whitelist import CrowdSecWhitelistApply

_ROW_ID = 1


@dataclass(frozen=True, slots=True)
class ApplyState:
    """The last apply attempt, as recorded."""

    applied_digest: str | None
    applied_at: datetime | None
    ok: bool
    error: str | None


def read_apply_state(conn: Connection) -> ApplyState:
    table = CrowdSecWhitelistApply.__table__
    row = conn.execute(select(table).where(table.c.id == _ROW_ID)).one_or_none()
    if row is None:
        return ApplyState(applied_digest=None, applied_at=None, ok=True, error=None)
    return ApplyState(
        applied_digest=row.applied_digest,
        applied_at=row.applied_at,
        ok=row.ok,
        error=row.error,
    )


def record_apply(
    conn: Connection, *, digest: str | None, ok: bool, error: str | None
) -> None:
    """Record the outcome. ``digest`` is only advanced on success."""
    table = CrowdSecWhitelistApply.__table__
    values: dict[str, object] = {"ok": ok, "error": error, "applied_at": func.now()}
    if ok and digest is not None:
        values["applied_digest"] = digest
    conn.execute(update(table).where(table.c.id == _ROW_ID).values(**values))
```

- [ ] **Step 4: Write the task module**

`backend/app/tasks/crowdsec.py`:

```python
"""Apply UI-authored CrowdSec whitelists and reload CrowdSec.

The pure part — :func:`apply_whitelists_to_disk` — takes the restart and health
check as callables so the write/restart/rollback sequence is testable without a
docker socket or a running CrowdSec.

Restarting CrowdSec makes AppSec briefly unreachable, and the bouncer runs
``APPSEC_FAILURE_ACTION=deny``, so every protected host fails closed for the
duration. Two things follow, and both are load-bearing rather than polish:

* an unchanged render must not restart anything (the digest short-circuit); and
* a file CrowdSec cannot load must be rolled back, because otherwise that
  few-second denial becomes indefinite.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.config import settings
from app.services.cluster import sync_engine
from app.services.crowdsec import CrowdSecClient, CrowdSecError
from app.services.crowdsec.apply_state import read_apply_state, record_apply
from app.services.crowdsec.reload import CrowdSecReloadError, restart_container
from app.services.crowdsec.whitelists import (
    WhitelistDoc,
    WhitelistValidationError,
    content_digest,
    read_whitelist_file,
    render_whitelists,
    write_whitelist_file,
)


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Outcome of one apply, JSON-serialisable via :meth:`as_dict`."""

    ok: bool
    digest: str | None
    error: str | None
    restarted: bool

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "digest": self.digest,
            "error": self.error,
            "restarted": self.restarted,
        }


def apply_whitelists_to_disk(
    docs: Sequence[WhitelistDoc],
    *,
    path: Path,
    applied_digest: str | None,
    restart: Callable[[], None],
    healthy: Callable[[], bool],
) -> ApplyResult:
    """Render, write in place, restart, verify — and roll back if it fails."""
    try:
        content = render_whitelists(docs)
    except WhitelistValidationError as exc:
        return ApplyResult(ok=False, digest=None, error=str(exc), restarted=False)

    digest = content_digest(content)
    if digest == applied_digest and read_whitelist_file(path) == content:
        return ApplyResult(ok=True, digest=digest, error=None, restarted=False)

    previous = read_whitelist_file(path)
    write_whitelist_file(path, content)

    try:
        restart()
    except CrowdSecReloadError as exc:
        write_whitelist_file(path, previous)
        return ApplyResult(ok=False, digest=None, error=str(exc), restarted=False)

    if healthy():
        return ApplyResult(ok=True, digest=digest, error=None, restarted=True)

    # CrowdSec did not answer again. The likeliest cause is a parser file it
    # cannot load, which leaves every protected host failing closed, so put the
    # last known-good content back and restart again.
    write_whitelist_file(path, previous)
    try:
        restart()
    except CrowdSecReloadError as exc:
        return ApplyResult(
            ok=False,
            digest=None,
            error=(
                "CrowdSec did not come back after the whitelist change, and the "
                f"rollback restart also failed: {exc}"
            ),
            restarted=True,
        )
    return ApplyResult(
        ok=False,
        digest=None,
        error=(
            "CrowdSec did not come back within "
            f"{settings.crowdsec_reload_health_timeout_seconds}s of the whitelist "
            "change. The previous whitelist file has been restored."
        ),
        restarted=True,
    )


def _load_docs(conn) -> list[WhitelistDoc]:
    from app.models.crowdsec_whitelist import CrowdSecWhitelist

    table = CrowdSecWhitelist.__table__
    rows = conn.execute(
        select(table).where(table.c.enabled.is_(True)).order_by(table.c.id)
    ).all()
    return [
        WhitelistDoc(
            name=r.name,
            reason=r.reason,
            description=r.description,
            ips=list(r.ips),
            cidrs=list(r.cidrs),
        )
        for r in rows
    ]


def _wait_for_lapi() -> bool:
    """Poll LAPI until it answers or the health timeout elapses."""
    import asyncio

    deadline = time.monotonic() + settings.crowdsec_reload_health_timeout_seconds

    async def _ping() -> bool:
        async with CrowdSecClient() as client:
            await client.ping()
        return True

    while time.monotonic() < deadline:
        try:
            return asyncio.run(_ping())
        except (CrowdSecError, OSError):
            time.sleep(2)
    return False


@celery_app.task(name="app.tasks.crowdsec.apply_crowdsec_whitelists")
def apply_crowdsec_whitelists() -> dict:
    """Render every enabled whitelist, apply it, and record the outcome."""
    engine = sync_engine()
    try:
        with engine.begin() as conn:
            docs = _load_docs(conn)
            state = read_apply_state(conn)

        result = apply_whitelists_to_disk(
            docs,
            path=Path(settings.crowdsec_whitelist_path),
            applied_digest=state.applied_digest,
            restart=lambda: restart_container(
                settings.crowdsec_container_name,
                socket_path=settings.docker_socket_path,
                timeout_seconds=settings.crowdsec_reload_health_timeout_seconds,
            ),
            healthy=_wait_for_lapi,
        )

        with engine.begin() as conn:
            record_apply(conn, digest=result.digest, ok=result.ok, error=result.error)
        return result.as_dict()
    finally:
        engine.dispose()
```

- [ ] **Step 5: Register the task module**

In `backend/app/core/celery_app.py`, extend `TASK_MODULES`:

```python
TASK_MODULES = [
    "app.tasks.sample",
    "app.tasks.nginx",
    "app.tasks.certs",
    "app.tasks.crowdsec",
]
```

- [ ] **Step 6: Run the tests**

`python -m pytest -q tests/test_crowdsec_reload.py` — Expected: 8 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/crowdsec/apply_state.py backend/app/tasks/crowdsec.py backend/app/core/celery_app.py backend/tests/test_crowdsec_reload.py
git commit -m "feat(crowdsec): apply whitelists with digest short-circuit and rollback"
```

---

### Task 7: API routes and schemas

**Files:**
- Create: `backend/app/schemas/crowdsec_whitelist.py`
- Modify: `backend/app/api/routes/crowdsec.py`
- Test: `backend/tests/test_crowdsec_whitelists_api.py`

**Interfaces:**
- Consumes: models (Task 1), `slugify`/`validate_entries`/`render_whitelists`/`WhitelistDoc` (Tasks 2–3), `read_apply_state` (Task 6), task name `app.tasks.crowdsec.apply_crowdsec_whitelists`, `node_queue` from `app.core.celery_app`.
- Produces: `WhitelistRead`, `WhitelistCreate`, `WhitelistUpdate`, `WhitelistPreview`, `WhitelistApplyStatus`; the seven routes below.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_crowdsec_whitelists_api.py`:

```python
"""Whitelist CRUD, preview and apply-status routes (admin-only)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_rejects_a_bad_ip_before_writing_anything(admin_client) -> None:
    resp = await admin_client.post(
        "/crowdsec/whitelists",
        json={"name": "bad", "reason": "typo", "ips": ["10.10.0.999"], "cidrs": []},
    )
    assert resp.status_code == 422
    assert "10.10.0.999" in resp.text


async def test_rejects_a_whitelist_matching_nothing(admin_client) -> None:
    resp = await admin_client.post(
        "/crowdsec/whitelists",
        json={"name": "empty", "reason": "nothing", "ips": [], "cidrs": []},
    )
    assert resp.status_code == 422


async def test_rejects_names_that_collide_after_slugification(admin_client) -> None:
    # "Internal Backends" and "internal-backends" both render
    # `megoopm/wl-internal-backends`, and CrowdSec requires unique parser names.
    first = await admin_client.post(
        "/crowdsec/whitelists",
        json={"name": "Internal Backends", "reason": "r", "ips": ["10.9.9.1"], "cidrs": []},
    )
    assert first.status_code == 201
    second = await admin_client.post(
        "/crowdsec/whitelists",
        json={"name": "internal-backends", "reason": "r", "ips": ["10.9.9.2"], "cidrs": []},
    )
    assert second.status_code == 409


async def test_preview_returns_the_yaml_that_would_be_written(admin_client) -> None:
    resp = await admin_client.post(
        "/crowdsec/whitelists/preview",
        json={"name": "Internal", "reason": "r", "ips": ["10.10.0.14"], "cidrs": []},
    )
    assert resp.status_code == 200
    assert "megoopm/wl-internal" in resp.json()["yaml"]


async def test_non_admin_cannot_list_whitelists(user_client) -> None:
    assert (await user_client.get("/crowdsec/whitelists")).status_code == 403


async def test_status_reports_when_reload_is_not_configured(admin_client) -> None:
    # CROWDSEC_CONTROL_NODE_ID is unset in tests. Saving must not silently
    # imply the whitelist is in force.
    resp = await admin_client.get("/crowdsec/whitelists/status")
    assert resp.status_code == 200
    assert resp.json()["reload_configured"] is False
```

These tests use the existing `admin_client` / `user_client` fixtures from `backend/tests/conftest.py`. The create/collision tests insert `ARRAY` rows, so add at the top of the module the same `_pg_available()` skip guard used in `test_crowdsec_whitelists_pg.py` (Task 1) — copy it verbatim rather than importing, so the module reads standalone.

- [ ] **Step 2: Run it and confirm it fails**

`python -m pytest -q tests/test_crowdsec_whitelists_api.py`

Expected: FAIL — 404 on every route.

- [ ] **Step 3: Write the schemas**

`backend/app/schemas/crowdsec_whitelist.py`:

```python
"""Pydantic schemas for UI-authored CrowdSec whitelists.

Validation mirrors the database ``CHECK`` constraint and the renderer's own
checks, so bad input is a 422 at the boundary rather than a 500 from the
database or — worse — a parser file CrowdSec refuses to load.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.crowdsec.whitelists import WhitelistValidationError, validate_entries


class WhitelistBase(BaseModel):
    """Fields describing one whitelist document."""

    name: str = Field(min_length=1, max_length=255, description="Operator-facing name")
    reason: str = Field(
        min_length=1, description="Why these addresses are exempt; appears in CrowdSec logs"
    )
    description: str = Field(default="", description="Free-text note")
    ips: list[str] = Field(default_factory=list, description="Exact IP addresses to exempt")
    cidrs: list[str] = Field(default_factory=list, description="CIDR ranges to exempt")
    enabled: bool = Field(default=True, description="Disabled whitelists are not rendered")

    @model_validator(mode="after")
    def _check_entries(self) -> "WhitelistBase":
        if not self.ips and not self.cidrs:
            raise ValueError("A whitelist needs at least one IP address or CIDR range.")
        try:
            validate_entries(self.ips, self.cidrs)
        except WhitelistValidationError as exc:
            raise ValueError(str(exc)) from exc
        return self


class WhitelistCreate(WhitelistBase):
    """Request body for creating a whitelist."""


class WhitelistUpdate(WhitelistBase):
    """Request body for replacing a whitelist."""


class WhitelistRead(WhitelistBase):
    """A stored whitelist."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class WhitelistPreview(BaseModel):
    """The YAML a given whitelist would contribute to the parser file."""

    yaml: str = Field(description="Exactly what the renderer would write")


class WhitelistApplyStatus(BaseModel):
    """Whether the last render actually reached CrowdSec."""

    ok: bool = Field(description="False when the last apply failed")
    error: str | None = Field(default=None, description="Operator-facing failure text")
    applied_at: datetime | None = Field(default=None)
    reload_configured: bool = Field(
        description="False when CROWDSEC_CONTROL_NODE_ID is unset; whitelists then save but never apply"
    )
```

- [ ] **Step 4: Add the routes**

Append to `backend/app/api/routes/crowdsec.py` (the module already has `router`, `AdminUser`, `SessionDep`):

```python
@router.get("/crowdsec/whitelists", response_model=list[WhitelistRead])
async def list_whitelists(session: SessionDep, _: AdminUser) -> list[CrowdSecWhitelist]:
    """Every whitelist, enabled or not, oldest first."""
    result = await session.execute(select(CrowdSecWhitelist).order_by(CrowdSecWhitelist.id))
    return list(result.scalars())


@router.post("/crowdsec/whitelists", response_model=WhitelistRead, status_code=201)
async def create_whitelist(
    body: WhitelistCreate, session: SessionDep, admin: AdminUser
) -> CrowdSecWhitelist:
    await _guard_slug_unique(session, body.name, exclude_id=None)
    row = CrowdSecWhitelist(**body.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await audit_service.record(
        session, admin, AuditAction.update, f"crowdsec whitelist {row.name!r} created"
    )
    _enqueue_apply()
    return row


@router.patch("/crowdsec/whitelists/{whitelist_id}", response_model=WhitelistRead)
async def update_whitelist(
    whitelist_id: int, body: WhitelistUpdate, session: SessionDep, admin: AdminUser
) -> CrowdSecWhitelist:
    row = await session.get(CrowdSecWhitelist, whitelist_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Whitelist not found.")
    await _guard_slug_unique(session, body.name, exclude_id=whitelist_id)
    for field, value in body.model_dump().items():
        setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    await audit_service.record(
        session, admin, AuditAction.update, f"crowdsec whitelist {row.name!r} updated"
    )
    _enqueue_apply()
    return row


@router.delete("/crowdsec/whitelists/{whitelist_id}", status_code=204)
async def delete_whitelist(whitelist_id: int, session: SessionDep, admin: AdminUser) -> None:
    row = await session.get(CrowdSecWhitelist, whitelist_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Whitelist not found.")
    name = row.name
    await session.delete(row)
    await session.commit()
    await audit_service.record(
        session, admin, AuditAction.update, f"crowdsec whitelist {name!r} deleted"
    )
    _enqueue_apply()


@router.post("/crowdsec/whitelists/preview", response_model=WhitelistPreview)
async def preview_whitelist(body: WhitelistCreate, _: AdminUser) -> WhitelistPreview:
    """Render one whitelist exactly as the writer would.

    The dialog shows this rather than re-implementing the renderer in
    TypeScript: a second renderer would drift, and the preview's whole value is
    being the same bytes that reach CrowdSec.
    """
    doc = WhitelistDoc(
        name=body.name,
        reason=body.reason,
        description=body.description,
        ips=body.ips,
        cidrs=body.cidrs,
    )
    return WhitelistPreview(yaml=render_whitelists([doc]))


@router.get("/crowdsec/whitelists/status", response_model=WhitelistApplyStatus)
async def whitelist_status(session: SessionDep, _: AdminUser) -> WhitelistApplyStatus:
    row = await session.get(CrowdSecWhitelistApply, 1)
    configured = bool(settings.crowdsec_control_node_id)
    if row is None:
        return WhitelistApplyStatus(ok=True, reload_configured=configured)
    return WhitelistApplyStatus(
        ok=row.ok, error=row.error, applied_at=row.applied_at, reload_configured=configured
    )


@router.post("/crowdsec/whitelists/apply", status_code=202)
async def apply_whitelists(_: AdminUser) -> dict:
    """Re-run the apply — the retry path after a failed reload."""
    if not _enqueue_apply():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "CrowdSec reloads are not configured: set CROWDSEC_CONTROL_NODE_ID "
                "to the node whose worker has the docker socket."
            ),
        )
    return {"queued": True}
```

with these helpers near the top of the module:

```python
def _enqueue_apply() -> bool:
    """Queue the apply onto the control-plane node. False when not configured.

    Returning False rather than enqueueing blindly matters: the CrowdSec
    container runs only on the control-plane node, so a task sent anywhere else
    would sit unconsumed and the operator would see a whitelist that never
    applies with nothing explaining why.
    """
    node = settings.crowdsec_control_node_id
    if not node:
        return False
    celery_app.send_task(
        "app.tasks.crowdsec.apply_crowdsec_whitelists", queue=node_queue(node)
    )
    return True


async def _guard_slug_unique(session, name: str, *, exclude_id: int | None) -> None:
    """409 when two names would render the same CrowdSec `name:`."""
    try:
        slug = slugify(name)
    except WhitelistValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    result = await session.execute(select(CrowdSecWhitelist))
    for row in result.scalars():
        if row.id != exclude_id and slugify(row.name) == slug:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Whitelist {row.name!r} already renders as megoopm/wl-{slug}.",
            )
```

Add the imports the new code needs: `select` from `sqlalchemy`, `settings` from `app.core.config`, `celery_app`/`node_queue` from `app.core.celery_app`, the two models, the five schemas, and `WhitelistDoc`/`render_whitelists`/`slugify`/`WhitelistValidationError` from the whitelists service.

- [ ] **Step 5: Run the tests**

`python -m pytest -q tests/test_crowdsec_whitelists_api.py` (with the Postgres env) — Expected: 6 passed.

- [ ] **Step 6: Regenerate both API surfaces**

```bash
cd backend && python -m scripts.export_openapi
cd ../frontend && npm run gen:api
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/crowdsec_whitelist.py backend/app/api/routes/crowdsec.py backend/tests/test_crowdsec_whitelists_api.py backend/openapi.json frontend/src/lib/api
git commit -m "feat(crowdsec): whitelist CRUD, preview and apply-status routes"
```

---

### Task 8: Whitelists tab, table and status banner

**Files:**
- Create: `frontend/src/components/security/whitelists-table.tsx`
- Create: `frontend/src/components/security/whitelist-status-banner.tsx`
- Modify: `frontend/src/components/security/security-view.tsx`
- Test: `frontend/src/components/security/whitelists-table.test.tsx`
- Test: `frontend/src/components/security/whitelist-status-banner.test.tsx`

**Interfaces:**
- Consumes: generated client methods for the Task 7 routes.
- Produces: `<WhitelistsTable />`, `<WhitelistStatusBanner status={...} onRetry={...} />`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/security/whitelist-status-banner.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { WhitelistStatusBanner } from "@/components/security/whitelist-status-banner";

afterEach(cleanup);

describe("WhitelistStatusBanner", () => {
  it("renders nothing when the last apply succeeded", () => {
    const { container } = render(
      <WhitelistStatusBanner
        status={{ ok: true, error: null, applied_at: null, reload_configured: true }}
        onRetry={() => {}}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the failure text so a failed reload is not invisible", () => {
    render(
      <WhitelistStatusBanner
        status={{
          ok: false,
          error: "CrowdSec did not come back within 60s.",
          applied_at: null,
          reload_configured: true,
        }}
        onRetry={() => {}}
      />,
    );
    expect(screen.getByText(/did not come back within 60s/)).toBeInTheDocument();
  });

  it("warns when reloads are not configured at all", () => {
    // Otherwise the table implies whitelists are in force when nothing applies.
    render(
      <WhitelistStatusBanner
        status={{ ok: true, error: null, applied_at: null, reload_configured: false }}
        onRetry={() => {}}
      />,
    );
    expect(screen.getByText(/CROWDSEC_CONTROL_NODE_ID/)).toBeInTheDocument();
  });

  it("retries on demand", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(
      <WhitelistStatusBanner
        status={{ ok: false, error: "boom", applied_at: null, reload_configured: true }}
        onRetry={onRetry}
      />,
    );
    await user.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
```

`frontend/src/components/security/whitelists-table.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { WhitelistsTable } from "@/components/security/whitelists-table";

const ROW = {
  id: 1,
  name: "Internal Backends",
  reason: "internal backends trip appsec generic rules",
  description: "",
  ips: ["10.10.0.14"],
  cidrs: ["10.10.0.0/24"],
  enabled: true,
  created_at: "2026-08-31T00:00:00Z",
  updated_at: "2026-08-31T00:00:00Z",
};

afterEach(cleanup);

describe("WhitelistsTable", () => {
  it("shows the name, reason and how many addresses it covers", () => {
    render(<WhitelistsTable rows={[ROW]} onToggle={() => {}} onEdit={() => {}} onDelete={() => {}} />);
    expect(screen.getByText("Internal Backends")).toBeInTheDocument();
    expect(screen.getByText(/1 IP/)).toBeInTheDocument();
    expect(screen.getByText(/1 CIDR/)).toBeInTheDocument();
  });

  it("toggles a whitelist off", async () => {
    const onToggle = vi.fn();
    const user = userEvent.setup();
    render(<WhitelistsTable rows={[ROW]} onToggle={onToggle} onEdit={() => {}} onDelete={() => {}} />);
    // base-ui switches render role="switch"; assert aria-disabled, not disabled.
    await user.click(screen.getByRole("switch"));
    expect(onToggle).toHaveBeenCalledWith(ROW, false);
  });

  it("tells the operator what an empty list means", () => {
    render(<WhitelistsTable rows={[]} onToggle={() => {}} onEdit={() => {}} onDelete={() => {}} />);
    expect(screen.getByText(/no whitelists/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run them and confirm they fail**

```bash
cd frontend && npx vitest run src/components/security
```
Expected: FAIL — cannot resolve `@/components/security/whitelists-table`.

- [ ] **Step 3: Implement the status banner**

`frontend/src/components/security/whitelist-status-banner.tsx`:

```tsx
"use client";

import { TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { WhitelistApplyStatus } from "@/lib/api";

/**
 * Why a whitelist may not be in force.
 *
 * Saving a whitelist returns 200 long before the apply runs — the write goes to
 * the database, then a task on the control-plane node writes the file and
 * restarts CrowdSec. Both later steps can fail. Without this banner the table
 * would show a whitelist that reads as active while CrowdSec has never seen it,
 * which is the failure shape that costs an afternoon to diagnose.
 */
export function WhitelistStatusBanner({
  status,
  onRetry,
}: {
  status: WhitelistApplyStatus;
  onRetry: () => void;
}) {
  if (status.reload_configured && status.ok) return null;

  const message = !status.reload_configured
    ? "Whitelists are saved but never applied: CROWDSEC_CONTROL_NODE_ID is not set, so no node is designated to restart CrowdSec."
    : (status.error ?? "The last whitelist apply failed.");

  return (
    <div className="border-destructive/40 bg-destructive/10 flex items-start gap-3 rounded-lg border p-3">
      <TriangleAlert className="text-destructive mt-0.5 size-4 shrink-0" />
      <div className="flex-1 space-y-2">
        <p className="text-sm">{message}</p>
        {status.reload_configured ? (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry apply
          </Button>
        ) : null}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Implement the table**

`frontend/src/components/security/whitelists-table.tsx`:

```tsx
"use client";

import { Pencil, Trash2 } from "lucide-react";

import { EnabledToggle } from "@/components/hosts/enabled-toggle";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { WhitelistRead } from "@/lib/api";

/** "1 IP, 2 CIDRs" — singular/plural both matter on a one-entry whitelist. */
function coverage(row: WhitelistRead): string {
  const parts: string[] = [];
  if (row.ips.length) parts.push(`${row.ips.length} IP${row.ips.length === 1 ? "" : "s"}`);
  if (row.cidrs.length)
    parts.push(`${row.cidrs.length} CIDR${row.cidrs.length === 1 ? "" : "s"}`);
  return parts.join(", ");
}

export function WhitelistsTable({
  rows,
  onToggle,
  onEdit,
  onDelete,
}: {
  rows: WhitelistRead[];
  onToggle: (row: WhitelistRead, next: boolean) => Promise<void>;
  onEdit: (row: WhitelistRead) => void;
  onDelete: (row: WhitelistRead) => void;
}) {
  if (rows.length === 0) {
    return (
      <p className="text-muted-foreground rounded-lg border border-dashed p-8 text-center text-sm">
        No whitelists yet. Add one to stop CrowdSec acting on traffic from an
        address you trust.
      </p>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Reason</TableHead>
          <TableHead>Covers</TableHead>
          <TableHead>Enabled</TableHead>
          <TableHead className="w-24" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.id}>
            <TableCell className="font-medium">{row.name}</TableCell>
            <TableCell className="text-muted-foreground">{row.reason}</TableCell>
            <TableCell>{coverage(row)}</TableCell>
            <TableCell>
              <EnabledToggle
                checked={row.enabled}
                name={row.name}
                onToggle={(next) => onToggle(row, next)}
              />
            </TableCell>
            <TableCell className="text-right">
              <Button
                size="icon"
                variant="ghost"
                aria-label={`Edit ${row.name}`}
                onClick={() => onEdit(row)}
              >
                <Pencil className="size-4" />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                aria-label={`Delete ${row.name}`}
                onClick={() => onDelete(row)}
              >
                <Trash2 className="size-4" />
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
```

- [ ] **Step 5: Add the tab**

In `frontend/src/components/security/security-view.tsx`, add a fourth tab after Recent alerts, keeping `defaultValue="dashboard"`:

```tsx
        <TabsTab value="whitelists"><ShieldCheck /> Whitelists</TabsTab>
```
```tsx
        <TabsPanel value="whitelists" className="space-y-3">
          <WhitelistStatusBanner status={whitelistStatus} onRetry={retryApply} />
          <WhitelistsTable rows={whitelists} onToggle={toggle} onEdit={edit} onDelete={remove} />
        </TabsPanel>
```

Import `ShieldCheck` from `lucide-react`.

- [ ] **Step 6: Run tests, lint and typecheck**

```bash
cd frontend && npx vitest run && npx eslint src && npx tsc --noEmit
```
Expected: all green; the existing Security tab tests still pass and Dashboard remains the default.

- [ ] **Step 7: Commit**

```bash
git ls-files --eol frontend/src/components/security
git add frontend/src/components/security
git commit -m "feat(ui): Whitelists tab with apply-status banner"
```

---

### Task 9: Whitelist dialog with server-rendered YAML preview

**Files:**
- Create: `frontend/src/components/security/whitelist-dialog.tsx`
- Modify: `frontend/src/components/security/security-view.tsx`
- Test: `frontend/src/components/security/whitelist-dialog.test.tsx`

**Interfaces:**
- Consumes: `POST /crowdsec/whitelists/preview` from the generated client; `WhitelistCreate` shape from Task 7.
- Produces: `<WhitelistDialog open onOpenChange whitelist={row | null} onSubmit={...} />`.

- [ ] **Step 1: Write the failing test**

`frontend/src/components/security/whitelist-dialog.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { WhitelistDialog } from "@/components/security/whitelist-dialog";

afterEach(cleanup);

describe("WhitelistDialog", () => {
  it("refuses a malformed IP without calling the API", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(
      <WhitelistDialog open onOpenChange={() => {}} whitelist={null} onSubmit={onSubmit} />,
    );

    await user.type(screen.getByLabelText(/name/i), "Internal");
    await user.type(screen.getByLabelText(/reason/i), "internal backends");
    await user.type(screen.getByLabelText(/ip addresses/i), "10.10.0.999");
    await user.click(screen.getByRole("button", { name: /save/i }));

    expect(await screen.findByText(/not a valid IP address/i)).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("accepts one address per line", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(
      <WhitelistDialog open onOpenChange={() => {}} whitelist={null} onSubmit={onSubmit} />,
    );

    await user.type(screen.getByLabelText(/name/i), "Internal");
    await user.type(screen.getByLabelText(/reason/i), "internal backends");
    await user.type(screen.getByLabelText(/ip addresses/i), "10.10.0.14{enter}10.10.0.15");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ ips: ["10.10.0.14", "10.10.0.15"], cidrs: [] }),
      ),
    );
  });

  it("shows the YAML the server would write", async () => {
    const user = userEvent.setup();
    render(
      <WhitelistDialog open onOpenChange={() => {}} whitelist={null} onSubmit={() => {}} />,
    );
    await user.type(screen.getByLabelText(/name/i), "Internal");
    await user.type(screen.getByLabelText(/reason/i), "r");
    await user.type(screen.getByLabelText(/ip addresses/i), "10.10.0.14");

    // Rendered by the backend, not re-implemented here: a second renderer
    // would drift from the file that actually reaches CrowdSec.
    expect(await screen.findByText(/megoopm\/wl-internal/)).toBeInTheDocument();
  });
});
```

Mock the generated preview client in this test file with `vi.mock`, returning `{ yaml: 'name: "megoopm/wl-internal"\n' }`, following the mocking style already used in the security component tests.

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd frontend && npx vitest run src/components/security/whitelist-dialog.test.tsx
```
Expected: FAIL — cannot resolve the module.

- [ ] **Step 3: Implement the dialog**

`frontend/src/components/security/whitelist-dialog.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { previewWhitelist, type WhitelistCreate, type WhitelistRead } from "@/lib/api";

/** One entry per line; blank lines are how people separate groups, not entries. */
function toEntries(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

const IPV4 = /^(\d{1,3}\.){3}\d{1,3}$/;

/**
 * Client-side check mirroring the server's message so the operator sees the
 * same words whichever side rejects it. Deliberately narrow: this catches
 * typos, and the server's `ipaddress` parse remains the authority.
 */
function badIp(value: string): boolean {
  if (value.includes(":")) return false; // IPv6 — leave it to the server
  if (!IPV4.test(value)) return true;
  return value.split(".").some((octet) => Number(octet) > 255);
}

function badCidr(value: string): boolean {
  const [addr, bits] = value.split("/");
  if (bits === undefined || addr === undefined) return true;
  const max = addr.includes(":") ? 128 : 32;
  const n = Number(bits);
  if (!Number.isInteger(n) || n < 0 || n > max) return true;
  return badIp(addr);
}

export function WhitelistDialog({
  open,
  onOpenChange,
  whitelist,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  whitelist: WhitelistRead | null;
  onSubmit: (body: WhitelistCreate) => Promise<void>;
}) {
  const [name, setName] = useState(whitelist?.name ?? "");
  const [reason, setReason] = useState(whitelist?.reason ?? "");
  const [description, setDescription] = useState(whitelist?.description ?? "");
  const [ipsText, setIpsText] = useState((whitelist?.ips ?? []).join("\n"));
  const [cidrsText, setCidrsText] = useState((whitelist?.cidrs ?? []).join("\n"));
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState("");

  const ips = toEntries(ipsText);
  const cidrs = toEntries(cidrsText);

  // The preview is rendered by the SERVER. Re-implementing the renderer here
  // would drift from the bytes that actually reach CrowdSec, and the preview's
  // whole value is being those bytes.
  useEffect(() => {
    if (!name || !reason || (!ips.length && !cidrs.length)) {
      setPreview("");
      return;
    }
    const handle = setTimeout(() => {
      previewWhitelist({ name, reason, description, ips, cidrs, enabled: true })
        .then((res) => setPreview(res.yaml))
        .catch(() => setPreview(""));
    }, 300);
    return () => clearTimeout(handle);
  }, [name, reason, description, ipsText, cidrsText]);

  async function handleSave() {
    const offendingIp = ips.find(badIp);
    if (offendingIp) {
      setError(`'${offendingIp}' is not a valid IP address.`);
      return;
    }
    const offendingCidr = cidrs.find(badCidr);
    if (offendingCidr) {
      setError(`'${offendingCidr}' is not a valid CIDR range.`);
      return;
    }
    if (!ips.length && !cidrs.length) {
      setError("A whitelist needs at least one IP address or CIDR range.");
      return;
    }
    setError(null);
    await onSubmit({ name, reason, description, ips, cidrs, enabled: true });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{whitelist ? "Edit whitelist" : "Add whitelist"}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="wl-name">Name</Label>
            <Input id="wl-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="wl-reason">Reason</Label>
            <Input
              id="wl-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <p className="text-muted-foreground text-xs">
              Shown in CrowdSec&apos;s own logs when this whitelist matches.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="wl-description">Description</Label>
            <Input
              id="wl-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="wl-ips">IP addresses</Label>
            <Textarea
              id="wl-ips"
              rows={3}
              value={ipsText}
              onChange={(e) => setIpsText(e.target.value)}
            />
            <p className="text-muted-foreground text-xs">One per line.</p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="wl-cidrs">CIDR ranges</Label>
            <Textarea
              id="wl-cidrs"
              rows={3}
              value={cidrsText}
              onChange={(e) => setCidrsText(e.target.value)}
            />
            <p className="text-muted-foreground text-xs">One per line.</p>
          </div>

          {error ? <p className="text-destructive text-sm">{error}</p> : null}

          {preview ? (
            <div className="space-y-1.5">
              <Label>Rendered YAML</Label>
              <pre className="bg-muted overflow-x-auto rounded-md p-3 text-xs">
                {preview}
              </pre>
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

`previewWhitelist` is the generated client function for `POST /crowdsec/whitelists/preview`; use whatever name `npm run gen:api` produced in Task 7 and adjust the import.

- [ ] **Step 4: Wire it into the tab**

Add create/edit state to `security-view.tsx`, an "Add whitelist" button above the table, and pass `onEdit` from the table into the dialog.

- [ ] **Step 5: Run tests, lint and typecheck**

```bash
cd frontend && npx vitest run && npx eslint src && npx tsc --noEmit
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/security
git commit -m "feat(ui): whitelist dialog with server-rendered YAML preview"
```

---

### Task 10: Documentation and end-to-end verification

**Files:**
- Modify: `docs/crowdsec.md`

- [ ] **Step 1: Document the feature**

Add a "Whitelists" section to `docs/crowdsec.md` covering: where the file lives and how it is mounted; that it is written in place because the mount is inode-pinned; that applying restarts CrowdSec and why that briefly fails closed; `CROWDSEC_CONTROL_NODE_ID` and `CROWDSEC_CONTAINER_NAME`; and the rollback behaviour.

- [ ] **Step 2: Run the whole backend suite**

Run the full containerised command from Global Constraints (ruff + pytest). Expected: green, no new warnings.

- [ ] **Step 3: Run the whole frontend suite**

```bash
cd frontend && npx vitest run && npx eslint src && npx tsc --noEmit
```

- [ ] **Step 4: Verify against a live stack**

```bash
docker compose -f docker-compose.ha.yml up -d
# create a whitelist through the UI, then:
docker compose exec crowdsec cat /etc/crowdsec/parsers/s02-enrich/99-megoopm-whitelist.yaml
docker compose exec crowdsec cscli parsers list | grep -i megoopm
```
Expected: the rendered document is present and CrowdSec lists the parser. Then confirm the Security page's status banner shows no error, and that saving the same whitelist again does **not** restart the container (`docker ps` uptime unchanged).

- [ ] **Step 5: Check line endings across everything touched**

```bash
git ls-files --eol $(git diff --name-only main...HEAD)
```
Expected: every row `w/lf`.

- [ ] **Step 6: Commit and merge**

```bash
git add docs/crowdsec.md && git commit -m "docs(crowdsec): whitelist authoring, reload and rollback"
git checkout main && git merge --ff-only feat/crowdsec-whitelists
git push origin main && git push origin --delete feat/crowdsec-whitelists && git branch -d feat/crowdsec-whitelists
```

---

## Verification checklist

- [ ] Migration 0016 applies and rolls back cleanly.
- [ ] A whitelist with no IPs and no CIDRs is rejected by both the API (422) and the database (constraint).
- [ ] Two names that slugify identically produce a 409.
- [ ] The rendered file parses as multi-document YAML and CrowdSec lists the parser.
- [ ] Saving with no change does not restart the container.
- [ ] A reason containing `:` and `#` survives a render/parse round trip.
- [ ] The file's inode is unchanged after a write **and** after a rollback.
- [ ] A CrowdSec that fails to come back leaves the previous file on disk and `ok=false` in the status banner.
- [ ] `CROWDSEC_CONTROL_NODE_ID` unset surfaces "reload not configured" rather than saving silently.
- [ ] The docker socket is mounted on `worker` and **not** on `backend`.
