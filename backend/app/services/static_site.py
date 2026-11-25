"""Static-site domain services.

CRUD business logic for static sites; routes stay thin. No FastAPI imports
— callers pass an :class:`~sqlalchemy.ext.asyncio.AsyncSession` and plain values.

Also answers what is on disk: :func:`folder_status` for one site's folder and
:func:`list_folders` for the folder picker. Both only read the sites mount and
never follow a symlink, the same rule nginx serves with (``disable_symlinks``).
A bad optional ``certificate_id``/``access_list_id`` becomes a typed error the
API maps to 422 rather than a raw database ``IntegrityError``/500.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.static_site import StaticSite
from app.schemas.static_site import FolderEntry, FoldersResult, FolderStatus

INDEX_FILE = "index.html"


class StaticSiteNotFoundError(Exception):
    """Raised when a static-site id does not exist."""


class InvalidReferenceError(Exception):
    """Raised when a referenced certificate or access list does not exist."""


# --- What is on disk --------------------------------------------------------


def _has_entries(root: Path) -> bool:
    try:
        with os.scandir(root) as entries:
            return any(True for _ in entries)
    except OSError:
        return False


def folder_status(root: Path, folder: str) -> FolderStatus:
    """What the sites mount holds at ``folder``. Advisory: saving never fails on it.

    ``folder`` has passed the schema's rule already; a value that still resolves
    outside ``root`` reads as ``missing`` without being looked at.
    """
    if not root.is_dir() or not _has_entries(root):
        return "unmounted"
    real_root = root.resolve()
    if not (root / folder).resolve().is_relative_to(real_root):
        return "missing"

    path = root
    for part in Path(folder).parts:
        path = path / part
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return "missing"
        except OSError:
            return "unreadable"
        if stat.S_ISLNK(mode):
            return "symlink"
    if not stat.S_ISDIR(mode):
        return "missing"
    try:
        with os.scandir(path) as entries:
            names = {e.name for e in entries}
    except OSError:
        return "unreadable"
    return "ok" if INDEX_FILE in names else "no_index"


def _walk(base: Path, rel: str, depth: int, max_depth: int, out: list[FolderEntry]) -> None:
    try:
        with os.scandir(base) as it:
            children = sorted(
                (e for e in it if not e.name.startswith(".") and e.is_dir(follow_symlinks=False)),
                key=lambda e: e.name,
            )
    except OSError:
        return
    for entry in children:
        path = f"{rel}/{entry.name}" if rel else entry.name
        try:
            with os.scandir(entry.path) as inner:
                names = {e.name for e in inner}
            readable = True
        except OSError:
            names, readable = set(), False
        out.append(FolderEntry(path=path, has_index=INDEX_FILE in names, readable=readable))
        if readable and depth < max_depth:
            _walk(Path(entry.path), path, depth + 1, max_depth, out)


def list_folders(root: Path, max_depth: int = 2) -> FoldersResult:
    """The visible sub-folders of the sites mount, ``max_depth`` levels down.

    Hidden entries and symlinks are left out: nginx would refuse to serve them.
    """
    if not root.is_dir():
        return FoldersResult(available=False, reason="not_mounted")
    if not _has_entries(root):
        # Docker creates an empty folder for a bind mount whose host path is
        # missing, so this is the usual "SITES_PATH not set up" case too.
        return FoldersResult(available=False, reason="empty")
    folders: list[FolderEntry] = []
    _walk(root, "", 1, max_depth, folders)
    folders.sort(key=lambda f: f.path)
    return FoldersResult(available=True, folders=folders)


# --- CRUD -------------------------------------------------------------------


async def get_static_site(db: AsyncSession, site_id: int) -> StaticSite | None:
    """Return the static site or ``None``."""
    return await db.get(StaticSite, site_id)


async def list_static_sites(db: AsyncSession) -> list[StaticSite]:
    """Return all static sites ordered by id."""
    result = await db.execute(select(StaticSite).order_by(StaticSite.id))
    return list(result.scalars().all())


async def create_static_site(db: AsyncSession, values: dict[str, Any]) -> StaticSite:
    """Create a static site.

    Raises :class:`InvalidReferenceError` if the optional certificate or access
    list does not exist.
    """
    site = StaticSite(**values)
    db.add(site)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidReferenceError(str(exc.orig)) from exc
    await db.refresh(site)
    return site


async def update_static_site(db: AsyncSession, site_id: int, changes: dict[str, Any]) -> StaticSite:
    """Apply a partial update to a static site.

    Raises :class:`StaticSiteNotFoundError` if missing or
    :class:`InvalidReferenceError` if a changed reference is invalid.
    """
    site = await get_static_site(db, site_id)
    if site is None:
        raise StaticSiteNotFoundError(str(site_id))

    for field, value in changes.items():
        setattr(site, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidReferenceError(str(exc.orig)) from exc
    await db.refresh(site)
    return site


async def delete_static_site(db: AsyncSession, site_id: int) -> None:
    """Delete a static site.

    Raises :class:`StaticSiteNotFoundError` if it does not exist.
    """
    site = await get_static_site(db, site_id)
    if site is None:
        raise StaticSiteNotFoundError(str(site_id))
    await db.delete(site)
    await db.commit()


__all__ = [
    "INDEX_FILE",
    "InvalidReferenceError",
    "StaticSiteNotFoundError",
    "create_static_site",
    "delete_static_site",
    "folder_status",
    "get_static_site",
    "list_folders",
    "list_static_sites",
    "update_static_site",
]
