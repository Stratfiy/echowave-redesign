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

Switches from the staff console (ADMIN-1): the ``feature_overrides`` table
holds rows set from Super admin -> Flags, for one organisation or for
everyone. ``is_on`` stays synchronous, so it reads an in-process snapshot of
that table, never the database. The snapshot is loaded in the app lifespan,
reloaded on every API worker through ``WorkerSyncEventType.FEATURE_OVERRIDES``
when a row is written, and reloaded every ``REFRESH_SECONDS`` by every
process (the ARQ worker does not listen on pub/sub, so that is how a change
reaches it). Resolution order:

1. a live table row for this organisation;
2. a live table row for everyone;
3. the environment's global switch;
4. ``FEATURE_ORG_OVERRIDES`` (kept as a read-only fallback, so rolling the
   console back is safe).

A row past its ``expires_at`` is ignored, as if it had been deleted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from loguru import logger

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
    # Studio: agents and a website for them, built from one chat.
    "studio": "STUDIO_ENABLED",
    # Free while we are early: no plans, nothing charged (on by default).
    "free_mode": "FREE_MODE_ENABLED",
    # Launch stream `controls` (LAUNCH-PLAN.md, phase 1).
    "capability_checklist": "CAPABILITY_CHECKLIST_ENABLED",
    "operational_quotas": "OPERATIONAL_QUOTAS_ENABLED",
    "task_ledger": "TASK_LEDGER_ENABLED",
    "personal_space": "PERSONAL_SPACE_ENABLED",
    "member_preferences": "MEMBER_PREFERENCES_ENABLED",
    "event_catalogue": "EVENT_CATALOGUE_ENABLED",
    "reply_feedback": "REPLY_FEEDBACK_ENABLED",
}


#: What each flag turns on, for the staff console. A flag with no line here
#: still shows, under its own name (see ``describe``), so a new flag is never
#: missing from the console.
DESCRIPTIONS: dict[str, str] = {
    "task_board": "The task board: work in progress, in columns.",
    "dialer_import": "Import calls from a business's own dialer for the coach.",
    "workspace_roles": "A workspace's own saved roles, and sharing them.",
    "agent_graph_extras": "Extra triggers and last-run state on the agent graph.",
    "shell": "The September app shell.",
    "voice_watch": "Live watch of voice calls.",
    "charge_rule": "Charge rules on calls.",
    "procurement_docs": "Purchase orders and tax invoices as documents.",
    "decibyl_long_tasks": "Decibyl works on long tasks in the background.",
    "decibyl_private_threads": "Private threads with Decibyl.",
    "approvals": "Approval matrix and approval cards.",
    "vendor_metering": "Per-vendor metering of usage.",
    "managed_realtime_gemini_only": "Managed speech-to-speech on Gemini only.",
    "connections_per_person": "App connections held per person, not per workspace.",
    "personal_memory": "Memory that belongs to one person.",
    "table_tools": "Agents read and write tables.",
    "budget_policies": "Spend budgets and hard stops.",
    "plan_ladder": "The plan ladder on the billing screen.",
    "decibyl_tools": "Decibyl can use connected tools.",
    "agent_builder": "Build an agent by describing it.",
    "managed_telephony": "Numbers on Decibyl's carrier account.",
    "invite_only_signup": "Signup needs an invite code.",
    "trial_plan": "The 14-day trial replaces the free plan.",
    "byok_text": "Bring your own model key on text paths.",
    "marketplace_publishing": "Publish an agent to the marketplace.",
    "whatsapp_channel_ui": "The WhatsApp channel card.",
    "voice_number_flow": "Pick a phone number while hiring an agent.",
    "approval_scopes": "Approval scopes, pause and retire.",
    "projects": "Projects.",
    "agent_faces": "Agent faces.",
    "ui_shell_v2": "The new app shell.",
    "decibyl_channels": "Decibyl in your apps (the umbrella switch).",
    "decibyl_telegram": "Decibyl in Telegram.",
    "decibyl_slack": "Decibyl in Slack.",
    "decibyl_teams": "Decibyl in Microsoft Teams.",
    "studio": "Studio: build agents and a website for them from one chat.",
    "free_mode": "Free while we are early: no plans, nothing charged, nothing locked.",
    "capability_checklist": "Staff see each capability's source, configuration and tested state.",
    "operational_quotas": "Daily limits per person (turns, voice, sends, browser), even in free mode.",
    "task_ledger": "One task state set, approvals bound to the exact payload, no stale updates.",
    "personal_space": "Every person has a personal space beside the workspaces they join.",
    "member_preferences": "A person's own language, timezone, voice and summary time.",
    "event_catalogue": "Versioned analytics events with a private envelope, sent from an outbox.",
    "reply_feedback": "Was this useful? Yes / Not quite under replies and finished tasks.",
}


def describe(name: str) -> str:
    """The console's line for ``name``; never empty."""
    return DESCRIPTIONS.get(name) or name.replace("_", " ").capitalize()


# ---------------------------------------------------------------------------
# The staff console's switches (ADMIN-1): an in-process snapshot of the
# ``feature_overrides`` table, so ``is_on`` never touches the database.
# ---------------------------------------------------------------------------

