"""Period-idempotent claiming for cluster-wide periodic sweeps.

Regression cover for a real defect: ``renew_due_certificates`` was guarded only
by ``leader_lock``, which is a *mutual exclusion* primitive — it stops two nodes
sweeping at the same instant, not two nodes sweeping in quick succession. The
lock is held for the few milliseconds it takes to read the due list and call
``.delay()``, so a second beat firing even a fraction of a second later takes the
now-free lock and re-enqueues every certificate that the first beat's renewals
have not yet marked. Each duplicate drives another ACME order against Let's
Encrypt's five-duplicates-per-week ceiling.

That was latent while a single node ran ``beat``. It became routine when beat
moved onto every node so each could reconcile its own nginx, which is what these
tests pin down.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.models.cluster_state import ClusterSweep
from app.services.cluster.sweeps import claim_sweep
from sqlalchemy import create_engine


def _engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sweeps.db'}", future=True)
    ClusterSweep.__table__.create(engine)
    return engine


def test_first_claim_succeeds(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        assert claim_sweep(conn, "cert-renew-sweep", min_interval_seconds=3600) is True


def test_second_claim_within_the_interval_is_refused(tmp_path: Path) -> None:
    """The regression: two beats moments apart must not both sweep."""
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        assert claim_sweep(conn, "cert-renew-sweep", min_interval_seconds=3600) is True
    with engine.begin() as conn:
        # A different node's beat, milliseconds later. The leader lock is free by
        # now, so only the period guard can stop it.
        assert claim_sweep(conn, "cert-renew-sweep", min_interval_seconds=3600) is False


def test_claim_succeeds_again_once_the_interval_has_elapsed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        assert claim_sweep(conn, "cert-renew-sweep", min_interval_seconds=3600) is True
        # Backdate the claim past the window rather than sleeping.
        conn.execute(
            ClusterSweep.__table__.update()
            .where(ClusterSweep.__table__.c.name == "cert-renew-sweep")
            .values(last_run_at=datetime.now(UTC) - timedelta(hours=2))
        )
    with engine.begin() as conn:
        assert claim_sweep(conn, "cert-renew-sweep", min_interval_seconds=3600) is True


def test_claims_are_independent_per_sweep_name(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        assert claim_sweep(conn, "cert-renew-sweep", min_interval_seconds=3600) is True
        # A different sweep must not be blocked by the certificate one.
        assert claim_sweep(conn, "crowdsec-sync", min_interval_seconds=3600) is True
