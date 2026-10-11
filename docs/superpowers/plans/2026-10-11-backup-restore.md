# Backup & Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin can take, schedule, download, upload and restore an encrypted copy of the whole instance — database plus TLS material — from a Backups page in the sidebar.

**Architecture:** A `.mgb` file is a streamed AES-256-GCM envelope (key derived from `SECRET_KEY`) around a tar of `meta.json`, a `pg_dump -Fc` dump, the certificates tree and the CrowdSec files. The backup directory on the host *is* the list — no table of backups. Celery does the work (manual, hourly-tick schedule, and restore) under a Redis lock; during a restore a middleware answers 503 everywhere but the status route so nothing writes to the database mid-restore.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, Celery + Redis, `cryptography` (HKDF + AESGCM), Postgres 16 client binaries (`pg_dump`/`pg_restore`), Next.js + vitest.

**Spec:** `docs/superpowers/specs/2026-10-11-backup-restore-design.md`

## Global Constraints

- File name: `megoopm-YYYYMMDD-HHMMSS-<trigger>.mgb`, UTC, `<trigger>` ∈ `manual` | `auto` | `upload`. Validated everywhere with `^megoopm-\d{8}-\d{6}-(manual|auto|upload)\.mgb$`.
- Envelope: magic `b"MEGOOPMBK"`, format version `1`, 16-byte salt, 8-byte key fingerprint, 8-byte nonce prefix — 42 clear bytes — then length-prefixed AES-256-GCM chunks of 1 MiB plaintext, ended by a 4-byte zero terminator.
- Key: `HKDF-SHA256(secret_key, salt, info=b"megoopm-backup-v1")` → 32 bytes. Fingerprint: first 8 bytes of `sha256(b"megoopm-backup-v1" + secret_key)`, hex.
- Archive members, in this order: `meta.json`, `db.dump`, `certs/…`, `crowdsec/…`.
- Env: `BACKUP_PATH` (host dir, default `./backups`) bind-mounted to `/backups`; backend settings `backup_dir: str = "/backups"`, `backup_max_bytes: int = 4 * 1024**3`.
- Retention sweeps `-auto` files only, keeping the newest `backup_keep`.
- Every route is admin-only and must be declared in `backend/tests/test_route_authorization.py`.
- Backups never run concurrently with each other or with a restore: Redis lock `megoopm:backup`.
- Backend tests run in the throwaway container: `export MSYS_NO_PATHCONV=1` then `docker exec -w /src megoopm-test python -m pytest …`. Tests that read repo files outside `backend/` run with the repo mounted: `docker run --rm -v C:/Projects/megoopm:/repo -w /repo/backend --entrypoint sh megoopm-backend -c 'pip install -q pytest >/dev/null 2>&1; python -m pytest <file> --noconftest -p no:cacheprovider -o addopts=""'`.
- Never run `alembic upgrade head` against `megoopm-testdb` by hand; migration tests use the throwaway-schema harness.
- Python files written from Windows: `sed -i 's/\r$//' <file>` before committing. Frontend prettier: format a touched file only at the width its `HEAD` version is already clean at; never reformat `src/lib/api/generated/schema.ts`.
- Commits end with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## Review Focus

Five things the spec implies, each pinned by a test in the task that owns the code:

1. **A file whose header is valid but whose body is not a MegooPM archive** (wrong tar, missing `db.dump`) must be refused *before* the database is touched — Task 6 verifies then restores, with a test that a junk archive leaves the database intact.
2. **A backup interrupted half-written** (disk full, worker killed) must never appear in the list or be restorable — Task 3 writes `.part` and renames; a test asserts a `.part` file is not listed.
3. **An upload larger than the cap, or with a name that escapes the directory** must be rejected without anything landing in `backup_dir` — Task 8 tests both.
4. **A second restore, or a backup during a restore** must be refused rather than interleaved — Task 7 tests the lock and the gate together.
5. **A backup made with a different `SECRET_KEY`** must be visible but un-restorable, with the reason shown — Task 1 (fingerprint), Task 3 (the listing says so), Task 6 (the restore refuses).

---

### Task 1: The encrypted envelope

**Files:**
- Create: `backend/app/services/backup/__init__.py`
- Create: `backend/app/services/backup/crypto.py`
- Test: `backend/tests/test_backup_crypto.py`

**Interfaces:**
- Produces:
  - `key_fingerprint(secret_key: str) -> str` (16 hex chars)
  - `Header` dataclass: `version: int`, `salt: bytes`, `fingerprint: str`, `nonce_prefix: bytes`, `raw: bytes`
  - `encrypt_stream(src: BinaryIO, dst: BinaryIO, *, secret_key: str) -> None`
  - `decrypt_stream(src: BinaryIO, dst: BinaryIO, *, secret_key: str) -> Header`
  - `read_header(src: BinaryIO) -> Header`
  - `decrypt_first_chunk(src: BinaryIO, *, secret_key: str) -> bytes`
  - `BackupFormatError`, `BackupKeyError` (both `RuntimeError`)
  - `CHUNK_BYTES = 1024 * 1024`, `HEADER_BYTES = 42`

- [ ] **Step 1: Write the failing tests**

```python
"""The .mgb envelope: streamed AES-256-GCM under a key derived from SECRET_KEY.

Streaming matters: a backup is gigabytes, and a design that holds the plaintext
in memory would fail on the machines this product runs on. Everything here
therefore works on file objects, and every test writes through them.
"""

from __future__ import annotations

import io

import pytest
from app.services.backup.crypto import (
    CHUNK_BYTES,
    BackupFormatError,
    BackupKeyError,
    decrypt_first_chunk,
    decrypt_stream,
    encrypt_stream,
    key_fingerprint,
    read_header,
)

KEY = "a-secret-key"
OTHER = "a-different-secret-key"


def _sealed(payload: bytes, *, secret_key: str = KEY) -> bytes:
    out = io.BytesIO()
    encrypt_stream(io.BytesIO(payload), out, secret_key=secret_key)
    return out.getvalue()


def _opened(sealed: bytes, *, secret_key: str = KEY) -> bytes:
    out = io.BytesIO()
    decrypt_stream(io.BytesIO(sealed), out, secret_key=secret_key)
    return out.getvalue()


def test_a_payload_comes_back_byte_for_byte() -> None:
    payload = b"proxy hosts, certificates, users" * 10
    assert _opened(_sealed(payload)) == payload


def test_a_payload_larger_than_one_chunk_round_trips() -> None:
    # The chunking is the whole design; one-chunk tests would not exercise it.
    payload = bytes(range(256)) * (CHUNK_BYTES // 128)
    assert _opened(_sealed(payload)) == payload


def test_an_empty_payload_round_trips() -> None:
    assert _opened(_sealed(b"")) == b""


def test_another_key_cannot_open_it() -> None:
    with pytest.raises(BackupKeyError):
        _opened(_sealed(b"secrets"), secret_key=OTHER)


def test_a_truncated_file_is_refused_rather_than_half_restored() -> None:
    # Half a backup restored is worse than no backup: the terminator is what
    # tells a reader the file is whole.
    sealed = _sealed(b"x" * (CHUNK_BYTES * 2))
    with pytest.raises(BackupFormatError):
        _opened(sealed[: len(sealed) - 8])


def test_a_flipped_byte_is_refused() -> None:
    sealed = bytearray(_sealed(b"payload"))
    sealed[-10] ^= 0x01
    with pytest.raises(BackupKeyError):
        _opened(bytes(sealed))


def test_a_foreign_file_is_refused_by_its_magic() -> None:
    with pytest.raises(BackupFormatError):
        _opened(b"not a megoopm backup at all" * 4)


def test_the_header_says_which_key_made_it_without_opening_the_file() -> None:
    # The page warns before an admin tries to restore something unreadable.
    header = read_header(io.BytesIO(_sealed(b"payload")))
    assert header.version == 1
    assert header.fingerprint == key_fingerprint(KEY)
    assert header.fingerprint != key_fingerprint(OTHER)


def test_the_first_chunk_can_be_read_alone() -> None:
    # Listing reads meta.json (the tar's first member) without decrypting GBs.
    payload = b"meta first" + b"\0" * (CHUNK_BYTES * 2)
    assert decrypt_first_chunk(io.BytesIO(_sealed(payload)), secret_key=KEY).startswith(b"meta first")


def test_each_file_uses_a_fresh_salt_and_nonce() -> None:
    # Reusing a nonce with the same key breaks GCM outright.
    first, second = read_header(io.BytesIO(_sealed(b"x"))), read_header(io.BytesIO(_sealed(b"x")))
    assert first.salt != second.salt
    assert first.nonce_prefix != second.nonce_prefix
```

- [ ] **Step 2: Run them and watch them fail**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_crypto.py`
Expected: collection error — `ModuleNotFoundError: No module named 'app.services.backup'`.

- [ ] **Step 3: Write the module**

`backend/app/services/backup/__init__.py` is empty. `backend/app/services/backup/crypto.py`:

```python
"""The .mgb envelope: streamed AES-256-GCM under a key derived from SECRET_KEY.

A backup is gigabytes, so nothing here holds a payload in memory: callers pass
file objects and the data moves in 1 MiB chunks, each sealed on its own. The
clear header carries what a reader needs *before* it can decrypt anything —
the format version, the salt, and a fingerprint of the key that made the file,
so the UI can say "this was made with a different SECRET_KEY" instead of
failing in the middle of a restore.

The terminator is load bearing. Without it a file cut short by a full disk
would decrypt cleanly up to the cut and look like a complete, shorter backup.
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"MEGOOPMBK"
FORMAT_VERSION = 1
SALT_BYTES = 16
FINGERPRINT_BYTES = 8
NONCE_PREFIX_BYTES = 8
HEADER_BYTES = len(MAGIC) + 1 + SALT_BYTES + FINGERPRINT_BYTES + NONCE_PREFIX_BYTES  # 42
CHUNK_BYTES = 1024 * 1024
_INFO = b"megoopm-backup-v1"
_TERMINATOR = b"\x00\x00\x00\x00"


class BackupFormatError(RuntimeError):
    """The file is not a MegooPM backup, or is incomplete."""


class BackupKeyError(RuntimeError):
    """The file cannot be opened with this instance's SECRET_KEY."""


@dataclass(frozen=True, slots=True)
class Header:
    version: int
    salt: bytes
    fingerprint: str
    nonce_prefix: bytes
    #: The header bytes verbatim; used as AEAD associated data.
    raw: bytes


def key_fingerprint(secret_key: str) -> str:
    """Which key made (or can open) a file, without revealing the key."""
    digest = hashlib.sha256(_INFO + secret_key.encode("utf-8")).digest()
    return digest[:FINGERPRINT_BYTES].hex()


def _derive(secret_key: str, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=_INFO).derive(
        secret_key.encode("utf-8")
    )


def read_header(src: BinaryIO) -> Header:
    raw = src.read(HEADER_BYTES)
    if len(raw) < HEADER_BYTES or not raw.startswith(MAGIC):
        raise BackupFormatError("not a MegooPM backup file")
    version = raw[len(MAGIC)]
    if version != FORMAT_VERSION:
        raise BackupFormatError(f"unsupported backup format version {version}")
    offset = len(MAGIC) + 1
    salt = raw[offset : offset + SALT_BYTES]
    offset += SALT_BYTES
    fingerprint = raw[offset : offset + FINGERPRINT_BYTES].hex()
    offset += FINGERPRINT_BYTES
    nonce_prefix = raw[offset : offset + NONCE_PREFIX_BYTES]
    return Header(
        version=version, salt=salt, fingerprint=fingerprint, nonce_prefix=nonce_prefix, raw=raw
    )


def encrypt_stream(src: BinaryIO, dst: BinaryIO, *, secret_key: str) -> None:
    salt = os.urandom(SALT_BYTES)
    nonce_prefix = os.urandom(NONCE_PREFIX_BYTES)
    header = (
        MAGIC
        + bytes([FORMAT_VERSION])
        + salt
        + bytes.fromhex(key_fingerprint(secret_key))
        + nonce_prefix
    )
    dst.write(header)

    aead = AESGCM(_derive(secret_key, salt))
    counter = 0
    while True:
        plain = src.read(CHUNK_BYTES)
        if not plain:
            break
        sealed = aead.encrypt(nonce_prefix + struct.pack(">I", counter), plain, header)
        dst.write(struct.pack(">I", len(sealed)))
        dst.write(sealed)
        counter += 1
    dst.write(_TERMINATOR)


def _chunks(src: BinaryIO, header: Header, aead: AESGCM):
    counter = 0
    while True:
        length_bytes = src.read(4)
        if len(length_bytes) < 4:
            raise BackupFormatError("backup file ends mid-chunk")
        (length,) = struct.unpack(">I", length_bytes)
        if length == 0:
            return
        sealed = src.read(length)
        if len(sealed) < length:
            raise BackupFormatError("backup file ends mid-chunk")
        try:
            yield aead.decrypt(
                header.nonce_prefix + struct.pack(">I", counter), sealed, header.raw
            )
        except InvalidTag as exc:
            raise BackupKeyError("wrong SECRET_KEY, or the backup is damaged") from exc
        counter += 1


def decrypt_stream(src: BinaryIO, dst: BinaryIO, *, secret_key: str) -> Header:
    header = read_header(src)
    aead = AESGCM(_derive(secret_key, header.salt))
    for plain in _chunks(src, header, aead):
        dst.write(plain)
    return header


def decrypt_first_chunk(src: BinaryIO, *, secret_key: str) -> bytes:
    """Open only the first chunk — enough for the archive's meta.json member."""
    header = read_header(src)
    aead = AESGCM(_derive(secret_key, header.salt))
    for plain in _chunks(src, header, aead):
        return plain
    return b""
