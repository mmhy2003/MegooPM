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
                # is_relative_to, not startswith: "/tmp/x-abc" starts with
                # "/tmp/x-ab" and would pass a string comparison.
                if not target.is_relative_to(dest.resolve()):
                    raise BackupArchiveError(
                        f"archive member escapes the destination: {member.name}"
                    )
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
