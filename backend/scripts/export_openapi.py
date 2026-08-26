"""Export the FastAPI OpenAPI document to a committed JSON file.

Run from the ``backend`` directory::

    python -m scripts.export_openapi

The committed ``backend/openapi.json`` is the *published* API contract that the
frontend's ``npm run gen:api`` consumes to generate its TypeScript types. CI
guards against drift via ``tests/test_openapi.py`` — if the app's schema and the
committed file diverge, that test (and therefore CI) fails until the file is
regenerated and committed.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app

# Repo layout: backend/scripts/export_openapi.py -> backend/openapi.json
OPENAPI_PATH = Path(__file__).resolve().parent.parent / "openapi.json"


def build_openapi_json() -> str:
    """Return the canonical, diff-stable JSON serialization of the schema.

    ``sort_keys`` keeps the output deterministic across runs so the committed
    file only changes when the API actually changes — that is what makes the
    drift gate meaningful.
    """
    schema = app.openapi()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    OPENAPI_PATH.write_text(build_openapi_json(), encoding="utf-8")
    print(f"Wrote {OPENAPI_PATH}")


if __name__ == "__main__":
    main()
