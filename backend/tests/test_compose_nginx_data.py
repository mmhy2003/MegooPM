"""nginx must see every directory under /data the worker renders into.

The worker writes the vhosts *and* the documents they include (the branded
error pages' ``megoopm-errors.conf.inc``, the ban page, the default site) under
``/data``. An nginx that mounts only some of those volumes fails ``nginx -t`` on
the first host anyone creates, and every apply is rolled back.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _services(compose_file: str) -> dict:
    path = REPO_ROOT / compose_file
    if not path.exists():  # pragma: no cover - partial checkout
        pytest.skip(f"{compose_file} not present at {REPO_ROOT}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


def _data_mounts(mounts: list) -> dict[str, str]:
    """``{target: source}`` for the short-form mounts at or under /data."""
    out: dict[str, str] = {}
    for mount in mounts:
        if not isinstance(mount, str):
            continue
        parts = mount.split(":")
        if parts[-1] in {"ro", "rw"}:
            parts = parts[:-1]
        source, target = ":".join(parts[:-1]), parts[-1]
        if target == "/data" or target.startswith("/data/"):
            out[target] = source
    return out


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.dev.yml"])
def test_nginx_mounts_what_the_worker_renders_into(compose_file: str) -> None:
    services = _services(compose_file)
    worker = _data_mounts(services["worker"]["volumes"])
    nginx = _data_mounts(services["nginx"]["volumes"])
    assert worker, "the worker renders under /data"
    missing = {target: source for target, source in worker.items() if nginx.get(target) != source}
    assert not missing, f"nginx does not mount {missing}"
