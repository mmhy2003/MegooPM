"""Pool context — which nginx context a pool may be rendered into.

``upstream`` blocks are context-local: one defined in ``http {}`` is invisible
to ``stream {}``. A pool therefore has to declare where it may be attached, and
that also constrains its load-balancing method, because ``ip_hash`` exists only
in ``http``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.models.enums import LoadBalanceMethod, UpstreamContext
from app.models.upstream import Upstream
from app.services import upstream as upstream_service
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pools.db'}", future=True)
    Upstream.__table__.create(engine)
    return engine


def test_context_defaults_to_http_on_insert(tmp_path: Path) -> None:
    """Every pool in the database today backs a proxy host; http keeps them working.

    The default applies at INSERT, so this has to round-trip through a flush —
    asserting on an unflushed instance would pass against ``None`` and prove
    nothing.
    """
    engine = _engine(tmp_path)
    with Session(engine) as session:
        pool = Upstream(name="app-pool")
        session.add(pool)
        session.flush()
        session.refresh(pool)
        assert pool.context is UpstreamContext.http


def test_context_round_trips_when_set(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with Session(engine) as session:
        pool = Upstream(name="db-pool", context=UpstreamContext.stream)
        session.add(pool)
        session.flush()
        session.refresh(pool)
        assert pool.context is UpstreamContext.stream


def test_context_values_are_stable_strings() -> None:
    # The value, not the member name, is what lands in Postgres.
    assert [c.value for c in UpstreamContext] == ["http", "stream", "both"]


# --- validation: combinations nginx would refuse at `nginx -t` --------------


@pytest.mark.parametrize("context", [UpstreamContext.stream, UpstreamContext.both])
def test_ip_hash_rejected_outside_http(context: UpstreamContext) -> None:
    """ip_hash is not a stream directive; nginx -t fails hard on it there."""
    with pytest.raises(upstream_service.InvalidPoolConfigError) as err:
        upstream_service.validate_pool_config(
            lb_method=LoadBalanceMethod.ip_hash, context=context, has_backup=False
        )
    assert "ip_hash" in str(err.value)


def test_ip_hash_allowed_for_http_pools() -> None:
    upstream_service.validate_pool_config(
        lb_method=LoadBalanceMethod.ip_hash, context=UpstreamContext.http, has_backup=False
    )


@pytest.mark.parametrize(
    "method",
    [LoadBalanceMethod.hash, LoadBalanceMethod.ip_hash, LoadBalanceMethod.random],
)
def test_backup_rejected_with_hashing_methods(method: LoadBalanceMethod) -> None:
    """nginx: "cannot be used along with the hash, ip_hash, and random methods".

    This combination is accepted by the editor today and only fails at
    `nginx -t`, which rolls back the whole apply with a generic message.
    """
    with pytest.raises(upstream_service.InvalidPoolConfigError) as err:
        upstream_service.validate_pool_config(
            lb_method=method, context=UpstreamContext.http, has_backup=True
        )
    assert "backup" in str(err.value)


def test_backup_allowed_with_round_robin_and_least_conn() -> None:
    for method in (LoadBalanceMethod.round_robin, LoadBalanceMethod.least_conn):
        upstream_service.validate_pool_config(
            lb_method=method, context=UpstreamContext.http, has_backup=True
        )


# --- attaching a pool where its context allows ------------------------------


def test_http_only_pool_rejected_for_streams() -> None:
    pool = Upstream(name="web", context=UpstreamContext.http)
    with pytest.raises(upstream_service.InvalidPoolConfigError) as err:
        upstream_service.assert_usable_in(pool, UpstreamContext.stream)
    assert "web" in str(err.value)
    assert "streams" in str(err.value)


def test_stream_only_pool_rejected_for_proxy_hosts() -> None:
    pool = Upstream(name="db", context=UpstreamContext.stream)
    with pytest.raises(upstream_service.InvalidPoolConfigError) as err:
        upstream_service.assert_usable_in(pool, UpstreamContext.http)
    assert "proxy hosts" in str(err.value)


def test_both_pool_is_usable_everywhere() -> None:
    pool = Upstream(name="shared", context=UpstreamContext.both)
    upstream_service.assert_usable_in(pool, UpstreamContext.http)
    upstream_service.assert_usable_in(pool, UpstreamContext.stream)


def test_matching_context_is_usable() -> None:
    upstream_service.assert_usable_in(
        Upstream(name="web", context=UpstreamContext.http), UpstreamContext.http
    )


# --- narrowing a context out from under its references ----------------------


def test_cannot_narrow_to_http_while_streams_reference_it() -> None:
    """Narrowing would stop the pool rendering into stream{}.

    The stream's server block would then name an upstream that no longer
    exists, which fails nginx -t and rolls back every node's apply.
    """
    with pytest.raises(upstream_service.InvalidPoolConfigError) as err:
        upstream_service.assert_context_change_allowed(
            pool_name="db", new_context=UpstreamContext.http,
            counts={"proxy_hosts": 0, "streams": 2},
        )
    assert "2 stream(s)" in str(err.value)


def test_cannot_narrow_to_stream_while_proxy_hosts_reference_it() -> None:
    with pytest.raises(upstream_service.InvalidPoolConfigError) as err:
        upstream_service.assert_context_change_allowed(
            pool_name="web", new_context=UpstreamContext.stream,
            counts={"proxy_hosts": 3, "streams": 0},
        )
    assert "3 proxy host(s)" in str(err.value)


def test_widening_to_both_is_always_allowed() -> None:
    upstream_service.assert_context_change_allowed(
        pool_name="web", new_context=UpstreamContext.both,
        counts={"proxy_hosts": 3, "streams": 2},
    )


def test_narrowing_is_allowed_once_nothing_references_it() -> None:
    upstream_service.assert_context_change_allowed(
        pool_name="unused", new_context=UpstreamContext.http,
        counts={"proxy_hosts": 0, "streams": 0},
    )
