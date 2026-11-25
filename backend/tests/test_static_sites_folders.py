"""What the backend reports about a site's folder, and the folder picker's list.

Pure filesystem tests against tmp_path standing in for the /sites mount.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from app.services.static_site import folder_status, list_folders

posix_only = pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks/permissions")


def _site(root: Path, folder: str, *, index: bool = True) -> Path:
    path = root / folder
    path.mkdir(parents=True)
    if index:
        (path / "index.html").write_text("<h1>hi</h1>")
    return path


def test_ok_when_the_folder_has_an_index(tmp_path: Path) -> None:
    _site(tmp_path, "company")
    assert folder_status(tmp_path, "company") == "ok"


def test_no_index_when_the_folder_lacks_index_html(tmp_path: Path) -> None:
    _site(tmp_path, "company", index=False)
    assert folder_status(tmp_path, "company") == "no_index"


def test_missing_when_the_folder_does_not_exist(tmp_path: Path) -> None:
    _site(tmp_path, "other")
    assert folder_status(tmp_path, "company") == "missing"


def test_unmounted_when_the_root_does_not_exist(tmp_path: Path) -> None:
    assert folder_status(tmp_path / "nope", "company") == "unmounted"


def test_unmounted_when_the_root_is_empty(tmp_path: Path) -> None:
    assert folder_status(tmp_path, "company") == "unmounted"


@posix_only
def test_symlink_when_the_folder_is_a_link(tmp_path: Path) -> None:
    real = _site(tmp_path, "real")
    (tmp_path / "company").symlink_to(real)
    assert folder_status(tmp_path, "company") == "symlink"


@posix_only
def test_symlink_when_a_parent_is_a_link(tmp_path: Path) -> None:
    _site(tmp_path, "real/inner")
    (tmp_path / "clients").symlink_to(tmp_path / "real")
    assert folder_status(tmp_path, "clients/inner") == "symlink"


def test_never_looks_outside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "sites"
    _site(root, "company")
    _site(tmp_path, "secret")
    assert folder_status(root, "../secret") == "missing"


@posix_only
def test_unreadable_when_the_folder_cannot_be_listed(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    path = _site(tmp_path, "company")
    path.chmod(0)
    try:
        assert folder_status(tmp_path, "company") == "unreadable"
    finally:
        path.chmod(0o755)


@posix_only
def test_list_folders_skips_hidden_links_and_deep_levels(tmp_path: Path) -> None:
    _site(tmp_path, "a")
    _site(tmp_path, "a/b", index=False)
    _site(tmp_path, "a/b/c")
    _site(tmp_path, ".git")
    (tmp_path / "link").symlink_to(tmp_path / "a")
    (tmp_path / "file.txt").write_text("x")

    result = list_folders(tmp_path)

    assert result.available is True
    assert result.reason is None
    assert [(f.path, f.has_index, f.readable) for f in result.folders] == [
        ("a", True, True),
        ("a/b", False, True),
    ]


def test_list_folders_when_not_mounted(tmp_path: Path) -> None:
    result = list_folders(tmp_path / "nope")
    assert (result.available, result.reason, result.folders) == (False, "not_mounted", [])


def test_list_folders_when_empty(tmp_path: Path) -> None:
    # Docker creates an empty folder for a bind mount whose host path is
    # missing, so an empty root reads as "nothing mounted yet" too.
    result = list_folders(tmp_path)
    assert (result.available, result.reason, result.folders) == (False, "empty", [])