```

- [ ] **Step 4: Run the tests**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_crypto.py`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
sed -i 's/\r$//' backend/app/services/backup/*.py backend/tests/test_backup_crypto.py
docker exec -w /src megoopm-test ruff format app/services/backup tests/test_backup_crypto.py
docker exec -w /src megoopm-test ruff check app/services/backup tests/test_backup_crypto.py
git add backend/app/services/backup backend/tests/test_backup_crypto.py
git commit -m "feat(backup): streamed AES-256-GCM envelope for backup files"
```

---

### Task 2: The archive inside

**Files:**
- Create: `backend/app/services/backup/archive.py`
- Test: `backend/tests/test_backup_archive.py`

**Interfaces:**
- Produces:
  - `BackupMeta` dataclass: `created_at: datetime`, `app_version: str`, `alembic_revision: str`, `trigger: str`, `db_bytes: int`, `cert_bytes: int`; `to_json() -> bytes`, `from_json(raw: bytes) -> BackupMeta`
  - `build_archive(dst: BinaryIO, *, meta: BackupMeta, db_dump: Path, certs_dir: Path | None, crowdsec_files: dict[str, Path]) -> None`
  - `extract_archive(src: BinaryIO, dest: Path) -> BackupMeta`
  - `meta_from_first_chunk(chunk: bytes) -> BackupMeta | None`
  - `MEMBER_META = "meta.json"`, `MEMBER_DB = "db.dump"`, `CERTS_PREFIX = "certs/"`, `CROWDSEC_PREFIX = "crowdsec/"`
  - `BackupArchiveError(RuntimeError)`

- [ ] **Step 1: Write the failing tests**

```python
"""The tar inside the envelope: meta first, then the dump, certs and CrowdSec.

meta.json is written first on purpose — the backup list reads it out of the
first decrypted chunk, without unpacking gigabytes.
"""

from __future__ import annotations

import io
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.services.backup.archive import (
    MEMBER_DB,
    MEMBER_META,
    BackupArchiveError,
    BackupMeta,
    build_archive,
    extract_archive,
    meta_from_first_chunk,
)

META = BackupMeta(
    created_at=datetime(2026, 10, 11, 3, 0, tzinfo=UTC),
    app_version="0.1.0",
    alembic_revision="0039_backups",
    trigger="auto",
    db_bytes=12,
    cert_bytes=3,
)


def _source(tmp_path: Path) -> tuple[Path, Path, dict[str, Path]]:
    dump = tmp_path / "db.dump"
    dump.write_bytes(b"PGDMP-pretend")
    certs = tmp_path / "certs"
    (certs / "7").mkdir(parents=True)
    (certs / "7" / "privkey.pem").write_text("key")
    (certs / "7" / "privkey.pem").chmod(0o600)
    whitelist = tmp_path / "whitelists.yaml"
    whitelist.write_text("# none")
    return dump, certs, {"whitelists.yaml": whitelist}


def _built(tmp_path: Path) -> io.BytesIO:
    dump, certs, crowdsec = _source(tmp_path)
    out = io.BytesIO()
    build_archive(out, meta=META, db_dump=dump, certs_dir=certs, crowdsec_files=crowdsec)
    out.seek(0)
    return out


def test_every_part_of_the_instance_is_in_the_archive(tmp_path: Path) -> None:
    with tarfile.open(fileobj=_built(tmp_path), mode="r:") as tar:
        names = tar.getnames()
    assert names[0] == MEMBER_META  # first, so the list can read it cheaply
    assert MEMBER_DB in names
    assert "certs/7/privkey.pem" in names
    assert "crowdsec/whitelists.yaml" in names


def test_unpacking_restores_the_files_and_their_modes(tmp_path: Path) -> None:
    # A private key that comes back world-readable is a quiet security defect.
    dest = tmp_path / "out"
    meta = extract_archive(_built(tmp_path), dest)
    assert meta == META
    assert (dest / "certs" / "7" / "privkey.pem").read_text() == "key"
    assert (dest / "certs" / "7" / "privkey.pem").stat().st_mode & 0o777 == 0o600
    assert (dest / MEMBER_DB).read_bytes() == b"PGDMP-pretend"


def test_the_list_reads_meta_from_the_first_chunk(tmp_path: Path) -> None:
    assert meta_from_first_chunk(_built(tmp_path).read(1024 * 1024)) == META


def test_an_archive_without_a_dump_is_refused(tmp_path: Path) -> None:
    # Caught before a restore drops anything: a tar missing db.dump would
    # otherwise empty the database and have nothing to put back.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        raw = META.to_json()
        info = tarfile.TarInfo(MEMBER_META)
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    buf.seek(0)
    with pytest.raises(BackupArchiveError):
        extract_archive(buf, tmp_path / "out")


def test_a_member_pointing_outside_the_destination_is_refused(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in (MEMBER_META, MEMBER_DB, "../escaped"):
            raw = META.to_json() if name == MEMBER_META else b"x"
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    buf.seek(0)
    with pytest.raises(BackupArchiveError):
        extract_archive(buf, tmp_path / "out")


def test_garbage_is_refused_rather_than_unpacked(tmp_path: Path) -> None:
    with pytest.raises(BackupArchiveError):
        extract_archive(io.BytesIO(b"this is not a tar"), tmp_path / "out")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_archive.py`
Expected: `ModuleNotFoundError: No module named 'app.services.backup.archive'`.

- [ ] **Step 3: Write the module**

```python
"""The tar inside the envelope.

Order matters: meta.json is written first so the backup list can read it from
the first decrypted chunk instead of unpacking the whole archive. Everything
else is the state that cannot be regenerated — the dump, the certificates and
the CrowdSec files the UI authors.

Extraction is deliberately strict. A restore is about to replace a live
instance, so an archive missing its dump, or carrying a member that points
outside the destination, is refused before anything is written.
"""

from __future__ import annotations

import io
import json
import tarfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

MEMBER_META = "meta.json"
MEMBER_DB = "db.dump"
CERTS_PREFIX = "certs/"
CROWDSEC_PREFIX = "crowdsec/"


class BackupArchiveError(RuntimeError):
    """The archive is not a MegooPM backup, or is missing a required member."""


@dataclass(frozen=True, slots=True)
class BackupMeta:
    created_at: datetime
    app_version: str
    alembic_revision: str
    trigger: str
    db_bytes: int
    cert_bytes: int

    def to_json(self) -> bytes:
        payload = asdict(self) | {"created_at": self.created_at.isoformat()}
        return json.dumps(payload, sort_keys=True).encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes) -> BackupMeta:
        try:
            payload = json.loads(raw)
            created = datetime.fromisoformat(payload["created_at"])
            return cls(
                created_at=created if created.tzinfo else created.replace(tzinfo=UTC),
                app_version=payload["app_version"],
                alembic_revision=payload["alembic_revision"],
                trigger=payload["trigger"],
                db_bytes=int(payload["db_bytes"]),
                cert_bytes=int(payload["cert_bytes"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise BackupArchiveError(f"unreadable {MEMBER_META}: {exc}") from exc


def build_archive(
    dst: BinaryIO,
    *,
    meta: BackupMeta,
    db_dump: Path,
    certs_dir: Path | None,
    crowdsec_files: dict[str, Path],
) -> None:
    with tarfile.open(fileobj=dst, mode="w|") as tar:
        raw = meta.to_json()
        info = tarfile.TarInfo(MEMBER_META)
        info.size = len(raw)
        info.mtime = int(meta.created_at.timestamp())
        tar.addfile(info, io.BytesIO(raw))

        tar.add(db_dump, arcname=MEMBER_DB)
        if certs_dir is not None and certs_dir.is_dir():
            tar.add(certs_dir, arcname=CERTS_PREFIX.rstrip("/"))
        for name, path in sorted(crowdsec_files.items()):
            if path.is_file():
                tar.add(path, arcname=f"{CROWDSEC_PREFIX}{name}")


def extract_archive(src: BinaryIO, dest: Path) -> BackupMeta:
    dest.mkdir(parents=True, exist_ok=True)
    meta: BackupMeta | None = None
    seen_dump = False
    try:
        with tarfile.open(fileobj=src, mode="r|*") as tar:
            for member in tar:
                target = (dest / member.name).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise BackupArchiveError(f"archive member escapes the destination: {member.name}")
                if member.name == MEMBER_META:
                    extracted = tar.extractfile(member)
                    meta = BackupMeta.from_json(extracted.read() if extracted else b"")
                    continue
                if member.name == MEMBER_DB:
                    seen_dump = True
                # filter="data" drops device nodes, links and setuid bits.
                tar.extract(member, dest, filter="data")
    except tarfile.TarError as exc:
        raise BackupArchiveError(f"unreadable archive: {exc}") from exc

    if meta is None:
        raise BackupArchiveError(f"archive has no {MEMBER_META}")
    if not seen_dump:
        raise BackupArchiveError(f"archive has no {MEMBER_DB}")
    return meta


def meta_from_first_chunk(chunk: bytes) -> BackupMeta | None:
    """Read meta.json out of the archive's opening bytes, or None if not there."""
    try:
        with tarfile.open(fileobj=io.BytesIO(chunk), mode="r|*") as tar:
            for member in tar:
                if member.name != MEMBER_META:
                    return None
                extracted = tar.extractfile(member)
                return BackupMeta.from_json(extracted.read() if extracted else b"")
    except (tarfile.TarError, BackupArchiveError):
        return None
    return None
```

- [ ] **Step 4: Run the tests**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_archive.py`
Expected: 6 passed. If `extract_archive` rejects the escaping member only via `filter="data"`'s own error, keep the explicit check — the test asserts `BackupArchiveError`, not `tarfile`'s.

- [ ] **Step 5: Commit**

```bash
sed -i 's/\r$//' backend/app/services/backup/archive.py backend/tests/test_backup_archive.py
docker exec -w /src megoopm-test ruff format app/services/backup tests/test_backup_archive.py
docker exec -w /src megoopm-test ruff check app/services/backup tests/test_backup_archive.py
git add backend/app/services/backup/archive.py backend/tests/test_backup_archive.py
git commit -m "feat(backup): the archive inside a backup, meta first"
```

---

### Task 3: The directory as the list — names, listing, retention, space

**Files:**
- Create: `backend/app/services/backup/store.py`
- Test: `backend/tests/test_backup_store.py`

**Interfaces:**
- Consumes: `crypto.read_header`, `crypto.decrypt_first_chunk`, `crypto.key_fingerprint`, `crypto.BackupFormatError`, `crypto.BackupKeyError`, `archive.meta_from_first_chunk`, `archive.BackupMeta`.
- Produces:
  - `NAME_RE: re.Pattern`, `TRIGGERS = ("manual", "auto", "upload")`
  - `new_name(now: datetime, trigger: str) -> str`
  - `safe_path(backup_dir: Path, name: str) -> Path` — raises `UnknownBackupError`
  - `BackupFile` dataclass: `name: str`, `size_bytes: int`, `created_at: datetime`, `trigger: str`, `key_matches: bool`, `app_version: str | None`, `alembic_revision: str | None`
  - `list_backups(backup_dir: Path, *, secret_key: str) -> list[BackupFile]` (newest first)
  - `describe(path: Path, *, secret_key: str) -> BackupFile`
  - `apply_retention(backup_dir: Path, *, keep: int) -> list[str]` (names deleted)
  - `free_bytes(backup_dir: Path) -> int`
  - `UnknownBackupError(RuntimeError)`
  - `PART_SUFFIX = ".part"`

- [ ] **Step 1: Write the failing tests**

```python
"""The backup directory is the list: no table, no drift.

A file an operator copies in appears; one they delete disappears. Which means
every fact the UI shows has to come from the file itself — its name, its stat,
and (when the key matches) the meta.json in its first chunk.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.services.backup.archive import BackupMeta, build_archive
from app.services.backup.crypto import encrypt_stream
from app.services.backup.store import (
    PART_SUFFIX,
    UnknownBackupError,
    apply_retention,
    free_bytes,
    list_backups,
    new_name,
    safe_path,
)

KEY = "a-secret-key"


def _write_backup(backup_dir: Path, name: str, *, secret_key: str = KEY) -> Path:
    dump = backup_dir / "tmp.dump"
    dump.write_bytes(b"PGDMP-pretend")
    meta = BackupMeta(
        created_at=datetime(2026, 10, 11, 3, 0, tzinfo=UTC),
        app_version="0.1.0",
        alembic_revision="0039_backups",
        trigger=name.rsplit("-", 1)[1].removesuffix(".mgb"),
        db_bytes=13,
        cert_bytes=0,
    )
    plain = io.BytesIO()
    build_archive(plain, meta=meta, db_dump=dump, certs_dir=None, crowdsec_files={})
    plain.seek(0)
    dump.unlink()
    path = backup_dir / name
    with path.open("wb") as out:
        encrypt_stream(plain, out, secret_key=secret_key)
    return path


def test_a_name_carries_the_time_and_how_it_was_made() -> None:
    assert new_name(datetime(2026, 10, 11, 3, 0, 5, tzinfo=UTC), "auto") == (
        "megoopm-20261011-030005-auto.mgb"
    )


@pytest.mark.parametrize(
    "name",
    ["../../etc/passwd", "megoopm-20261011-030005-auto.mgb/../x", "notes.txt", "megoopm-bad.mgb"],
)
def test_a_name_that_is_not_ours_is_refused(tmp_path: Path, name: str) -> None:
    # Every route takes a name from the URL; this is what keeps it in the box.
    with pytest.raises(UnknownBackupError):
        safe_path(tmp_path, name)


def test_listing_reads_each_file_and_puts_the_newest_first(tmp_path: Path) -> None:
    _write_backup(tmp_path, "megoopm-20261010-030000-auto.mgb")
    _write_backup(tmp_path, "megoopm-20261011-030000-manual.mgb")

    rows = list_backups(tmp_path, secret_key=KEY)

    assert [r.name for r in rows] == [
        "megoopm-20261011-030000-manual.mgb",
        "megoopm-20261010-030000-auto.mgb",
    ]
    assert rows[0].trigger == "manual"
    assert rows[0].key_matches is True
    assert rows[0].app_version == "0.1.0"
    assert rows[0].size_bytes > 0


def test_a_backup_from_another_instance_is_listed_but_marked(tmp_path: Path) -> None:
    # Visible, because an admin has to be able to see and delete it; marked,
    # because restoring it would fail and its secrets would be unreadable.
    _write_backup(tmp_path, "megoopm-20261011-030000-upload.mgb", secret_key="another-key")

    (row,) = list_backups(tmp_path, secret_key=KEY)

    assert row.key_matches is False
    assert row.app_version is None


def test_a_half_written_backup_is_not_offered(tmp_path: Path) -> None:
    # A disk that filled mid-backup leaves a .part file. Restoring it would
    # restore a truncated database.
    _write_backup(tmp_path, f"megoopm-20261011-030000-auto.mgb{PART_SUFFIX}")
    (tmp_path / "readme.txt").write_text("not a backup")

    assert list_backups(tmp_path, secret_key=KEY) == []


def test_retention_keeps_the_newest_automatic_backups(tmp_path: Path) -> None:
    for day in (9, 10, 11):
        _write_backup(tmp_path, f"megoopm-202610{day:02d}-030000-auto.mgb")

    deleted = apply_retention(tmp_path, keep=2)

    assert deleted == ["megoopm-20261009-030000-auto.mgb"]
    assert not (tmp_path / "megoopm-20261009-030000-auto.mgb").exists()


def test_retention_never_touches_a_manual_backup_or_an_upload(tmp_path: Path) -> None:
    # The copy an admin took before a risky change must not expire overnight.
    _write_backup(tmp_path, "megoopm-20261001-030000-manual.mgb")
    _write_backup(tmp_path, "megoopm-20261002-030000-upload.mgb")
    _write_backup(tmp_path, "megoopm-20261011-030000-auto.mgb")

    assert apply_retention(tmp_path, keep=1) == []
    assert len(list(tmp_path.glob("*.mgb"))) == 3


def test_free_space_is_reported_for_the_backup_directory(tmp_path: Path) -> None:
    assert free_bytes(tmp_path) > 0
```

- [ ] **Step 2: Run them and watch them fail**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_store.py`
Expected: `ModuleNotFoundError: No module named 'app.services.backup.store'`.

- [ ] **Step 3: Write the module**

```python
"""The backup directory, treated as the source of truth.

No table of backups: the page lists what is on disk, so a file copied in by
hand appears and a file deleted on the host disappears. Everything the list
shows therefore comes out of the file — its name (time and trigger), its stat
(size), and, when the key matches, the meta.json in its first chunk.

Writes land on `<name>.part` and are renamed, so a backup interrupted by a full
disk is never offered for restore.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.services.backup.archive import meta_from_first_chunk
from app.services.backup.crypto import (
    BackupFormatError,
    BackupKeyError,
    decrypt_first_chunk,
    key_fingerprint,
    read_header,
)

TRIGGERS = ("manual", "auto", "upload")
NAME_RE = re.compile(r"^megoopm-\d{8}-\d{6}-(manual|auto|upload)\.mgb$")
PART_SUFFIX = ".part"


class UnknownBackupError(RuntimeError):
    """No backup by that name (or the name is not one we would ever write)."""


@dataclass(frozen=True, slots=True)
class BackupFile:
    name: str
    size_bytes: int
    created_at: datetime
    trigger: str
    key_matches: bool
    app_version: str | None
    alembic_revision: str | None


def new_name(now: datetime, trigger: str) -> str:
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown trigger {trigger!r}")
    return f"megoopm-{now.astimezone(UTC):%Y%m%d-%H%M%S}-{trigger}.mgb"


def safe_path(backup_dir: Path, name: str) -> Path:
    """Resolve a name from a request, or refuse it. Never escapes the directory."""
    if not NAME_RE.match(name):
        raise UnknownBackupError(f"no backup named {name!r}")
    path = backup_dir / name
    if not path.is_file():
        raise UnknownBackupError(f"no backup named {name!r}")
    return path


def _created_at(name: str) -> datetime:
    return datetime.strptime(name[8:23], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)


def describe(path: Path, *, secret_key: str) -> BackupFile:
    name = path.name
    trigger = name.rsplit("-", 1)[1].removesuffix(".mgb")
    key_matches = False
    app_version: str | None = None
    revision: str | None = None
    try:
        with path.open("rb") as handle:
            header = read_header(handle)
            key_matches = header.fingerprint == key_fingerprint(secret_key)
            if key_matches:
                handle.seek(0)
                meta = meta_from_first_chunk(decrypt_first_chunk(handle, secret_key=secret_key))
                if meta is not None:
                    app_version = meta.app_version
                    revision = meta.alembic_revision
    except (OSError, BackupFormatError, BackupKeyError):
        # A damaged file still belongs in the list: an admin has to see it to
        # delete it. It simply offers nothing beyond its name and size.
        pass
    return BackupFile(
        name=name,
        size_bytes=path.stat().st_size,
        created_at=_created_at(name),
        trigger=trigger,
        key_matches=key_matches,
        app_version=app_version,
        alembic_revision=revision,
    )


def list_backups(backup_dir: Path, *, secret_key: str) -> list[BackupFile]:
    if not backup_dir.is_dir():
        return []
    rows = [
        describe(path, secret_key=secret_key)
        for path in backup_dir.iterdir()
        if path.is_file() and NAME_RE.match(path.name)
    ]
    return sorted(rows, key=lambda row: row.name, reverse=True)


def apply_retention(backup_dir: Path, *, keep: int) -> list[str]:
    """Delete all but the newest ``keep`` automatic backups. Returns their names."""
    automatic = sorted(
        (p for p in backup_dir.glob("*-auto.mgb") if NAME_RE.match(p.name)),
        key=lambda p: p.name,
        reverse=True,
    )
    deleted: list[str] = []
    for path in automatic[max(keep, 0) :]:
        path.unlink(missing_ok=True)
        deleted.append(path.name)
    return deleted


def free_bytes(backup_dir: Path) -> int:
    return shutil.disk_usage(backup_dir).free
```

- [ ] **Step 4: Run the tests**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_store.py`
Expected: 9 passed (two come from the parametrized name test).

- [ ] **Step 5: Commit**

```bash
sed -i 's/\r$//' backend/app/services/backup/store.py backend/tests/test_backup_store.py
docker exec -w /src megoopm-test ruff format app/services/backup tests/test_backup_store.py
docker exec -w /src megoopm-test ruff check app/services/backup tests/test_backup_store.py
git add backend/app/services/backup/store.py backend/tests/test_backup_store.py
git commit -m "feat(backup): list, name and sweep the backup directory"
```

---

### Task 4: Settings, schedule decision, migration 0039

**Files:**
- Create: `backend/alembic/versions/0039_backups.py`
- Create: `backend/tests/test_backup_settings_migration.py`
- Create: `backend/app/services/backup/schedule.py`
- Create: `backend/tests/test_backup_schedule.py`
- Modify: `backend/app/models/enums.py` (add `BackupFrequency`, add `restore` to `AuditAction`)
- Modify: `backend/app/models/instance_settings.py`
- Modify: `backend/app/schemas/instance_settings.py`
- Modify: `backend/app/services/instance_settings.py`
- Modify: `backend/app/api/routes/settings.py`
- Modify: `backend/tests/test_route_authorization.py`, `backend/tests/test_settings_api.py`
- Modify: `backend/app/core/config.py` (`backup_dir`, `backup_max_bytes`)

**Interfaces:**
- Produces:
  - `BackupFrequency` enum: `off`, `daily`, `weekly`
  - `InstanceSettings.backup_frequency|backup_hour_utc|backup_weekday|backup_keep|last_backup_ok|last_backup_at|last_backup_message`
  - `BackupSettingsUpdate` schema: `frequency: BackupFrequency`, `hour_utc: int (0-23)`, `weekday: int (0-6)`, `keep: int (1-365)`
  - `settings_service.update_backup_settings(db, changes: dict) -> InstanceSettings`
  - `schedule.is_backup_due(*, now, frequency, weekday, hour_utc, last_backup_at) -> tuple[bool, str]`
  - `PATCH /api/v1/settings/backups`
  - `settings.backup_dir: str`, `settings.backup_max_bytes: int`

- [ ] **Step 1: Write the failing schedule tests**

`backend/tests/test_backup_schedule.py`:

```python
"""Whether the hourly tick should take a backup.

Pure arithmetic over the settings and the clock, so the task can stay a thin
wrapper and the decision can be tested without Celery, Redis or a database.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.enums import BackupFrequency
from app.services.backup.schedule import is_backup_due

SUNDAY_3AM = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)


def _due(**over) -> tuple[bool, str]:
    kwargs = {
        "now": SUNDAY_3AM,
        "frequency": BackupFrequency.daily,
        "weekday": 6,
        "hour_utc": 3,
        "last_backup_at": None,
    }
    return is_backup_due(**(kwargs | over))


def test_a_daily_backup_runs_in_its_hour() -> None:
    assert _due() == (True, "due")


def test_nothing_runs_when_automatic_backups_are_off() -> None:
    assert _due(frequency=BackupFrequency.off)[0] is False


def test_another_hour_is_not_the_hour() -> None:
    assert _due(now=SUNDAY_3AM.replace(hour=4))[0] is False


def test_a_weekly_backup_waits_for_its_weekday() -> None:
    assert _due(frequency=BackupFrequency.weekly, weekday=6)[0] is True
    assert _due(frequency=BackupFrequency.weekly, weekday=2)[0] is False


def test_it_does_not_run_twice_in_the_same_hour() -> None:
    # Beat can fire a tick twice (a worker restart, a retry); a second backup
    # in the same hour would evict a real one through retention.
    assert _due(last_backup_at=SUNDAY_3AM.replace(minute=10))[0] is False


def test_a_naive_timestamp_is_read_as_utc() -> None:
    # SQLite hands back naive datetimes; treating them as local time would skip
    # or duplicate a run depending on the server's zone.
    assert _due(last_backup_at=SUNDAY_3AM.replace(minute=10, tzinfo=None))[0] is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_schedule.py`
Expected: `ModuleNotFoundError: No module named 'app.services.backup.schedule'` (and `BackupFrequency` missing).

- [ ] **Step 3: Add the enum and the decision**

In `backend/app/models/enums.py`, beside the other enums:

```python
class BackupFrequency(enum.StrEnum):
    """How often an automatic backup runs."""

    off = "off"
    daily = "daily"
    weekly = "weekly"
```

and add `restore = "restore"` to `AuditAction` (after `disable`), with the comment:

```python
    #: Replacing this instance's state from a backup. Its own action because
    #: "update" would hide the most consequential thing an admin can do.
    restore = "restore"
```

`backend/app/services/backup/schedule.py`:

```python
"""Whether the hourly tick should take a backup.

Mirrors services/crowdsec/hub.is_due: beat ticks every hour and the decision
lives here, so a tick that could not run on time is skipped rather than queued
and run at the wrong hour.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.enums import BackupFrequency


def is_backup_due(
    *,
    now: datetime,
    frequency: BackupFrequency,
    weekday: int,
    hour_utc: int,
    last_backup_at: datetime | None,
) -> tuple[bool, str]:
    """Whether the tick at ``now`` should take a backup, and why not."""
    if frequency is BackupFrequency.off:
        return False, "automatic backups are off"
    if now.hour != hour_utc:
        return False, "not the configured hour"
    if frequency is BackupFrequency.weekly and now.weekday() != weekday:
        return False, "not the configured weekday"
    if last_backup_at is not None:
        # SQLite (the test factory) hands back naive datetimes; treat as UTC.
        if last_backup_at.tzinfo is None:
            last_backup_at = last_backup_at.replace(tzinfo=UTC)
        hour = lambda moment: moment.replace(minute=0, second=0, microsecond=0)  # noqa: E731
        if hour(last_backup_at) == hour(now):
            return False, "already ran this hour"
    return True, "due"
```

- [ ] **Step 4: Run the schedule tests**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_schedule.py`
Expected: 6 passed.

- [ ] **Step 5: Write the failing migration test**

`backend/tests/test_backup_settings_migration.py` — copy the harness helpers (`SCHEMA`, `_exec`, `_set_role_search_path`, `_reset_schema`, the `migrated` fixture) verbatim from `backend/tests/test_crowdsec_more_hosts_migration.py`, with `SCHEMA = "backups_probe"`, this docstring, and this test:

```python
"""0039 against a real Postgres: backup settings land with safe defaults.

Uses the same throwaway-schema harness as the other migration tests, so running
it never leaves the shared test database seeded.
"""


def test_an_upgraded_instance_has_automatic_backups_off(migrated) -> None:
    # Nobody's disk fills up because they upgraded: the schedule starts off,
    # and an admin turns it on when they have chosen a directory.
    migrated("0039_backups")

    rows = asyncio.run(
        _exec(
            [
                "SELECT backup_frequency, backup_keep, last_backup_ok"
                " FROM instance_settings WHERE id = 1"
            ]
        )
    )

    assert [tuple(r) for r in rows] == [("off", 7, None)]


def test_the_audit_log_can_record_a_restore(migrated) -> None:
    migrated("0039_backups")

    rows = asyncio.run(
        _exec(
            [
                "INSERT INTO audit_log (actor, action, object_type, created_at)"
                " VALUES ('admin@example.com', 'restore', 'backup', now())",
                "SELECT action FROM audit_log WHERE object_type = 'backup'",
            ]
        )
    )

    assert [tuple(r) for r in rows] == [("restore",)]
```

If `audit_log` has other `NOT NULL` columns without defaults, add them to the INSERT with valid values; keep the assertion.

- [ ] **Step 6: Run it and watch it fail**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_settings_migration.py`
Expected: `Can't locate revision identified by '0039_backups'`.

- [ ] **Step 7: Write the migration**

`backend/alembic/versions/0039_backups.py`:

```python
"""Backup schedule, retention, last-run, and the restore audit action

Automatic backups start off: an upgrade must not begin filling a directory the
operator has not chosen yet.

Revision ID: 0039_backups
Revises: 0038_crowdsec_more_hosts
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0039_backups"
down_revision: str | None = "0038_crowdsec_more_hosts"
branch_labels: str | None = None
depends_on: str | None = None

_FREQUENCY = sa.Enum("off", "daily", "weekly", name="backup_frequency")


def upgrade() -> None:
    _FREQUENCY.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "instance_settings",
        sa.Column("backup_frequency", _FREQUENCY, nullable=False, server_default="off"),
    )
    op.add_column(
        "instance_settings",
        sa.Column("backup_hour_utc", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "instance_settings",
        sa.Column("backup_weekday", sa.Integer(), nullable=False, server_default="6"),
    )
    op.add_column(
        "instance_settings",
        sa.Column("backup_keep", sa.Integer(), nullable=False, server_default="7"),
    )
    op.add_column("instance_settings", sa.Column("last_backup_ok", sa.Boolean(), nullable=True))
    op.add_column(
        "instance_settings",
        sa.Column("last_backup_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("instance_settings", sa.Column("last_backup_message", sa.Text(), nullable=True))
    # Postgres 12+ accepts ADD VALUE inside a transaction; the value is usable
    # from the next statement onwards, which is the next migration or request.
    op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'restore'")


def downgrade() -> None:
    for column in (
        "last_backup_message",
        "last_backup_at",
        "last_backup_ok",
        "backup_keep",
        "backup_weekday",
        "backup_hour_utc",
        "backup_frequency",
    ):
        op.drop_column("instance_settings", column)
    _FREQUENCY.drop(op.get_bind(), checkfirst=True)
    # The enum value stays: Postgres cannot drop one, and an audit row may use it.
```

- [ ] **Step 8: Add the model columns, schema, service and route**

`backend/app/models/instance_settings.py` (import `BackupFrequency`), after the CrowdSec block:

```python
    # --- Backups (Backups page) ---------------------------------------------
    # Off on an upgrade: a schedule must be chosen, not inherited.
    backup_frequency: Mapped[BackupFrequency] = mapped_column(
        Enum(
            BackupFrequency,
            name="backup_frequency",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=BackupFrequency.off,
        server_default="off",
    )
    backup_hour_utc: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    #: Monday = 0. Only consulted when the frequency is weekly.
    backup_weekday: Mapped[int] = mapped_column(
        Integer, nullable=False, default=6, server_default="6"
    )
    #: How many *automatic* backups to keep. Manual ones are never swept.
    backup_keep: Mapped[int] = mapped_column(Integer, nullable=False, default=7, server_default="7")
    last_backup_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_backup_message: Mapped[str | None] = mapped_column(Text, nullable=True)
```

`backend/app/schemas/instance_settings.py`: add the seven fields to `InstanceSettingsRead` (and to its `from_row`, following the existing style), and add:

```python
class BackupSettingsUpdate(BaseModel):
    """The automatic-backup schedule and how many copies to keep."""

    frequency: BackupFrequency = BackupFrequency.off
    hour_utc: int = Field(default=3, ge=0, le=23)
    weekday: int = Field(default=6, ge=0, le=6, description="Monday = 0; weekly only")
    keep: int = Field(default=7, ge=1, le=365)
```

`backend/app/services/instance_settings.py`:

```python
async def update_backup_settings(db: AsyncSession, changes: dict[str, Any]) -> InstanceSettings:
    """Apply the backup schedule. Takes effect at the next hourly tick."""
    row = await get_instance_settings(db)
    row.backup_frequency = changes["frequency"]
    row.backup_hour_utc = changes["hour_utc"]
    row.backup_weekday = changes["weekday"]
    row.backup_keep = changes["keep"]
    await db.commit()
    await db.refresh(row)
    return row
```

`backend/app/api/routes/settings.py`, mirroring the `crowdsec-hub` route:

```python
@router.patch("/backups", response_model=InstanceSettingsRead)
async def update_backup_settings(
    body: BackupSettingsUpdate, admin: AdminUser, db: SessionDep
) -> InstanceSettingsRead:
    """The automatic-backup schedule. Admin-only. Takes effect at the next tick."""
    row = await settings_service.update_backup_settings(db, body.model_dump())
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.update,
        object_type="instance_settings",
        object_id=row.id,
        meta={"backups": body.model_dump(mode="json")},
    )
    await db.commit()
    return InstanceSettingsRead.from_row(row)
```

`backend/app/core/config.py`, beside the other paths:

```python
    # Where backups are written, inside the container. The host directory is
    # BACKUP_PATH in .env, bind-mounted here by compose.
    backup_dir: str = "/backups"
    #: Largest backup an admin may upload. A restore reads the whole file.
    backup_max_bytes: int = 4 * 1024**3
```

`backend/tests/test_route_authorization.py`: add `("PATCH", "/api/v1/settings/backups"): "admin",`.

- [ ] **Step 9: Write the failing settings API test**

Append to `backend/tests/test_settings_api.py` (follow the file's existing fixtures):

```python
async def test_admin_sets_the_backup_schedule(client: AsyncClient, auth) -> None:
    patched = await client.patch(
        "/api/v1/settings/backups",
        headers=auth,
        json={"frequency": "weekly", "hour_utc": 2, "weekday": 0, "keep": 14},
    )

    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["backup_frequency"] == "weekly"
    assert body["backup_hour_utc"] == 2
    assert body["backup_keep"] == 14


async def test_the_schedule_rejects_an_hour_that_does_not_exist(
    client: AsyncClient, auth
) -> None:
    rejected = await client.patch(
        "/api/v1/settings/backups",
        headers=auth,
        json={"frequency": "daily", "hour_utc": 24, "weekday": 0, "keep": 7},
    )

    assert rejected.status_code == 422
```

- [ ] **Step 10: Run the backend suite for this task**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_settings_migration.py tests/test_settings_api.py tests/test_route_authorization.py tests/test_backup_schedule.py`
Expected: all pass.

- [ ] **Step 11: Regenerate the contract and commit**

```bash
docker exec -w /src megoopm-test python -m scripts.export_openapi
(cd frontend && npm run gen:api)
docker exec -w /src megoopm-test ruff format app alembic tests
docker exec -w /src megoopm-test ruff check app alembic tests
git checkout -- $(git diff --name-only backend | grep -v -e backup -e instance_settings -e settings.py -e enums.py -e config.py -e openapi.json || true)
git add backend frontend/src/lib/api/generated/schema.ts
git commit -m "feat(backup): schedule, retention and last-run settings"
```

(The `git checkout` line undoes any file `ruff format` touched that this task did not change; check `git status` before committing and keep the diff to this task's files.)

---

### Task 5: Taking a backup for real (pg_dump), with the image that can

**Files:**
- Create: `backend/app/services/backup/runner.py`
- Test: `backend/tests/test_backup_runner.py`
- Test: `backend/tests/test_backup_roundtrip_pg.py`
- Modify: `backend/Dockerfile`
- Modify: `backend/pyproject.toml` (add `python-multipart>=0.0.9`)

**Interfaces:**
- Consumes: `crypto.encrypt_stream`, `archive.build_archive`, `archive.BackupMeta`, `store.new_name`, `store.free_bytes`, `store.PART_SUFFIX`.
- Produces:
  - `PgTools` dataclass: `dump: Callable[[Path], None]`, `restore: Callable[[Path], None]`, `revision: Callable[[], str]`
  - `real_pg_tools(database_url: str) -> PgTools`
  - `NotEnoughSpaceError(RuntimeError)`
  - `create_backup(*, backup_dir: Path, secret_key: str, trigger: str, now: datetime, certs_dir: Path | None, crowdsec_files: dict[str, Path], pg: PgTools, app_version: str) -> Path`

- [ ] **Step 1: Write the failing tests**

```python
"""Taking a backup: dump, pack, seal, rename.

The Postgres calls are injected, so everything here is exercised without a
database; tests/test_backup_roundtrip_pg.py covers the real pg_dump path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.services.backup.crypto import read_header
from app.services.backup.runner import NotEnoughSpaceError, PgTools, create_backup
from app.services.backup.store import PART_SUFFIX, list_backups

KEY = "a-secret-key"
NOW = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)


def _pg(dump_bytes: bytes = b"PGDMP-pretend") -> PgTools:
    def dump(dst: Path) -> None:
        dst.write_bytes(dump_bytes)

    def restore(src: Path) -> None:  # pragma: no cover - not used here
        raise AssertionError("restore must not run during a backup")

    return PgTools(dump=dump, restore=restore, revision=lambda: "0039_backups")


def _certs(tmp_path: Path) -> Path:
    certs = tmp_path / "certs" / "7"
    certs.mkdir(parents=True)
    (certs / "privkey.pem").write_text("key")
    return tmp_path / "certs"


def test_a_backup_lands_under_its_name_and_can_be_listed(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    path = create_backup(
        backup_dir=backup_dir,
        secret_key=KEY,
        trigger="manual",
        now=NOW,
        certs_dir=_certs(tmp_path),
        crowdsec_files={},
        pg=_pg(),
        app_version="0.1.0",
    )

    assert path.name == "megoopm-20261011-030000-manual.mgb"
    (row,) = list_backups(backup_dir, secret_key=KEY)
    assert row.key_matches is True
    assert row.app_version == "0.1.0"
    with path.open("rb") as handle:
        assert read_header(handle).version == 1


def test_a_failed_dump_leaves_no_backup_behind(tmp_path: Path) -> None:
    # Half a backup in the list is worse than none: someone would restore it.
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    def explode(dst: Path) -> None:
        dst.write_bytes(b"partial")
        raise RuntimeError("pg_dump: connection refused")

    with pytest.raises(RuntimeError, match="connection refused"):
        create_backup(
            backup_dir=backup_dir,
            secret_key=KEY,
            trigger="auto",
            now=NOW,
            certs_dir=None,
            crowdsec_files={},
            pg=PgTools(dump=explode, restore=lambda src: None, revision=lambda: "0039_backups"),
            app_version="0.1.0",
        )

    assert list(backup_dir.iterdir()) == []


def test_a_full_disk_is_refused_before_anything_is_written(tmp_path: Path, monkeypatch) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr("app.services.backup.runner.free_bytes", lambda _dir: 1024)

    with pytest.raises(NotEnoughSpaceError):
        create_backup(
            backup_dir=backup_dir,
            secret_key=KEY,
            trigger="auto",
            now=NOW,
            certs_dir=_certs(tmp_path),
            crowdsec_files={},
            pg=_pg(b"x" * 100_000),
            app_version="0.1.0",
        )

    assert not any(p.name.endswith(PART_SUFFIX) for p in backup_dir.iterdir())
```

- [ ] **Step 2: Run them and watch them fail**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_runner.py`
Expected: `ModuleNotFoundError: No module named 'app.services.backup.runner'`.

- [ ] **Step 3: Write the runner**

```python
"""Taking a backup, and the Postgres calls it needs.

The dump/restore calls are injected (:class:`PgTools`) so the orchestration is
testable without a database, and so a future deployment can swap how it reaches
Postgres without touching this file.

The sequence is: check space, dump, pack, seal into ``<name>.part``, fsync,
rename. A failure anywhere before the rename leaves the directory as it was —
a half-written backup that someone later restores is the worst outcome here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.services.backup.archive import BackupMeta, build_archive
from app.services.backup.crypto import encrypt_stream
from app.services.backup.store import PART_SUFFIX, free_bytes, new_name

#: Head-room over the estimated size before a backup is attempted.
_SPACE_MARGIN = 1.2


class NotEnoughSpaceError(RuntimeError):
    """The backup directory does not have room for this backup."""


@dataclass(frozen=True, slots=True)
class PgTools:
    #: Write a custom-format dump of the whole database to this path.
    dump: Callable[[Path], None]
    #: Replace the database from this dump path.
    restore: Callable[[Path], None]
    #: The alembic revision the database is currently at.
    revision: Callable[[], str]


def _run(argv: list[str], *, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{argv[0]} failed: {result.stderr.strip() or result.returncode}")


def real_pg_tools(database_url: str) -> PgTools:
    """pg_dump/pg_restore/psql against ``database_url`` (a libpq URL)."""
    url = database_url.replace("+asyncpg", "").replace("+psycopg", "")

    def dump(dst: Path) -> None:
        _run(["pg_dump", "--format=custom", "--no-owner", "--file", str(dst), url])

    def restore(src: Path) -> None:
        _run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--single-transaction",
                "--dbname",
                url,
                str(src),
            ]
        )

    def revision() -> str:
        result = subprocess.run(
            ["psql", url, "-tAc", "SELECT version_num FROM alembic_version"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    return PgTools(dump=dump, restore=restore, revision=revision)


def _tree_bytes(root: Path | None) -> int:
    if root is None or not root.is_dir():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def create_backup(
    *,
    backup_dir: Path,
    secret_key: str,
    trigger: str,
    now: datetime,
    certs_dir: Path | None,
    crowdsec_files: dict[str, Path],
    pg: PgTools,
    app_version: str,
) -> Path:
    """Dump, pack, seal and rename. Returns the finished file's path."""
    # 0700: a backup holds TLS private keys and every stored secret.
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = backup_dir / new_name(now, trigger)
    part = final.with_name(final.name + PART_SUFFIX)

    with tempfile.TemporaryDirectory(prefix="megoopm-backup-") as work:
        dump_path = Path(work) / "db.dump"
        pg.dump(dump_path)

        cert_bytes = _tree_bytes(certs_dir)
        needed = int((dump_path.stat().st_size + cert_bytes) * _SPACE_MARGIN)
        if free_bytes(backup_dir) < needed:
            raise NotEnoughSpaceError(
                f"{needed // 1024 // 1024} MB needed in {backup_dir}, "
                f"{free_bytes(backup_dir) // 1024 // 1024} MB free"
            )

        meta = BackupMeta(
            created_at=now,
            app_version=app_version,
            alembic_revision=pg.revision(),
            trigger=trigger,
            db_bytes=dump_path.stat().st_size,
            cert_bytes=cert_bytes,
        )
        plain_path = Path(work) / "archive.tar"
        with plain_path.open("wb") as plain:
            build_archive(
                plain,
                meta=meta,
                db_dump=dump_path,
                certs_dir=certs_dir,
                crowdsec_files=crowdsec_files,
            )

        try:
            with plain_path.open("rb") as plain, part.open("wb") as sealed:
                encrypt_stream(plain, sealed, secret_key=secret_key)
                sealed.flush()
                os.fsync(sealed.fileno())
        except BaseException:
            part.unlink(missing_ok=True)
            raise

    part.replace(final)
    final.chmod(0o600)
    return final
```

Note the `except BaseException` placement: `pg.dump` raising leaves nothing behind because `part` has not been opened yet, and the temp dir is removed by its context manager.

- [ ] **Step 4: Run the runner tests**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_runner.py`
Expected: 3 passed.

- [ ] **Step 5: Add the Postgres client to the image and the multipart dependency**

`backend/Dockerfile`, replacing the `curl` apt block:

```dockerfile
# System deps: curl for container healthchecks, and the Postgres 16 client for
# backups (pg_dump/pg_restore/psql). The client comes from pgdg because Debian
# bookworm ships 15, and pg_dump refuses a server newer than itself.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*
```

`backend/pyproject.toml`, in `dependencies`:

```toml
    # Multipart parsing for the backup upload route (FastAPI needs it for
    # UploadFile); nothing else in the API takes a file.
    "python-multipart>=0.0.9",
```

- [ ] **Step 6: Write the real-Postgres round-trip test**

`backend/tests/test_backup_roundtrip_pg.py`:

```python
"""A real pg_dump/pg_restore round trip against the test database.

The unit tests inject the Postgres calls; this one proves the real ones work —
that the dump restores, and that what comes back is what went in. Skips where
the binaries or a database are absent, so it is honest about what it checked.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.core.config import settings
from app.services.backup.runner import create_backup, real_pg_tools
from app.services.backup.crypto import decrypt_stream
from app.services.backup.archive import extract_archive

pytestmark = pytest.mark.skipif(
    shutil.which("pg_dump") is None or shutil.which("pg_restore") is None,
    reason="Postgres client binaries not installed",
)

KEY = "a-secret-key"
URL = settings.database_url.replace("+asyncpg", "").replace("+psycopg", "")


@pytest.fixture
def engine():
    engine = sa.create_engine(URL.replace("postgresql://", "postgresql+psycopg://"))
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("DROP TABLE IF EXISTS backup_probe"))
            conn.execute(sa.text("CREATE TABLE backup_probe (note text)"))
            conn.execute(sa.text("INSERT INTO backup_probe VALUES ('before')"))
        yield engine
        with engine.begin() as conn:
            conn.execute(sa.text("DROP TABLE IF EXISTS backup_probe"))
    finally:
        engine.dispose()


def test_a_backup_restores_the_rows_it_captured(engine, tmp_path: Path) -> None:
    pg = real_pg_tools(URL)
    path = create_backup(
        backup_dir=tmp_path / "backups",
        secret_key=KEY,
        trigger="manual",
        now=datetime(2026, 10, 11, 3, 0, tzinfo=UTC),
        certs_dir=None,
        crowdsec_files={},
        pg=pg,
        app_version="0.1.0",
    )

    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE backup_probe SET note = 'after'"))

    work = tmp_path / "work"
    work.mkdir()
    with path.open("rb") as sealed, (work / "archive.tar").open("wb") as plain:
        decrypt_stream(sealed, plain, secret_key=KEY)
    with (work / "archive.tar").open("rb") as plain:
        extract_archive(plain, work)
    pg.restore(work / "db.dump")

    with engine.begin() as conn:
        assert conn.execute(sa.text("SELECT note FROM backup_probe")).scalar_one() == "before"
```

- [ ] **Step 7: Rebuild the test image and run everything in this task**

```bash
docker build -t megoopm-backend backend
docker rm -f megoopm-test && docker run -d --name megoopm-test --network megoopm-testnet \
  -e DATABASE_URL=postgresql+asyncpg://megoopm:megoopm@megoopm-testdb:5432/megoopm \
  -v C:/Projects/megoopm/backend:/src -w /src megoopm-backend sleep infinity
docker exec -w /src megoopm-test sh -c 'pip install -q pytest pytest-asyncio aiosqlite ruff maxminddb webauthn cbor2'
docker exec -w /src megoopm-test python -m pytest tests/test_backup_runner.py tests/test_backup_roundtrip_pg.py
```

Expected: 4 passed. If the round-trip test skips, the image rebuild did not pick up the Postgres client — fix that before moving on; this task's point is that the binaries are there.

- [ ] **Step 8: Commit**

```bash
sed -i 's/\r$//' backend/app/services/backup/runner.py backend/tests/test_backup_runner.py backend/tests/test_backup_roundtrip_pg.py
docker exec -w /src megoopm-test ruff format app/services/backup tests/test_backup_runner.py tests/test_backup_roundtrip_pg.py
docker exec -w /src megoopm-test ruff check app/services/backup tests/test_backup_runner.py tests/test_backup_roundtrip_pg.py
git add backend/app/services/backup/runner.py backend/tests/test_backup_runner.py backend/tests/test_backup_roundtrip_pg.py backend/Dockerfile backend/pyproject.toml
git commit -m "feat(backup): take a real backup with pg_dump"
```

---

### Task 6: Restoring, with verification before destruction

**Files:**
- Modify: `backend/app/services/backup/runner.py`
- Test: `backend/tests/test_backup_restore.py`

**Interfaces:**
- Consumes: `crypto.decrypt_stream`, `archive.extract_archive`, `PgTools`.
- Produces:
  - `RestoreRejected(RuntimeError)`
  - `restore_backup(*, path: Path, secret_key: str, certs_dir: Path | None, crowdsec_files: dict[str, Path], pg: PgTools, known_revisions: set[str], on_step: Callable[[str], None] = lambda step: None) -> BackupMeta`

- [ ] **Step 1: Write the failing tests**

```python
"""Restoring: everything is verified before anything is replaced.

The ordering is the design. A restore that drops the database and then finds
the archive unreadable has destroyed the instance it was asked to protect, so
every check that can run first, runs first.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.services.backup.archive import BackupMeta, build_archive
from app.services.backup.crypto import encrypt_stream
from app.services.backup.runner import PgTools, RestoreRejected, restore_backup

KEY = "a-secret-key"
REVISIONS = {"0039_backups"}


def _sealed_backup(tmp_path: Path, *, secret_key: str = KEY, revision: str = "0039_backups") -> Path:
    dump = tmp_path / "db.dump"
    dump.write_bytes(b"PGDMP-pretend")
    certs = tmp_path / "src-certs" / "7"
    certs.mkdir(parents=True)
    (certs / "privkey.pem").write_text("restored-key")
    meta = BackupMeta(
        created_at=datetime(2026, 10, 11, 3, 0, tzinfo=UTC),
        app_version="0.1.0",
        alembic_revision=revision,
        trigger="manual",
        db_bytes=13,
        cert_bytes=12,
    )
    plain = io.BytesIO()
    build_archive(plain, meta=meta, db_dump=dump, certs_dir=tmp_path / "src-certs", crowdsec_files={})
    plain.seek(0)
    path = tmp_path / "megoopm-20261011-030000-manual.mgb"
    with path.open("wb") as out:
        encrypt_stream(plain, out, secret_key=secret_key)
    return path


def _pg(restored: list[Path]) -> PgTools:
    return PgTools(
        dump=lambda dst: (_ for _ in ()).throw(AssertionError("no dump during a restore")),
        restore=restored.append,
        revision=lambda: "0039_backups",
    )


def test_a_restore_replaces_the_database_and_the_certificates(tmp_path: Path) -> None:
    restored: list[Path] = []
    certs_dir = tmp_path / "live-certs"
    (certs_dir / "7").mkdir(parents=True)
    (certs_dir / "7" / "privkey.pem").write_text("old-key")
    steps: list[str] = []

    meta = restore_backup(
        path=_sealed_backup(tmp_path),
        secret_key=KEY,
        certs_dir=certs_dir,
        crowdsec_files={},
        pg=_pg(restored),
        known_revisions=REVISIONS,
        on_step=steps.append,
    )

    assert meta.app_version == "0.1.0"
    assert len(restored) == 1
    assert (certs_dir / "7" / "privkey.pem").read_text() == "restored-key"
    assert steps  # the UI shows these while the API is otherwise 503


def test_a_backup_from_another_instance_is_refused(tmp_path: Path) -> None:
    restored: list[Path] = []
    with pytest.raises(RestoreRejected, match="SECRET_KEY"):
        restore_backup(
            path=_sealed_backup(tmp_path, secret_key="another-key"),
            secret_key=KEY,
            certs_dir=None,
            crowdsec_files={},
            pg=_pg(restored),
            known_revisions=REVISIONS,
        )
    assert restored == []


def test_a_backup_from_a_newer_megoopm_is_refused(tmp_path: Path) -> None:
    # Its schema is ahead of this code; restoring it would leave an instance
    # the running version cannot read.
    restored: list[Path] = []
    with pytest.raises(RestoreRejected, match="newer"):
        restore_backup(
            path=_sealed_backup(tmp_path, revision="0099_from_the_future"),
            secret_key=KEY,
            certs_dir=None,
            crowdsec_files={},
            pg=_pg(restored),
            known_revisions=REVISIONS,
        )
    assert restored == []


def test_a_file_that_is_not_a_backup_never_reaches_the_database(tmp_path: Path) -> None:
    # Review Focus 1: a valid envelope around garbage must be caught before
    # pg_restore --clean drops a single table.
    restored: list[Path] = []
    junk = tmp_path / "megoopm-20261011-030000-upload.mgb"
    with junk.open("wb") as out:
        encrypt_stream(io.BytesIO(b"not a tar at all"), out, secret_key=KEY)

    with pytest.raises(RestoreRejected):
        restore_backup(
            path=junk,
            secret_key=KEY,
            certs_dir=None,
            crowdsec_files={},
            pg=_pg(restored),
            known_revisions=REVISIONS,
        )

    assert restored == []


def test_a_failing_pg_restore_leaves_the_certificates_alone(tmp_path: Path) -> None:
    # Files are replaced only after the database is back: a restore that failed
    # halfway must not leave new keys beside an old database.
    certs_dir = tmp_path / "live-certs"
    (certs_dir / "7").mkdir(parents=True)
    (certs_dir / "7" / "privkey.pem").write_text("old-key")

    def explode(src: Path) -> None:
        raise RuntimeError("pg_restore: relation does not exist")

    with pytest.raises(RuntimeError, match="pg_restore"):
        restore_backup(
            path=_sealed_backup(tmp_path),
            secret_key=KEY,
            certs_dir=certs_dir,
            crowdsec_files={},
            pg=PgTools(dump=lambda dst: None, restore=explode, revision=lambda: "0039_backups"),
            known_revisions=REVISIONS,
        )

    assert (certs_dir / "7" / "privkey.pem").read_text() == "old-key"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_restore.py`
Expected: `ImportError: cannot import name 'RestoreRejected'`.

- [ ] **Step 3: Add the restore to the runner**

Append to `backend/app/services/backup/runner.py` (and add the imports it needs:
`from app.services.backup.archive import BackupArchiveError, BackupMeta, extract_archive`,
`from app.services.backup.crypto import BackupFormatError, BackupKeyError, decrypt_stream, key_fingerprint, read_header`):

```python
class RestoreRejected(RuntimeError):
    """The backup was refused before anything was replaced."""


def restore_backup(
    *,
    path: Path,
    secret_key: str,
    certs_dir: Path | None,
    crowdsec_files: dict[str, Path],
    pg: PgTools,
    known_revisions: set[str],
    on_step: Callable[[str], None] = lambda step: None,
) -> BackupMeta:
    """Verify a backup completely, then replace the database and the files.

    Nothing is destroyed until the archive has been decrypted, unpacked and
    found to contain what a restore needs. ``on_step`` receives short progress
    labels; during a restore the API is 503 and they are all the UI can show.
    """
    with tempfile.TemporaryDirectory(prefix="megoopm-restore-") as work:
        work_dir = Path(work)

        on_step("verifying")
        with path.open("rb") as sealed:
            header = read_header(sealed)
        if header.fingerprint != key_fingerprint(secret_key):
            raise RestoreRejected(
                "this backup was made with a different SECRET_KEY and cannot be read"
            )

        on_step("unpacking")
        archive_path = work_dir / "archive.tar"
        try:
            with path.open("rb") as sealed, archive_path.open("wb") as plain:
                decrypt_stream(sealed, plain, secret_key=secret_key)
            with archive_path.open("rb") as plain:
                meta = extract_archive(plain, work_dir)
        except (BackupFormatError, BackupKeyError, BackupArchiveError) as exc:
            raise RestoreRejected(str(exc)) from exc

        if meta.alembic_revision and meta.alembic_revision not in known_revisions:
            raise RestoreRejected(
                f"this backup is from a newer MegooPM (database revision "
                f"{meta.alembic_revision}); upgrade before restoring it"
            )

        on_step("restoring the database")
        pg.restore(work_dir / "db.dump")

        on_step("restoring files")
        unpacked_certs = work_dir / "certs"
        if certs_dir is not None and unpacked_certs.is_dir():
            _replace_tree(unpacked_certs, certs_dir)
        unpacked_crowdsec = work_dir / "crowdsec"
        for name, target in crowdsec_files.items():
            source = unpacked_crowdsec / name
            if source.is_file():
                _replace_file(source, target)

        return meta


def _replace_tree(source: Path, target: Path) -> None:
    """Swap ``target`` for ``source`` without ever leaving it half-written."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(target.name + ".restoring")
    shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(source, staged)
    previous = target.with_name(target.name + ".previous")
    shutil.rmtree(previous, ignore_errors=True)
    if target.exists():
        target.rename(previous)
    staged.rename(target)
    shutil.rmtree(previous, ignore_errors=True)


def _replace_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(target.name + ".restoring")
    shutil.copy2(source, staged)
    staged.replace(target)
```

- [ ] **Step 4: Run the restore tests**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_restore.py tests/test_backup_runner.py`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
sed -i 's/\r$//' backend/app/services/backup/runner.py backend/tests/test_backup_restore.py
docker exec -w /src megoopm-test ruff format app/services/backup tests/test_backup_restore.py
docker exec -w /src megoopm-test ruff check app/services/backup tests/test_backup_restore.py
git add backend/app/services/backup/runner.py backend/tests/test_backup_restore.py
git commit -m "feat(backup): restore a backup, verified before anything is replaced"
```

---

### Task 7: Celery tasks, the lock, and the restore gate

**Files:**
- Create: `backend/app/services/backup/gate.py`
- Create: `backend/app/tasks/backup.py`
- Create: `backend/tests/test_backup_gate.py`
- Create: `backend/tests/test_backup_tasks.py`
- Modify: `backend/app/core/celery_app.py` (import the module, add the beat entry)
- Modify: `backend/app/main.py` (the 503 middleware)

**Interfaces:**
- Consumes: `runner.create_backup`, `runner.restore_backup`, `store.apply_retention`, `schedule.is_backup_due`, settings columns from Task 4.
- Produces:
  - `gate.RESTORE_KEY = "megoopm:restore"`, `gate.begin_restore(client, step)`, `gate.set_step(client, step)`, `gate.end_restore(client, error=None)`, `gate.restore_status(client) -> dict` with keys `running: bool`, `step: str | None`, `started_at: str | None`, `error: str | None`
  - `BACKUP_LOCK_KEY = "megoopm:backup"`
  - tasks `app.tasks.backup.create_backup_task(trigger: str = "manual")`, `app.tasks.backup.restore_backup_task(name: str)`, `app.tasks.backup.backup_tick()`
  - beat entry `backup-tick` at `crontab(minute=10)`

- [ ] **Step 1: Write the failing gate tests**

`backend/tests/test_backup_gate.py`:

```python
"""The flag that makes the API stand still during a restore.

While a restore runs, every write would land in a database that is about to be
replaced — or worse, in one mid-replacement. The flag lives in Redis because
the worker sets it and the API reads it.
"""

from __future__ import annotations

import fakeredis
import pytest
from app.services.backup import gate


@pytest.fixture
def client():
    return fakeredis.FakeStrictRedis()


def test_nothing_is_running_by_default(client) -> None:
    assert gate.restore_status(client)["running"] is False


def test_a_restore_reports_its_step(client) -> None:
    gate.begin_restore(client, "verifying")
    gate.set_step(client, "restoring the database")

    status = gate.restore_status(client)

    assert status["running"] is True
    assert status["step"] == "restoring the database"
    assert status["started_at"]


def test_the_flag_clears_when_the_restore_ends(client) -> None:
    gate.begin_restore(client, "verifying")
    gate.end_restore(client)

    assert gate.restore_status(client)["running"] is False


def test_a_failure_is_remembered_after_the_flag_clears(client) -> None:
    # The admin's browser is blocked on this status; it has to learn why the
    # restore stopped, not just that it did.
    gate.begin_restore(client, "verifying")
    gate.end_restore(client, error="this backup was made with a different SECRET_KEY")

    status = gate.restore_status(client)

    assert status["running"] is False
    assert "SECRET_KEY" in status["error"]
```

If `fakeredis` is not installed in the test container, add it to the test-container `pip install` line and to `backend/pyproject.toml`'s `dev` extra.

- [ ] **Step 2: Run it and watch it fail**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_gate.py`
Expected: `ModuleNotFoundError: No module named 'app.services.backup.gate'`.

- [ ] **Step 3: Write the gate**

```python
"""Restore-in-progress flag, shared by the worker and the API.

Redis, not the database: the database is the thing being replaced. The flag
carries a TTL so a worker killed mid-restore cannot wedge the API permanently —
it unblocks itself, and the next request finds a database that either restored
or did not.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import redis

RESTORE_KEY = "megoopm:restore"
#: Long enough for a large restore, short enough to self-heal after a crash.
RESTORE_TTL_S = 3600
_LAST_ERROR_KEY = "megoopm:restore:last-error"
_LAST_ERROR_TTL_S = 900


def begin_restore(client: redis.Redis, step: str) -> None:
    client.delete(_LAST_ERROR_KEY)
    client.set(
        RESTORE_KEY,
        json.dumps({"started_at": datetime.now(UTC).isoformat(), "step": step}),
        ex=RESTORE_TTL_S,
    )


def set_step(client: redis.Redis, step: str) -> None:
    raw = client.get(RESTORE_KEY)
    payload = json.loads(raw) if raw else {"started_at": datetime.now(UTC).isoformat()}
    payload["step"] = step
    client.set(RESTORE_KEY, json.dumps(payload), ex=RESTORE_TTL_S)


def end_restore(client: redis.Redis, *, error: str | None = None) -> None:
    client.delete(RESTORE_KEY)
    if error:
        client.set(_LAST_ERROR_KEY, error, ex=_LAST_ERROR_TTL_S)


def is_restoring(client: redis.Redis) -> bool:
    return client.exists(RESTORE_KEY) == 1


def restore_status(client: redis.Redis) -> dict[str, Any]:
    raw = client.get(RESTORE_KEY)
    error = client.get(_LAST_ERROR_KEY)
    payload = json.loads(raw) if raw else {}
    return {
        "running": raw is not None,
        "step": payload.get("step"),
        "started_at": payload.get("started_at"),
        "error": error.decode() if isinstance(error, bytes) else error,
    }
```

- [ ] **Step 4: Run the gate tests**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_gate.py`
Expected: 4 passed.

- [ ] **Step 5: Write the failing task tests**

`backend/tests/test_backup_tasks.py`:

```python
"""The Celery layer: locking, the schedule tick, and recording the outcome.

The work itself is tested in test_backup_runner / test_backup_restore; these
cover what the task adds — that two backups cannot overlap, that the tick obeys
the settings, and that a failure is recorded where the page can show it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import fakeredis
import pytest
from app.models.enums import BackupFrequency
from app.tasks import backup as backup_tasks


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    client = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(backup_tasks, "_lock_client", lambda: client)
    return client


def test_a_second_backup_is_refused_while_one_runs(monkeypatch, fake_redis) -> None:
    # Two pg_dumps at once would double the disk and could evict each other
    # through retention.
    lock = fake_redis.lock(backup_tasks.BACKUP_LOCK_KEY, timeout=60)
    assert lock.acquire(blocking=False)

    result = backup_tasks.create_backup_task("manual")

    assert result == {"ran": False, "reason": "already running"}


def test_the_tick_does_nothing_when_backups_are_off(monkeypatch) -> None:
    monkeypatch.setattr(
        backup_tasks,
        "_load_backup_settings",
        lambda conn: backup_tasks.BackupSettings(
            frequency=BackupFrequency.off, weekday=6, hour_utc=3, keep=7, last_backup_at=None
        ),
    )
    monkeypatch.setattr(backup_tasks, "_now", lambda: datetime(2026, 10, 11, 3, 0, tzinfo=UTC))
    monkeypatch.setattr(backup_tasks, "sync_engine", lambda: _NullEngine())
    ran: list[str] = []
    monkeypatch.setattr(backup_tasks, "_create", lambda trigger: ran.append(trigger))

    result = backup_tasks.backup_tick()

    assert result["ran"] is False
    assert ran == []


class _NullEngine:
    """Stands in for the sync engine: `with engine.connect() as conn` only."""

    def connect(self):
        return self

    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False

    def dispose(self):
        return None
```

Extend this file as the implementation settles — the two behaviours above are the contract; keep their assertions.

- [ ] **Step 6: Run it and watch it fail**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_tasks.py`
Expected: `ModuleNotFoundError: No module named 'app.tasks.backup'`.

- [ ] **Step 7: Write the tasks**

`backend/app/tasks/backup.py`, following `app/tasks/crowdsec.py` (the lock
helper and the sync-engine settings read are deliberately the same shape):

```python
"""Taking and restoring backups, off the request path.

A backup runs pg_dump over the whole database and writes gigabytes; a restore
replaces the database the API is talking to. Neither belongs in a request, and
neither may overlap the other — both hold the same Redis lock.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import redis
from sqlalchemy import Connection, select, update

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.enums import BackupFrequency
from app.models.instance_settings import InstanceSettings
from app.services.backup import gate, store
from app.services.backup.runner import create_backup, real_pg_tools, restore_backup
from app.services.backup.schedule import is_backup_due
from app.services.cluster import sync_engine

log = logging.getLogger(__name__)

BACKUP_LOCK_KEY = "megoopm:backup"
#: A large instance's dump plus the encrypt pass; well under this.
_LOCK_TIMEOUT_S = 3600


@dataclass(frozen=True, slots=True)
class BackupSettings:
    frequency: BackupFrequency
    weekday: int
    hour_utc: int
    keep: int
    last_backup_at: datetime | None


def _now() -> datetime:
    return datetime.now(UTC)


def _lock_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url)


def _run_locked(key: str, fn: Callable[[], dict]) -> dict:
    """Run ``fn`` under a Redis lock, or report that it is already running."""
    client = _lock_client()
    lock = client.lock(key, timeout=_LOCK_TIMEOUT_S)
    try:
        if not lock.acquire(blocking=False):
            return {"ran": False, "reason": "already running"}
        try:
            return fn()
        finally:
            lock.release()
    finally:
        client.close()


def _load_backup_settings(conn: Connection) -> BackupSettings:
    table = InstanceSettings.__table__
    row = conn.execute(select(table).where(table.c.id == 1)).one()
    return BackupSettings(
        frequency=BackupFrequency(row.backup_frequency),
        weekday=row.backup_weekday,
        hour_utc=row.backup_hour_utc,
        keep=row.backup_keep,
        last_backup_at=row.last_backup_at,
    )


def _record_outcome(ok: bool, message: str | None, at: datetime) -> None:
    """Write the last run where the page reads it. Never raises."""
    try:
        engine = sync_engine()
        try:
            with engine.begin() as conn:
                conn.execute(
                    update(InstanceSettings.__table__)
                    .where(InstanceSettings.__table__.c.id == 1)
                    .values(last_backup_ok=ok, last_backup_at=at, last_backup_message=message)
                )
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001 - a backup that ran must not fail on bookkeeping
        log.warning("could not record the backup outcome: %s", exc)


def _crowdsec_files() -> dict[str, Path]:
    return {
        "whitelists.yaml": Path(settings.crowdsec_whitelist_path),
        "config.yaml.local": Path(settings.crowdsec_config_local_path),
    }


def _certs_dir() -> Path | None:
    return Path(settings.nginx_certs_dir) if settings.nginx_certs_dir else None


def _known_revisions() -> set[str]:
    """Every revision this build knows, so a newer backup can be refused."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    return {revision.revision for revision in script.walk_revisions()}


def _create(trigger: str) -> dict:
    started = _now()
    try:
        path = create_backup(
            backup_dir=Path(settings.backup_dir),
            secret_key=settings.secret_key,
            trigger=trigger,
            now=started,
            certs_dir=_certs_dir(),
            crowdsec_files=_crowdsec_files(),
            pg=real_pg_tools(settings.sync_database_url),
            app_version=version("megoopm-backend"),
        )
    except Exception as exc:  # noqa: BLE001 - the page has to be able to show why
        log.warning("backup failed: %s", exc)
        _record_outcome(False, str(exc), started)
        _announce("backup.failed")
        return {"ran": True, "ok": False, "error": str(exc)}

    swept: list[str] = []
    if trigger == "auto":
        engine = sync_engine()
        try:
            with engine.connect() as conn:
                keep = _load_backup_settings(conn).keep
        finally:
            engine.dispose()
        swept = store.apply_retention(Path(settings.backup_dir), keep=keep)

    _record_outcome(True, None, started)
    _announce("backup.finished")
    return {"ran": True, "ok": True, "name": path.name, "swept": swept}


def _restore(name: str) -> dict:
    client = _lock_client()
    path = store.safe_path(Path(settings.backup_dir), name)
    gate.begin_restore(client, "verifying")
    _announce("restore.started")
    try:
        meta = restore_backup(
            path=path,
            secret_key=settings.secret_key,
            certs_dir=_certs_dir(),
            crowdsec_files=_crowdsec_files(),
            pg=real_pg_tools(settings.sync_database_url),
            known_revisions=_known_revisions(),
            on_step=lambda step: gate.set_step(client, step),
        )
        gate.set_step(client, "running migrations")
        # A backup can predate the running code; the restored database has to
        # catch up before the API is allowed to talk to it again.
        subprocess.run(["alembic", "upgrade", "head"], cwd="/app", check=True)
    except Exception as exc:  # noqa: BLE001 - the blocking screen shows this
        log.warning("restore failed: %s", exc)
        gate.end_restore(client, error=str(exc))
        client.close()
        _announce("restore.failed")
        return {"ran": True, "ok": False, "error": str(exc)}

    gate.end_restore(client)
    client.close()
    # Rebuild the generated nginx configuration from the restored database.
    from app.tasks.nginx import reload_nginx_config

    reload_nginx_config.delay()
    _announce("restore.finished")
    return {"ran": True, "ok": True, "restored_from": meta.created_at.isoformat()}


@celery_app.task(name="app.tasks.backup.create_backup_task")
def create_backup_task(trigger: str = "manual") -> dict:
    """Take one backup, record the outcome, sweep old automatic ones."""
    return _run_locked(BACKUP_LOCK_KEY, lambda: _create(trigger))


@celery_app.task(name="app.tasks.backup.restore_backup_task")
def restore_backup_task(name: str) -> dict:
    """Replace this instance from a backup. Holds the backup lock throughout."""
    return _run_locked(BACKUP_LOCK_KEY, lambda: _restore(name))


@celery_app.task(name="app.tasks.backup.backup_tick")
def backup_tick() -> dict:
    """Hourly: take a scheduled backup if this is its hour."""
    now = _now()
    engine = sync_engine()
    try:
        with engine.connect() as conn:
            config = _load_backup_settings(conn)
    finally:
        engine.dispose()

    due, reason = is_backup_due(
        now=now,
        frequency=config.frequency,
        weekday=config.weekday,
        hour_utc=config.hour_utc,
        last_backup_at=config.last_backup_at,
    )
    if not due:
        return {"ran": False, "reason": reason}
    return _run_locked(BACKUP_LOCK_KEY, lambda: _create("auto"))
```

Two things to copy rather than invent:

- `_announce(event_type)` — take it verbatim from `app/tasks/nginx.py`. It skips
  publishing when an event loop is already running (Celery eager mode in tests)
  and never raises, so an announcement cannot fail a backup that worked.
- the sync engine import — check the name with
  `grep -rn "def sync_engine" backend/app/services/cluster` and match what
  `app/tasks/crowdsec.py` imports.

In `backend/app/core/celery_app.py`: add `"app.tasks.backup"` to the imported
task modules list, and the beat entry:

```python
        "backup-tick": {
            "task": "app.tasks.backup.backup_tick",
            # Minute 10, so it never collides with the hub tick at :05. The
            # tick decides whether this hour is the configured slot.
            "schedule": crontab(minute=10),
            "options": {"expires": 3000},
        },
```

- [ ] **Step 8: Add the 503 middleware**

In `backend/app/main.py`, after the CORS middleware:

```python
@app.middleware("http")
async def refuse_writes_during_a_restore(request: Request, call_next):
    """While a restore runs, the database is being replaced — serve nothing else.

    The exceptions are the health check (so the container is not killed) and the
    restore status (so the admin's browser can watch it finish).
    """
    path = request.url.path
    if not path.startswith(settings.api_v1_prefix) or path in _RESTORE_SAFE_PATHS:
        return await call_next(request)
    client = redis.Redis.from_url(settings.redis_url)
    try:
        if gate.is_restoring(client):
            return JSONResponse(
                {"detail": "A restore is in progress."},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers={"Retry-After": "10"},
            )
    except redis.RedisError:
        # Redis being unreachable is not evidence of a restore; let the request
        # through rather than taking the API down with it.
        pass
    finally:
        client.close()
    return await call_next(request)
```

with `_RESTORE_SAFE_PATHS = {f"{settings.api_v1_prefix}/backups/restore-status"}` beside it, and `/health` excluded because it does not start with the API prefix.

- [ ] **Step 9: Run the tests**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backup_tasks.py tests/test_backup_gate.py tests/test_health.py`
Expected: all pass (adjust `tests/test_health.py` to whatever the health test file is called; the point is that the middleware has not broken ordinary requests).

- [ ] **Step 10: Commit**

```bash
sed -i 's/\r$//' backend/app/services/backup/gate.py backend/app/tasks/backup.py backend/app/main.py backend/app/core/celery_app.py backend/tests/test_backup_gate.py backend/tests/test_backup_tasks.py
docker exec -w /src megoopm-test ruff format app tests
docker exec -w /src megoopm-test ruff check app tests
git add backend
git commit -m "feat(backup): backup and restore tasks, the lock, and the restore gate"
```

---

### Task 8: The API

**Files:**
- Create: `backend/app/api/routes/backups.py`
- Create: `backend/app/schemas/backup.py`
- Create: `backend/tests/test_backups_api.py`
- Modify: `backend/app/api/routes/__init__.py` (register the router)
- Modify: `backend/tests/test_route_authorization.py`
- Regenerate: `backend/openapi.json`, `frontend/src/lib/api/generated/schema.ts`

**Interfaces:**
- Consumes: `store.list_backups`, `store.safe_path`, `store.free_bytes`, `store.NAME_RE`, `store.new_name`, `crypto.read_header`, `gate.restore_status`, the two Celery tasks.
- Produces: schemas `BackupRead` (name, size_bytes, created_at, trigger, key_matches, app_version, alembic_revision), `BackupList` (items, free_bytes), `RestoreStatus` (running, step, started_at, error); the routes listed in the spec.

- [ ] **Step 1: Write the failing API tests**

```python
"""The backup routes: admin-only, name-safe, and never a surprise restore.

Uses a tmp backup directory through the settings override the other API tests
use for paths, so nothing here touches a real backup.
"""

from __future__ import annotations

import io

import pytest
from app.services.backup.archive import BackupMeta, build_archive
from app.services.backup.crypto import encrypt_stream
from httpx import AsyncClient

# ... fixtures: `client`, `auth`, `member_auth`, and a `backup_dir` fixture that
# points settings.backup_dir at tmp_path (follow tests/test_settings_api.py for
# the settings-override style used in this repo).


async def test_the_list_shows_what_is_in_the_directory(client, auth, backup_dir) -> None:
    _write_backup(backup_dir, "megoopm-20261011-030000-manual.mgb")

    body = (await client.get("/api/v1/backups", headers=auth)).json()

    assert [row["name"] for row in body["items"]] == ["megoopm-20261011-030000-manual.mgb"]
    assert body["items"][0]["key_matches"] is True
    assert body["free_bytes"] > 0


async def test_a_member_cannot_see_or_take_backups(client, member_auth) -> None:
    assert (await client.get("/api/v1/backups", headers=member_auth)).status_code == 403
    assert (await client.post("/api/v1/backups", headers=member_auth)).status_code == 403


async def test_taking_a_backup_enqueues_the_task(client, auth, backup_dir) -> None:
    response = await client.post("/api/v1/backups", headers=auth)

    assert response.status_code == 202
    assert response.json()["task_id"]


async def test_downloading_streams_the_file(client, auth, backup_dir) -> None:
    path = _write_backup(backup_dir, "megoopm-20261011-030000-manual.mgb")

    response = await client.get(
        "/api/v1/backups/megoopm-20261011-030000-manual.mgb/download", headers=auth
    )

    assert response.status_code == 200
    assert response.content == path.read_bytes()


@pytest.mark.parametrize(
    "name", ["../../etc/passwd", "secrets.txt", "megoopm-20261011-030000-manual.mgb.part"]
)
async def test_a_name_that_is_not_a_backup_is_a_404(client, auth, backup_dir, name) -> None:
    # Review Focus 3: the name comes from the URL, so this is the boundary.
    assert (await client.get(f"/api/v1/backups/{name}/download", headers=auth)).status_code == 404
    assert (await client.delete(f"/api/v1/backups/{name}", headers=auth)).status_code == 404


async def test_deleting_removes_the_file(client, auth, backup_dir) -> None:
    _write_backup(backup_dir, "megoopm-20261011-030000-auto.mgb")

    response = await client.delete("/api/v1/backups/megoopm-20261011-030000-auto.mgb", headers=auth)

    assert response.status_code == 204
    assert list(backup_dir.glob("*.mgb")) == []


async def test_an_upload_lands_as_an_upload(client, auth, backup_dir) -> None:
    # Renamed on arrival: a file called ...-auto.mgb from somewhere else must
    # not be swept by this instance's retention.
    payload = _sealed_bytes()

    response = await client.post(
        "/api/v1/backups/upload",
        headers=auth,
        files={"file": ("megoopm-20261011-030000-auto.mgb", io.BytesIO(payload), "application/octet-stream")},
    )

    assert response.status_code == 201, response.text
    assert response.json()["trigger"] == "upload"
    assert [p.name.rsplit("-", 1)[1] for p in backup_dir.glob("*.mgb")] == ["upload.mgb"]


async def test_an_upload_that_is_not_a_backup_is_refused(client, auth, backup_dir) -> None:
    response = await client.post(
        "/api/v1/backups/upload",
        headers=auth,
        files={"file": ("megoopm-20261011-030000-upload.mgb", io.BytesIO(b"nope"), "application/octet-stream")},
    )

    assert response.status_code == 400
    assert list(backup_dir.iterdir()) == []


async def test_an_oversized_upload_is_refused_and_leaves_nothing(
    client, auth, backup_dir, monkeypatch
) -> None:
    # Review Focus 3: the cap must bite while streaming, not after the disk is
    # full. settings.backup_max_bytes is tiny here.
    monkeypatch.setattr("app.api.routes.backups.settings.backup_max_bytes", 16)

    response = await client.post(
        "/api/v1/backups/upload",
        headers=auth,
        files={"file": ("megoopm-20261011-030000-upload.mgb", io.BytesIO(_sealed_bytes()), "application/octet-stream")},
    )

    assert response.status_code == 413
    assert list(backup_dir.iterdir()) == []


async def test_restore_status_is_quiet_when_nothing_runs(client, auth) -> None:
    body = (await client.get("/api/v1/backups/restore-status", headers=auth)).json()

    assert body["running"] is False


async def test_restoring_enqueues_the_task_and_audits_it(client, auth, backup_dir) -> None:
    _write_backup(backup_dir, "megoopm-20261011-030000-manual.mgb")

    response = await client.post(
        "/api/v1/backups/megoopm-20261011-030000-manual.mgb/restore", headers=auth
    )

    assert response.status_code == 202
    audit = await client.get("/api/v1/audit-log?object_type=backup", headers=auth)
    assert audit.json()["items"][0]["action"] == "restore"
```

Helpers `_write_backup` and `_sealed_bytes` build a real sealed archive the way `tests/test_backup_store.py` does; copy that helper and keep one definition per file.

- [ ] **Step 2: Run them and watch them fail**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backups_api.py`
Expected: 404s / import errors — the router does not exist.

- [ ] **Step 3: Write the schemas and the router**

`backend/app/schemas/backup.py`:

```python
"""What the backups API returns. Mirrors services/backup/store.BackupFile."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.backup.store import BackupFile


class BackupRead(BaseModel):
    """One file in the backup directory, as the page shows it."""

    name: str
    size_bytes: int
    created_at: datetime
    trigger: str = Field(description="manual | auto | upload")
    #: False when the file was made under a different SECRET_KEY: it is listed
    #: (so it can be deleted) but cannot be read or restored here.
    key_matches: bool
    app_version: str | None = None
    alembic_revision: str | None = None

    @classmethod
    def from_file(cls, row: BackupFile) -> BackupRead:
        return cls(
            name=row.name,
            size_bytes=row.size_bytes,
            created_at=row.created_at,
            trigger=row.trigger,
            key_matches=row.key_matches,
            app_version=row.app_version,
            alembic_revision=row.alembic_revision,
        )


class BackupList(BaseModel):
    """The directory's contents, newest first, plus room left on the disk."""

    items: list[BackupRead] = Field(default_factory=list)
    free_bytes: int


class RestoreStatus(BaseModel):
    """What a restore is doing — the one thing the API answers during one."""

    running: bool
    step: str | None = None
    started_at: str | None = None
    #: Why the last restore stopped, kept briefly after it ends.
    error: str | None = None
```

`backend/app/api/routes/backups.py`:

```python
"""The backups API: list, take, download, upload, delete, restore.

Admin-only throughout. Every route that takes a name resolves it through
``store.safe_path``, which refuses anything that is not a file this instance
would have written — the name comes from the URL, so that check is the boundary
between an admin and the filesystem.

Route order matters: the literal paths are declared before ``/{name}`` so
``/upload`` and ``/restore-status`` are never captured as backup names.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import redis
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import AdminUser, SessionDep
from app.core.config import settings
from app.models.enums import AuditAction
from app.schemas.backup import BackupList, BackupRead, RestoreStatus
from app.schemas.tasks import TaskEnqueued
from app.services.audit import record_audit
from app.services.backup import gate, store
from app.services.backup.crypto import BackupFormatError, read_header
from app.tasks.backup import create_backup_task, restore_backup_task

router = APIRouter(tags=["backups"])

#: Streamed in both directions; the files are far too large for one buffer.
_IO_CHUNK = 1024 * 1024


def _dir() -> Path:
    return Path(settings.backup_dir)


def _resolve(name: str) -> Path:
    try:
        return store.safe_path(_dir(), name)
    except store.UnknownBackupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("", response_model=BackupList)
async def list_backups(_admin: AdminUser) -> BackupList:
    """Every backup in the directory, newest first. Admin-only."""
    rows = store.list_backups(_dir(), secret_key=settings.secret_key)
    return BackupList(
        items=[BackupRead.from_file(row) for row in rows],
        free_bytes=store.free_bytes(_dir()) if _dir().is_dir() else 0,
    )


@router.get("/restore-status", response_model=RestoreStatus)
async def read_restore_status(_admin: AdminUser) -> RestoreStatus:
    """What a restore is doing. The one route that answers during a restore."""
    client = redis.Redis.from_url(settings.redis_url)
    try:
        return RestoreStatus(**gate.restore_status(client))
    finally:
        client.close()


@router.post("", response_model=TaskEnqueued, status_code=status.HTTP_202_ACCEPTED)
async def take_backup(admin: AdminUser, db: SessionDep) -> TaskEnqueued:
    """Take a backup now. Admin-only; runs on the worker."""
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.create,
        object_type="backup",
        meta={"trigger": "manual"},
    )
    await db.commit()
    async_result = create_backup_task.delay("manual")
    return TaskEnqueued(task_id=async_result.id, status=async_result.status)


@router.post("/upload", response_model=BackupRead, status_code=status.HTTP_201_CREATED)
async def upload_backup(
    admin: AdminUser, db: SessionDep, file: Annotated[UploadFile, File()]
) -> BackupRead:
    """Accept a backup made elsewhere. Admin-only.

    Streamed to a ``.part`` file and checked before it is given a real name: an
    oversized or malformed upload must leave nothing behind, and must never be
    listed as something an admin could restore.
    """
    _dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    name = store.new_name(datetime.now(UTC), "upload")
    part = _dir() / f"{name}{store.PART_SUFFIX}"
    written = 0
    try:
        with part.open("wb") as out:
            while chunk := await file.read(_IO_CHUNK):
                written += len(chunk)
                if written > settings.backup_max_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"a backup may be at most {settings.backup_max_bytes} bytes",
                    )
                out.write(chunk)
        with part.open("rb") as handle:
            read_header(handle)
    except HTTPException:
        part.unlink(missing_ok=True)
        raise
    except BackupFormatError as exc:
        part.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise

    final = _dir() / name
    part.replace(final)
    final.chmod(0o600)
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.create,
        object_type="backup",
        meta={"trigger": "upload", "name": name, "bytes": written},
    )
    await db.commit()
    return BackupRead.from_file(store.describe(final, secret_key=settings.secret_key))


@router.get("/{name}/download")
async def download_backup(name: str, admin: AdminUser, db: SessionDep) -> StreamingResponse:
    """Stream one backup to the browser. Admin-only, and audited: this is the
    one route that takes TLS private keys off the server."""
    path = _resolve(name)
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.update,
        object_type="backup",
        meta={"event": "download", "name": name},
    )
    await db.commit()

    def chunks():
        with path.open("rb") as handle:
            while chunk := handle.read(_IO_CHUNK):
                yield chunk

    return StreamingResponse(
        chunks(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Content-Length": str(path.stat().st_size),
        },
    )


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backup(name: str, admin: AdminUser, db: SessionDep) -> None:
    """Delete one backup. Admin-only."""
    path = _resolve(name)
    path.unlink(missing_ok=True)
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.delete,
        object_type="backup",
        meta={"name": name},
    )
    await db.commit()


@router.post("/{name}/restore", response_model=TaskEnqueued, status_code=status.HTTP_202_ACCEPTED)
async def restore_from_backup(name: str, admin: AdminUser, db: SessionDep) -> TaskEnqueued:
    """Replace this instance from a backup. Admin-only; runs on the worker.

    The audit row is written *before* the task is enqueued: the restore will
    replace the audit log itself, so this record of who asked only survives in
    the backup being restored — and in the worker log.
    """
    _resolve(name)
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.restore,
        object_type="backup",
        meta={"name": name},
    )
    await db.commit()
    async_result = restore_backup_task.delay(name)
    return TaskEnqueued(task_id=async_result.id, status=async_result.status)
```

Register it in `backend/app/api/routes/__init__.py` the way the others are
registered, with `prefix="/backups"`, and add every route to `ROUTE_ROLES` in
`backend/tests/test_route_authorization.py` as `"admin"`:

```python
    ("GET", "/api/v1/backups"): "admin",
    ("POST", "/api/v1/backups"): "admin",
    ("GET", "/api/v1/backups/restore-status"): "admin",
    ("POST", "/api/v1/backups/upload"): "admin",
    ("GET", "/api/v1/backups/{name}/download"): "admin",
    ("DELETE", "/api/v1/backups/{name}"): "admin",
    ("POST", "/api/v1/backups/{name}/restore"): "admin",
```

- [ ] **Step 4: Run the tests**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_backups_api.py tests/test_route_authorization.py`
Expected: all pass.

- [ ] **Step 5: Regenerate the contract and commit**

```bash
docker exec -w /src megoopm-test python -m scripts.export_openapi
(cd frontend && npm run gen:api)
docker exec -w /src megoopm-test ruff format app tests
docker exec -w /src megoopm-test ruff check app tests
git add backend frontend/src/lib/api/generated/schema.ts
git commit -m "feat(backup): the backups API"
```

---

### Task 9: The page — list, download, upload, delete

**Files:**
- Create: `frontend/src/lib/api/resources/backups.ts`
- Create: `frontend/src/app/(app)/backups/page.tsx`
- Create: `frontend/src/components/backups/backups-view.tsx`
- Create: `frontend/src/components/backups/lib.ts`
- Create: `frontend/src/components/backups/lib.test.ts`
- Create: `frontend/src/components/backups/backups-view.test.tsx`
- Modify: `frontend/src/lib/api/index.ts`, `frontend/src/config/nav.ts`

**Interfaces:**
- Produces:
  - `backups.list()`, `backups.create()`, `backups.remove(name)`, `backups.restore(name)`, `backups.restoreStatus()`, `backups.download(name)`, `backups.upload(file)`
  - `formatBytes(n: number): string`, `triggerLabel(trigger: string): string` in `components/backups/lib.ts`

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/backups/lib.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { formatBytes, triggerLabel } from "@/components/backups/lib";

describe("formatBytes", () => {
  it("scales to the unit a person would say out loud", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatBytes(3 * 1024 ** 3)).toBe("3.0 GB");
  });

  it("says zero rather than nothing", () => {
    expect(formatBytes(0)).toBe("0 B");
  });
});

describe("triggerLabel", () => {
  it("names how a backup came to exist", () => {
    expect(triggerLabel("manual")).toBe("Manual");
    expect(triggerLabel("auto")).toBe("Automatic");
    expect(triggerLabel("upload")).toBe("Uploaded");
  });
});
```

`frontend/src/components/backups/backups-view.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { backups, instanceSettings } from "@/lib/api";
import { BackupsView } from "@/components/backups/backups-view";

vi.mock("@/lib/events", () => ({ subscribeToEvents: () => () => {} }));

function makeBackup(over: Partial<Awaited<ReturnType<typeof backups.list>>["items"][number]> = {}) {
  return {
    name: "megoopm-20261011-030000-manual.mgb",
    size_bytes: 5 * 1024 * 1024,
    created_at: "2026-10-11T03:00:00Z",
    trigger: "manual",
    key_matches: true,
    app_version: "0.1.0",
    alembic_revision: "0039_backups",
    ...over,
  };
}

beforeEach(() => {
  vi.spyOn(backups, "list").mockResolvedValue({ items: [makeBackup()], free_bytes: 10 ** 10 });
  vi.spyOn(backups, "create").mockResolvedValue({ task_id: "t1", status: "PENDING" } as never);
  vi.spyOn(backups, "remove").mockResolvedValue(undefined as never);
  vi.spyOn(backups, "restoreStatus").mockResolvedValue({
    running: false,
    step: null,
    started_at: null,
    error: null,
  });
  vi.spyOn(instanceSettings, "get").mockResolvedValue({
    backup_frequency: "off",
    backup_hour_utc: 3,
    backup_weekday: 6,
    backup_keep: 7,
    last_backup_ok: true,
    last_backup_at: "2026-10-11T03:00:05Z",
    last_backup_message: null,
  } as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("BackupsView", () => {
  it("lists what is on the server", async () => {
    render(<BackupsView />);

    expect(await screen.findByText("megoopm-20261011-030000-manual.mgb")).toBeInTheDocument();
    expect(screen.getByText("5.0 MB")).toBeInTheDocument();
    expect(screen.getByText(/manual/i)).toBeInTheDocument();
  });

  it("takes a backup on request", async () => {
    const user = userEvent.setup();
    render(<BackupsView />);

    await user.click(await screen.findByRole("button", { name: /back up now/i }));

    expect(backups.create).toHaveBeenCalledTimes(1);
  });

  it("warns about a backup this instance cannot read", async () => {
    // Restoring it would fail, and its secrets would be unreadable: say so
    // before an admin plans a recovery around it.
    vi.mocked(backups.list).mockResolvedValue({
      items: [makeBackup({ key_matches: false, app_version: null })],
      free_bytes: 10 ** 10,
    });
    render(<BackupsView />);

    expect(await screen.findByText(/different secret key/i)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /restore/i })).toBeDisabled();
  });

  it("says when there is nothing to restore from", async () => {
    vi.mocked(backups.list).mockResolvedValue({ items: [], free_bytes: 10 ** 10 });
    render(<BackupsView />);

    expect(await screen.findByText(/no backups yet/i)).toBeInTheDocument();
  });

  it("deletes a backup after confirmation", async () => {
    const user = userEvent.setup();
    render(<BackupsView />);

    await user.click(await screen.findByRole("button", { name: /delete/i }));
    await user.click(await screen.findByRole("button", { name: /^delete$/i }));

    await waitFor(() =>
      expect(backups.remove).toHaveBeenCalledWith("megoopm-20261011-030000-manual.mgb"),
    );
  });
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd frontend && npx vitest run src/components/backups`
Expected: unresolved imports for `@/components/backups/lib` and `backups-view`.

- [ ] **Step 3: Write the API resource**

```ts
/**
 * Typed client for the backups API (admin-only).
 *
 * Two calls do not go through `api`: downloading needs the raw bytes and
 * uploading needs multipart, and the shared client serialises JSON. Both reach
 * for the same bearer token the rest of the app uses.
 */
