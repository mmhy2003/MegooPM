"""Tests for the default-site-over-TLS name arithmetic.

Pure: no database, no nginx. This is where the risk lives — a mistake here
either leaves a disabled host pointing at a stranger's site or steals a name
from a working host — so it takes the bulk of the coverage.
"""

from __future__ import annotations

from app.models.certificate import Certificate
from app.models.enums import CertificateProvider, CertificateStatus
from app.services.nginx.default_tls import claimed_tls_names, plan_default_tls
from app.services.nginx.state import (
    CertificateSpec,
    DeadHostSpec,
    DesiredState,
    ProxyHostSpec,
    RedirectionHostSpec,
)

CERTS_DIR = "/data/certs"


def _cert(**kw) -> Certificate:
    """An in-memory Certificate. Declarative models need no session."""
    base = {
        "id": 1,
        "name": "cert",
        "provider": CertificateProvider.letsencrypt,
        "status": CertificateStatus.active,
        "domain_names": ["example.com", "*.example.com"],
        "expires_on": None,
        "meta": {},
    }
    base.update(kw)
    return Certificate(**base)


def test_a_name_no_host_claims_is_covered() -> None:
    """The reported bug: a disabled host's name must reach the default site."""
    specs = plan_default_tls([_cert(domain_names=["disabled.example.com"])], set(), CERTS_DIR)
    assert len(specs) == 1
    assert specs[0].server_names == ("disabled.example.com",)


def test_a_name_an_enabled_tls_host_claims_is_never_taken() -> None:
    """Stealing a working host's name would be worse than the bug being fixed."""
    specs = plan_default_tls(
        [_cert(domain_names=["live.example.com"])], {"live.example.com"}, CERTS_DIR
    )
    assert specs == ()


def test_the_certificate_paths_and_fingerprint_are_carried() -> None:
    specs = plan_default_tls([_cert(id=7, domain_names=["a.example.com"])], set(), CERTS_DIR)
    cert = specs[0].certificate
    assert cert.id == 7
    assert cert.fullchain_path == "/data/certs/7/fullchain.pem"
    assert cert.privkey_path == "/data/certs/7/privkey.pem"
    assert cert.fingerprint  # non-empty: renewal must change the rendered text


def test_only_unclaimed_names_of_a_partly_claimed_certificate_are_used() -> None:
    specs = plan_default_tls(
        [_cert(domain_names=["live.example.com", "disabled.example.com"])],
        {"live.example.com"},
        CERTS_DIR,
    )
    assert specs[0].server_names == ("disabled.example.com",)


def test_a_certificate_with_every_name_claimed_produces_nothing() -> None:
    """No block at all, rather than an empty server_name nginx would reject."""
    specs = plan_default_tls(
        [_cert(domain_names=["a.example.com", "b.example.com"])],
        {"a.example.com", "b.example.com"},
        CERTS_DIR,
    )
    assert specs == ()


def test_pending_failed_and_expired_certificates_contribute_nothing() -> None:
    """Their files may not exist; referencing one fails nginx -t and rolls back
    the entire apply for the whole instance."""
    for status in (
        CertificateStatus.pending,
        CertificateStatus.failed,
        CertificateStatus.expired,
    ):
        specs = plan_default_tls(
            [_cert(status=status, domain_names=["a.example.com"])], set(), CERTS_DIR
        )
        assert specs == (), status


def test_the_identical_name_in_two_certificates_lands_in_one_block() -> None:
    """Two blocks declaring one name leaves nginx picking arbitrarily — the very
    bug this feature removes."""
    specs = plan_default_tls(
        [
            _cert(id=2, domain_names=["shared.example.com"]),
            _cert(id=1, domain_names=["shared.example.com"]),
        ],
        set(),
        CERTS_DIR,
    )
    assert len(specs) == 1
    assert specs[0].certificate.id == 1  # lowest id wins, deterministically
    assert specs[0].server_names == ("shared.example.com",)


def test_an_exact_name_and_a_wildcard_in_different_certificates_both_survive() -> None:
    """They are different strings, so they do not collide. nginx prefers the
    exact one at match time; ranking them here would risk diverging from it."""
    specs = plan_default_tls(
        [
            _cert(id=1, domain_names=["a.example.com"]),
            _cert(id=2, domain_names=["*.example.com"]),
        ],
        set(),
        CERTS_DIR,
    )
    assert [s.server_names for s in specs] == [("a.example.com",), ("*.example.com",)]


def test_names_are_sorted_and_blocks_ordered_by_certificate_id() -> None:
    """Byte-identical output across nodes, or the engine reloads for nothing."""
    specs = plan_default_tls(
        [
            _cert(id=5, domain_names=["z.example.com", "a.example.com"]),
            _cert(id=2, domain_names=["m.other.com"]),
        ],
        set(),
        CERTS_DIR,
    )
    assert [s.certificate.id for s in specs] == [2, 5]
    assert specs[1].server_names == ("a.example.com", "z.example.com")


def test_a_certificate_with_no_names_is_skipped() -> None:
    assert plan_default_tls([_cert(domain_names=[])], set(), CERTS_DIR) == ()


# --- Which names an enabled host already answers for on :443 ---------------

_CERT = CertificateSpec(
    id=1,
    fullchain_path="/data/certs/1/fullchain.pem",
    privkey_path="/data/certs/1/privkey.pem",
    fingerprint="f",
)


def test_a_host_with_a_certificate_claims_its_names() -> None:
    state = DesiredState(
        proxy_hosts=(
            ProxyHostSpec(
                id=1, domain_names=("live.example.com",), upstream_id=1, certificate=_CERT
            ),
        )
    )
    assert claimed_tls_names(state) == {"live.example.com"}


def test_a_host_without_a_certificate_claims_nothing() -> None:
    """It renders no :443 block at all, so HTTPS to it currently reaches a
    stranger's site. Leaving its name unclaimed is what fixes that."""
    state = DesiredState(
        proxy_hosts=(ProxyHostSpec(id=1, domain_names=("plain.example.com",), upstream_id=1),)
    )
    assert claimed_tls_names(state) == set()


def test_redirection_and_dead_hosts_claim_their_names_too() -> None:
    """They render :443 blocks from their own templates on the same condition."""
    state = DesiredState(
        redirection_hosts=(
            RedirectionHostSpec(
                id=1,
                domain_names=("r.example.com",),
                forward_domain_name="x.example.com",
                certificate=_CERT,
            ),
        ),
        dead_hosts=(DeadHostSpec(id=1, domain_names=("d.example.com",), certificate=_CERT),),
    )
    assert claimed_tls_names(state) == {"r.example.com", "d.example.com"}
