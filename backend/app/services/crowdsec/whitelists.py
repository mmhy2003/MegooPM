"""Render CrowdSec whitelist rows into the parser YAML file CrowdSec reads.

The file lives on the shared ``/data`` volume and the CrowdSec container sees it
through a single-file bind mount at
``/etc/crowdsec/parsers/s02-enrich/99-megoopm-whitelist.yaml``. See
``docs/crowdsec.md`` for the deployment side.

Everything here is pure except :func:`read_whitelist_file` and
:func:`write_whitelist_file`, so the renderer is testable without a database, a
container, or a running CrowdSec.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates" / "crowdsec"


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

    ``strict=False`` on networks is deliberate: operators routinely write
    ``10.10.0.14/24`` with host bits set, and CrowdSec accepts it.
    """
    for ip in ips:
        try:
            ipaddress.ip_address(ip.strip())
        except ValueError as exc:
            raise WhitelistValidationError(
                f"{ip!r} is not a valid IP address."
            ) from exc
    for cidr in cidrs:
        try:
            ipaddress.ip_network(cidr.strip(), strict=False)
        except ValueError as exc:
            raise WhitelistValidationError(
                f"{cidr!r} is not a valid CIDR range."
            ) from exc


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


@lru_cache(maxsize=1)
def _env() -> Environment:
    """Build the Jinja environment once and cache it."""
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,  # fail loudly on a typo'd template variable
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )


def render_whitelists(docs: Sequence[WhitelistDoc]) -> str:
    """Render every doc into one multi-document YAML file.

    Validates as it goes: this output is the thing that can stop CrowdSec from
    starting, so an invalid entry must never reach the file.
    """
    for doc in docs:
        validate_entries(doc.ips, doc.cidrs)
        slugify(doc.name)
    return _env().get_template("whitelist.yaml.j2").render(docs=docs)


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
    replacement is correct everywhere else in this codebase.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "r+" if path.exists() else "w"
    with path.open(mode, encoding="utf-8") as fh:
        fh.seek(0)
        fh.write(content)
        fh.truncate()


__all__ = [
    "WhitelistDoc",
    "WhitelistValidationError",
    "content_digest",
    "read_whitelist_file",
    "render_whitelists",
    "slugify",
    "validate_entries",
    "write_whitelist_file",
]
