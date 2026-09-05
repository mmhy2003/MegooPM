"""Whitelist slugification, validation, rendering and file I/O (no database).

These cover the two things that would silently break the feature in production:
a render that is not byte-stable (which would restart the cluster's WAF on every
save) and a write that swaps the file's inode (which would leave the CrowdSec
container reading stale content forever).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from app.services.crowdsec.whitelists import (
    WhitelistDoc,
    WhitelistValidationError,
    content_digest,
    read_whitelist_file,
    render_whitelists,
    slugify,
    validate_entries,
    write_whitelist_file,
)

DOC = WhitelistDoc(
    name="Internal Backends",
    reason="internal backends trip appsec generic rules",
    description="Internal backend pool",
    ips=["10.10.0.14"],
    cidrs=["10.10.0.0/24"],
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Internal Backends", "internal-backends"),
        ("  spaced  out  ", "spaced-out"),
        ("MiXeD_Case.99", "mixed-case-99"),
        ("already-slug", "already-slug"),
    ],
)
def test_slugify_normalises_names(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


def test_slugify_rejects_a_name_with_nothing_to_slug() -> None:
    # CrowdSec needs a unique non-empty `name:`; "!!!" would render `megoopm/wl-`.
    with pytest.raises(WhitelistValidationError, match="at least one letter or digit"):
        slugify("!!!")


def test_validate_entries_accepts_ipv4_ipv6_and_cidr() -> None:
    validate_entries(["10.10.0.14", "2001:db8::1"], ["10.10.0.0/24", "2001:db8::/32"])


def test_validate_entries_names_the_bad_ip() -> None:
    with pytest.raises(WhitelistValidationError, match=r"10\.10\.0\.999"):
        validate_entries(["10.10.0.999"], [])


def test_validate_entries_names_the_bad_cidr() -> None:
    with pytest.raises(WhitelistValidationError, match="10.10.0.0/99"):
        validate_entries([], ["10.10.0.0/99"])


def test_validate_entries_accepts_a_host_bit_cidr() -> None:
    # 10.10.0.14/24 has host bits set; operators write these constantly and
    # CrowdSec accepts them, so strict=False is deliberate.
    validate_entries([], ["10.10.0.14/24"])


def test_renders_one_valid_crowdsec_document() -> None:
    docs = [d for d in yaml.safe_load_all(render_whitelists([DOC])) if d]
    assert len(docs) == 1
    assert docs[0]["name"] == "megoopm/wl-internal-backends"
    assert docs[0]["description"] == "Internal backend pool"
    assert docs[0]["whitelist"]["reason"] == "internal backends trip appsec generic rules"
    assert docs[0]["whitelist"]["ip"] == ["10.10.0.14"]
    assert docs[0]["whitelist"]["cidr"] == ["10.10.0.0/24"]


def test_renders_one_document_per_whitelist() -> None:
    second = WhitelistDoc(
        name="Monitoring",
        reason="prometheus scrape",
        description="",
        ips=["10.10.0.99"],
        cidrs=[],
    )
    docs = [d for d in yaml.safe_load_all(render_whitelists([DOC, second])) if d]
    assert [d["name"] for d in docs] == [
        "megoopm/wl-internal-backends",
        "megoopm/wl-monitoring",
    ]


def test_a_whitelist_with_only_cidrs_omits_the_ip_key() -> None:
    doc = WhitelistDoc(name="range only", reason="r", description="", ips=[], cidrs=["10.0.0.0/8"])
    rendered = next(d for d in yaml.safe_load_all(render_whitelists([doc])) if d)
    assert "ip" not in rendered["whitelist"]
    assert rendered["whitelist"]["cidr"] == ["10.0.0.0/8"]


def test_no_whitelists_renders_a_parseable_placeholder() -> None:
    # The path is a bind-mount source: it must never be deleted, and must never
    # be content CrowdSec refuses to parse.
    out = render_whitelists([])
    assert out.strip().startswith("#")
    assert [d for d in yaml.safe_load_all(out) if d] == []


def test_render_is_byte_stable() -> None:
    # The digest of this output decides whether CrowdSec is restarted at all,
    # so unstable rendering would restart the cluster's WAF on every save.
    assert render_whitelists([DOC]) == render_whitelists([DOC])


def test_reason_containing_yaml_metacharacters_stays_one_scalar() -> None:
    tricky = WhitelistDoc(
        name="odd", reason='he said: "no" # really', description="", ips=["1.2.3.4"], cidrs=[]
    )
    doc = next(d for d in yaml.safe_load_all(render_whitelists([tricky])) if d)
    assert doc["whitelist"]["reason"] == 'he said: "no" # really'


def test_render_rejects_an_invalid_entry_before_producing_anything() -> None:
    bad = WhitelistDoc(name="bad", reason="typo", description="", ips=["10.10.0.999"], cidrs=[])
    with pytest.raises(WhitelistValidationError, match=r"10\.10\.0\.999"):
        render_whitelists([bad])


def test_digest_changes_with_content() -> None:
    assert content_digest("a") != content_digest("b")
    assert content_digest("a") == content_digest("a")


def test_write_keeps_the_same_inode(tmp_path: Path) -> None:
    # THE trap. The CrowdSec container resolves this path to an inode when it
    # starts; a write-then-rename would leave it reading the old content
    # forever, with no error in any log.
    path = tmp_path / "megoopm.yaml"
    path.write_text("# seed\n", encoding="utf-8")
    before = path.stat().st_ino

    write_whitelist_file(path, render_whitelists([DOC]))

    assert path.stat().st_ino == before
    assert "megoopm/wl-internal-backends" in read_whitelist_file(path)


def test_write_truncates_a_longer_previous_file(tmp_path: Path) -> None:
    path = tmp_path / "megoopm.yaml"
    path.write_text("x" * 5000, encoding="utf-8")
    write_whitelist_file(path, "# short\n")
    assert read_whitelist_file(path) == "# short\n"


def test_write_creates_the_file_when_absent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "megoopm.yaml"
    write_whitelist_file(path, "# new\n")
    assert read_whitelist_file(path) == "# new\n"


def test_read_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_whitelist_file(tmp_path / "absent.yaml") == ""


# --- expression whitelists -------------------------------------------------

EXPR = WhitelistDoc(
    name="Health checks",
    reason="GET /health",
    description="",
    kind="expression",
    filter="evt.Meta.service == 'http'",
    expressions=["evt.Meta.http_verb == 'GET' && evt.Meta.http_path == '/health'"],
)


def test_renders_an_expression_whitelist() -> None:
    doc = next(d for d in yaml.safe_load_all(render_whitelists([EXPR])) if d)
    assert doc["name"] == "megoopm/wl-health-checks"
    assert doc["filter"] == "evt.Meta.service == 'http'"
    assert doc["whitelist"]["expression"] == [
        "evt.Meta.http_verb == 'GET' && evt.Meta.http_path == '/health'"
    ]
    # ip/cidr keys would be meaningless here and CrowdSec would evaluate them.
    assert "ip" not in doc["whitelist"]
    assert "cidr" not in doc["whitelist"]


def test_expression_filter_is_optional() -> None:
    doc = next(d for d in yaml.safe_load_all(render_whitelists([replace(EXPR, filter=None)])) if d)
    # An absent filter means "evaluate against every event", which is what
    # CrowdSec does when the key is missing. Rendering `filter: null` would not
    # be the same thing.
    assert "filter" not in doc


def test_an_expression_containing_quotes_survives_the_round_trip() -> None:
    tricky = replace(EXPR, filter=None, expressions=['evt.Meta.x == "a: b #c"'])
    doc = next(d for d in yaml.safe_load_all(render_whitelists([tricky])) if d)
    assert doc["whitelist"]["expression"] == ['evt.Meta.x == "a: b #c"']


def test_ip_and_expression_whitelists_share_one_file() -> None:
    docs = [d for d in yaml.safe_load_all(render_whitelists([DOC, EXPR])) if d]
    assert [d["name"] for d in docs] == [
        "megoopm/wl-internal-backends",
        "megoopm/wl-health-checks",
    ]


def test_an_ip_kind_never_renders_expression_keys() -> None:
    doc = next(d for d in yaml.safe_load_all(render_whitelists([DOC])) if d)
    assert "filter" not in doc
    assert "expression" not in doc["whitelist"]


def test_expression_render_is_byte_stable() -> None:
    assert render_whitelists([EXPR]) == render_whitelists([EXPR])


def test_an_expression_kind_with_no_expressions_is_rejected() -> None:
    with pytest.raises(WhitelistValidationError, match="at least one expression"):
        render_whitelists([replace(EXPR, expressions=[])])


def test_an_ip_kind_with_no_addresses_is_rejected() -> None:
    with pytest.raises(WhitelistValidationError, match="at least one IP address"):
        render_whitelists([replace(DOC, ips=[], cidrs=[])])


def test_a_blank_expression_is_rejected() -> None:
    # CrowdSec compiles every entry; an empty string is a compile error, and a
    # compile error is fatal at startup.
    with pytest.raises(WhitelistValidationError, match="empty"):
        render_whitelists([replace(EXPR, expressions=["  "])])


def test_expressions_render_readably_not_html_escaped() -> None:
    # Jinja's `tojson` is HTML-safe: it escapes apostrophes and ampersands
    # into numeric unicode escapes. YAML decodes those back, so the file
    # still *works* - but the dialog shows this text to an operator, and an
    # expression full of quotes and `&&` rendered that way reads as garbage.
    raw = render_whitelists([EXPR])
    assert "evt.Meta.http_verb == 'GET' && evt.Meta.http_path == '/health'" in raw
    # chr(92) is a backslash; spelling it avoids escaping it in this source.
    assert chr(92) + "u00" not in raw
