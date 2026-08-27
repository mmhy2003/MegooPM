"""Community-origin classification and pagination for CrowdSec lists (MEG-43).

CrowdSec records carry an *origin* that says where a decision came from. The
Security UI defaults to showing only what *this* deployment produced or curated
locally — manual bans, engine scenario hits, AppSec/WAF detections — and hides
the large, noisy set pulled from the community (the Central API, subscribed
blocklists, imported lists). ``include_community=true`` opts back into the full
set.

Classification is by origin string. The community set is matched
case-insensitively so minor LAPI casing differences don't leak community records
into the default view. Exact live-LAPI origin strings should be confirmed
against the running stack (see ``docs/crowdsec.md``); the set below covers the
documented values (``CAPI``, ``lists``, ``cscli-import``) plus the community
blocklist origin.

Filtering and pagination are applied **server-side** after fetching from LAPI —
LAPI has no reliable total-count/offset contract for these endpoints — so the
API can return a stable ``total`` and a bounded slice without shipping thousands
of records to the client in one response.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.schemas.crowdsec import Alert, Decision

# Origins considered "community" (hidden unless ``include_community=true``),
# lower-cased for case-insensitive matching.
COMMUNITY_ORIGINS = frozenset(
    {"capi", "lists", "cscli-import", "community-blocklist"}
)

# Max records fetched from LAPI for a single alerts listing before server-side
# pagination. Bounds memory/latency on very large alert histories; the API
# documents ``total`` as being relative to this window.
ALERT_FETCH_CAP = 1000


def is_community_origin(origin: str | None) -> bool:
    """True if ``origin`` denotes a community/blocklist source."""
    return bool(origin) and origin.strip().lower() in COMMUNITY_ORIGINS


def is_community_decision(decision: Decision) -> bool:
    """A decision is community iff its origin is a community source."""
    return is_community_origin(decision.origin)


def is_community_alert(alert: Alert) -> bool:
    """An alert is community iff any of its decisions has a community origin.

    Decision-less alerts (AppSec/WAF detections) are therefore treated as local
    and always shown.
    """
    return any(is_community_origin(d.origin) for d in alert.decisions)


def paginate[T](items: Sequence[T], *, page: int, page_size: int) -> tuple[list[T], int]:
    """Return ``(slice, total)`` for 1-based ``page`` of size ``page_size``."""
    total = len(items)
    start = (page - 1) * page_size
    return list(items[start : start + page_size]), total


__all__ = [
    "ALERT_FETCH_CAP",
    "COMMUNITY_ORIGINS",
    "is_community_alert",
    "is_community_decision",
    "is_community_origin",
    "paginate",
]
