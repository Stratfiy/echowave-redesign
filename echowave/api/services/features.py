"""The switched-off features, in one place.

Each new slice ships dark behind an environment flag and waits for the
founder to try it. Three things need to know whether a flag is on -- the
slice's routes (a 404 while off), its service code, and the UI (which shows
nothing while off) -- and each used to answer that on its own: routes with
a copied ``_enabled()`` dependency, the UI by calling the slice's route and
reading a 404. That cost a request per flag per page and made every screen
near a new slice need a mock for a call it never makes.

Now the registry below is the one answer. ``is_on`` for code, ``require``
for a router, and ``public()`` for ``/health``, which the UI already reads
at start-up -- so the UI learns every flag in the request it was making
anyway. A flag is a boolean about what the product offers, not a secret.
"""

from __future__ import annotations

from fastapi import HTTPException

from api import constants

#: Feature name -> the constant that switches it. Names are what the UI and
#: a pack's ``requires_feature`` use.
FLAGS: dict[str, str] = {
    "task_board": "TASK_BOARD_2026_09_ENABLED",
    "dialer_import": "DIALER_IMPORT_ENABLED",
    "workspace_roles": "WORKSPACE_ROLES_ENABLED",
    "agent_graph_extras": "AGENT_GRAPH_EXTRAS_ENABLED",
    "shell": "SHELL_2026_09_ENABLED",
    "voice_watch": "VOICE_WATCH_ENABLED",
    "charge_rule": "CHARGE_RULE_2026_09_ENABLED",
    "procurement_docs": "PROCUREMENT_DOCS_2026_09_ENABLED",
}


def is_on(name: str) -> bool:
    """Read at call time, so a test can switch a flag with monkeypatch."""
    return bool(getattr(constants, FLAGS[name]))


def public() -> dict[str, bool]:
    return {name: is_on(name) for name in FLAGS}


def require(name: str):
    """A router dependency: every route under it is a 404 while ``name`` is
    off, so nothing about the feature is visible before it is switched on."""
    if name not in FLAGS:
        raise KeyError(name)

    def _dependency() -> None:
        if not is_on(name):
            raise HTTPException(status_code=404, detail="Not Found")

    _dependency.feature = name  # read by tests
    return _dependency


__all__ = ["FLAGS", "is_on", "public", "require"]
