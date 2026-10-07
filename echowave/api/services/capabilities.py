"""The runtime capability checklist (handoff packet A, sections 3, 4, 29).

"A runtime capability checklist separates source, configuration and tested
behavior." Three questions per capability, answered separately because a
yes to one says nothing about the others:

* **Source** -- is the code here? Checked by finding each module without
  importing it. A capability still to be built names the stream that owns it
  and reads ``absent`` until it lands.
* **Configuration** -- is it switched on and given what it needs here? The
  flags it hangs off and the settings it reads, reported as present or not.
  Never a value: this endpoint is for staff, and a key's last four digits
  are still a key.
* **Tested** -- which tests in this repository exercise it, and whether they
  are present. A test in the repository is not a staging run: the staging
  column stays "not verified" until ``scripts/staging_check.py`` evidence is
  recorded against it (phase 3). Source presence does not prove readiness.

Then one honest state per the design's capability contract: ``available``,
``needs_setup``, ``disabled_by_policy`` or ``unavailable``, with the reason.

The list is data, not discovery: a capability nobody wrote down here is not
on the checklist, so ``test_capability_checklist.py`` checks every module,
flag, setting and test named here exists -- a renamed file shows up as a
failing test rather than a quietly shorter list.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from api import constants
from api.services import features

TESTS_DIR = Path(__file__).resolve().parents[1] / "tests"

AVAILABLE = "available"
NEEDS_SETUP = "needs_setup"
DISABLED = "disabled_by_policy"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Capability:
    key: str
    name: str
    #: Handoff section(s) it answers.
    handoff: str
    #: Dotted module paths under ``api``.
    modules: tuple[str, ...] = ()
    #: Feature registry names that must be on.
    flags: tuple[str, ...] = ()
    #: Settings that must be non-empty: ``api.constants`` names, or
    #: ``env:NAME`` for the ones a module reads from the environment itself.
    settings: tuple[str, ...] = ()
    #: Test files under ``api/tests``.
    tests: tuple[str, ...] = ()
    #: Not built yet: the launch stream that builds it.
    planned_by: str | None = None
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "central_assistant",
        "Decibyl, the central assistant",
        "3, 5",
        modules=("api.services.workflow.decibyl",),
        tests=("test_decibyl_assistant.py", "test_decibyl_keeps_working.py"),
    ),
    Capability(
        "auto_brain",
        "Auto model routing",
        "8",
        modules=("api.services.routing.brain",),
        tests=("test_auto_brain.py",),
    ),
    Capability(
        "connected_tools",
        "Connected app reads and proposed writes",
        "3",
        modules=("api.services.workflow.connected_tools",),
        flags=("decibyl_tools",),
        settings=("COMPOSIO_API_KEY",),
        tests=("test_connected_tools.py", "test_decibyl_connected_tools.py"),
    ),
    Capability(
        "approval_cards",
        "Approval cards that run once",
        "3, 10",
        modules=("api.services.workflow.actions",),
        tests=(
            "test_an_approved_action_runs_once.py",
            "test_one_ask_is_one_card.py",
            "test_a_send_is_a_card.py",
        ),
    ),
    Capability(
        "task_ledger",
        "Task ledger, payload-bound approval, idempotency, stale rejection",
        "10, 15 B",
        modules=("api.services.workflow.task_ledger",),
        flags=("task_ledger",),
        tests=("test_task_ledger.py",),
    ),
    Capability(
        "background_tasks",
        "Long tasks carried on in the background",
        "3",
        modules=("api.services.workflow.decibyl_tasks",),
        flags=("decibyl_long_tasks",),
        tests=("test_decibyl_keeps_working.py",),
    ),
    Capability(
        "routines",
        "Routines and schedules",
        "3, 10",
        modules=("api.services.workflow.routines",),
        tests=("test_routines.py", "test_decibyl_has_routines.py"),
    ),
    Capability(
        "personal_memory",
        "Memory that belongs to one person",
        "3, 8",
        modules=("api.services.knowledge_graph.personal",),
        flags=("personal_memory",),
        tests=("test_personal_memory.py",),
    ),
    Capability(
        "personal_space",
        "A personal space beside the workspaces a person joins",
        "8, founder",
        modules=("api.services.personal_space",),
        flags=("personal_space",),
        tests=("test_personal_space.py",),
    ),
    Capability(
        "member_preferences",
        "A person's own language, timezone, voice and summary time",
        "31.3",
        modules=("api.services.member_preferences",),
        flags=("member_preferences",),
        tests=("test_member_preferences.py",),
    ),
    # Launch stream `settings` (SETTINGS.md).
    Capability(
        "settings_shell",
        "Settings grouped for a person, with search; Account, Personalization, Voice",
        "24, 30; screens 17-19",
        modules=("api.services.settings.profile", "api.routes.settings"),
        flags=("settings_shell", "member_preferences"),
        tests=("test_settings_profile.py",),
    ),
    Capability(
        "memory_manager",
        "Memory opt-in, provenance, edits as revisions, forget by card, sharing",
        "8; screen 16",
        modules=("api.services.settings.memory", "api.services.settings.temporary"),
        flags=("memory_manager",),
        tests=("test_settings_memory.py", "test_settings_profile.py"),
    ),
    Capability(
        "saved_items",
        "Saved items and search in one scope",
        "screen 15",
        modules=("api.services.settings.saved",),
        flags=("saved_items",),
        tests=("test_settings_saved.py",),
    ),
    Capability(
        "privacy_center",
        "A person's own export and deletion, effective retention, MFA",
        "24, 25; screen 25",
        modules=("api.services.settings.privacy",),
        flags=("privacy_center", "personal_space"),
        tests=("test_settings_privacy.py",),
    ),
    Capability(
        "model_inheritance",
        "Model defaults with source, readiness, fallback and agent overrides",
        "8, 25, 30; screen 26",
        modules=("api.services.settings.models",),
        flags=("model_inheritance",),
        tests=("test_settings_models.py",),
    ),
    Capability(
        "operational_quotas",
        "Daily limits per person, in free mode too",
        "9, 15 B",
        modules=("api.services.quotas",),
        flags=("operational_quotas",),
        tests=("test_operational_quotas.py",),
    ),
    Capability(
        "event_catalogue",
        "Versioned analytics events through an outbox",
        "35, 36",
        modules=("api.services.events.catalogue", "api.services.events.outbox"),
        flags=("event_catalogue",),
        settings=("ANALYTICS_PSEUDONYM_KEY", "POSTHOG_API_KEY"),
        tests=("test_event_catalogue.py",),
    ),
    Capability(
        "reply_feedback",
        "Was this useful? on replies and finished tasks",
        "2, 4, 6",
        modules=("api.services.feedback",),
        flags=("reply_feedback",),
        tests=("test_reply_feedback.py",),
    ),
    Capability(
        "learning",
        "Learning practice and progress",
        "3, 6, 23",
        modules=("api.services.knowledge_graph.spaced_recall",),
        tests=(),
        note="Recalled facts only; no curriculum or mastery yet (stream learning).",
        extra={"partial": True},
    ),
    Capability(
        "skills",
        "Skills shelf, catalogue and imports",
        "3, 8",
        modules=(
            "api.services.skills.shelf",
            "api.services.skills.catalogue",
            "api.services.skills.imports",
        ),
        tests=("test_skills_shelf.py", "test_skills_catalogue.py"),
    ),
    Capability(
        "invites",
        "Invite-only signup",
        "3, 16",
        modules=("api.services.auth.signup_invites",),
        flags=("invite_only_signup",),
        tests=("test_invite_only_signup.py",),
    ),
    Capability(
        "channel_whatsapp",
        "Decibyl on WhatsApp",
        "3, 7",
        modules=("api.services.messaging.channels.whatsapp",),
        flags=("decibyl_channels",),
        settings=(
            "WHATSAPP_ACCESS_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID",
            "WHATSAPP_APP_SECRET",
        ),
        tests=("test_whatsapp_inbound.py", "test_decibyl_channels.py"),
    ),
    Capability(
        "channel_telegram",
        "Decibyl on Telegram",
        "3, 7",
        modules=("api.services.messaging.channels.telegram",),
        flags=("decibyl_channels", "decibyl_telegram"),
        settings=("env:TELEGRAM_BOT_TOKEN", "env:TELEGRAM_WEBHOOK_SECRET"),
        tests=("test_decibyl_channels.py",),
    ),
    Capability(
        "channel_slack",
        "Decibyl in Slack",
        "3, 7",
        modules=("api.services.messaging.channels.slack",),
        flags=("decibyl_channels", "decibyl_slack"),
        settings=(
            "env:SLACK_CLIENT_ID",
            "env:SLACK_CLIENT_SECRET",
            "env:SLACK_SIGNING_SECRET",
        ),
        tests=("test_decibyl_channels.py",),
    ),
    Capability(
        "channel_teams",
        "Decibyl in Microsoft Teams",
        "3, 7",
        modules=("api.services.messaging.channels.teams",),
        flags=("decibyl_channels", "decibyl_teams"),
        settings=("env:MICROSOFT_APP_ID", "env:MICROSOFT_APP_PASSWORD"),
        tests=("test_decibyl_channels.py",),
    ),
    Capability(
        "inbound_email",
        "Inbound trigger address (a webhook, not a mailbox)",
        "4, 7",
        modules=("api.routes.public_email",),
        settings=("INBOUND_EMAIL_DOMAIN",),
        tests=("test_email_triggers.py",),
        note="Friendly name@decibyl.ai identity is not built (stream identity).",
    ),
    Capability(
        "phone_kyc",
        "Phone number after KYC",
        "4, 7",
        modules=("api.services.kyc",),
        flags=("managed_telephony",),
        tests=("test_kyc_state.py", "test_kyc_flow.py"),
    ),
    Capability(
        "web_voice",
        "Live voice in the browser",
        "4, 12",
        modules=("api.routes.webrtc_signaling", "api.services.pipecat.run_pipeline"),
        tests=("test_webrtc_signaling_concurrency.py",),
        note="Live voice with Decibyl itself, distinct from dictation, is stream voice.",
    ),
    Capability(
        "free_mode",
        "Free while early (no plans, nothing charged)",
        "4, 9",
        modules=("api.services.billing.free_mode",),
        flags=("free_mode",),
        tests=("test_free_mode.py",),
    ),
    Capability(
        "error_tracking",
        "Exception and release tracking",
        "35",
        modules=("api.observability.sentry",),
        settings=("SENTRY_DSN",),
        tests=("test_sentry_scrub.py",),
    ),
    Capability(
        "daily_brief",
        "One daily brief with source coverage",
        "10, 22",
        modules=("api.services.workflow.daily_brief",),
        planned_by="today",
    ),
    Capability(
        "meeting_capture",
        "Meeting capture and record",
        "23",
        modules=("api.services.meetings",),
        planned_by="meetings",
    ),
    Capability(
        "private_browser",
        "Decibyl's private browser",
        "founder",
        modules=("api.services.browser",),
        planned_by="browser",
    ),
    Capability(
        "virtual_card",
        "Virtual card",
        "4, 7",
        planned_by="identity",
        note="Coming soon only: no issuance, spending or card storage at launch.",
    ),
)


def _module_present(path: str) -> bool:
    try:
        return importlib.util.find_spec(path) is not None
    except (ImportError, ValueError):
        return False


def _setting_present(name: str) -> bool:
    if name.startswith("env:"):
        return bool(os.getenv(name[4:], "").strip())
    return bool(getattr(constants, name, None))


def evaluate(capability: Capability, organization_id: int | None = None) -> dict:
    modules = {m: _module_present(m) for m in capability.modules}
    flags = {f: features.is_on(f, organization_id) for f in capability.flags}
    settings = {
        s.removeprefix("env:"): _setting_present(s) for s in capability.settings
    }
    tests = {t: (TESTS_DIR / t).is_file() for t in capability.tests}

    if capability.planned_by and not all(modules.values() or [False]):
        source = "absent"
    elif modules and all(modules.values()):
        source = "partial" if capability.extra.get("partial") else "present"
    elif any(modules.values()):
        source = "partial"
    else:
        source = "absent"

    if source == "absent":
        state = UNAVAILABLE
        reason = (
            f"Not built yet (stream {capability.planned_by})."
            if capability.planned_by
            else "The code for this is not in this build."
        )
    elif flags and not all(flags.values()):
        state = DISABLED
        off = ", ".join(f for f, on in flags.items() if not on)
        reason = f"Switched off: {off}."
    elif settings and not all(settings.values()):
        state = NEEDS_SETUP
        missing = ", ".join(s for s, ok in settings.items() if not ok)
        reason = f"Needs configuration: {missing}."
    else:
        state = AVAILABLE
        reason = (
            (capability.note or "Only part of this is built.")
            if source == "partial"
            else ""
        )

    if not tests:
        tested = "none"
    elif all(tests.values()):
        tested = "unit_tested"
    else:
        tested = "tests_missing"

    return {
        "key": capability.key,
        "name": capability.name,
        "handoff": capability.handoff,
        "state": state,
        "reason": reason,
        "source": {"status": source, "modules": modules},
        "configuration": {
            "status": "configured"
            if all(flags.values()) and all(settings.values())
            else "incomplete",
            "flags": flags,
            "settings": settings,
        },
        "tested": {
            "status": tested,
            "files": tests,
            "staging": "not_verified",
        },
        "planned_by": capability.planned_by,
        "note": capability.note,
    }


def checklist(organization_id: int | None = None) -> list[dict]:
    return [evaluate(c, organization_id) for c in CAPABILITIES]
