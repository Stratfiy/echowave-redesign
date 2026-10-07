"""Memory opt-in and temporary conversations (screens 16 and 18).

Two promises, kept in the write path rather than on the screen:

* **Memory starts off until chosen** (handoff 24). With ``memory_manager``
  on, a person's turn with Decibyl writes nothing to memory -- not their
  personal memory, not the workspace's, not the graph -- unless they have
  turned "Remember things from my conversations" on. Facts already kept
  stay until they are forgotten.
* **A temporary conversation** saves nothing to memory whatever the switch
  says, and its messages are deleted ``TEMPORARY_CONVERSATION_HOURS`` after
  it starts. It is still processed to answer the person -- the screen says
  so rather than promising zero processing.

The pause is a context variable set once at the top of a Decibyl turn
(``decibyl.ask``), for the same reason ``acting`` is one: the writers sit
several calls below the one place that knows whose turn it is. Background
work started inside the turn inherits it.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import delete, select, update

from api import constants
from api.db import db_client
from api.db.models import AgentEventModel
from api.db.settings_models import TemporaryConversationModel
from api.services import features
from api.services.settings import MEMORY_MANAGER

#: Temporary threads are told apart by their id; the row is the authority.
PREFIX = "tmp-"

_paused: ContextVar[str | None] = ContextVar("memory_paused", default=None)


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(MEMORY_MANAGER, organization_id)


@contextmanager
def paused(reason: str | None) -> Iterator[None]:
    """Everything inside writes no memory when ``reason`` is set."""
    token = _paused.set(reason)
    try:
        yield
    finally:
        _paused.reset(token)


def is_paused() -> bool:
    return _paused.get() is not None


def pause_reason() -> str | None:
    """Why memory is not being written in this turn, for the model to say."""
    return _paused.get()


#: What the model is told when a person asks it to remember something while
#: memory is off, so it says so rather than claiming it remembered.
OFF_REASON = (
    "Memory is off for this person, so nothing from this conversation is "
    "saved. They can turn it on in Settings, Memory."
)
TEMPORARY_REASON = (
    "This is a temporary conversation: nothing from it is saved to memory, "
    "and it is deleted after a while."
)


async def reason_for_turn(
    organization_id: int, author_id: int | None, thread_id: str | None
) -> str | None:
    """Why this turn must write no memory, or None. Never raises: when the
    answer cannot be read, memory is paused -- the safe side of a privacy
    promise."""
    if not enabled(organization_id) or not author_id:
        return None
    try:
        if (
            thread_id
            and thread_id.startswith(PREFIX)
            and await is_temporary(organization_id, thread_id)
        ):
            return TEMPORARY_REASON
        from api.services import member_preferences

        stored = await member_preferences.get(int(author_id))
        if stored.get("memory_enabled") is True:
            return None
        return OFF_REASON
    except Exception as exc:  # noqa: BLE001 - see above
        logger.warning("Could not read memory choice for {}: {}", author_id, exc)
        return OFF_REASON


async def is_temporary(organization_id: int, thread_id: str | None) -> bool:
    if not thread_id or not thread_id.startswith(PREFIX):
        return False
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(TemporaryConversationModel.id).where(
                TemporaryConversationModel.organization_id == organization_id,
                TemporaryConversationModel.thread_id == thread_id,
            )
        )
        return row is not None


def retention_line(hours: int | None = None) -> str:
    hours = hours or constants.TEMPORARY_CONVERSATION_HOURS
    return (
        "Nothing from it is saved to memory, and its messages are deleted "
        f"{hours} hours after it starts. Decibyl still reads what you write "
        "to answer you, and anything you approve in it still happens."
    )


async def start(*, organization_id: int, user_id: int) -> dict[str, Any]:
    """A new temporary conversation for this person in this workspace."""
    thread_id = PREFIX + secrets.token_hex(16)
    now = datetime.now(UTC)
    expires = now + timedelta(hours=constants.TEMPORARY_CONVERSATION_HOURS)
    async with db_client.async_session() as session:
        session.add(
            TemporaryConversationModel(
                organization_id=organization_id,
                user_id=user_id,
                thread_id=thread_id,
                created_at=now,
                expires_at=expires,
            )
        )
        await session.commit()
    return {
        "thread_id": thread_id,
        "href": f"/overview?thread={thread_id}",
        "expires_at": expires.isoformat(),
        "retention": retention_line(),
    }


async def describe(
    *, organization_id: int, user_id: int, thread_id: str
) -> dict[str, Any] | None:
    """The temporary conversation behind ``thread_id``, if it is this
    person's. Somebody else's is None, the way a wrong tenant is."""
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(TemporaryConversationModel).where(
                TemporaryConversationModel.organization_id == organization_id,
                TemporaryConversationModel.thread_id == thread_id,
                TemporaryConversationModel.user_id == user_id,
            )
        )
    if row is None:
        return None
    return {
        "thread_id": row.thread_id,
        "expires_at": row.expires_at.isoformat(),
        "purged": row.purged_at is not None,
        "retention": retention_line(),
    }


async def purge_expired(now: datetime | None = None) -> int:
    """Delete the messages of every temporary conversation past its time.
    Returns how many conversations were purged. Runs from the ARQ worker."""
    now = now or datetime.now(UTC)
    purged = 0
    async with db_client.async_session() as session:
        due = list(
            (
                await session.execute(
                    select(TemporaryConversationModel).where(
                        TemporaryConversationModel.expires_at <= now,
                        TemporaryConversationModel.purged_at.is_(None),
                    )
                )
            ).scalars()
        )
        for row in due:
            await session.execute(
                delete(AgentEventModel).where(
                    AgentEventModel.organization_id == row.organization_id,
                    AgentEventModel.thread_id == row.thread_id,
                )
            )
            await session.execute(
                update(TemporaryConversationModel)
                .where(TemporaryConversationModel.id == row.id)
                .values(purged_at=now)
            )
            purged += 1
        await session.commit()
    if purged:
        logger.info("Purged {} temporary conversations", purged)
    return purged
