"""Structural checks for the error_page mapping (no database)."""

from __future__ import annotations

from app.models.error_page import ERROR_CODES, ErrorPage


def test_the_eight_codes_are_fixed() -> None:
    # The set is closed: the UI renders exactly these rows and the renderer
    # writes exactly these files, so a ninth code would be invisible in both.
    assert ERROR_CODES == (400, 401, 403, 404, 500, 502, 503, 504)


def test_table_shape() -> None:
    table = ErrorPage.__table__
    assert table.name == "error_page"
    assert {c.name for c in table.columns} == {
        "code",
        "mode",
        "custom_page_id",
        "created_at",
        "updated_at",
    }
    # The code is the identity: one row per code, at most.
    assert [c.name for c in table.primary_key.columns] == ["code"]
    assert table.c.mode.nullable is False
    assert set(table.c.mode.type.enums) == {"default", "custom_page"}


def test_the_page_reference_is_restrict() -> None:
    # A page an error binding uses cannot be deleted out from under it, the
    # same rule the default site and the ban page already follow.
    fks = {fk.column.table.name: fk.ondelete for fk in ErrorPage.__table__.foreign_keys}
    assert fks == {"custom_pages": "RESTRICT"}
