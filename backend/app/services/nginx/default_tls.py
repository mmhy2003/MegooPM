"""Which names get the default site over TLS, and on which certificate.

Pure: no database, no I/O. The loader supplies rows and the set of names
already claimed; everything decided here is decided from those two inputs,
which is what makes the whole feature testable without Postgres or nginx.

A name is covered when some *active* certificate holds it and no enabled host
claims it on :443. Disabling a host stops it claiming its name, so the name
falls to the default site — the case this exists for.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from app.models.certificate import Certificate
from app.models.enums import CertificateStatus

from .state import DefaultTlsSpec, DesiredState


def plan_default_tls(
    certificates: Sequence[Certificate],
    claimed_names: Collection[str],
    certs_dir: str,
) -> tuple[DefaultTlsSpec, ...]:
    """Build one spec per certificate that still covers at least one name."""
    # Imported here: loader imports this module, so a module-level import back
    # into it would be circular. Reused rather than rebuilt so the on-disk path
    # format lives in exactly one place.
    from .loader import _certificate_spec

    claimed = set(claimed_names)

    # name -> id of the certificate that will serve it. Two certificates listing
    # the identical name would otherwise emit two blocks declaring it, leaving
    # nginx to pick one arbitrarily; the lowest id wins, deterministically.
    #
    # Only *identical* strings need arbitration. An exact name in one
    # certificate and a wildcard covering it in another are different strings,
    # so both are emitted and nginx prefers the exact one at match time.
    owner: dict[str, int] = {}
    by_id: dict[int, Certificate] = {}

    for certificate in certificates:
        if certificate.status is not CertificateStatus.active:
            continue
        by_id[certificate.id] = certificate
        for name in certificate.domain_names or ():
            if name in claimed:
                continue
            current = owner.get(name)
            if current is None or certificate.id < current:
                owner[name] = certificate.id

    names_for: dict[int, list[str]] = {}
    for name, cert_id in owner.items():
        names_for.setdefault(cert_id, []).append(name)

    return tuple(
        DefaultTlsSpec(
            certificate=_certificate_spec(by_id[cert_id], certs_dir),
            server_names=tuple(sorted(names_for[cert_id])),
        )
        for cert_id in sorted(names_for)
    )


def claimed_tls_names(state: DesiredState) -> set[str]:
    """Names an enabled host already answers for on :443.

    A host renders a 443 block if and only if it has a certificate — exactly
    what ``server.conf.j2``, ``redirect.conf.j2`` and ``dead.conf.j2`` branch
    on. Deriving the set from the same field means the two cannot drift.

    A duplicate ``server_name`` only conflicts within one listen port, so a
    host with no certificate is deliberately NOT counted: it has nothing on
    :443 today, which is precisely why HTTPS to it lands on a stranger's site.
    """
    hosts = (*state.proxy_hosts, *state.redirection_hosts, *state.dead_hosts)
    return {
        name for host in hosts if host.certificate is not None for name in host.domain_names
    }


__all__ = ["claimed_tls_names", "plan_default_tls"]
