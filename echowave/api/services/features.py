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

Per-organisation overrides (FLAG-1, KAN-275): ``FEATURE_ORG_OVERRIDES``
lists organisations a feature is on for while its global flag is still off,
so a slice can be tried by the platform organisation and one invited
account before everyone. ``is_on(name, organization_id)`` honours it;
``for_organization`` is what the authenticated ``/features`` route returns
and the UI merges over the global map from ``/health``.
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
    "decibyl_long_tasks": "DECIBYL_LONG_TASKS_ENABLED",
    "decibyl_private_threads": "DECIBYL_PRIVATE_THREADS_ENABLED",
    "approvals": "APPROVALS_2026_09_ENABLED",
    "vendor_metering": "VENDOR_METERING_2026_09_ENABLED",
    "managed_realtime_gemini_only": "MANAGED_REALTIME_GEMINI_ONLY_ENABLED",
    "connections_per_person": "CONNECTIONS_PER_PERSON_ENABLED",
    "personal_memory": "PERSONAL_MEMORY_ENABLED",
    "table_tools": "TABLE_TOOLS_ENABLED",
    # Switches that existed before the registry and were read straight from
    # constants; registered so /health and the UI can see them.
    "budget_policies": "BUDGET_POLICIES_ENABLED",
    "plan_ladder": "PLAN_LADDER_2026_09_ENABLED",
    "decibyl_tools": "DECIBYL_TOOLS_2026_09_ENABLED",
    "agent_builder": "AGENT_BUILDER_ENABLED",
    "managed_telephony": "MANAGED_TELEPHONY_ENABLED",
    # Launch flags, 4 October 2026 (FLAG-1, KAN-275).
    "invite_only_signup": "INVITE_ONLY_SIGNUP_ENABLED",
    "trial_plan": "TRIAL_PLAN_ENABLED",
    "byok_text": "BYOK_TEXT_ENABLED",
    "marketplace_publishing": "MARKETPLACE_PUBLISHING_ENABLED",
    "whatsapp_channel_ui": "WHATSAPP_CHANNEL_UI_ENABLED",
    "voice_number_flow": "VOICE_NUMBER_FLOW_ENABLED",
    "approval_scopes": "APPROVAL_SCOPES_ENABLED",
    "projects": "PROJECTS_ENABLED",
    "agent_faces": "AGENT_FACES_ENABLED",
    "ui_shell_v2": "UI_SHELL_V2_ENABLED",
    "decibyl_channels": "DECIBYL_CHANNELS_ENABLED",
    "decibyl_telegram": "DECIBYL_TELEGRAM_ENABLED",
    "decibyl_slack": "DECIBYL_SLACK_ENABLED",
    "decibyl_teams": "DECIBYL_TEAMS_ENABLED",
}


def org_overrides() -> dict[str, frozenset[int]]:
    """``FEATURE_ORG_OVERRIDES`` parsed: feature name -> organisation ids.

    Format: ``"feature:1,2;feature2:3"``. Unknown names and non-numeric ids
    are ignored rather than raised, because a typo in ``.env`` on launch
    morning must not take the api down. Read at call time so a test can
    monkeypatch the constant.
    """
    raw = getattr(constants, "FEATURE_ORG_OVERRIDES", "") or ""
    out: dict[str, frozenset[int]] = {}
    for entry in raw.split(";"):
        name, _, ids = entry.strip().partition(":")
        name = name.strip()
        if name not in FLAGS:
            continue
        parsed = set()
        for token in ids.split(","):
            token = token.strip()
            if token.isdigit():
                parsed.add(int(token))
        if parsed:
            out[name] = frozenset(parsed)
    return out


def is_on(name: str, organization_id: int | None = None) -> bool:
    """Read at call time, so a test can switch a flag with monkeypatch.

    True when the global switch is on, or when ``organization_id`` is listed
    for ``name`` in ``FEATURE_ORG_OVERRIDES``.
    """
    if bool(getattr(constants, FLAGS[name])):
        return True
    if organization_id is None:
        return False
    return organization_id in org_overrides().get(name, frozenset())


def public() -> dict[str, bool]:
    """The global map, for ``/health`` (no session, so no organisation)."""
    return {name: is_on(name) for name in FLAGS}


def for_organization(organization_id: int | None) -> dict[str, bool]:
    """The map one organisation sees: global plus its overrides."""
    return {name: is_on(name, organization_id) for name in FLAGS}


def require(name: str, *, per_organization: bool = False):
    """A router dependency: every route under it is a 404 while ``name`` is
    off, so nothing about the feature is visible before it is switched on.

    With ``per_organization=True`` the check also honours the signed-in
    user's organisation overrides, at the cost of requiring a session on
    every route under it (the default stays global and session-free, so a
    public route can carry it).
    """
    if name not in FLAGS:
        raise KeyError(name)

    if per_organization:
        from fastapi import Depends

        from api.services.auth.depends import get_user

        def _org_dependency(user=Depends(get_user)) -> None:
            if not is_on(name, getattr(user, "selected_organization_id", None)):
                raise HTTPException(status_code=404, detail="Not Found")

        _org_dependency.feature = name  # read by tests
        return _org_dependency

    def _dependency() -> None:
        if not is_on(name):
            raise HTTPException(status_code=404, detail="Not Found")

    _dependency.feature = name  # read by tests
    return _dependency


__all__ = ["FLAGS", "for_organization", "is_on", "org_overrides", "public", "require"]
