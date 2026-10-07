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
INTERNAL_PATH_PREFIXES = (
    "/api/v1/admin/",
    "/api/v1/superuser/",
    # Ending an impersonation: called by the borrowed session, so it cannot
    # carry a staff gate, and only the impersonation banner has a use for it.
    "/api/v1/impersonation/",
)

#: The public reference, in the order a customer reads it: each group is a
#: heading, each tag under it a router. Rendered into the public document as
#: ``x-tagGroups`` (the Redoc/Scalar convention the docs site reads), and
#: enforced by ``tests/test_public_api_is_grouped.py``: a public tag that is
#: in no group fails CI, so a new router is placed rather than lost.
TAG_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Agents",
        (
            "bots",
            "agent-builder",
            # Agents and a website for them, built from one chat.
            "studio",
            # Decibyl's private browser: live view, Take over, saved logins.
            "browser",
            "agent-templates",
            # A workspace's own saved roles, and sharing them (MP-2, MP-3).
            "workspace-roles",
            # What starts an agent, and what its last run reached (G-1).
            "agent-graph",
            "agent-options",
            "agent-timeline",
            "workflow-text-chat",
            "workflow-recordings",
            "workflow-outcomes",
            "evals",
            "cost-estimate",
            "node-types",
            "extraction-library",
            "folders",
        ),
    ),
    (
        "Calls and telephony",
        (
            "public-calls",
            "telephony",
            "managed-numbers",
            # A business's own dialer, connected so its team's calls can be
            # imported for the telecaller coach (CR-1).
            "dialer-connections",
            "campaigns",
            "contacts",
            "turn",
            "translate",
        ),
    ),
    (
        "Channels and triggers",
        (
            "embed",
            "public-embed",
            "public-download",
            "triggers",
            "public-triggers",
            # The outbound half of a trigger: where a bot's own events go.
            "event-webhooks",
            "public-email",
            "public-whatsapp",
            "routines",
            "tasks",
            "google-calendar",
            # Members talking to Decibyl from Slack, Teams, Telegram and
            # WhatsApp, and linking those accounts to themselves (KAN-277).
            "public-decibyl-channels",
            "channel-links",
            # The Windows and Mac app: approval cards for steps it takes on
            # a person's own computer, and the receipt it leaves.
            "desktop",
        ),
    ),
    (
        "Knowledge",
        (
            "knowledge-base",
            "s3",
            # The purchase orders, RFQs and bid sheets agents draft, and
            # their register (PROCUREMENT_DOCS_2026_09_ENABLED).
            "procurement",
        ),
    ),
    (
        "Tools and connectors",
        (
            "tools",
            "tool-library",
            "skills",
            "connectors",
            "credentials",
            "provider-keys",
            "service-keys",
            # A person's own outside tools and ordering apps, and their
            # order cards (launch stream `reach`).
            "reach",
        ),
    ),
    (
        "Account and team",
        (
            "auth",
            "user",
            "organisation",
            "organizations",
            "organization-members",
            "team",
            "organisation-memory",
            "notifications",
            "onboarding",
            # A person's own preferences, allowances, personal space, and
            # their feedback on replies (launch stream controls).
            "controls",
            # The door before an account exists (screen 01): the waitlist
            # and what an invitation link says.
            "public-early-access",
            # Where a person lands after sign-in, their first answers, and
            # Stop for a reply forming in Chat (launch stream `shell`).
            "shell",
            # Older people and their families: medicine reminder calls,
            # scam checks, tech help and the family circle (stream `care`).
            "care",
            # A person's learning goals, lessons, practice and progress
            # (launch stream `learning`).
            "learning",
            # Meeting capture and the meeting record (launch stream
            # `meetings`, screens 11-12).
            "meetings",
        ),
    ),
    (
        "Billing",
        (
            "billing",
            "packs",
            # The same shelf read from outside, before an account exists.
            "public-marketplace",
            "usage",
            "reports",
            "referrals",
            "partners",
            "kyc",
            # Spend caps on the workspace or an agent (S-1), and what an
            # agent's run costs and may not exceed (OP-5).
            "budgets",
            "workflow-spend",
        ),
    ),
    # `public-trust` is the same subject read from outside: the platform's own
    # sub-processors and retention, for somebody doing a security review
    # before they have an account to log into.
    ("Compliance and privacy", ("compliance", "privacy", "public-trust")),
    ("Operations", ("health",)),
)

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
    # One spelling per resource: /workflows, not /workflow beside it. The
    # app serves both (services/api_paths.py); the reference shows this one.
    from api.services.api_paths import pluralise_spec

    pluralise_spec(spec)
    spec["x-tagGroups"] = [
        {"name": name, "tags": list(members)} for name, members in TAG_GROUPS
    ]
    return spec


def public_tags(spec: dict[str, Any]) -> dict[str, list[str]]:
    """Every tag on a public operation, and the operations that carry it.
    An untagged operation is listed under ``""``."""
    seen: dict[str, list[str]] = {}
    for path, operations in (spec.get("paths") or {}).items():
        for method, operation in operations.items():
            if not isinstance(operation, dict):
                continue
            for tag in operation.get("tags") or [""]:
                seen.setdefault(str(tag), []).append(f"{method.upper()} {path}")
    return seen


def install(app: FastAPI) -> None:
    """Serve the public document at ``openapi_url``. Cached the way FastAPI
    caches its own."""

    def openapi() -> dict[str, Any]:
        if not app.openapi_schema:
            app.openapi_schema = public_spec(app)
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
