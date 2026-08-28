"""Async client for the CrowdSec Local API (LAPI) — MEG-22.

The backend talks to CrowdSec over two credential types, mirroring what CrowdSec
itself enforces:

* a **bouncer API key** (``X-Api-Key``) to *read* active decisions — the same
  key the nginx bouncer uses; and
* **machine credentials** (id + password, exchanged for a short-lived JWT) to
  read *alerts* and to *push manual decisions* — bouncers cannot write.

Each credential path is independently optional: a deployment that only wants to
surface decisions in the UI can set just the bouncer key. Missing credentials
raise :class:`CrowdSecNotConfigured` (surfaced as HTTP 503 by the route),
never a 500.

The client is a thin, well-typed wrapper over ``httpx.AsyncClient`` and holds no
global state; construct one per request (see ``get_crowdsec_client``) so the
event loop and connection pool stay request-scoped. A custom ``transport`` may
be injected for tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.schemas.crowdsec import Alert, Decision, DecisionCreate


class CrowdSecError(RuntimeError):
    """A LAPI call failed (transport error or non-2xx response)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CrowdSecNotConfigured(CrowdSecError):
    """A required credential for the attempted operation is not configured."""


def _rfc3339(dt: datetime) -> str:
    """CrowdSec expects RFC3339 timestamps with a ``Z`` UTC suffix."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class CrowdSecClient:
    """Typed async facade over the CrowdSec LAPI."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._s = settings or default_settings
        self._http = httpx.AsyncClient(
            base_url=self._s.crowdsec_lapi_url.rstrip("/"),
            timeout=self._s.crowdsec_timeout_seconds,
            transport=transport,
        )
        self._machine_token: str | None = None

    @property
    def settings(self) -> Settings:
        """The settings snapshot this client was built from."""
        return self._s

    async def __aenter__(self) -> CrowdSecClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- auth helpers ------------------------------------------------------

    def _bouncer_headers(self) -> dict[str, str]:
        if not self._s.crowdsec_lapi_key:
            raise CrowdSecNotConfigured(
                "CrowdSec bouncer API key is not configured (CROWDSEC_LAPI_KEY)."
            )
        return {"X-Api-Key": self._s.crowdsec_lapi_key}

    async def _machine_token_header(self) -> dict[str, str]:
        if not (self._s.crowdsec_machine_id and self._s.crowdsec_machine_password):
            raise CrowdSecNotConfigured(
                "CrowdSec machine credentials are not configured "
                "(CROWDSEC_MACHINE_ID / CROWDSEC_MACHINE_PASSWORD)."
            )
        if self._machine_token is None:
            self._machine_token = await self._login_machine()
        return {"Authorization": f"Bearer {self._machine_token}"}

    async def _login_machine(self) -> str:
        try:
            resp = await self._http.post(
                "/v1/watchers/login",
                json={
                    "machine_id": self._s.crowdsec_machine_id,
                    "password": self._s.crowdsec_machine_password,
                },
            )
        except httpx.HTTPError as exc:  # pragma: no cover - network error path
            raise CrowdSecError(f"CrowdSec LAPI login failed: {exc}") from exc
        if resp.status_code != httpx.codes.OK:
            raise CrowdSecError(
                f"CrowdSec LAPI login rejected credentials (HTTP {resp.status_code}).",
                status_code=resp.status_code,
            )
        token = resp.json().get("token")
        if not token:
            raise CrowdSecError("CrowdSec LAPI login returned no token.")
        return token

    # --- registration ------------------------------------------------------

    async def register_machine(
        self, machine_id: str, password: str, *, registration_token: str | None = None
    ) -> None:
        """Self-register a watcher/machine against LAPI (``POST /v1/watchers``).

        Idempotent: a machine that already exists (LAPI answers 403) is treated
        as success so re-runs don't fail. With ``registration_token`` matching
        the LAPI's ``auto_registration`` token (and the caller inside its
        ``allowed_ranges``) the machine is validated on the spot; otherwise it
        stays pending until ``cscli machines validate``. See ``docs/crowdsec.md``.
        """
        body: dict[str, str] = {"machine_id": machine_id, "password": password}
        if registration_token:
            body["registration_token"] = registration_token
        try:
            resp = await self._http.post("/v1/watchers", json=body)
        except httpx.HTTPError as exc:  # pragma: no cover - network error path
            raise CrowdSecError(f"CrowdSec machine registration failed: {exc}") from exc
        # 201/200 (new), 202 Accepted (auto-registration), 403 = already registered.
        accepted = (
            httpx.codes.CREATED,
            httpx.codes.OK,
            httpx.codes.ACCEPTED,
            httpx.codes.FORBIDDEN,
        )
        if resp.status_code in accepted:
            return
        raise CrowdSecError(
            f"CrowdSec machine registration rejected (HTTP {resp.status_code}).",
            status_code=resp.status_code,
        )

    # --- request helper ----------------------------------------------------

    async def _request(self, method: str, url: str, *, headers: dict[str, str], **kw: Any) -> Any:
        try:
            resp = await self._http.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise CrowdSecError(f"CrowdSec LAPI request failed: {exc}") from exc
        if resp.status_code >= httpx.codes.BAD_REQUEST:
            raise CrowdSecError(
                f"CrowdSec LAPI returned HTTP {resp.status_code} for {method} {url}.",
                status_code=resp.status_code,
            )
        if resp.status_code == httpx.codes.NO_CONTENT or not resp.content:
            return None
        return resp.json()

    # --- read paths --------------------------------------------------------

    async def _decisions_read_headers(self) -> dict[str, str]:
        """Auth for reading decisions: bouncer key if set, else machine token.

        A self-registered deployment may hold only machine credentials (CrowdSec
        exposes no LAPI HTTP path to mint a bouncer key), so fall back to the
        machine JWT when no bouncer key is configured.
        """
        if self._s.crowdsec_lapi_key:
            return self._bouncer_headers()
        return await self._machine_token_header()

    async def list_decisions(self) -> list[Decision]:
        """Return all active decisions the bouncer would enforce."""
        headers = await self._decisions_read_headers()
        data = await self._request("GET", "/v1/decisions", headers=headers)
        # LAPI returns ``null`` (not ``[]``) when there are no decisions.
        return [Decision.model_validate(d) for d in (data or [])]

    async def list_alerts(self, *, limit: int = 50) -> list[Alert]:
        """Return recent alerts, newest first (machine-authenticated)."""
        headers = await self._machine_token_header()
        data = await self._request(
            "GET", "/v1/alerts", headers=headers, params={"limit": limit}
        )
        return [Alert.model_validate(a) for a in (data or [])]

    # --- write path --------------------------------------------------------

    async def add_decision(self, decision: DecisionCreate) -> Decision:
        """Push a manual decision by creating a MegooPM-sourced alert.

        CrowdSec models operator bans as an alert carrying a decision, so the
        bouncer starts enforcing it on its next decision poll. Returns the
        decision as accepted by LAPI.
        """
        headers = await self._machine_token_header()
        now = datetime.now(UTC)
        origin = self._s.crowdsec_origin
        scenario = f"{origin}/manual-{decision.type}"
        message = decision.reason or f"Manual {decision.type} via MegooPM"
        payload = [
            {
                "scenario": scenario,
                "scenario_hash": "",
                "scenario_version": "",
                "message": message,
                "events_count": 1,
                "start_at": _rfc3339(now),
                "stop_at": _rfc3339(now),
                "capacity": 0,
                "leakspeed": "0",
                "simulated": False,
                "events": [],
                "remediation": True,
                "source": {
                    "scope": decision.scope,
                    "value": decision.value,
                    "ip": decision.value if decision.scope == "Ip" else None,
                },
                "decisions": [
                    {
                        "origin": origin,
                        "type": decision.type,
                        "scope": decision.scope,
                        "value": decision.value,
                        "duration": decision.duration,
                        "scenario": scenario,
                    }
                ],
            }
        ]
        await self._request("POST", "/v1/alerts", headers=headers, json=payload)
        # LAPI's alert-create response is a list of alert ids, not the decision;
        # echo back the decision we asked it to enforce.
        return Decision(
            origin=origin,
            type=decision.type,
            scope=decision.scope,
            value=decision.value,
            duration=decision.duration,
            scenario=scenario,
        )

    async def delete_decision(self, decision_id: int) -> int:
        """Delete a decision by id; returns the number LAPI reports deleted."""
        headers = await self._machine_token_header()
        data = await self._request(
            "DELETE", f"/v1/decisions/{decision_id}", headers=headers
        )
        try:
            return int((data or {}).get("nbDeleted", 0))
        except (TypeError, ValueError):
            return 0

    # --- health ------------------------------------------------------------

    async def ping(self) -> None:
        """Raise if LAPI is unreachable. Uses the bouncer key when available."""
        headers = (
            self._bouncer_headers() if self._s.crowdsec_lapi_key else {}
        )
        await self._request("GET", "/v1/decisions", headers=headers, params={"limit": 1})


__all__ = ["CrowdSecClient", "CrowdSecError", "CrowdSecNotConfigured"]
