"""Propagation verification polls every authoritative nameserver for the TXT."""

from __future__ import annotations

import pytest
from app.services.certs.dns_providers.propagation import (
    PropagationTimeoutError,
    authoritative_nameservers,
    wait_for_txt,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _scripted(answers: dict[str, list[set[str]]]):
    """``query(ns, name)`` returning successive scripted answers per nameserver."""
    calls: dict[str, int] = {}

    def query(ns: str, name: str) -> set[str]:
        i = calls.get(ns, 0)
        calls[ns] = i + 1
        script = answers[ns]
        return script[min(i, len(script) - 1)]

    return query, calls


def test_returns_once_every_nameserver_serves_the_value() -> None:
    query, calls = _scripted({"ns1": [set(), {"v"}], "ns2": [{"v"}]})
    clock = _Clock()
    slept: list[int] = []

    def sleep(seconds: float) -> None:
        slept.append(int(seconds))
        clock.now += seconds

    wait_for_txt(
        "_acme-challenge.example.com",
        "v",
        timeout_seconds=60,
        interval_seconds=5,
        nameservers=["ns1", "ns2"],
        query=query,
        sleep=sleep,
        clock=clock,
    )
    assert slept == [5]  # one wait for ns1 to catch up
    assert calls == {"ns1": 2, "ns2": 1}


def test_settle_delay_runs_once_after_every_nameserver_serves_the_value() -> None:
    """Regression: the authoritative IPs are anycast, so one vantage point serving
    the record does not mean every PoP does. Let's Encrypt's remote validators
    saw the previous record set ("During secondary validation: Incorrect TXT
    record ... (and 1 more)"). A settle delay after the last nameserver catches
    up gives the provider's edge time to converge before the challenge is answered."""
    query, calls = _scripted({"ns1": [set(), {"v"}], "ns2": [{"v"}]})
    clock = _Clock()
    slept: list[int] = []

    def sleep(seconds: float) -> None:
        slept.append(int(seconds))
        clock.now += seconds

    wait_for_txt(
        "_acme-challenge.example.com",
        "v",
        timeout_seconds=60,
        interval_seconds=5,
        settle_seconds=10,
        nameservers=["ns1", "ns2"],
        query=query,
        sleep=sleep,
        clock=clock,
    )
    assert slept == [5, 10]  # poll wait for ns1, then one settle delay
    assert calls == {"ns1": 2, "ns2": 1}  # no re-polling after the settle


def test_settle_delay_is_not_applied_on_timeout() -> None:
    query, _ = _scripted({"ns1": [{"other"}]})
    clock = _Clock()
    slept: list[int] = []

    def sleep(seconds: float) -> None:
        slept.append(int(seconds))
        clock.now += seconds

    with pytest.raises(PropagationTimeoutError):
        wait_for_txt(
            "_acme-challenge.example.com",
            "v",
            timeout_seconds=4,
            interval_seconds=5,
            settle_seconds=10,
            nameservers=["ns1"],
            query=query,
            sleep=sleep,
            clock=clock,
        )
    assert 10 not in slept


def test_times_out_when_a_nameserver_never_serves_it() -> None:
    query, _ = _scripted({"ns1": [{"v"}], "ns2": [{"other"}]})
    clock = _Clock()

    def sleep(seconds: float) -> None:
        clock.now += seconds

    with pytest.raises(PropagationTimeoutError, match="1/2"):
        wait_for_txt(
            "_acme-challenge.example.com",
            "v",
            timeout_seconds=12,
            interval_seconds=5,
            nameservers=["ns1", "ns2"],
            query=query,
            sleep=sleep,
            clock=clock,
        )


def test_query_errors_count_as_not_propagated() -> None:
    def query(ns: str, name: str) -> set[str]:
        raise OSError("SERVFAIL")

    clock = _Clock()

    def sleep(seconds: float) -> None:
        clock.now += seconds

    with pytest.raises(PropagationTimeoutError):
        wait_for_txt(
            "_acme-challenge.example.com",
            "v",
            timeout_seconds=1,
            interval_seconds=1,
            nameservers=["ns1"],
            query=query,
            sleep=sleep,
            clock=clock,
        )


def test_no_nameservers_is_an_error() -> None:
    with pytest.raises(PropagationTimeoutError, match="authoritative"):
        wait_for_txt(
            "_acme-challenge.example.com",
            "v",
            timeout_seconds=1,
            interval_seconds=1,
            nameservers=[],
        )


class _Rdata:
    def __init__(self, text: str, target: bool = False) -> None:
        self._text = text
        if target:
            self.target = self

    def to_text(self) -> str:
        return self._text


class _FakeResolver:
    def resolve(self, name: str, rtype: str):
        if rtype == "NS" and name == "example.com":
            return [
                _Rdata("ns1.example.net.", target=True),
                _Rdata("ns2.example.net.", target=True),
            ]
        if rtype == "A" and name == "ns1.example.net.":
            return [_Rdata("192.0.2.1")]
        if rtype == "A" and name == "ns2.example.net.":
            return [_Rdata("192.0.2.2")]
        raise OSError("NXDOMAIN")  # no AAAA records, etc.


def test_authoritative_nameservers_resolves_ns_then_addresses() -> None:
    assert authoritative_nameservers("example.com", resolver=_FakeResolver()) == [
        "192.0.2.1",
        "192.0.2.2",
    ]
