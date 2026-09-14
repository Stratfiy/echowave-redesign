"""Two OpenAPI documents from one app: the public one and the staff one.

The FastAPI app mounts every router, and ``app.openapi()`` describes every
route it can find -- including the seventy-odd operations under ``/admin``
and ``/superuser`` that only staff can call. Access to those was always
enforced (every admin router carries a staff dependency; the guard test in
``tests/test_the_public_spec_hides_staff_routes.py`` proves it), but their
*shape* was published: the schema at ``/api/v1/openapi.json``, the checked-in
``docs/api-reference/openapi.json`` and the docs site all listed the
operations for markup changes, provider keys, KYC review and impersonation.
A customer could not call them. A customer could read exactly what they do.

So the surface is split. ``public`` is what the world sees: everything that
is not staff-only. ``internal`` is the whole app, for generating the UI
client, whose superadmin screens need those operations. Staff-only is
decided by tag, because tags are the one thing every admin router already
declares, and a new router that forgets to tag itself ``admin-…`` is caught
by the same guard test.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

#: A tag that marks an operation as staff-only. Prefix match on purpose: the
#: admin routers are tagged ``admin-billing``, ``admin-kyc`` and so on.
INTERNAL_TAG_PREFIXES = ("admin", "superuser", "superadmin", "staff", "internal")
#: A path that marks an operation as staff-only even when untagged.
INTERNAL_PATH_PREFIXES = ("/api/v1/admin/", "/api/v1/superuser/")

_REF = re.compile(r"#/components/schemas/([^/\"]+)")


def is_internal(path: str, operation: dict[str, Any]) -> bool:
    if path.startswith(INTERNAL_PATH_PREFIXES):
        return True
    for tag in operation.get("tags") or []:
        if str(tag).lower().startswith(INTERNAL_TAG_PREFIXES):
            return True
    return False


def _referenced_schemas(spec: dict[str, Any]) -> set[str]:
    """Every schema name reachable from the paths, transitively."""
    schemas = (spec.get("components") or {}).get("schemas") or {}
    seen: set[str] = set()
    stack = list(_REF.findall(_dumps(spec.get("paths") or {})))
    while stack:
        name = stack.pop()
        if name in seen or name not in schemas:
            continue
        seen.add(name)
        stack.extend(_REF.findall(_dumps(schemas[name])))
    return seen


def _dumps(value: Any) -> str:
    import json

    return json.dumps(value)


def full_spec(app: FastAPI) -> dict[str, Any]:
    return get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        servers=app.servers,
    )


def public_spec(app: FastAPI) -> dict[str, Any]:
    """The whole app minus staff-only operations, and minus the schemas only
    they referenced -- a response model named ``ImpersonateRequest`` is a
    disclosure on its own."""
    spec = copy.deepcopy(full_spec(app))
    paths = spec.get("paths") or {}
    kept: dict[str, Any] = {}
    for path, operations in paths.items():
        remaining = {
            method: operation
            for method, operation in operations.items()
            if not (isinstance(operation, dict) and is_internal(path, operation))
        }
        if remaining:
            kept[path] = remaining
    spec["paths"] = kept
    schemas = (spec.get("components") or {}).get("schemas")
    if schemas:
        wanted = _referenced_schemas(spec)
        spec["components"]["schemas"] = {
            name: schema for name, schema in schemas.items() if name in wanted
        }
    tags = spec.get("tags")
    if tags:
        spec["tags"] = [
            tag
            for tag in tags
            if not str(tag.get("name", "")).lower().startswith(INTERNAL_TAG_PREFIXES)
        ]
    return spec


def install(app: FastAPI) -> None:
    """Serve the public document at ``openapi_url``. Cached the way FastAPI
    caches its own."""

    def openapi() -> dict[str, Any]:
        if not app.openapi_schema:
            app.openapi_schema = public_spec(app)
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
