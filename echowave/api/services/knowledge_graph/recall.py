"""Asking the memory graph a question from the Home thread (Family B, B1).

"What did Ravi say about the payment last month?" "Who did we choose for
the printing, and why?" The graph holds what calls, the thread and channel
documents implied, with time; :func:`api.services.knowledge_graph.client.
search_facts` answers within one organisation's partition. This module is
the tool Decibyl calls to ask it, and the three rules the tool holds in
code rather than in the prompt:

- **Confirm before believe.** Every fact comes back labelled ``inferred``
  or ``confirmed``; only the account's own record (Postgres) confers
  ``confirmed``. The prompt tells Decibyl to say inferred facts as what
  was said, never as fact.
- **Billed at the knowledge-answer rate.** A read that reached the graph
  costs the same as an answer from the company's documents (2 credits),
  once per tool call, whatever it found. A deployment with no graph
  answers "unavailable" and costs nothing.
- **Nobody is gated.** The tool lives on the Home thread only, needs no
  bot, no number and no business profile.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from loguru import logger

from api.services.billing import events as billing_events
from api.services.knowledge_graph.client import Fact, search_facts

TOOL_NAME = "recall"

#: Facts a single answer may carry. The thread is a conversation, not a
#: report; more than this and the person asks a narrower question.
MAX_FACTS = 12


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Ask the business's memory what was said or done, by whom and "
            "when: on calls the bots took, on this thread, and in documents "
            "that arrived on WhatsApp or email. Use it for questions about a "
            'person, a supplier, a promise, a decision or a reason ("what did '
            'Ravi say about the payment", "why did we pick that vendor"). '
            "Runs now. Each fact comes back labelled inferred (something was "
            "said that implies it) or confirmed (the business confirmed it)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What to look for, in plain words.",
                },
                "about": {
                    "type": "string",
                    "description": "A person, company or thing to narrow to.",
                },
                "since": {
                    "type": "string",
                    "description": "Only from this date on (YYYY-MM-DD).",
                },
                "until": {
                    "type": "string",
                    "description": "Only up to this date (YYYY-MM-DD).",
                },
            },
            "required": ["question"],
        },
    }


def parse_day(value: Any, *, end: bool = False) -> datetime | None:
    """A YYYY-MM-DD (or ISO datetime) into an aware datetime; None for
    anything else. ``end`` makes a bare date mean the end of that day."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            start = datetime(day.year, day.month, day.day, tzinfo=UTC)
            return (
                start + timedelta(days=1) - timedelta(microseconds=1) if end else start
            )
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


async def for_thread(
    organization_id: int, arguments: dict[str, Any], *, ref_id: str
) -> dict[str, Any]:
    """The tool call. Never raises: the thread must keep answering."""
    question = str(arguments.get("question") or "").strip()[:300]
    about = str(arguments.get("about") or "").strip()[:100]
    if not question and not about:
        return {"status": "error", "error": "Say what to look for."}
    query = f"{about}: {question}".strip(": ") if about else question
    since = parse_day(arguments.get("since"))
    until = parse_day(arguments.get("until"), end=True)

    try:
        facts = await search_facts(
            organization_id, query, limit=MAX_FACTS, since=since, until=until
        )
    except Exception as exc:  # noqa: BLE001 - search_facts should not raise; belt and braces
        logger.warning("Recall failed for org {}: {}", organization_id, exc)
        facts = None
    if facts is None:
        return {
            "status": "unavailable",
            "reason": (
                "Memory is not switched on for this workspace, so there is "
                "nothing to recall from calls or messages yet."
            ),
        }

    # A read that reached the graph is a knowledge answer, found or not.
    await billing_events.charge_in_own_session(
        organization_id=organization_id,
        event=billing_events.KNOWLEDGE_ANSWER,
        ref_id=ref_id,
        note="recall from memory (Decibyl)",
    )

    # What the person confirmed about the subject outranks anything the
    # graph inferred (B5): the record's facts come first, as confirmed.
    facts = [*await _record_facts(organization_id, about), *facts]

    if not facts:
        return {
            "status": "success",
            "facts": [],
            "note": (
                "Nothing in memory matched. Memory holds what was said on "
                "calls, on this thread and in documents that arrived on "
                "WhatsApp or email."
            ),
        }
    return {
        "status": "success",
        "facts": [f.as_dict() for f in facts],
        "note": (
            "Say an inferred fact as something that was said or implied, "
            "with when; state a confirmed fact as fact."
        ),
    }


async def _record_facts(organization_id: int, about: str) -> list[Fact]:
    """Confirmed facts in the account's own record about ``about``."""
    if not about:
        return []
    from api.db import db_client
    from api.services.knowledge_graph.teach import subject_key

    try:
        rows = await db_client.subject_facts(
            organization_id=organization_id,
            subject_key=subject_key(about),
            status="confirmed",
            limit=20,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Recall could not read the record for org {}: {}", organization_id, exc
        )
        return []
    return [
        Fact(
            uuid=f"record:{row.id}",
            fact=f"{about} — {row.key}: {row.value}",
            valid_at=getattr(row, "confirmed_at", None),
            invalid_at=None,
            created_at=getattr(row, "confirmed_at", None),
            episodes=(),
            status="confirmed",
        )
        for row in rows
    ]


__all__ = ["TOOL_NAME", "for_thread", "parse_day", "tool_schema"]
