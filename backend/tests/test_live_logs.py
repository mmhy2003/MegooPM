"""The pure parts of Live logs: host keys, the rate limiter, new decisions, frames.

The WebSocket that strings them together is in test_live_logs_ws.py.
"""

from __future__ import annotations

import json

import pytest
from app.core.security import LIVE_TICKET_SECONDS, create_live_ticket, decode_token
from app.schemas.crowdsec import Decision
from app.services import live_logs
from app.services.live_logs import (
    CHANNEL_PREFIX,
    RateLimiter,
    channel_for,
    line_frame,
    new_decisions,
    parse_host_key,
)


@pytest.mark.parametrize("key", ["p1", "r22", "d3", "s4", "u", "p1234567"])
def test_host_keys_are_validated(key: str) -> None:
    assert parse_host_key(key) == key
    assert channel_for(key) == f"{CHANNEL_PREFIX}{key}"


@pytest.mark.parametrize(
    "key", ["p0", "x1", "p1;flushall", "", "p", "u1", "P1", "p01", "p1\n", " p1", "p-1", "*"]
)
def test_bad_host_keys_raise(key: str) -> None:
    with pytest.raises(ValueError):
        parse_host_key(key)
    with pytest.raises(ValueError):
        channel_for(key)


def test_a_host_key_that_is_not_a_string_raises() -> None:
    with pytest.raises(ValueError):
        parse_host_key(1)  # type: ignore[arg-type]


def test_rate_limiter_caps_and_counts() -> None:
    limiter = RateLimiter()
    allowed = sum(limiter.allow(100.0 + i / 1000) for i in range(250))

    assert allowed == 200
    # Still inside the second the lines were skipped in: nothing to report yet,
    # so one burst becomes one frame rather than a frame per skipped line.
    assert limiter.take_skipped(100.5) == 0
    assert limiter.take_skipped(101.0) == 50
    assert limiter.take_skipped(101.0) == 0
    assert limiter.take_skipped(105.0) == 0


def test_rate_limiter_opens_a_new_second() -> None:
    limiter = RateLimiter(per_second=2)
    assert [limiter.allow(10.0) for _ in range(3)] == [True, True, False]
    assert [limiter.allow(11.0) for _ in range(3)] == [True, True, False]
    assert limiter.take_skipped(12.0) == 2


def test_rate_limiter_reports_at_most_once_per_second() -> None:
    limiter = RateLimiter(per_second=1)
    limiter.allow(10.0)
    limiter.allow(10.0)
    assert limiter.take_skipped(11.0) == 1

    limiter.allow(11.2)
    limiter.allow(11.3)
    assert limiter.take_skipped(11.9) == 0
    assert limiter.take_skipped(12.3) == 1


def _decision(id_: int | None, value: str = "203.0.113.9") -> Decision:
    return Decision(id=id_, type="ban", scope="Ip", value=value, duration="4h", origin="crowdsec")


def test_new_decisions_reports_each_once() -> None:
    seen: set[int] = set()
    first = [_decision(1), _decision(2)]

    assert [d.id for d in new_decisions(seen, first)] == [1, 2]
    assert seen == {1, 2}
    assert new_decisions(seen, first) == []
    assert [d.id for d in new_decisions(seen, [*first, _decision(3)])] == [3]
    assert seen == {1, 2, 3}


def test_a_decision_without_an_id_is_never_reported() -> None:
    """It could not be told apart from itself on the next poll."""
    seen: set[int] = set()
    assert new_decisions(seen, [_decision(None)]) == []
    assert seen == set()


def test_a_live_ticket_is_short_and_typed() -> None:
    ticket = create_live_ticket(7, token_version=3)
    payload = decode_token(ticket, expected_type="live")

    assert payload["sub"] == "7"
    assert payload["tv"] == 3
    assert payload["exp"] - payload["iat"] == LIVE_TICKET_SECONDS == 30


def _message(**over: object) -> dict:
    base = {
        "node": "node-a",
        "host": "p1",
        "log": "request",
        "sev": 6,
        "f": {"ip": "203.0.113.9", "st": "200", "m": "GET", "u": "/", "ua": "curl", "cs": ""},
        "raw": None,
        "truncated": False,
    }
    base.update(over)
    return base


def test_a_line_frame_carries_the_message_and_its_country(monkeypatch) -> None:
    monkeypatch.setattr(
        live_logs,
        "country_for",
        lambda scope, value: "DE" if (scope, value) == ("Ip", "203.0.113.9") else None,
    )
    message = _message()

    assert line_frame(json.dumps(message).encode()) == {**message, "type": "line", "cc": "DE"}
    no_ip = _message(log="error", f=None, raw="upstream timed out")
    assert line_frame(json.dumps(no_ip)) == {**no_ip, "type": "line", "cc": None}


@pytest.mark.parametrize(
    "data",
    [b"not json", b"[1, 2]", b'"a string"', b"", b'{"f": NaN}', None, 42],
)
def test_a_message_that_is_not_a_json_object_is_dropped(data) -> None:
    assert line_frame(data) is None


def test_invalid_utf8_is_replaced_not_fatal() -> None:
    data = b'{"node":"a","host":"p1","log":"request","sev":6,"f":{"ua":"bad \xff here"},"raw":null}'
    frame = line_frame(data)
    assert frame is not None
    assert frame["f"]["ua"] == "bad � here"


def test_a_lone_surrogate_still_serialises() -> None:
    """``\\ud800`` is valid JSON text but not encodable as UTF-8.

    The frame must still serialise to something a WebSocket can carry, or one
    hostile user agent would end the session.
    """
    frame = line_frame(b'{"host":"p1","f":{"ua":"\\ud800"}}')
    assert frame is not None
    text = live_logs.dump_frame(frame)
    text.encode("utf-8")
    assert json.loads(text)["f"]["ua"] == "\ud800"


def test_a_decision_frame_has_the_page_shape(monkeypatch) -> None:
    monkeypatch.setattr(live_logs, "country_for", lambda scope, value: "FR")
    decision = _decision(5).model_copy(update={"scenario": "crowdsecurity/http-probing"})

    assert live_logs.decision_frame(decision) == {
        "type": "decision",
        "value": "203.0.113.9",
        "scope": "Ip",
        "type_": "ban",
        "scenario": "crowdsecurity/http-probing",
        "duration": "4h",
        "origin": "crowdsec",
        "cc": "FR",
    }
