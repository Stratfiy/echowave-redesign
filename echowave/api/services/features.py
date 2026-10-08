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
    # The Windows and Mac app, and working on a person's own computer.
    "desktop_app": "DESKTOP_APP_ENABLED",
    "desktop_computer_use": "DESKTOP_COMPUTER_USE_ENABLED",
    # Free while we are early: no plans, nothing charged (on by default).
    "free_mode": "FREE_MODE_ENABLED",
    # AWS model gateway (stream aws-gateway).
    "aws_fallback_brain": "AWS_FALLBACK_BRAIN_ENABLED",
    "aws_cheap_tier": "AWS_CHEAP_TIER_ENABLED",
    "aws_embeddings": "AWS_EMBEDDINGS_ENABLED",
    "aws_nova_sonic": "AWS_NOVA_SONIC_ENABLED",
    # Decibyl's private browser: one isolated browser per person and task.
    "decibyl_browser": "DECIBYL_BROWSER_ENABLED",
    # Launch stream `controls` (LAUNCH-PLAN.md, phase 1).
    "capability_checklist": "CAPABILITY_CHECKLIST_ENABLED",
    "operational_quotas": "OPERATIONAL_QUOTAS_ENABLED",
    "task_ledger": "TASK_LEDGER_ENABLED",
    "personal_space": "PERSONAL_SPACE_ENABLED",
    "member_preferences": "MEMBER_PREFERENCES_ENABLED",
    "event_catalogue": "EVENT_CATALOGUE_ENABLED",
    "reply_feedback": "REPLY_FEEDBACK_ENABLED",
    # Launch stream `shell` (LAUNCH-PLAN.md, phase 1).
    "early_access": "EARLY_ACCESS_ENABLED",
    "first_task_onboarding": "FIRST_TASK_ONBOARDING_ENABLED",
    "chat_shell": "CHAT_SHELL_ENABLED",
    "shell_mobile": "SHELL_MOBILE_ENABLED",
    # Launch stream `agents` (LAUNCH-PLAN.md, phase 2).
    "launch_helpers": "LAUNCH_HELPERS_ENABLED",
    "research_reports": "RESEARCH_REPORTS_ENABLED",
    "follow_up_ledger": "FOLLOW_UP_LEDGER_ENABLED",
    "trading_summaries": "TRADING_SUMMARIES_ENABLED",
    "describe_builder": "DESCRIBE_BUILDER_ENABLED",
    # Launch stream `today` (LAUNCH-PLAN.md, phase 2).
    "today_list": "TODAY_LIST_ENABLED",
    "approval_dock": "APPROVAL_DOCK_ENABLED",
    "today_reminders": "TODAY_REMINDERS_ENABLED",
    "daily_brief": "DAILY_BRIEF_ENABLED",
    "end_of_day_note": "END_OF_DAY_NOTE_ENABLED",
    "routine_start_on": "ROUTINE_START_ON_ENABLED",
    # Launch stream `support` (LAUNCH-PLAN.md, phase 2).
    "support_help": "SUPPORT_HELP_ENABLED",
    "support_inbox": "SUPPORT_INBOX_ENABLED",
    "support_actions": "SUPPORT_ACTIONS_ENABLED",
    # Stream ops (handoff 11, 14, 15 G-H, 34, 35).
    "ops_console": "OPS_CONSOLE_ENABLED",
    "server_analytics": "SERVER_ANALYTICS_ENABLED",
    "telemetry_redaction": "TELEMETRY_REDACTION_ENABLED",
    "session_replay": "SESSION_REPLAY_ENABLED",
    "laya_guardrails": "LAYA_GUARDRAILS_ENABLED",
    "laya_rollback": "LAYA_ROLLBACK_ENABLED",
    "cost_stop": "COST_STOP_ENABLED",
    # Launch stream `care` (LAUNCH-PLAN.md, phase 2).
    "care_simple_mode": "CARE_SIMPLE_MODE_ENABLED",
    "care_medicine_calls": "CARE_MEDICINE_CALLS_ENABLED",
    "care_scam_check": "CARE_SCAM_CHECK_ENABLED",
    "care_tech_help": "CARE_TECH_HELP_ENABLED",
    "care_family_circle": "CARE_FAMILY_CIRCLE_ENABLED",
    # Launch stream `reach` (LAUNCH-PLAN.md, phase 2).
    "outside_tools": "OUTSIDE_TOOLS_ENABLED",
    "ordering": "ORDERING_ENABLED",
    "price_compare": "PRICE_COMPARE_ENABLED",
    # Launch stream `learning` (LAUNCH-PLAN.md, phase 2).
    "learning": "LEARNING_ENABLED",
    "learning_today": "LEARNING_TODAY_ENABLED",
    # Launch stream `meetings` (LAUNCH-PLAN.md, phase 2).
    "meeting_capture": "MEETING_CAPTURE_ENABLED",
    # Launch stream `staff` (LAUNCH-PLAN.md, phase 2; STAFF.md).
    "staff_console": "STAFF_CONSOLE_ENABLED",
    "staff_roles": "STAFF_ROLES_ENABLED",
    "staff_refunds": "STAFF_REFUNDS_ENABLED",
    "staff_evaluations": "STAFF_EVALUATIONS_ENABLED",
    "staff_incidents": "STAFF_INCIDENTS_ENABLED",
    # Launch stream `identity` (LAUNCH-PLAN.md, phase 2).
    "identity_connections": "IDENTITY_CONNECTIONS_ENABLED",
    "identity_email": "IDENTITY_EMAIL_ENABLED",
    "identity_phone": "IDENTITY_PHONE_ENABLED",
    "identity_notifications": "IDENTITY_NOTIFICATIONS_ENABLED",
    "identity_reconciliation": "IDENTITY_RECONCILIATION_ENABLED",
    # Launch stream `settings` (LAUNCH-PLAN.md, phase 2).
    "settings_shell": "SETTINGS_SHELL_ENABLED",
    "memory_manager": "MEMORY_MANAGER_ENABLED",
    "privacy_center": "PRIVACY_CENTER_ENABLED",
    "saved_items": "SAVED_ITEMS_ENABLED",
    "model_inheritance": "MODEL_INHERITANCE_ENABLED",
    # Launch stream `voice` (LAUNCH-PLAN.md, phase 2).
    "decibyl_voice": "DECIBYL_VOICE_ENABLED",
    "voice_latency": "VOICE_LATENCY_ENABLED",
    "call_for_me": "CALL_FOR_ME_ENABLED",
    "call_appointment": "CALL_APPOINTMENT_ENABLED",
    # The native app for iOS and Android (MOBILE.md).
    "mobile_push": "MOBILE_PUSH_ENABLED",
    # People: synced contacts with context (PEOPLE.md).
    "people": "PEOPLE_ENABLED",
    # Voice isolation: background voices (VOICE.md).
    "caller_voice_lock": "CALLER_VOICE_LOCK_ENABLED",
    "deepfilternet_filter": "DEEPFILTERNET_FILTER_ENABLED",
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
    "desktop_app": "The Windows and Mac app: notifications, files from disk, a watched folder.",
    "desktop_computer_use": "Work on my computer: Decibyl uses the apps a person allows, asking before it sends, pays, deletes or submits.",
    "free_mode": "Free while we are early: no plans, nothing charged, nothing locked.",
    "aws_fallback_brain": "A Bedrock model answers when Claude fails, and says so.",
    "aws_cheap_tier": "A small Bedrock model sorts work for Auto instead of Laya.",
    "aws_embeddings": "Knowledge search on Bedrock embeddings as a managed choice.",
    "aws_nova_sonic": "Nova Sonic speech-to-speech, Hindi and Indian English only.",
    "decibyl_browser": (
        "Decibyl's private browser: browses for a person in an isolated box, "
        "live view and Take over, asks before submit, pay, send or book."
    ),
    "capability_checklist": "Staff see each capability's source, configuration and tested state.",
    "operational_quotas": "Daily limits per person (turns, voice, sends, browser), even in free mode.",
    "task_ledger": "One task state set, approvals bound to the exact payload, no stale updates.",
    "personal_space": "Every person has a personal space beside the workspaces they join.",
    "member_preferences": "A person's own language, timezone, voice and summary time.",
    "event_catalogue": "Versioned analytics events with a private envelope, sent from an outbox.",
    "reply_feedback": "Was this useful? Yes / Not quite under replies and finished tasks.",
    "early_access": "The public waitlist and invitation pages (screen 01).",
    "first_task_onboarding": "Language, timezone and a first task; new people land in Chat (screen 02).",
    "chat_shell": "Chat: three starters, attach menu, Dictate and Talk, Stop, New content, sources and task states (screens 03-04).",
    "staff_console": "The staff console: eight role-gated destinations under /superadmin (screens 29-31, 34-44).",
    "staff_roles": "Console roles (operations, finance, quality) granted by an owner through an approved command.",
    "staff_refunds": "Finance-only refunds: preview, second-person approval, run once, reconcile.",
    "staff_evaluations": "Versioned evaluation cases, runs against a fixed set, and case comparison.",
    "staff_incidents": "Incidents with an approved runbook: preflight, approval, execution, verification.",
    "shell_mobile": "Phone shell: Chat and Today at the bottom, profile in the header, keyboard-aware bar, read-only workflow steps.",
    "launch_helpers": "The helper picker in Chat: Automatic and the five helpers, each with its capability state (screen 06).",
    "research_reports": "Research keeps saved reports with their sources; the export matches what was shown.",
    "follow_up_ledger": "Follow-up tracks commitments a person approved, and answers who owes me.",
    "trading_summaries": "Research summarises markets by a person's own interests: information only, never advice.",
    "describe_builder": "Ask Decibyl to build anything: agents, routines and trackers from a description, in Chat.",
    "today_list": "Today as one ordered list, the exact approval screen, task detail and activity (screens 07-09).",
    "approval_dock": 'Pending approvals docked above the composer: "Decibyl wants to: ..." with Do it / Don\'t.',
    "today_reminders": "Reminders and event-linked reminders with their editor and delivery (screen 10).",
    "daily_brief": "One daily brief with source coverage, in-app, WhatsApp and push at the person's time (screen 20).",
    "end_of_day_note": "An end-of-day note: what was done, what is left, missed calls handled.",
    "routine_start_on": "A routine set from chat starts on once its card is confirmed.",
    "support_help": "Help: ask support, choose exactly what is shared, follow the ticket (screen 28).",
    "support_inbox": "Staff support inbox and case with internal notes (screen 32).",
    "support_actions": "Typed support actions with a preview, a second person's approval and an audit (screen 33).",
    "ops_console": "Staff operations: health, provider key lifecycle, typed commands, evidence.",
    "server_analytics": "Server-owned product events to PostHog through a durable outbox.",
    "telemetry_redaction": "Redact secrets and personal data from logs and error reports.",
    "session_replay": "Session replay on non-sensitive screens only (off: no replay at all).",
    "laya_guardrails": "Laya hard deadline, circuit breaker and shadow agreement statistics.",
    "laya_rollback": "Rollback: Auto routes by rules alone and never asks Laya.",
    "cost_stop": "Stop new billable work when provider spend runs away.",
    "care_simple_mode": "Simple mode: large text, voice first, one thing at a time (needs member_preferences).",
    "care_medicine_calls": "Medicine reminder calls in the person's language, with a family alert when a dose is missed.",
    "care_scam_check": "Is this a scam? Paste or describe a message or call; a plain answer and why.",
    "care_tech_help": "Step-by-step phone help in plain words, one step at a time, with did that work?",
    "care_family_circle": "A family circle the older person consents to; family see only what is shared with them.",
    "outside_tools": "Outside AI tools (MCP servers) a person connects in the Chat thread and uses from Chat; writes ask first.",
    "ordering": "Order food and groceries from a list in Chat (Zomato; Swiggy when access arrives), always through an order card.",
    "price_compare": "Compare prices and coupons across the ordering apps a person has connected, saying which and when.",
    "learning": "Learning Guide: goals on any subject, lessons in Chat, evaluated practice and progress (screens 13-14).",
    "learning_today": "Learning reviews that are due, listed in Today.",
    "meeting_capture": "Meeting mode: record, upload or paste a meeting with consent; Sarvam transcript; summary, decisions and follow-ups confirmed one card at a time (screens 11-12).",
    "identity_connections": "Connected apps and channels per person: consent, revocation and verified channel capabilities (screen 22).",
    "identity_email": "A person's Decibyl email address: alias lifecycle, inbound routing, sends through cards (screen 23).",
    "identity_phone": "Phone and verification lifecycle with the number payment flow explained (screen 24).",
    "identity_notifications": "Notification preferences per person and web push (screen 21).",
    "identity_reconciliation": "Checks with each provider whether a send whose outcome was unknown arrived.",
    "settings_shell": "Settings grouped as Personal, Connections, Privacy, Advanced and the workspace, with search; Account, Personalization and Voice on the person's own preferences (screens 17-19).",
    "memory_manager": "Memory manager: opt-in memory, provenance, edits as revisions, forget through a card, share to a team, temporary chats (screen 16).",
    "privacy_center": "Privacy and security: personal export, personal deletion through a card, effective retention, MFA (screen 25).",
    "saved_items": "Saved items and search in one scope at a time (screen 15).",
    "model_inheritance": "Model defaults show where each comes from, readiness and agent overrides; saves are revision-checked (screen 26).",
    "decibyl_voice": "Talk with Decibyl: live voice from Chat with interruption, mute, captions and reconnect (screen 05).",
    "voice_latency": "Voice latency per turn: response and interruption times, p50/p95 by language and channel (handoff 12).",
    "call_for_me": "Call it for me: Decibyl places one approved phone call for a person and announces itself first.",
    "call_appointment": "Call and Appointment: booking policy, open slots, booking within policy, verification and escalation on calls.",
    "mobile_push": "Push to the iOS and Android app through Expo: replies, approvals, reminders and calls, on the person's notification settings.",
    "people": "People: a person's own contacts synced from Google and Outlook or imported, each with a brief and the last few interactions; private to them.",
    "caller_voice_lock": "Only the caller can interrupt a phone agent: speech that does not match the caller's voice, learnt in their first seconds, no longer stops the agent.",
    "deepfilternet_filter": "DeepFilterNet3 instead of RNNoise as the noise filter on calls with noise suppression on.",
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
