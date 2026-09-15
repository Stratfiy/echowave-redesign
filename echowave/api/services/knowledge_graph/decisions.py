"""The decision journal (Family B, B2).

"We went with Sharma for the printing, Patel was slower." A conversation
that contains a decision -- chose X over Y, agreed to Z, declined W --
leaves a decision in the record: what was decided, what was chosen, what
it was chosen over, the reason stated, who was involved, when. Recall
(B1) answers "why did we pick that supplier" from it.

Inferred, like anything read out of a conversation: a decision arrives
``learned`` and is labelled inferred until the person confirms it (a
correction, or "yes, that's right" on the thread, both through
correct_memory). Only lines that look like a decision are sent to the
model, so the cost is one small call on the lines that carry one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.services.knowledge_graph import feed
from api.services.knowledge_graph.teach import subject_key

SUBJECT_DECISION = "decision"
MAX_TEXT_CHARS = 6_000

#: A line with none of these carries no decision worth a model call.
CUES = re.compile(
    r"\b(decid\w*|chose|choose|chosen|picked|went with|going with|agreed|"
    r"declin\w*|reject\w*|finali[sz]\w*|settled on|instead of|over the|"
    r"rather than|opted|selected|will go with|let'?s go with|not going with)\b",
    re.IGNORECASE,
)

SYSTEM = (
    "Read the text and list the decisions it contains, if any. A decision "
    "is a choice made: chose X over Y, agreed to Z, declined W, decided to "
    'do something. Return JSON only: {"decisions": [{"what": the matter '
    'decided in a few words, "chosen": what was chosen, "over": [what '
    'it was chosen over, may be empty], "reason": the reason stated or '
    '"", "people": [names involved, may be empty]}]}. Only decisions '
    "actually made in the text, never ones merely discussed. If there are "
    'none: {"decisions": []}.'
)


@dataclass
class Decision:
    what: str
    chosen: str
    over: list[str] = field(default_factory=list)
    reason: str = ""
    people: list[str] = field(default_factory=list)

    def facts(self, *, at: datetime, source: str) -> dict[str, str]:
        out = {"chose": self.chosen, "on": at.date().isoformat(), "source": source}
        if self.over:
            out["over"] = ", ".join(self.over)
        if self.reason:
            out["because"] = self.reason
        if self.people:
            out["who"] = ", ".join(self.people)
        return out

    def line(self) -> str:
        text = f"Decided {self.what}: {self.chosen}"
        if self.over:
            text += f" over {', '.join(self.over)}"
        if self.reason:
            text += f", because {self.reason}"
        if self.people:
            text += f" (with {', '.join(self.people)})"
        return text


def looks_like_decision(text: str) -> bool:
    return bool(CUES.search(text or ""))


def clean(raw: Any) -> list[Decision]:
    out: list[Decision] = []
    items = (raw or {}).get("decisions") if isinstance(raw, dict) else None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        what = str(item.get("what") or "").strip()[:120]
        chosen = str(item.get("chosen") or "").strip()[:200]
        if not what or not chosen:
            continue
        over = [
            str(o).strip()[:100] for o in (item.get("over") or []) if str(o).strip()
        ]
        people = [
            str(p).strip()[:60] for p in (item.get("people") or []) if str(p).strip()
        ]
        out.append(
            Decision(
                what=what,
                chosen=chosen,
                over=over[:5],
                reason=str(item.get("reason") or "").strip()[:300],
                people=people[:6],
            )
        )
    return out[:5]


async def extract(organization_id: int, text: str) -> list[Decision]:
    """The decisions in ``text``, from the account's own model. Empty on
    any failure, and empty without a model call when there is no cue."""
    from api.services.agent_builder import client, settings
    from api.services.gen_ai.json_parser import parse_llm_json

    text = (text or "").strip()[:MAX_TEXT_CHARS]
    if not text or not looks_like_decision(text):
        return []
    try:
        async with db_client.async_session() as session:
            model = await settings.resolve_model(session)
        conversation = client.Conversation()
        conversation.add_user(text)
        reply = await client.complete(
            provider=model.provider,
            model=model.model,
            api_key=model.api_key,
            system=SYSTEM,
            conversation=conversation,
            tools=[],
        )
        return clean(parse_llm_json(reply.text or ""))
    except Exception as exc:  # noqa: BLE001 - a decision missed is not a failure
        logger.warning("Could not read decisions for org {}: {}", organization_id, exc)
        return []


async def note(
    organization_id: int,
    text: str,
    *,
    at: datetime | None = None,
    source: str = "thread",
) -> int:
    """Read the decisions in ``text`` into the record (inferred) and the
    graph. Never raises. Returns how many were noted."""
    at = at or datetime.now(UTC)
    try:
        decisions = await extract(organization_id, text)
    except Exception:  # noqa: BLE001
        return 0
    noted = 0
    for decision in decisions:
        try:
            await db_client.remember_organisation_facts(
                organization_id=organization_id,
                facts=decision.facts(at=at, source=source),
                status="learned",
                subject_type=SUBJECT_DECISION,
                subject_key=subject_key(decision.what),
            )
            noted += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not note a decision for org {}: {}", organization_id, exc
            )
            continue
        await feed.remember_decision(
            organization_id=organization_id, line=decision.line(), at=at
        )
    return noted


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if t not in STOP
    }


STOP = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "what",
    "why",
    "did",
    "was",
    "were",
    "our",
    "you",
    "your",
    "about",
    "from",
    "have",
    "has",
    "had",
    "who",
    "which",
    "when",
    "how",
    "we",
    "choose",
    "chose",
    "pick",
    "picked",
    "decide",
    "decided",
    "decision",
    "over",
    "because",
}


async def recall_decisions(
    organization_id: int, query: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    """Decisions in the record that share a word with the question, as
    recall facts: inferred until confirmed."""
    words = _tokens(query)
    if not words:
        return []
    try:
        rows = await db_client.subject_facts(
            organization_id=organization_id, subject_type=SUBJECT_DECISION, limit=300
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Recall could not read decisions for org {}: {}", organization_id, exc
        )
        return []
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        if getattr(row, "status", "") == "rejected":
            continue
        entry = by_key.setdefault(
            row.subject_key, {"fields": {}, "status": "confirmed", "when": None}
        )
        entry["fields"][row.key] = row.value
        if getattr(row, "status", "") != "confirmed":
            entry["status"] = "inferred"
        entry["when"] = (
            entry["when"]
            or getattr(row, "confirmed_at", None)
            or getattr(row, "last_seen_at", None)
        )
    out: list[dict[str, Any]] = []
    for key, entry in by_key.items():
        haystack = " ".join([key, *entry["fields"].values()])
        if not (words & _tokens(haystack)):
            continue
        f = entry["fields"]
        text = f"Decided {key}: {f.get('chose', '?')}"
        if f.get("over"):
            text += f" over {f['over']}"
        if f.get("because"):
            text += f", because {f['because']}"
        if f.get("who"):
            text += f" (with {f['who']})"
        when = entry["when"]
        out.append(
            {
                "fact": text,
                "status": entry["status"],
                "from": f.get("on"),
                "until": None,
                "recorded": when.date().isoformat() if when else f.get("on"),
            }
        )
    return out[:limit]


__all__ = [
    "Decision",
    "SUBJECT_DECISION",
    "clean",
    "extract",
    "looks_like_decision",
    "note",
    "recall_decisions",
]