import { api } from "@/lib/api/client";
import { getAccessToken } from "@/lib/auth/session";
import type { Schemas } from "@/lib/api/types";

export type Backup = Schemas["BackupRead"];
export type BackupList = Schemas["BackupList"];
export type RestoreStatus = Schemas["RestoreStatus"];

const BASE = "/api/v1/backups";

function url(path: string): string {
  return `${process.env.NEXT_PUBLIC_API_BASE_URL ?? ""}${path}`;
}

export const backups = {
  list: () => api.get<BackupList>(BASE),
  create: () => api.post<Schemas["TaskEnqueued"]>(BASE, {}),
  remove: (name: string) => api.delete<void>(`${BASE}/${encodeURIComponent(name)}`),
  restore: (name: string) =>
    api.post<Schemas["TaskEnqueued"]>(`${BASE}/${encodeURIComponent(name)}/restore`, {}),
  restoreStatus: () => api.get<RestoreStatus>(`${BASE}/restore-status`),

  /** Fetches the bytes and hands them to the browser as a save dialog. */
  async download(name: string): Promise<void> {
    const response = await fetch(url(`${BASE}/${encodeURIComponent(name)}/download`), {
      headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` },
    });
    if (!response.ok) throw new Error(`Download failed with ${response.status}`);
    const href = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = href;
    link.download = name;
    link.click();
    URL.revokeObjectURL(href);
  },

  async upload(file: File): Promise<Backup> {
    const body = new FormData();
    body.append("file", file);
    const response = await fetch(url(`${BASE}/upload`), {
      method: "POST",
      headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` },
      body,
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(detail?.detail ?? `Upload failed with ${response.status}`);
    }
    return (await response.json()) as Backup;
  },
} as const;
```

Export `backups` and its types from `frontend/src/lib/api/index.ts` beside the others.

- [ ] **Step 4: Write `lib.ts`, the view, the page and the nav entry**

`frontend/src/components/backups/lib.ts`:

```ts
/** Sizes as a person would say them: three significant digits, binary units. */
export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${value} B` : `${value.toFixed(1)} ${units[unit]}`;
}

/** How a backup came to exist, in the words the page uses. */
export function triggerLabel(trigger: string): string {
  if (trigger === "auto") return "Automatic";
  if (trigger === "upload") return "Uploaded";
  return "Manual";
}
```

