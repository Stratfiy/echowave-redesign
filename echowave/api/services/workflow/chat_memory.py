"""How much of a conversation a bot keeps in mind, and how much is in use.

Every reply in a chat carries the thread before it, and the thread is what
lets "book her for Tuesday" mean the Meera from three messages ago. The
question is how far back. A fixed number of turns treats a one-line "ok"
and a pasted contract alike; a token budget does not, and a budget that
grows with the plan is the memory a bigger account is paying for.

One figure, ``chat_context_tokens`` in ``plan_limits``, read here and
nowhere else. Decibyl's thread and a bot's chat both fill their window from
the newest message backwards until the budget is spent, and the meter in
the composer shows the same arithmetic: what is kept, out of what could be.

Tokens are estimated, not counted. Four characters a token is close enough
across the vendors a chat may run on, and the meter is a gauge, not an
invoice -- the invoice is the usage the vendor reports back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from loguru import logger

from api.db import db_client

#: The estimate. Vendors' tokenisers land between three and five characters
#: a token on English and Hinglish; four is the middle and the convention.
CHARS_PER_TOKEN = 4

#: The cap the caps default to when the plan cannot be read: Free's figure,
#: so a database hiccup narrows memory rather than widening the bill.
FALLBACK_TOKENS = 8_000

#: The most rows ever read to fill a window. The budget is what bounds the
#: window; this bounds the query behind it on a plan whose budget is large
#: and whose messages are short.
MAX_ROWS = 400

LIMIT_KEY = "chat_context_tokens"


@dataclass(frozen=True)
class Budget:
    tokens: int
    plan_code: str
    #: The plan with more, or None when none has.
    raise_to: Optional[str]


@dataclass(frozen=True)
class Usage:
    """The window as the meter shows it."""

    used_tokens: int
    budget_tokens: int
    #: Messages inside the window, and in the thread altogether (of those
    #: read: capped at MAX_ROWS).
    messages_kept: int
    messages_total: int
    plan_code: str
    raise_to: Optional[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "used_tokens": self.used_tokens,
            "budget_tokens": self.budget_tokens,
            "messages_kept": self.messages_kept,
            "messages_total": self.messages_total,
            "plan_code": self.plan_code,
            "raise_to": self.raise_to,
        }


def tokens_of(text: Optional[str]) -> int:
    """The estimate for one piece of text. Never zero for a non-empty one."""
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


async def budget(organization_id: int) -> Budget:
    """The account's memory, from the plan it is on."""
    from api.services.billing import plan_limits

    try:
        async with db_client.async_session() as session:
            limit = await plan_limits.limit_for_organization(
                session, organization_id=organization_id, key=LIMIT_KEY
            )
    except Exception as exc:  # noqa: BLE001 - memory is a feature, not a dependency
        logger.warning(
            "Could not read the chat memory cap for organisation {}: {}",
            organization_id,
            exc,
        )
        return Budget(FALLBACK_TOKENS, "free", None)
    # Unlimited is a row an operator can write; a window still needs an
    # edge, and the biggest rung's figure is what unlimited means in practice.
    tokens = (
        limit.value
        if limit.value is not None
        else max(v for v in _seed_values() if v is not None)
    )
    return Budget(int(tokens), limit.plan_code, limit.raise_to)


def _seed_values() -> Iterable[Optional[int]]:
    from api.services.billing import plan_limits

    return (plan.get(LIMIT_KEY) for plan in plan_limits.SEED.values())


def window(texts: Iterable[Optional[str]], budget_tokens: int) -> tuple[int, int]:
    """How many of ``texts`` (newest first) fit, and the tokens they take.

    Fills from the newest backwards and stops at the first that does not
    fit: a window with a hole in the middle would be a conversation with a
    message missing, which is worse than one that starts later. The newest
    message always counts, even alone over budget -- the question being
    answered is never dropped.
    """
    used = 0
    kept = 0
    for text in texts:
        cost = tokens_of(text)
        if kept and used + cost > budget_tokens:
            break
        used += cost
        kept += 1
    return kept, used


async def usage(
    organization_id: int,
    *,
    workflow_id: Optional[int] = None,
    folder_id: Optional[int] = None,
    assistant: bool = False,
) -> Usage:
    """What the meter shows for one chat: kept, out of the budget.

    The same rows and the same arithmetic the reply uses, so the gauge and
    the memory cannot drift apart.
    """
    from api.enums import AgentEventKind

    plan = await budget(organization_id)
    filters: dict[str, Any] = {"kinds": [AgentEventKind.MESSAGE.value]}
    if assistant:
        filters["assistant_thread"] = True
    elif workflow_id is not None:
        filters["workflow_id"] = workflow_id
    elif folder_id is not None:
        filters["folder_id"] = folder_id
    rows = await db_client.agent_events(
        organization_id=organization_id, limit=MAX_ROWS, **filters
    )
    texts = [message_text(row) for row in rows]
    kept, used = window(texts, plan.tokens)
    return Usage(
        used_tokens=used,
        budget_tokens=plan.tokens,
        messages_kept=kept,
        messages_total=len(texts),
        plan_code=plan.plan_code,
        raise_to=plan.raise_to,
    )


def message_text(row: Any) -> str:
    """The words of a timeline row, as the model would be given them."""
    payload = getattr(row, "payload", None) or {}
    body = payload.get("body") if isinstance(payload, dict) else None
    return str(body or getattr(row, "summary", "") or "")
