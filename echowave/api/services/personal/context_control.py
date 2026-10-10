"""What a conversation uses, and leaving a source out of it (skills-and-
context: "In chat, show a compact Context control -- for example: Personal
· Tamil · 2 files · Calendar. Users can inspect the sources, add or exclude
something").

**Sources.** ``personal`` (the person's own preferences and personal
memory), ``workspace`` (what the workspace has confirmed; not offered in a
personal space, where there is no one else's memory), ``knowledge`` (the
space's uploaded files), ``files`` (files attached in this conversation) and
one ``app:<toolkit>`` per connected app.

**A choice is one person's, for one conversation.** Kept in
``conversation_context_choices`` by (workspace, person, thread). Leaving a
source out changes what that person's turns in that thread read -- the turn
sets the choice once (``for_turn``) and each reading asks ``is_excluded`` --
and nothing else: not a colleague's turns, not another thread, not what the
workspace has. Putting it back is the same switch.

**Never a permission.** Leaving an app out takes its tools out of the turn;
putting it back gives back only what the workspace already connected. No
source here can add access the person or workspace did not already have.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.personal_models import ConversationContextChoiceModel
from api.services import personal

PERSONAL = "personal"
WORKSPACE = "workspace"
KNOWLEDGE = "knowledge"
FILES = "files"
APP_PREFIX = "app:"
FIXED = (PERSONAL, WORKSPACE, KNOWLEDGE, FILES)
ORIGINAL = "original"

_APP = re.compile(r"^app:[a-z0-9_\-]{1,60}$")

#: How an app is called on the chip. Anything not listed is shown under its
#: own name, capitalised -- never dropped.
APP_NAMES = {
    "googlecalendar": "Calendar",
    "google_calendar": "Calendar",
    "outlook": "Outlook",
    "gmail": "Gmail",
    "googledrive": "Drive",
    "googledocs": "Docs",
    "googlesheets": "Sheets",
    "slack": "Slack",
    "notion": "Notion",
    "hubspot": "HubSpot",
    "zoho": "Zoho",
    "whatsapp": "WhatsApp",
}


class InvalidSource(ValueError):
    pass


def app_name(toolkit: str) -> str:
    return APP_NAMES.get(toolkit, toolkit.replace("_", " ").title())


def thread_key(thread_id: str | None) -> str:
    return (thread_id or ORIGINAL)[:40]


_excluded: ContextVar[frozenset[str]] = ContextVar(
    "context_excluded", default=frozenset()
)


@contextmanager
def for_turn(excluded: frozenset[str] | set[str] | None) -> Iterator[None]:
    token = _excluded.set(frozenset(excluded or ()))
    try:
        yield
    finally:
        _excluded.reset(token)


def is_excluded(source: str) -> bool:
    return source in _excluded.get()


def excluded_apps() -> frozenset[str]:
    """The toolkits left out of this turn."""
    return frozenset(
        s[len(APP_PREFIX) :] for s in _excluded.get() if s.startswith(APP_PREFIX)
    )


def keep_app(toolkit: str | None) -> bool:
    """Whether a connected app's tool stays in this turn."""
    return not toolkit or toolkit not in excluded_apps()


async def excluded_for(
    organization_id: int, user_id: int | None, thread_id: str | None
) -> frozenset[str]:
    """What this person left out of this conversation. Empty while the flag
    is off, for nobody, and when nothing was left out. Never raises."""
    if not user_id or not personal.enabled(organization_id):
        return frozenset()
    try:
        async with db_client.async_session() as session:
            row = await session.scalar(
                select(ConversationContextChoiceModel).where(
                    ConversationContextChoiceModel.organization_id == organization_id,
                    ConversationContextChoiceModel.user_id == user_id,
                    ConversationContextChoiceModel.thread_key == thread_key(thread_id),
                )
            )
    except Exception as exc:  # noqa: BLE001 - a turn must not fail on this
        logger.warning("Context choices for {} not read: {}", user_id, exc)
        return frozenset()
    return frozenset(str(s) for s in (row.excluded if row else []) or [])


def _valid(source: str) -> str:
    source = (source or "").strip().lower()
    if source in FIXED or _APP.match(source):
        return source
    raise InvalidSource("That is not one of this conversation's sources.")


async def set_included(
    *,
    organization_id: int,
    user_id: int,
    thread_id: str | None,
    source: str,
    included: bool,
) -> frozenset[str]:
    """Put a source back in, or leave it out, for this person's turns in
    this conversation. Returns what is left out afterwards."""
    source = _valid(source)
    current = set(await excluded_for(organization_id, user_id, thread_id))
    if included:
        current.discard(source)
    else:
        current.add(source)
    now = datetime.now(UTC)
    values = sorted(current)
    async with db_client.async_session() as session:
        await session.execute(
            insert(ConversationContextChoiceModel)
            .values(
                organization_id=organization_id,
                user_id=user_id,
                thread_key=thread_key(thread_id),
                excluded=values,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["organization_id", "user_id", "thread_key"],
                set_={"excluded": values, "updated_at": now},
            )
        )
        await session.commit()
    return frozenset(values)


# --- what the chip and its panel show ------------------------------------------


async def _is_personal_space(organization_id: int) -> tuple[bool, str]:
    try:
        organization = await db_client.get_organization_by_id(organization_id)
    except Exception:  # noqa: BLE001 - a label
        organization = None
    kind = getattr(organization, "kind", None)
    name = getattr(organization, "name", None) or "Workspace"
    return kind == "personal", name