`backups-view.tsx` is a client component following `components/audit-log/audit-log-view.tsx`: load `backups.list()` and `instanceSettings.get()` on mount, refresh on `backup.`/`restore.` events plus a 60s poll, render

- a header card with **Back up now**, the last-run line (`last_backup_ok`, `last_backup_at`, `last_backup_message`), free space from `free_bytes`, and an **Upload** input (`<input type="file" accept=".mgb">`);
- `<ScheduleCard />` from Task 10;
- the list: a `<table>` on desktop, `RowCards`/`RowCard` under `useIsMobile()`, with Download, Restore (disabled, with the "made with a different secret key" note, when `key_matches` is false) and a red Delete that confirms in a dialog.

`frontend/src/app/(app)/backups/page.tsx`:

```tsx
import type { Metadata } from "next";

import { AdminOnly } from "@/components/admin-only";
import { BackupsView } from "@/components/backups/backups-view";

export const metadata: Metadata = { title: "Backups" };

export default function BackupsPage() {
  return (
    <AdminOnly>
      <BackupsView />
    </AdminOnly>
  );
}
```

`frontend/src/config/nav.ts`: import `Archive` from lucide and add, immediately before the Settings entry:

```ts
  {
    title: "Backups",
    href: "/backups",
    icon: Archive,
    description: "Encrypted copies of this instance, and restoring from one.",
    adminOnly: true,
  },
```

