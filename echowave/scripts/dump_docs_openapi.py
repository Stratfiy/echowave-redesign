"""Dump the OpenAPI documents: the public one for the docs, the whole one for
the UI client.

Run from the repo root with the api environment available:

    python -m scripts.dump_docs_openapi

Writes two files. ``docs/api-reference/openapi.json`` is the **public**
surface (no staff-only operations) and is checked in; CI dumps it and asserts
it is unchanged. ``ui/openapi.internal.json`` is the **whole** app, for
``npm run generate-client`` -- the superadmin screens need the staff
operations -- and is ignored by git, because it describes routes the public
document deliberately does not. See api/services/openapi_surface.py.
"""

import json
from pathlib import Path

from loguru import logger

logger.remove()

from api.app import app  # noqa: E402
from api.services import openapi_surface  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "docs" / "api-reference" / "openapi.json"
INTERNAL_OUTPUT = REPO_ROOT / "ui" / "openapi.internal.json"


def main() -> None:
    public = openapi_surface.public_spec(app)
    OUTPUT.write_text(json.dumps(public, separators=(",", ":")))
    print(f"Wrote {len(public['paths'])} paths to {OUTPUT.relative_to(REPO_ROOT)}")
    internal = openapi_surface.full_spec(app)
    INTERNAL_OUTPUT.write_text(json.dumps(internal, separators=(",", ":")))
    print(
        f"Wrote {len(internal['paths'])} paths to "
        f"{INTERNAL_OUTPUT.relative_to(REPO_ROOT)} (not checked in)"
    )


if __name__ == "__main__":
    main()
