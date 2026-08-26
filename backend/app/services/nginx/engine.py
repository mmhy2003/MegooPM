"""Apply a rendered nginx configuration to disk, safely.

``apply_config`` is the transactional core of the reload engine. It:

1. Serialises with a cross-process file lock so concurrent applies (multiple
   Celery workers on the shared ``conf.d`` volume) never interleave writes.
2. Renders the desired state and compares it to the managed files already on
   disk. Identical → returns immediately without touching nginx (**idempotent**;
   no needless reloads).
3. Writes the new files atomically, then validates the whole config with the
   controller's ``nginx -t``.
4. If validation fails, **rolls back** to the exact previous file set and does
   *not* reload — a broken config can never reach a running nginx.
5. Only on a clean validation does it reload; if the reload itself fails it
   restores the last-known-good files and re-reloads.

"Managed" files are exactly those whose name starts with ``managed_prefix``;
anything an operator drops into ``conf.d`` by hand is left untouched.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from app.services.nginx.controller import NginxController
from app.services.nginx.renderer import render_config, render_stream_config
from app.services.nginx.state import DesiredState

DEFAULT_MANAGED_PREFIX = "megoopm-"
LOCK_FILENAME = ".megoopm-nginx.lock"


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """The observable outcome of an :func:`apply_config` run (JSON-friendly)."""

    changed: bool
    valid: bool
    reloaded: bool
    rolled_back: bool
    message: str
    managed_files: list[str] = field(default_factory=list)
    test_output: str = ""
    reload_output: str = ""

    def as_dict(self) -> dict:
        """A plain dict suitable as a Celery (JSON) task result."""
        return {
            "changed": self.changed,
            "valid": self.valid,
            "reloaded": self.reloaded,
            "rolled_back": self.rolled_back,
            "message": self.message,
            "managed_files": list(self.managed_files),
            "test_output": self.test_output,
            "reload_output": self.reload_output,
        }


@contextmanager
def _dir_lock(lock_path: Path) -> Iterator[None]:
    """Exclusive advisory lock via ``flock`` — serialises applies cross-process."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_managed(confd: Path, prefix: str) -> dict[str, str]:
    """Read every managed file currently on disk into ``{name: content}``.

    Managed = the filename starts with ``prefix``. This covers both ``.conf``
    files and sidecar data files (e.g. access-list ``.htpasswd`` files) so the
    idempotency compare and rollback account for them too. The advisory lock file
    and atomic-write temp files start with ``.`` and are excluded by the glob.
    """
    current: dict[str, str] = {}
    for path in confd.glob(f"{prefix}*"):
        if path.is_file():
            current[path.name] = path.read_text(encoding="utf-8")
    return dict(sorted(current.items()))


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically (write temp, fsync, rename)."""
    tmp = path.parent / f".{path.name}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _sync_to(confd: Path, prefix: str, desired: dict[str, str]) -> None:
    """Make the managed files on disk exactly match ``desired``."""
    for name, content in desired.items():
        _atomic_write(confd / name, content)
    for path in confd.glob(f"{prefix}*"):
        if path.is_file() and path.name not in desired:
            path.unlink()


def apply_config(
    state: DesiredState,
    *,
    confd_dir: str | os.PathLike[str],
    controller: NginxController,
    managed_prefix: str = DEFAULT_MANAGED_PREFIX,
    stream_dir: str | os.PathLike[str] | None = None,
) -> ApplyResult:
    """Render ``state`` and reconcile nginx's config dirs to it (see module doc).

    ``confd_dir`` receives the HTTP-context files (upstreams, proxy, redirection
    and dead hosts). When ``stream_dir`` is given, TCP/UDP stream files are
    reconciled there too — both directories are written, validated with a single
    ``nginx -t``, and rolled back together, so a bad stream can never leave the
    HTTP config half-applied (or vice versa).
    """
    confd = Path(confd_dir)
    confd.mkdir(parents=True, exist_ok=True)

    # (directory, managed-prefix, desired-files). The stream directory is a
    # second reconciliation target sharing the same lock and validation.
    targets: list[tuple[Path, str, dict[str, str]]] = [
        (confd, managed_prefix, render_config(state)),
    ]
    if stream_dir is not None:
        streamd = Path(stream_dir)
        streamd.mkdir(parents=True, exist_ok=True)
        targets.append((streamd, managed_prefix, render_stream_config(state)))

    # Lock on the primary conf.d dir; it serialises the whole multi-dir apply.
    with _dir_lock(confd / LOCK_FILENAME):
        currents = [_read_managed(d, prefix) for d, prefix, _ in targets]
        desireds = [files for _, _, files in targets]
        all_desired = sorted(name for files in desireds for name in files)

        if all(desired == current for desired, current in zip(desireds, currents)):
            return ApplyResult(
                changed=False,
                valid=True,
                reloaded=False,
                rolled_back=False,
                message="Configuration already up to date; nginx not reloaded.",
                managed_files=all_desired,
            )

        for (d, prefix, _), desired in zip(targets, desireds):
            _sync_to(d, prefix, desired)

        all_current = sorted(name for current in currents for name in current)

        def _rollback() -> None:
            for (d, prefix, _), current in zip(targets, currents):
                _sync_to(d, prefix, current)

        test = controller.test()
        if not test.ok:
            _rollback()  # roll back every dir to last-known-good
            return ApplyResult(
                changed=False,
                valid=False,
                reloaded=False,
                rolled_back=True,
                message="Generated configuration failed `nginx -t`; rolled back, nginx untouched.",
                managed_files=all_current,
                test_output=test.output,
            )

        reload = controller.reload()
        if not reload.ok:
            _rollback()  # restore previous good files
            controller.reload()  # best-effort: return nginx to the good config
            return ApplyResult(
                changed=False,
                valid=True,
                reloaded=False,
                rolled_back=True,
                message="nginx reload failed; rolled back to the previous configuration.",
                managed_files=all_current,
                test_output=test.output,
                reload_output=reload.output,
            )

        return ApplyResult(
            changed=True,
            valid=True,
            reloaded=True,
            rolled_back=False,
            message=f"Applied {len(all_desired)} managed file(s) and reloaded nginx.",
            managed_files=all_desired,
            test_output=test.output,
            reload_output=reload.output,
        )


__all__ = ["ApplyResult", "apply_config", "DEFAULT_MANAGED_PREFIX"]