- [ ] **Step 5: Run the tests**

Run: `cd frontend && npx vitest run src/components/backups && npx tsc --noEmit`
Expected: all pass, no type errors.

- [ ] **Step 6: Commit**

```bash
cd frontend && npx eslint src/components/backups src/lib/api src/config
# prettier: only files whose HEAD version is already clean, at that width
git add frontend/src
git commit -m "feat(ui): the Backups page — list, take, download, upload, delete"
```

---

### Task 10: The schedule card

**Files:**
- Create: `frontend/src/components/backups/schedule-card.tsx`
- Create: `frontend/src/components/backups/schedule-card.test.tsx`
- Modify: `frontend/src/lib/api/resources/settings.ts`
- Modify: `frontend/src/components/backups/backups-view.tsx` (render it)

**Interfaces:**
- Consumes: `instanceSettings.get()`, Task 4's `PATCH /api/v1/settings/backups`.
- Produces: `instanceSettings.updateBackups(body: Schemas["BackupSettingsUpdate"])`; `<ScheduleCard settings={…} onSaved={…} />`.

- [ ] **Step 1: Write the failing test**

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { instanceSettings } from "@/lib/api";
import { ScheduleCard } from "@/components/backups/schedule-card";

const SETTINGS = {
  backup_frequency: "daily",
  backup_hour_utc: 3,
  backup_weekday: 6,
  backup_keep: 7,
} as never;

