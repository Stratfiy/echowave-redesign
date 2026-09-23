"""One spelling per resource in the public API, without breaking a caller.

The API grew five resources under a singular name -- ``/workflow``,
``/campaign``, ``/folder``, ``/organisation`` (British, beside the
``/organizations`` routers), ``/user`` -- next to plural ones like
``/workflows/{id}/routines`` and ``/tasks``. A developer reading the
reference could not guess which a resource used, and the same agent lived
under two words.

Renaming the routes would break every integration already written against
them and rename every generated UI client function (the operation ids
carry the path). So nothing is renamed:

* The app **serves both**. :class:`PluralPaths` maps a plural request onto
  the singular route that handles it -- only where that route exists, so a
  path that is natively plural is never touched.
* The **public reference** (``/api/v1/openapi.json`` and the docs site)
  shows only the plural, via :func:`pluralise_spec`. That is the spelling to
  build against; the singular keeps working for whoever already did.
* The UI's own client is generated from the internal document, which keeps
  the paths the routes were written with, so no screen changes.
"""

from __future__ import annotations

import re
from typing import Any

API_PREFIX = "/api/v1"

#: Singular route prefix -> the plural the reference shows.
PLURAL: dict[str, str] = {
    "workflow": "workflows",
    "campaign": "campaigns",
    "folder": "folders",
    "organisation": "organizations",
    "user": "users",
}

_SINGULAR = re.compile(rf"^{re.escape(API_PREFIX)}/({'|'.join(PLURAL)})(?=/|$)")
_PLURAL = {plural: singular for singular, plural in PLURAL.items()}
_PLURAL_RE = re.compile(
    rf"^{re.escape(API_PREFIX)}/({'|'.join(map(re.escape, _PLURAL))})(?=/|$)"
)


def plural_of(path: str) -> str:
    """The reference's spelling of a route path; unchanged if it has none."""
    return _SINGULAR.sub(lambda m: f"{API_PREFIX}/{PLURAL[m.group(1)]}", path, 1)


def singular_of(path: str) -> str | None:
    """The singular path a plural request stands for, or None if the path
    does not start with one of the plural names."""
    match = _PLURAL_RE.match(path)
    if not match:
        return None
    return f"{API_PREFIX}/{_PLURAL[match.group(1)]}{path[match.end() :]}"


def pluralise_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Rename the public document's paths to their plural spelling."""
    paths = spec.get("paths") or {}
    spec["paths"] = {plural_of(path): operations for path, operations in paths.items()}
    return spec


class PluralPaths:
    """ASGI middleware: a request to a plural path is served by the singular
    route it stands for. Checked against the app's own routes, so a path
    that is natively plural -- ``/workflows/{id}/routines`` -- or that
    matches nothing either way reaches the router untouched and gets its
    ordinary answer."""

    def __init__(self, app, *, routes_of):
        self.app = app
        self._routes_of = routes_of
        self._patterns: list[re.Pattern] | None = None

    def _serves(self, path: str) -> bool:
        if self._patterns is None:
            self._patterns = [
                route.path_regex
                for route in self._routes_of()
                if getattr(route, "path_regex", None) is not None
                and _SINGULAR.match(getattr(route, "path", ""))
            ]
        return any(pattern.match(path) for pattern in self._patterns)

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            singular = singular_of(scope["path"])
            if singular is not None:
                # A collection route may be written ``/campaign/``; the
                # plural is asked for as ``/campaigns`` either way.
                other = singular[:-1] if singular.endswith("/") else singular + "/"
                for candidate in (singular, other):
                    if self._serves(candidate):
                        scope = dict(scope, path=candidate, raw_path=candidate.encode())
                        break
        await self.app(scope, receive, send)


__all__ = ["PLURAL", "PluralPaths", "plural_of", "pluralise_spec", "singular_of"]