#: How often every process re-reads the table: the only path for a process
#: that does not hear the pub/sub event, and a backstop for one that missed it.
REFRESH_SECONDS = 30


@dataclass(frozen=True)
class Override:
    enabled: bool
    expires_at: datetime | None = None

    def live(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return True
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return (now or datetime.now(UTC)) < expires


#: (feature, organization_id or None) -> the row. Replaced wholesale on each
#: load, never merged, so a deleted row disappears here too. Empty until the
#: first load, which simply means the environment decides -- the state every
#: deployment was in before the table existed.
_SNAPSHOT: dict[tuple[str, int | None], Override] = {}
_loaded_at: datetime | None = None
_refresher: asyncio.Task | None = None


def _console_row(name: str, organization_id: int | None) -> Override | None:
    row = _SNAPSHOT.get((name, organization_id))
    if row is None or not row.live():
        return None
    return row


def set_snapshot(rows: dict[tuple[str, int | None], Override]) -> None:
    """Replace the snapshot. For the loader, and for tests."""
    global _SNAPSHOT, _loaded_at
    _SNAPSHOT = dict(rows)
    _loaded_at = datetime.now(UTC)


def clear_snapshot() -> None:
    """Back to "the environment decides". For tests."""
    global _SNAPSHOT, _loaded_at
    _SNAPSHOT = {}
    _loaded_at = None


def snapshot_loaded_at() -> datetime | None:
    return _loaded_at


async def refresh_overrides() -> int:
    """Reload the snapshot from ``feature_overrides``. Returns the row count.

    A database error keeps the snapshot already held: a blip must not flip
    every console-set flag back to its environment value mid-shift.
    """
    from sqlalchemy import select

    from api.db import db_client
    from api.db.feature_override_models import FeatureOverrideModel

    try:
        async with db_client.async_session() as session:
            rows = (await session.scalars(select(FeatureOverrideModel))).all()
    except Exception as exc:  # noqa: BLE001 -- flags must not fail startup
        logger.warning("Could not refresh feature overrides: {}", exc)
        return len(_SNAPSHOT)

    loaded: dict[tuple[str, int | None], Override] = {}
    for row in rows:
        if row.feature not in FLAGS:
            # Logged rather than dropped silently: a row for a flag since
            # removed from the registry does nothing, and someone should know.
            logger.warning("feature_overrides row for unknown flag {}", row.feature)
            continue
        loaded[(row.feature, row.organization_id)] = Override(
            enabled=bool(row.enabled), expires_at=row.expires_at
        )
    set_snapshot(loaded)
    return len(loaded)


async def _refresh_forever(interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        await refresh_overrides()


def start_periodic_refresh(interval: float = REFRESH_SECONDS) -> asyncio.Task:
    """Re-read the table every ``interval`` seconds in this process.

    Idempotent. Started by the app lifespan and by the ARQ worker's startup.
    """
    global _refresher
    if _refresher is None or _refresher.done():
        _refresher = asyncio.create_task(_refresh_forever(interval))
    return _refresher


async def stop_periodic_refresh() -> None:
    global _refresher
    task, _refresher = _refresher, None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


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


def env_global(name: str) -> bool:
    """The environment's global switch alone."""
    return bool(getattr(constants, FLAGS[name]))


def global_state(name: str) -> tuple[bool, str]:
    """What everyone gets, and where that came from: ``console`` for a live
    global row in the table, ``environment`` otherwise."""
    row = _console_row(name, None)
    if row is not None:
        return row.enabled, "console"
    return env_global(name), "environment"


def is_on(name: str, organization_id: int | None = None) -> bool:
    """Read at call time, so a test can switch a flag with monkeypatch.

    In order: the console's row for this organisation, the console's global
    row, the environment's global switch, then ``FEATURE_ORG_OVERRIDES``.
    The first with an answer decides; an expired row has none.
    """
    if organization_id is not None:
        row = _console_row(name, organization_id)
        if row is not None:
            return row.enabled
    row = _console_row(name, None)
    if row is not None:
        return row.enabled
    if env_global(name):
        return True
    if organization_id is None:
        return False
    return organization_id in org_overrides().get(name, frozenset())


def on_anywhere(name: str) -> bool:
    """Whether ``name`` is on for anyone at all: everyone, or at least one
    organisation by a console row or ``FEATURE_ORG_OVERRIDES``. For a
    scheduled job that would otherwise scan every workspace to find none."""
    if is_on(name):
        return True
    if any(
        feature == name and org is not None and row.enabled and row.live()
        for (feature, org), row in _SNAPSHOT.items()
    ):
        return True
    return bool(org_overrides().get(name))


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


__all__ = [
    "DESCRIPTIONS",
    "FLAGS",
    "Override",
    "clear_snapshot",
    "describe",
    "env_global",
    "for_organization",
    "global_state",
    "is_on",
    "org_overrides",
    "public",
    "refresh_overrides",
    "require",
    "set_snapshot",
    "start_periodic_refresh",
    "stop_periodic_refresh",
]
