"""Each helper's capability state for one person in one workspace.

The design's capability contract: ``available``, ``needs_setup``,
``disabled_by_policy`` or ``unavailable``, each with a reason and a next
step. The backend decides; the picker only shows it, and a turn asked with
a helper that is not available is refused (``hidden buttons are not
authorization``, handoff 27).

Every reading here is local -- tool rows, phone-number rows, the search
key's credential row, the workspace's own switches -- so opening the picker
never calls an outside service. A reading that fails says so
(``unavailable``, "could not check"), never "needs setup": a database blip
must not tell a person their connected Gmail is missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select

from api.db import db_client
from api.db.agents_models import HelperWorkspaceSettingModel
from api.services import features
from api.services.helpers import catalogue

FLAG = "launch_helpers"

AVAILABLE = "available"
NEEDS_SETUP = "needs_setup"
DISABLED = "disabled_by_policy"
UNAVAILABLE = "unavailable"


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


@dataclass(frozen=True)
class Setup:
    """The next step. ``connect`` puts a connect card on the thread (Chat
    keeps the draft); ``operator`` is a step only staff can take, said
    rather than offered."""

    kind: str  # connect | operator | number
    label: str
    app: str | None = None


@dataclass(frozen=True)
class State:
    key: str
    state: str
    reason: str | None = None
    setup: Setup | None = None
    #: Things that work and things that do not yet, said beside an
    #: available state rather than hidden in it.
    notes: tuple[str, ...] = ()


@dataclass
class Readings:
    """What the states are computed from, read once per request."""

    toolkits: set[str] | None = None  # None: could not read
    has_number: bool | None = None
    search_key: bool | None = None
    apps_configured: bool = False
    web_tools: bool = False
    workspace_off: set[str] = field(default_factory=set)
    flags: dict[str, bool] = field(default_factory=dict)


async def _toolkits(organization_id: int) -> set[str] | None:
    from api.enums import ToolStatus
    from api.services.workflow import connected_tools

    try:
        rows = await db_client.get_tools_for_organization(
            organization_id, status=ToolStatus.ACTIVE.value
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Helpers could not read tools for {}: {}", organization_id, exc)
        return None
    return {
        kit
        for t in rows
        if connected_tools.is_connected(t)
        and (kit := connected_tools.toolkit_of(t)) is not None
    }


async def _has_number(organization_id: int) -> bool | None:
    from api.db.models import TelephonyPhoneNumberModel

    try:
        async with db_client.async_session() as session:
            row = await session.scalar(
                select(TelephonyPhoneNumberModel.id)
                .where(
                    TelephonyPhoneNumberModel.organization_id == organization_id,
                    TelephonyPhoneNumberModel.is_active.is_(True),
                )
                .limit(1)
            )
        return row is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Helpers could not read numbers for {}: {}", organization_id, exc
        )
        return None


async def _search_key() -> bool | None:
    from api.services.workflow import web_tools

    try:
        return bool(await web_tools._search_key())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Helpers could not read the search key: {}", exc)
        return None


async def workspace_switches(organization_id: int) -> dict[str, bool]:
    async with db_client.async_session() as session:
        rows = (
            await session.scalars(
                select(HelperWorkspaceSettingModel).where(
                    HelperWorkspaceSettingModel.organization_id == organization_id
                )
            )
        ).all()
    return {r.helper: bool(r.enabled) for r in rows}


async def set_workspace_switch(
    organization_id: int, helper: str, *, enabled: bool, user_id: int
) -> None:
    if helper not in catalogue.BY_KEY:
        raise KeyError(helper)
    from sqlalchemy.dialects.postgresql import insert

    now = datetime.now(UTC)
    statement = insert(HelperWorkspaceSettingModel).values(
        organization_id=organization_id,
        helper=helper,
        enabled=enabled,
        updated_by=user_id,
        updated_at=now,
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_helper_workspace_settings",
        set_={"enabled": enabled, "updated_by": user_id, "updated_at": now},
    )
    async with db_client.async_session() as session:
        await session.execute(statement)
        await session.commit()


async def read(organization_id: int) -> Readings:
    from api.services.integrations.composio.client import is_configured
    from api.services.workflow import web_tools

    readings = Readings(
        toolkits=await _toolkits(organization_id),
        has_number=await _has_number(organization_id),
        apps_configured=is_configured(),
        web_tools=web_tools.enabled(),
        flags={
            name: features.is_on(name, organization_id)
            for name in (
                "research_reports",
                "follow_up_ledger",
                "trading_summaries",
                "describe_builder",
                "learning",
            )
        },
    )
    readings.search_key = await _search_key() if readings.web_tools else False
    try:
        readings.workspace_off = {
            k for k, on in (await workspace_switches(organization_id)).items() if not on
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Helpers could not read workspace switches: {}", exc)
    return readings


_COULD_NOT_CHECK = "Could not check this just now. Try again in a moment."


def evaluate(helper: catalogue.Helper, r: Readings) -> State:
    key = helper.key
    if key in r.workspace_off:
        return State(key, DISABLED, "Turned off by your workspace.")

    if key == catalogue.INBOX:
        if r.toolkits is None:
            return State(key, UNAVAILABLE, _COULD_NOT_CHECK)
        if not r.toolkits & catalogue.MAIL_APPS:
            if not r.apps_configured:
                return State(
                    key,
                    UNAVAILABLE,
                    "Connecting mail is not set up on this platform yet.",
                    Setup(
                        "operator", "An operator needs to switch on app connections."
                    ),
                )
            return State(
                key,
                NEEDS_SETUP,
                "Connect Gmail or Outlook so Inbox can read your mail.",
                Setup("connect", "Connect Gmail", app="gmail"),
            )
        return State(key, AVAILABLE)

    if key == catalogue.RESEARCH:
        if not r.web_tools:
            return State(
                key,
                UNAVAILABLE,
                "Web search is switched off on this platform.",
                Setup(
                    "operator", "An operator needs to switch on Decibyl's web tools."
                ),
            )
        if r.search_key is None:
            return State(key, UNAVAILABLE, _COULD_NOT_CHECK)
        if not r.search_key:
            return State(
                key,
                NEEDS_SETUP,
                "Web search has no key on this platform yet.",
                Setup("operator", "An operator needs to add a search key."),
            )
        notes = []
        if not r.flags.get("research_reports"):
            notes.append("Saved reports are not switched on here yet.")
        return State(key, AVAILABLE, notes=tuple(notes))

    if key == catalogue.FOLLOW_UP:
        if not r.flags.get("follow_up_ledger"):
            return State(
                key,
                UNAVAILABLE,
                "Tracking commitments is not switched on here yet.",
            )
        notes = []
        if r.toolkits is not None and not r.toolkits & catalogue.MESSAGE_APPS:
            notes.append(
                "Tracks and drafts now; sending a follow-up needs Gmail, "
                "Outlook, WhatsApp or Slack connected."
            )
        return State(key, AVAILABLE, notes=tuple(notes))

    if key == catalogue.LEARNING_GUIDE:
        notes = ["Lessons, practice and feedback in this conversation."]
        if r.flags.get("learning"):
            notes.append("Builds on your goals and marked practice in Learning.")
        else:
            notes.append(
                "Saved progress and reviews arrive when Learning is switched on."
            )
        return State(key, AVAILABLE, notes=tuple(notes))

    if key == catalogue.CALL_APPOINTMENT:
        if r.has_number is None or r.toolkits is None:
            return State(key, UNAVAILABLE, _COULD_NOT_CHECK)
        if not r.has_number:
            return State(
                key,
                NEEDS_SETUP,
                "Answering calls needs a phone number, which needs KYC first.",
                Setup("number", "Get a phone number"),
            )
        if not r.toolkits & catalogue.CALENDAR_APPS:
            return State(
                key,
                NEEDS_SETUP,
                "Connect a calendar so it can suggest and book slots.",
                Setup("connect", "Connect Google Calendar", app="googlecalendar"),
            )
        return State(key, AVAILABLE)

    if key == catalogue.BUILDER:
        if not r.flags.get("describe_builder"):
            return State(key, UNAVAILABLE, "The builder is not switched on here yet.")
        return State(key, AVAILABLE)

    # A helper added to the catalogue with no rule here is said, not hidden.
    return State(key, UNAVAILABLE, "Not ready yet.")


async def for_workspace(organization_id: int) -> list[State]:
    readings = await read(organization_id)
    return [evaluate(catalogue.BY_KEY[k], readings) for k in catalogue.BY_KEY]


async def state_of(organization_id: int, key: str) -> State | None:
    helper = catalogue.BY_KEY.get(key)
    if helper is None:
        return None
    return evaluate(helper, await read(organization_id))


def describe(
    helper: catalogue.Helper, state: State, *, advanced: bool
) -> dict[str, Any]:
    """The picker's row and detail (screen 06)."""
    out: dict[str, Any] = {
        "key": helper.key,
        "name": helper.name,
        "job": helper.job,
        "evidence": helper.evidence,
        "boundary": helper.boundary,
        "example": helper.example,
        "permissions": list(helper.permissions),
        "connections": sorted(helper.apps),
        "templates": list(helper.templates),
        "state": state.state,
        "reason": state.reason,
        "setup": (
            {
                "kind": state.setup.kind,
                "label": state.setup.label,
                "app": state.setup.app,
            }
            if state.setup
            else None
        ),
        "notes": list(state.notes),
    }
    if advanced:
        # Screen 06: skill assignments and model inheritance stay visible to
        # authorised advanced users.
        out["advanced"] = {
            "tools": sorted(helper.tools),
            "skills": list(helper.skills),
            "model": "Inherits the workspace's brain (Auto).",
            "voice": helper.voice or "Inherits your voice preference.",
        }
    return out


class Unusable(Exception):
    """A turn asked as a helper that cannot run; ``str`` is safe to show."""


async def assert_usable(organization_id: int, key: str) -> State:
    """The check behind the picker: a helper is used only when the backend
    says it is available, whatever the screen showed."""
    if key == catalogue.AUTOMATIC:
        raise Unusable("Automatic is chosen by sending no helper.")
    if not enabled(organization_id):
        raise Unusable("Helpers are not switched on here.")
    state = await state_of(organization_id, key)
    if state is None:
        raise Unusable("There is no helper by that name.")
    if state.state != AVAILABLE:
        raise Unusable(state.reason or "That helper is not available.")
    return state
