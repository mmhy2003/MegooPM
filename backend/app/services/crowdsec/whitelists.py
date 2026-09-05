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
import json
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
            raise WhitelistValidationError(f"{ip!r} is not a valid IP address.") from exc
    for cidr in cidrs:
        try:
            ipaddress.ip_network(cidr.strip(), strict=False)
        except ValueError as exc:
            raise WhitelistValidationError(f"{cidr!r} is not a valid CIDR range.") from exc


def validate_expressions(expressions: Sequence[str]) -> None:
    """Reject what we *can* check about expressions, which is not much.

    CrowdSec compiles ``expr`` expressions itself and there is no offline
    compiler to call from here, so a syntactically wrong expression is only
    discovered when CrowdSec refuses to start — measured on v1.6.4:

        level=fatal msg="crowdsec init: while loading parsers: failed to
        compile node ... unable to compile whitelist expression ..."

    The apply's rollback is what makes that survivable. All this can do is stop
    the two cases that are certainly wrong before paying a restart for them.
    """
    if not expressions:
        raise WhitelistValidationError("An expression whitelist needs at least one expression.")
    for expression in expressions:
        if not expression.strip():
            raise WhitelistValidationError(
                "An expression cannot be empty — CrowdSec fails to compile it."
            )


@dataclass(frozen=True, slots=True)
class WhitelistDoc:
    """One whitelist, decoupled from the ORM so the renderer needs no database.

    ``kind`` selects which fields are rendered: ``ip_cidr`` emits ``ip:``/
    ``cidr:``, ``expression`` emits an optional top-level ``filter:`` plus
    ``expression:``. Rendering the other kind's keys would not be inert —
    CrowdSec evaluates every key it finds.
    """

    name: str
    reason: str
    description: str
    ips: tuple[str, ...] | list[str] = ()
    cidrs: tuple[str, ...] | list[str] = ()
    kind: str = "ip_cidr"
    filter: str | None = None
    expressions: tuple[str, ...] | list[str] = ()

    @property
    def slug(self) -> str:
        return slugify(self.name)

    @property
    def is_expression(self) -> bool:
        return self.kind == "expression"


def _yaml_str(value: str) -> str:
    """Quote a scalar as a JSON string, which YAML accepts verbatim.

    Not Jinja's ``tojson``: that one is HTML-safe, so it escapes ``'`` and
    ``&`` into ``\u0027`` / ``\u0026``. YAML decodes those back correctly, but
    expressions are full of both — ``evt.Meta.x == 'a' && ...`` came out as
    ``evt.Meta.x == \u0027a\u0027 \u0026\u0026 ...``, which is unreadable in
    the dialog's preview and in the file an operator may have to debug.
    """
    return json.dumps(value)


@lru_cache(maxsize=1)
def _env() -> Environment:
    """Build the Jinja environment once and cache it."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,  # fail loudly on a typo'd template variable
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.filters["yamlstr"] = _yaml_str
    return env


def render_whitelists(docs: Sequence[WhitelistDoc]) -> str:
    """Render every doc into one multi-document YAML file.

    Validates as it goes: this output is the thing that can stop CrowdSec from
    starting, so an invalid entry must never reach the file.
    """
    for doc in docs:
        slugify(doc.name)
        if doc.is_expression:
            validate_expressions(doc.expressions)
        else:
            validate_entries(doc.ips, doc.cidrs)
            if not doc.ips and not doc.cidrs:
                raise WhitelistValidationError(
                    f"Whitelist {doc.name!r} needs at least one IP address or CIDR range."
                )
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
    "validate_expressions",
    "write_whitelist_file",
]
