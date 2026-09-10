"""The outcome of the most recent nginx apply, and where it is kept.

Written by the worker after every apply, read by the API for the admin UI's
banner. Kept on the ``cluster_state`` singleton because the condition it
describes outlives the task that found it: a config that failed ``nginx -t`` is
rolled back, the host that broke it stays in the database, and every later apply
fails the same way until someone fixes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Connection, update

from app.models.cluster_state import CLUSTER_STATE_ROW_ID, ClusterState
from app.services.nginx.engine import ApplyResult

#: nginx stops at its first emergency, so real output is a few lines. The cap
#: exists for the pathological case, not the normal one.
OUTPUT_LIMIT = 8000


@dataclass(frozen=True, slots=True)
class ApplyOutcome:
    """What the UI needs to know about one apply."""

    ok: bool
    message: str
    #: nginx's own words when it failed; empty on success.
    output: str = ""


def _cap(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text
    return text[:OUTPUT_LIMIT].rstrip() + "\n… (truncated)"


def outcome_of(result: ApplyResult) -> ApplyOutcome:
    """Reduce an apply result to its outcome.

    Healthy means nothing was rolled back — which includes "already up to date".
    That case matters: after a rollback the disk holds the old good config, so
    deleting the host that broke it makes the next apply report "up to date",
    and that is exactly the moment the problem is fixed.

    On a failure the output is whichever step failed. A reload failure comes
    after a passing ``nginx -t``, and "syntax is ok" is not why it failed.
    """
    if not result.rolled_back:
        return ApplyOutcome(ok=True, message=result.message)
    output = result.test_output if not result.valid else result.reload_output
    return ApplyOutcome(ok=False, message=result.message, output=_cap(output.strip()))


def record_apply_outcome(
    conn: Connection, outcome: ApplyOutcome, *, at: datetime | None = None
) -> None:
    """Write ``outcome`` onto the cluster-state row, in the caller's transaction."""
    conn.execute(
        update(ClusterState)
        .where(ClusterState.id == CLUSTER_STATE_ROW_ID)
        .values(
            last_apply_ok=outcome.ok,
            last_apply_at=at or datetime.now(UTC),
            last_apply_message=outcome.message,
            last_apply_output=outcome.output,
        )
    )


__all__ = ["OUTPUT_LIMIT", "ApplyOutcome", "outcome_of", "record_apply_outcome"]