async def _conversation_files(
    organization_id: int, user_id: int, thread_id: str | None
) -> int:
    from api.enums import AgentEventKind

    try:
        if thread_id:
            # Somebody else's conversation is not counted, not even as a
            # number of files.
            author = await db_client.thread_author(
                organization_id=organization_id, thread_id=thread_id
            )
            if author is not None and author != user_id:
                return 0
        rows = await db_client.agent_events(
            organization_id=organization_id,
            assistant_thread=True,
            thread_id=thread_id,
            kinds=[AgentEventKind.MESSAGE.value],
            viewer_id=user_id,
            limit=200,
        )
    except Exception as exc:  # noqa: BLE001 - a count
        logger.warning("Conversation files not counted: {}", exc)
        return 0
    seen: set[str] = set()
    for row in rows:
        for item in (row.payload or {}).get("attachments") or []:
            if isinstance(item, dict):
                key = str(
                    item.get("document_uuid")
                    or item.get("uuid")
                    or item.get("filename")
                    or item.get("name")
                    or ""
                )
                if key:
                    seen.add(key)
    return len(seen)


async def _headline_language(user_id: int, organization_id: int) -> str | None:
    from api.services import member_preferences
    from api.services.personal import capture, preferences

    rows = await preferences._live(user_id, organization_id)
    for topic in (capture.CHAT, capture.CALLS, capture.EMAIL):
        tag = preferences._one(rows, capture.LANGUAGE, topic)
        if tag:
            return capture.language_name(tag)
    try:
        tag = (await member_preferences.get(user_id)).get("language")
    except Exception:  # noqa: BLE001 - a label
        tag = None
    return capture.language_name(tag) if tag and not tag.startswith("en") else None


async def describe(
    *, organization_id: int, user_id: int, thread_id: str | None
) -> dict[str, Any]:
    """The chip's line and the panel's sources, for this person and thread."""
    from api.services.personal import preferences
    from api.services.workflow import connected_tools

    excluded = await excluded_for(organization_id, user_id, thread_id)
    personal_space, space_name = await _is_personal_space(organization_id)
    kept = await preferences.mine(user_id)
    language = await _headline_language(user_id, organization_id)

    sources: list[dict[str, Any]] = [
        {
            "id": PERSONAL,
            "kind": "personal",
            "label": "Personal",
            "detail": (
                f"{len(kept)} preference{'s' if len(kept) != 1 else ''}"
                + (f" · {language}" if language else "")
                + " · only you"
            ),
            "included": PERSONAL not in excluded,
        }
    ]
    if not personal_space:
        try:
            facts = [
                r
                for r in await db_client.organisation_memory(
                    organization_id=organization_id,
                    kind="fact",
                    status="confirmed",
                    user_id=None,
                )
                if getattr(r, "user_id", None) is None
            ]
        except Exception as exc:  # noqa: BLE001 - a count
            logger.warning("Workspace facts not counted: {}", exc)
            facts = []
        sources.append(
            {
                "id": WORKSPACE,
                "kind": "workspace",
                "label": space_name,
                "detail": f"{len(facts)} confirmed fact{'s' if len(facts) != 1 else ''}",
                "included": WORKSPACE not in excluded,
            }
        )
    try:
        documents = await db_client.get_documents_for_organization(
            organization_id, limit=200
        )
    except Exception as exc:  # noqa: BLE001 - a count
        logger.warning("Documents not counted: {}", exc)
        documents = []
    sources.append(
        {
            "id": KNOWLEDGE,
            "kind": "knowledge",
            "label": "Your files" if personal_space else "Company knowledge",
            "detail": f"{len(documents)} file{'s' if len(documents) != 1 else ''}",
            "included": KNOWLEDGE not in excluded,
        }
    )
    attached = await _conversation_files(organization_id, user_id, thread_id)
    sources.append(
        {
            "id": FILES,
            "kind": "files",
            "label": "Files in this conversation",
            "detail": f"{attached} file{'s' if attached != 1 else ''}",
            "included": FILES not in excluded,
            "count": attached,
        }
    )
    apps = await connected_tools.list_for_organization(organization_id)
    toolkits = sorted({t for t in (connected_tools.toolkit_of(a) for a in apps) if t})
    for toolkit in toolkits:
        source = f"{APP_PREFIX}{toolkit}"
        sources.append(
            {
                "id": source,
                "kind": "app",
                "label": app_name(toolkit),
                "detail": "Connected app",
                "included": source not in excluded,
            }
        )

    # The chip: the layers in use, shortest first-glance form.
    parts: list[str] = []
    if PERSONAL not in excluded:
        parts.append("Personal")
        if language:
            parts.append(language)
    if not personal_space and WORKSPACE not in excluded:
        parts.append(space_name)
    if attached and FILES not in excluded:
        parts.append(f"{attached} file{'s' if attached != 1 else ''}")
    in_apps = [app_name(t) for t in toolkits if f"{APP_PREFIX}{t}" not in excluded]
    if in_apps:
        parts.append(in_apps[0] + (f" +{len(in_apps) - 1}" if len(in_apps) > 1 else ""))
    left_out = sum(1 for s in sources if not s["included"])
    return {
        "chip": " · ".join(parts) if parts else "No context",
        "left_out": left_out,
        "space": "personal" if personal_space else "workspace",
        "sources": sources,
    }
