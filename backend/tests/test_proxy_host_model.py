"""Structural checks for the proxy_host_locations mapping (no database)."""

from __future__ import annotations

from app.models.proxy_host import ProxyHost, ProxyHostLocation


def test_location_table_shape() -> None:
    table = ProxyHostLocation.__table__
    assert table.name == "proxy_host_locations"
    assert {c.name for c in table.columns} >= {
        "id",
        "proxy_host_id",
        "path",
        "upstream_id",
        "forward_scheme",
        "created_at",
        "updated_at",
    }
    fks = {fk.column.table.name: fk.ondelete for fk in table.foreign_keys}
    assert fks == {"proxy_hosts": "CASCADE", "upstreams": "RESTRICT"}
    unique = [c for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"]
    assert [tuple(col.name for col in u.columns) for u in unique] == [("proxy_host_id", "path")]


def test_host_locations_relationship_cascades_orphans() -> None:
    rel = ProxyHost.__mapper__.relationships["locations"]
    assert rel.cascade.delete_orphan
    assert rel.mapper.class_ is ProxyHostLocation
