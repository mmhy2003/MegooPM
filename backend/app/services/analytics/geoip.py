"""Country lookup against a bundled MMDB database.

The reader is opened once and reused: opening it per lookup would re-read the
file thousands of times a flush.

Every failure path returns ``None``. This runs inside a batch, so a raise would
cost a whole minute of visitor data to spare one unparseable address — and the
addresses come from Redis hash fields, which anything able to reach the proxy
can influence.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import maxminddb

from app.core.config import settings

log = logging.getLogger(__name__)

_lock = threading.Lock()
_reader: maxminddb.Reader | None = None
_tried = False


def reset_reader() -> None:
    """Drop the cached reader. For tests that change the configured path."""
    global _reader, _tried
    with _lock:
        if _reader is not None:
            _reader.close()
        _reader = None
        _tried = False


def _get_reader() -> maxminddb.Reader | None:
    global _reader, _tried
    with _lock:
        if _reader is not None or _tried:
            return _reader
        _tried = True
        path = Path(settings.geoip_database_path)
        if not path.exists():
            # Once, not per lookup. The build ships this on a best-effort
            # download, so its absence is a supported state rather than a fault,
            # and logging it every request would drown the log.
            log.warning("GeoIP database missing at %s; country resolution disabled", path)
            return None
        try:
            _reader = maxminddb.open_database(str(path))
        except Exception as exc:  # noqa: BLE001 - a corrupt file must not crash
            log.warning("GeoIP database at %s unreadable: %s", path, exc)
            return None
        return _reader


def database_available() -> bool:
    """Whether lookups can resolve anything at all."""
    return _get_reader() is not None


def lookup_country(ip: str) -> str | None:
    """ISO-3166 alpha-2 for ``ip``, or ``None`` if it cannot be determined."""
    if not ip:
        return None
    reader = _get_reader()
    if reader is None:
        return None
    try:
        record = reader.get(ip)
    except (ValueError, TypeError):
        # Not a valid address. Untrusted input, so this is expected traffic,
        # not an anomaly worth logging.
        return None
    if not isinstance(record, dict):
        return None
    # DB-IP and MaxMind both use `country`; `registered_country` is the fallback
    # for addresses assigned to one country but registered in another.
    country = record.get("country") or record.get("registered_country") or {}
    code = country.get("iso_code") if isinstance(country, dict) else None
    return code if isinstance(code, str) else None


__all__ = ["database_available", "lookup_country", "reset_reader"]
