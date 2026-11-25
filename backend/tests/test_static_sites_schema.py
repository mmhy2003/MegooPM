"""Static site schema: the folder rule is the boundary that keeps a site inside /sites."""

from __future__ import annotations

import pytest
from app.schemas.static_site import StaticSiteCreate, StaticSiteUpdate
from pydantic import ValidationError


def _create(**overrides: object) -> StaticSiteCreate:
    data: dict[str, object] = {"domain_names": ["site.example.com"], "folder": "company-site"}
    data.update(overrides)
    return StaticSiteCreate(**data)


@pytest.mark.parametrize("folder", ["company-site", "clients/acme", "a.b_c-1", "x/y"])
def test_accepts_plain_relative_folders(folder: str) -> None:
    assert _create(folder=folder).folder == folder


@pytest.mark.parametrize(
    "folder",
    [
        "",
        "/abs",
        "trail/",
        "a//b",
        "..",
        "a/../b",
        "../data",
        ".git",
        "a/.hidden",
        "sp ace",
        "a\\b",
        "%2e%2e",
        "x" * 256,
    ],
)
def test_rejects_folders_that_could_leave_sites_or_hide(folder: str) -> None:
    with pytest.raises(ValidationError):
        _create(folder=folder)


def test_update_validates_folder_too() -> None:
    with pytest.raises(ValidationError):
        StaticSiteUpdate(folder="../x")


def test_update_leaves_unset_folder_alone() -> None:
    assert StaticSiteUpdate().folder is None


def test_crowdsec_is_on_by_default() -> None:
    assert _create().crowdsec_enabled is True


def test_domains_are_normalised() -> None:
    site = _create(domain_names=[" Site.Example.COM ", "site.example.com"])
    assert site.domain_names == ["site.example.com"]
