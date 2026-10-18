"""DNS-over-HTTPS for the DNS-01 propagation check.

Some networks only let HTTPS out: port 53 is firewalled, and the local resolver
is an internal server that may host its own copy of the zone (split-horizon).
Asking that resolver who a zone's nameservers are then returns a confident,
wrong answer, and checking the record on port 53 times out. DoH sidesteps
both, over the one port such networks always allow.
"""

from __future__ import annotations

import json

import httpx
import pytest
from app.services.certs.dns_providers.doh import (
    DohError,
    nameservers_doh,
    query_txt_doh,
)
from app.services.certs.dns_providers.propagation import (
    PropagationTimeoutError,
    check_propagation,
    plan_propagation,
    wait_for_txt,
)

CF = "https://cloudflare-dns.com/dns-query"
GOOGLE = "https://dns.google/resolve"


def _client(answers: dict[tuple[str, str], dict]) -> httpx.Client:
    """A client whose DoH server answers from ``answers[(name, type)]``."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/dns-json"
        key = (request.url.params["name"], request.url.params["type"])
        body = answers.get(key, {"Status": 3})
        return httpx.Response(200, content=json.dumps(body))

    return httpx.Client(transport=httpx.MockTransport(handler))


def _txt(*data: str) -> dict:
    return {"Status": 0, "Answer": [{"type": 16, "data": d} for d in data]}


# --- reading DoH answers ------------------------------------------------------


def test_a_txt_value_is_read_out_of_its_quotes() -> None:
    client = _client({("_acme-challenge.slnee.sa", "TXT"): _txt('"token-123"')})
    assert query_txt_doh(CF, "_acme-challenge.slnee.sa", client=client) == {"token-123"}


def test_a_txt_value_split_into_strings_is_joined() -> None:
    # Long TXT values arrive as several quoted strings; DNS joins them.
    client = _client({("_acme-challenge.a.com", "TXT"): _txt('"abc" "def"')})
    assert query_txt_doh(CF, "_acme-challenge.a.com", client=client) == {"abcdef"}


def test_an_unquoted_txt_value_is_taken_as_it_is() -> None:
    # Google's JSON endpoint returns TXT data without the quotes.
    client = _client({("_acme-challenge.a.com", "TXT"): _txt("token-123")})
    assert query_txt_doh(GOOGLE, "_acme-challenge.a.com", client=client) == {"token-123"}


def test_nxdomain_is_an_error_not_an_empty_answer() -> None:
    # The checker counts an error as "not yet"; an empty set would mean the
    # same, but an error keeps the reason in the timeout message.
    with pytest.raises(DohError, match="NXDOMAIN"):
        query_txt_doh(CF, "_acme-challenge.nowhere.com", client=_client({}))


def test_servfail_is_an_error() -> None:
    client = _client({("_acme-challenge.a.com", "TXT"): {"Status": 2}})
    with pytest.raises(DohError, match="SERVFAIL"):
        query_txt_doh(CF, "_acme-challenge.a.com", client=client)


def test_nameservers_are_found_over_doh() -> None:
    client = _client(
        {
            ("slnee.sa", "NS"): {
                "Status": 0,
                "Answer": [
                    {"type": 2, "data": "jillian.ns.cloudflare.com."},
                    {"type": 2, "data": "sam.ns.cloudflare.com."},
                ],
            },
            ("jillian.ns.cloudflare.com", "A"): {
                "Status": 0,
                "Answer": [{"type": 1, "data": "173.245.58.92"}],
            },
            ("sam.ns.cloudflare.com", "A"): {
                "Status": 0,
                "Answer": [{"type": 1, "data": "172.64.33.193"}],
            },
        }
    )
    assert nameservers_doh("slnee.sa", [CF], client=client) == ["173.245.58.92", "172.64.33.193"]


def test_nameserver_discovery_tries_the_next_resolver() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cloudflare-dns.com":
            raise httpx.ConnectError("blocked")
        key = (request.url.params["name"], request.url.params["type"])
        answers = {
            ("a.com", "NS"): {"Status": 0, "Answer": [{"type": 2, "data": "ns1.a.com."}]},
            ("ns1.a.com", "A"): {"Status": 0, "Answer": [{"type": 1, "data": "192.0.2.1"}]},
        }
        return httpx.Response(200, content=json.dumps(answers.get(key, {"Status": 3})))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert nameservers_doh("a.com", [CF, GOOGLE], client=client) == ["192.0.2.1"]


def test_nameserver_discovery_fails_when_no_resolver_answers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("blocked")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(DohError):
        nameservers_doh("a.com", [CF, GOOGLE], client=client)


# --- choosing the path (auto mode) --------------------------------------------


def test_auto_uses_the_authoritative_servers_when_port_53_is_open() -> None:
    # The most accurate check — and with the nameservers DoH found, so a
    # split-horizon local resolver can no longer mislead it.
    plan = plan_propagation(
        "_acme-challenge.slnee.sa",
        mode="auto",
        doh_urls=[CF],
        discover_via_doh=lambda zone, urls: ["173.245.58.92"],
        discover_local=lambda zone: pytest.fail("the local resolver must not be asked"),
        port53_reachable=lambda ip: True,
    )
    assert plan.servers == ["173.245.58.92"]
    assert plan.label == "authoritative nameservers"


def test_auto_checks_over_doh_when_port_53_is_blocked() -> None:
    # The network this was written for: HTTPS out, DNS not.
    plan = plan_propagation(
        "_acme-challenge.slnee.sa",
        mode="auto",
        doh_urls=[CF, GOOGLE],
        discover_via_doh=lambda zone, urls: ["173.245.58.92"],
        discover_local=lambda zone: pytest.fail("the local resolver must not be asked"),
        port53_reachable=lambda ip: False,
    )
    assert plan.servers == [CF, GOOGLE]
    assert plan.label == "DoH resolvers"
    assert "port 53" in plan.reason


def test_auto_falls_back_to_the_local_resolver_when_doh_is_unreachable() -> None:
    def no_doh(zone, urls):
        raise DohError("blocked")

    plan = plan_propagation(
        "_acme-challenge.a.com",
        mode="auto",
        doh_urls=[CF],
        discover_via_doh=no_doh,
        discover_local=lambda zone: ["192.0.2.53"],
        port53_reachable=lambda ip: pytest.fail("nothing to probe"),
    )
    assert plan.servers == ["192.0.2.53"]
    assert plan.label == "authoritative nameservers"
    assert "DoH" in plan.reason


def test_authoritative_mode_is_exactly_the_old_behaviour() -> None:
    plan = plan_propagation(
        "_acme-challenge.a.com",
        mode="authoritative",
        doh_urls=[CF],
        discover_via_doh=lambda zone, urls: pytest.fail("DoH must not be used"),
        discover_local=lambda zone: ["192.0.2.53"],
        port53_reachable=lambda ip: pytest.fail("no probe in this mode"),
    )
    assert plan.servers == ["192.0.2.53"]


def test_doh_mode_never_touches_port_53() -> None:
    plan = plan_propagation(
        "_acme-challenge.a.com",
        mode="doh",
        doh_urls=[CF, GOOGLE],
        discover_via_doh=lambda zone, urls: pytest.fail("no discovery needed"),
        discover_local=lambda zone: pytest.fail("no local resolver"),
        port53_reachable=lambda ip: pytest.fail("no probe"),
    )
    assert plan.servers == [CF, GOOGLE]
    assert plan.label == "DoH resolvers"


# --- what a failure says --------------------------------------------------------


def test_a_doh_timeout_names_the_path_and_why() -> None:
    clock = iter(range(0, 1000, 10))
    with pytest.raises(PropagationTimeoutError) as caught:
        wait_for_txt(
            "_acme-challenge.slnee.sa",
            "token",
            timeout_seconds=20,
            interval_seconds=10,
            nameservers=[CF, GOOGLE],
            query=lambda server, name: {"other"},
            sleep=lambda s: None,
            clock=lambda: next(clock),
            server_label="DoH resolvers",
            reason="port 53 to the zone's nameservers is blocked",
        )
    message = str(caught.value)
    assert "2/2 DoH resolvers" in message
    assert "port 53 to the zone's nameservers is blocked" in message


def test_check_propagation_runs_the_planned_path() -> None:
    queried: list[str] = []

    def plan(name, *, mode, doh_urls):
        from app.services.certs.dns_providers.propagation import PropagationPlan

        return PropagationPlan(
            servers=[CF],
            query=lambda server, record: queried.append(server) or {"token"},
            label="DoH resolvers",
            reason="test",
        )

    check_propagation(
        "_acme-challenge.slnee.sa",
        "token",
        mode="auto",
        doh_urls=[CF],
        timeout_seconds=10,
        interval_seconds=1,
        settle_seconds=0,
        planner=plan,
    )
    assert queried == [CF]
