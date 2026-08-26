"""Cross-node locks for HA coordination (MEG-35).

Two locks, both backed by Postgres advisory locks in production because
Postgres is a hard shared dependency for every node — this avoids relying on
NFS ``flock`` semantics, which are notoriously mount-option-dependent:

* :func:`apply_lock` — an **exclusive** lock held around the whole nginx
  render → ``nginx -t`` → reload → version-bump sequence, so two nodes can never
  half-write the shared config set. Transaction-scoped
  (``pg_advisory_xact_lock``): released automatically on commit/rollback. It
  yields the live :class:`~sqlalchemy.engine.Connection` so the caller bumps the
  config version in the *same* transaction as the lock.
* :func:`leader_lock` — a **non-blocking** try-lock used to make periodic sweeps
  (cert renewal, CrowdSec sync) run once cluster-wide: whichever node grabs it
  does the work, the rest skip.

On non-Postgres engines (the SQLite test engine, or a single-host deployment
without a shared DB) both degrade to an OS file lock, which is correct within a
single host and keeps the code path exercisable without Postgres.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Connection, Engine, text

# Stable key for the global nginx-apply advisory lock ("MEGOngnx" as ASCII).
# Top bit is 0, so it is already a valid positive signed 64-bit ``bigint``.
APPLY_LOCK_KEY = 0x4D45474F4E474E58

_DEFAULT_LOCK_DIR = "/var/run/megoopm"


def _leader_key(name: str) -> int:
    """Derive a stable signed 64-bit advisory-lock key from a leader name."""
    digest = hashlib.sha256(name.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


@contextlib.contextmanager
def _file_lock(path: Path, *, blocking: bool = True) -> Iterator[bool]:
    """OS ``flock`` fallback. Yields True if the lock was acquired.

    Blocking mode always yields True (it waits). Non-blocking yields False when
    another holder already owns it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    acquired = False
    try:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(fd, flags)
            acquired = True
        except BlockingIOError:
            acquired = False
        yield acquired
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextlib.contextmanager
def apply_lock(
    engine: Engine,
    *,
    lock_file: str | os.PathLike[str] | None = None,
    key: int = APPLY_LOCK_KEY,
) -> Iterator[Connection]:
    """Hold the cluster-wide nginx-apply lock; yield a live DB connection.

    On Postgres the lock is a transaction-scoped advisory lock and the yielded
    connection has an open transaction, so a version bump inside the ``with``
    block commits atomically with the lock release. On other engines a file
    lock provides same-host mutual exclusion around the still-yielded
    connection.
    """
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            if engine.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})
                yield conn
            else:
                path = Path(lock_file or f"{_DEFAULT_LOCK_DIR}/nginx-apply.lock")
                with _file_lock(path, blocking=True):
                    yield conn
            trans.commit()
        except Exception:
            trans.rollback()
            raise


@contextlib.contextmanager
def leader_lock(
    engine: Engine,
    name: str,
    *,
    lock_file: str | os.PathLike[str] | None = None,
) -> Iterator[bool]:
    """Try (non-blocking) to become the cluster leader for ``name``.

    Yields True if this node acquired the lock (and should do the work), False
    otherwise. On Postgres it is a session-scoped ``pg_try_advisory_lock``,
    explicitly released on exit; elsewhere a non-blocking file lock.
    """
    if engine.dialect.name == "postgresql":
        key = _leader_key(name)
        with engine.connect() as conn:
            acquired = bool(
                conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar()
            )
            try:
                yield acquired
            finally:
                if acquired:
                    conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
    else:
        path = Path(lock_file or f"{_DEFAULT_LOCK_DIR}/leader-{name}.lock")
        with _file_lock(path, blocking=False) as acquired:
            yield acquired


__all__ = ["APPLY_LOCK_KEY", "apply_lock", "leader_lock"]