beforeEach(() => {
  vi.spyOn(instanceSettings, "updateBackups").mockResolvedValue(SETTINGS);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ScheduleCard", () => {
  it("shows the stored schedule", () => {
    render(<ScheduleCard settings={SETTINGS} onSaved={() => {}} />);

    expect(screen.getByLabelText(/how often/i)).toHaveTextContent(/daily/i);
    expect(screen.getByLabelText(/keep the last/i)).toHaveValue(7);
  });

  it("saves a changed retention count", async () => {
    const user = userEvent.setup();
    render(<ScheduleCard settings={SETTINGS} onSaved={() => {}} />);

    await user.clear(screen.getByLabelText(/keep the last/i));
    await user.type(screen.getByLabelText(/keep the last/i), "14");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(instanceSettings.updateBackups).toHaveBeenCalledWith(
        expect.objectContaining({ keep: 14, frequency: "daily", hour_utc: 3 }),
      ),
    );
  });

  it("hides the weekday unless the schedule is weekly", async () => {
    const user = userEvent.setup();
    render(<ScheduleCard settings={SETTINGS} onSaved={() => {}} />);
    expect(screen.queryByLabelText(/day of the week/i)).not.toBeInTheDocument();

    await user.click(screen.getByLabelText(/how often/i));
    await user.click(await screen.findByRole("option", { name: /weekly/i }));

    expect(await screen.findByLabelText(/day of the week/i)).toBeInTheDocument();
  });

  it("says plainly that manual backups are never swept", () => {
    // Retention is the one setting here that deletes data.
    render(<ScheduleCard settings={SETTINGS} onSaved={() => {}} />);

    expect(screen.getByText(/manual backups are kept until you delete them/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && npx vitest run src/components/backups/schedule-card.test.tsx`
Expected: unresolved import for `schedule-card`.

- [ ] **Step 3: Write the card and the resource method**

Add to `frontend/src/lib/api/resources/settings.ts`:

```ts
  /** The automatic-backup schedule; takes effect at the next hourly tick. */
  updateBackups: (body: BackupSettingsUpdate) =>
    api.patch<InstanceSettings>(`${BASE}/backups`, body),
```

with `export type BackupSettingsUpdate = Schemas["BackupSettingsUpdate"];` beside the other type aliases.

`schedule-card.tsx` follows `components/security/hub-updates-card.tsx`: a `Select` for frequency (Off / Daily / Weekly), a `Select` for the hour (00:00–23:00 UTC), a `Select` for the weekday shown only when weekly, a number `Input` for "Keep the last", a Save button, a `toast.success("Backup schedule saved")` / `toast.error(describeError(err).message)`, and the sentence "Manual backups are kept until you delete them."

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npx vitest run src/components/backups && npx tsc --noEmit`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): the automatic-backup schedule card"
```

---

### Task 11: Restoring from the page

**Files:**
- Create: `frontend/src/components/backups/restore-dialog.tsx`
- Create: `frontend/src/components/backups/restore-dialog.test.tsx`
- Create: `frontend/src/components/backups/restore-blocker.tsx`
- Create: `frontend/src/components/backups/restore-blocker.test.tsx`
- Modify: `frontend/src/components/backups/backups-view.tsx`

**Interfaces:**
- Consumes: `backups.restore(name)`, `backups.restoreStatus()`, `Backup`.
- Produces: `<RestoreDialog backup={…} hostname={…} open onOpenChange={…} onStarted={…} />`, `<RestoreBlocker />`.

- [ ] **Step 1: Write the failing tests**

`restore-dialog.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { backups } from "@/lib/api";
import { RestoreDialog } from "@/components/backups/restore-dialog";

const BACKUP = {
  name: "megoopm-20261011-030000-manual.mgb",
  size_bytes: 1024,
  created_at: "2026-10-11T03:00:00Z",
  trigger: "manual",
  key_matches: true,
  app_version: "0.1.0",
  alembic_revision: "0039_backups",
};

function renderDialog() {
  return render(
    <RestoreDialog
      backup={BACKUP as never}
      hostname="pm.example.com"
      open
      onOpenChange={() => {}}
      onStarted={() => {}}
    />,
  );
}

beforeEach(() => {
  vi.spyOn(backups, "restore").mockResolvedValue({ task_id: "t1", status: "PENDING" } as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RestoreDialog", () => {
  it("says what a restore will do before it is possible to start one", () => {
    renderDialog();

    expect(screen.getByText(/replaces this instance/i)).toBeInTheDocument();
    expect(screen.getByText(/everyone is signed out/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^restore$/i })).toBeDisabled();
  });

  it("stays disabled until the hostname is typed exactly", async () => {
    // The typed confirmation is the only thing between a mis-click and a
    // replaced database.
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText(/type .*pm\.example\.com/i), "pm.example.co");
    expect(screen.getByRole("button", { name: /^restore$/i })).toBeDisabled();

    await user.type(screen.getByLabelText(/type .*pm\.example\.com/i), "m");
    expect(screen.getByRole("button", { name: /^restore$/i })).toBeEnabled();
  });

  it("starts the restore once confirmed", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText(/type .*pm\.example\.com/i), "pm.example.com");
    await user.click(screen.getByRole("button", { name: /^restore$/i }));

    await waitFor(() => expect(backups.restore).toHaveBeenCalledWith(BACKUP.name));
  });
});
```

`restore-blocker.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { backups } from "@/lib/api";
import { RestoreBlocker } from "@/components/backups/restore-blocker";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RestoreBlocker", () => {
  it("shows the step while a restore runs", async () => {
    vi.spyOn(backups, "restoreStatus").mockResolvedValue({
      running: true,
      step: "restoring the database",
      started_at: "2026-10-11T03:00:00Z",
      error: null,
    });

    render(<RestoreBlocker />);

    expect(await screen.findByText(/restoring the database/i)).toBeInTheDocument();
  });

  it("tells the admin to sign in again when the restore finishes", async () => {
    // The sessions table came from the backup, so their token is gone.
    const status = vi.spyOn(backups, "restoreStatus");
    status.mockResolvedValueOnce({
      running: true,
      step: "restoring files",
      started_at: "2026-10-11T03:00:00Z",
      error: null,
    });
    status.mockResolvedValue({ running: false, step: null, started_at: null, error: null });

    render(<RestoreBlocker />);

    await waitFor(() => expect(screen.getByText(/sign in again/i)).toBeInTheDocument(), {
      timeout: 5000,
    });
  });

  it("shows why a restore stopped", async () => {
    vi.spyOn(backups, "restoreStatus").mockResolvedValue({
      running: false,
      step: null,
      started_at: null,
      error: "this backup was made with a different SECRET_KEY",
    });

    render(<RestoreBlocker />);

    expect(await screen.findByText(/different secret key/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd frontend && npx vitest run src/components/backups`
Expected: unresolved imports for the two new components.

- [ ] **Step 3: Write the components**

`restore-dialog.tsx`: a `Dialog` (size `md`) whose body states, in sentences: the database is replaced by the one in this backup; the certificates are replaced; everyone is signed out; nginx keeps serving while it runs. It lists the backup's name, time, size and app version, then an `Input` labelled `Type pm.example.com to confirm`. The Restore button is `variant="destructive"`, disabled until the typed value equals `hostname`, and on click calls `backups.restore(backup.name)`, toasts, and calls `onStarted()`.

`restore-blocker.tsx`: polls `backups.restoreStatus()` every 2s. While `running`, it renders a full-screen overlay with the step; when it flips to not-running after having been running, it says "Restore finished — sign in again" with a link to `/login`; when `error` is set it shows the error and a Close button. The view mounts it whenever a restore is running.

In `backups-view.tsx`: wire the Restore button to open `RestoreDialog`, read the hostname from `window.location.hostname`, and render `<RestoreBlocker />` when `restoreStatus().running` is true (it is already polled there).

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: the whole frontend suite passes.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): restore from the Backups page, behind a typed confirmation"
```

---

### Task 12: Wiring the deployment, documenting it, and the full gate

**Files:**
- Modify: `.env.example`, `docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.ha.yml`
- Create: `backend/tests/test_compose_backups.py`
- Create: `docs/backups.md`
- Modify: `README.md` (one line linking the doc)

- [ ] **Step 1: Write the failing compose test**

```python
"""Backup wiring in the compose files.

The backend lists and streams backups; the worker writes them. Both need the
same directory, or a backup an admin takes is invisible to the page that took
it — a failure nobody notices until a restore is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MOUNT = "${BACKUP_PATH:-./backups}:/backups"


def _services(compose_file: str) -> dict:
    path = REPO_ROOT / compose_file
    if not path.exists():  # pragma: no cover - partial checkout
        pytest.skip(f"{compose_file} not present at {REPO_ROOT}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.dev.yml"])
@pytest.mark.parametrize("service", ["backend", "worker"])
def test_the_backup_directory_is_mounted(compose_file: str, service: str) -> None:
    assert MOUNT in _services(compose_file)[service]["volumes"]


def test_the_example_env_documents_the_host_path() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "BACKUP_PATH=" in text
```

(The HA file's control-plane services use `${SHARED_DATA_PATH:?}`-style paths; add the same mount there with the project's HA convention and extend the parametrize list if it matches.)

- [ ] **Step 2: Run it and watch it fail**

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v C:/Projects/megoopm:/repo -w /repo/backend --entrypoint sh megoopm-backend \
  -c 'pip install -q pytest >/dev/null 2>&1; python -m pytest tests/test_compose_backups.py --noconftest -p no:cacheprovider -o addopts=""'
```

Expected: failures — no mount, no env line.

- [ ] **Step 3: Wire it up**

`.env.example`, after the CrowdSec block:

```bash
# Where encrypted backups are written on the HOST. Keep it off any web-served
# path: a backup contains TLS private keys and every stored secret, and anyone
# with both the file and SECRET_KEY has the instance.
BACKUP_PATH=./backups
```

In `docker-compose.yml`, `docker-compose.dev.yml` and `docker-compose.ha.yml`, add to the `backend` and `worker` services' `volumes`:

```yaml
      # Backups: the backend lists and streams them, the worker writes them.
      - ${BACKUP_PATH:-./backups}:/backups
```

- [ ] **Step 4: Write the documentation**

`docs/backups.md` covers: what a backup contains and what it does not; the `SECRET_KEY` relationship (and that a restore onto a new server needs the same key); where files live and the `0700`/`0600` modes; the schedule and that retention never sweeps manual backups; how to restore, and what happens during one (API 503, everyone signed out, nginx unaffected); how to restore onto a fresh server (install, same `SECRET_KEY`, upload, restore); and the warning that whoever holds a backup plus the key holds the instance. Link it from `README.md`.

- [ ] **Step 5: Run the full gate**

```bash
export MSYS_NO_PATHCONV=1
docker exec -w /src megoopm-test python -m pytest
docker exec -w /src megoopm-test ruff check app tests alembic
docker run --rm -v C:/Projects/megoopm:/repo -w /repo/backend --entrypoint sh megoopm-backend \
  -c 'pip install -q pytest >/dev/null 2>&1; python -m pytest tests/test_compose_backups.py --noconftest -p no:cacheprovider -o addopts=""'
(cd frontend && npx vitest run && npx tsc --noEmit && npx eslint src)
```

Expected: everything green.

- [ ] **Step 6: Exercise it once by hand before calling it done**

```bash
docker compose up -d --build
# In the UI: Backups → Back up now. Then:
ls -l ./backups
# Take a second backup, delete a proxy host, restore the first, and check the
# host is back and you were signed out.
```

Expected: a `.mgb` file appears with mode `0600`; the restore completes; the deleted host is back. Report what you saw — a green test suite is not evidence that a restore works end to end.

- [ ] **Step 7: Commit**

```bash
git add .env.example docker-compose.yml docker-compose.dev.yml docker-compose.ha.yml backend/tests/test_compose_backups.py docs/backups.md README.md
git commit -m "feat(backup): wire the backup directory into compose, and document it"
```
