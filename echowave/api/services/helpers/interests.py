"""What one person follows, for trading summaries by interest.

Personal like their preferences (``member_preferences``): one row per
person, whatever workspace they are in, never readable by a teammate. Saves
name the revision they read; an older one is a conflict that returns what
is stored, so two tabs cannot silently overwrite each other.

A summary is a Research turn with these interests in the question
(``summary_request``); the reply and any saved report pass through
``guard`` -- information only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update

from api.db import db_client
from api.db.agents_models import ResearchInterestsModel
from api.services import features
from api.services.helpers import guard

FLAG = "trading_summaries"
KINDS = ("ticker", "sector", "topic")
MAX_INTERESTS = 25


class Invalid(ValueError):
    pass


class Conflict(Exception):
    def __init__(self, stored: "Interests"):
        super().__init__("These interests changed since you opened them.")
        self.stored = stored


@dataclass(frozen=True)
class Interests:
    items: list[dict[str, str]]
    revision: int

    def as_dict(self) -> dict[str, Any]:
        return {"interests": self.items, "revision": self.revision}


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


def clean(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        raise Invalid("Send a list of interests.")
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise Invalid("Each interest has a label and a kind.")
        label = re.sub(r"\s+", " ", str(item.get("label") or "")).strip()[:60]
        kind = str(item.get("kind") or "topic").strip().lower()
        if not label:
            continue
        if kind not in KINDS:
            raise Invalid("An interest is a ticker, a sector or a topic.")
        if kind == "ticker":
            label = label.upper()
        if (kind, label.lower()) in seen:
            continue
        seen.add((kind, label.lower()))
        out.append({"label": label, "kind": kind})
    if len(out) > MAX_INTERESTS:
        raise Invalid(f"Follow up to {MAX_INTERESTS} at a time.")
    return out


async def get(user_id: int) -> Interests:
    async with db_client.async_session() as session:
        row = await session.get(ResearchInterestsModel, user_id)
    if row is None:
        return Interests([], 0)
    return Interests(list(row.interests or []), int(row.revision))


async def save(user_id: int, raw: Any, *, revision: int) -> Interests:
    items = clean(raw)
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = await session.get(ResearchInterestsModel, user_id)
        if row is None:
            if revision != 0:
                raise Conflict(Interests([], 0))
            session.add(
                ResearchInterestsModel(
                    user_id=user_id, interests=items, revision=1, updated_at=now
                )
            )
            try:
                await session.commit()
            except Exception:  # a second first save won the insert
                await session.rollback()
                raise Conflict(await get(user_id)) from None
            return Interests(items, 1)
        result = await session.execute(
            update(ResearchInterestsModel)
            .where(
                ResearchInterestsModel.user_id == user_id,
                ResearchInterestsModel.revision == revision,
            )
            .values(interests=items, revision=revision + 1, updated_at=now)
        )
        await session.commit()
        if result.rowcount != 1:
            raise Conflict(await get(user_id))
    return Interests(items, revision + 1)


def summary_request(items: list[dict[str, str]]) -> str:
    """The line a summary asks Research, built from the person's list."""
    if not items:
        raise Invalid("Add something to follow first.")
    named = ", ".join(f"{i['label']} ({i['kind']})" for i in items)
    return (
        "Give me a trading summary for what I follow: "
        f"{named}. Information only: recent moves, news and scheduled events, "
        "each with its source and date. No buy, sell or hold views."
    )


TRADING_WORDS = re.compile(
    r"\b(trading summary|market summary|stocks?|shares?|nifty|sensex|ticker|portfolio)\b",
    re.IGNORECASE,
)


def is_trading_turn(text: str) -> bool:
    return bool(TRADING_WORDS.search(text or ""))


async def for_tool(user_id: int | None) -> dict[str, Any]:
    if not user_id:
        return {"status": "unavailable", "reason": "Nobody is signed in on this turn."}
    found = await get(user_id)
    return {
        "status": "success",
        "interests": found.items,
        "rule": guard.NOTICE,
        "note": (
            "Nothing followed yet: ask what they want to follow."
            if not found.items
            else "Summarise these, information only."
        ),
    }
