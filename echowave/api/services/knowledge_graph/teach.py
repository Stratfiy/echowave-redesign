"""Teach it back (Family B, B5).

"No, Arun is from college, not work." A correction on the thread becomes
a confirmed fact in the record -- the account's own Postgres row, keyed
to the subject -- and a dated correction episode in the graph, so the
edge it contradicts is invalidated there too. From then on the confirmed
fact outranks anything inferred on the same relation: recall lists the
record's facts about a subject first, and a graph edge the correction
closed carries an end date and is left out of a present-tense answer.

The person is shown what changed, in one line.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.knowledge_graph import feed
from api.services.workflow import agent_timeline

TOOL_NAME = "correct_memory"
KINDS = ("person", "company", "thing", "self")
MAX_KEY = 128
MAX_VALUE = 500


def subject_key(subject: str) -> str:
    """ "Arun Mehta" and "arun mehta" are one person: lowercase, single
    spaces, letters digits and a few marks."""
    text = re.sub(r"\s+", " ", str(subject or "").strip().lower())
    text = re.sub(r"[^\w .@+'-]", "", text)
    return text[:255]


def changed_line(subject: str, key: str, value: str, was: str | None) -> str:
    arrow = f"{was} → {value}" if was else value
    return f"{subject} · {key}: {arrow} (confirmed)"


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "The person corrected something memory had wrong or half-right "
            '("no, Arun is from college, not work", "the plumber\'s number '
            'is …", "we chose Sharma for the printing, not Patel"). '
            "Records the correction as confirmed, outranking anything "
            "inferred on the same point, permanently. Runs now; say what "
            "changed in one line."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "Who or what it is about.",
                },
                "subject_kind": {"type": "string", "enum": list(KINDS)},
                "key": {
                    "type": "string",
                    "description": "The point corrected: relationship, phone, supplier for printing …",
                },
                "value": {"type": "string", "description": "What is true."},
                "was": {"type": "string", "description": "What memory had, if said."},
            },
            "required": ["subject", "key", "value"],
        },
    }


async def correct(
    organization_id: int, arguments: dict[str, Any], *, ref_id: str
) -> dict[str, Any]:
    subject = str(arguments.get("subject") or "").strip()[:120]
    key = str(arguments.get("key") or "").strip().lower()[:MAX_KEY]
    value = str(arguments.get("value") or "").strip()[:MAX_VALUE]
    was = (str(arguments.get("was") or "").strip()[:MAX_VALUE]) or None
    kind = str(arguments.get("subject_kind") or "person").strip().lower()
    if kind not in KINDS:
        kind = "person"
    if not subject or not key or not value:
        return {
            "status": "error",
            "error": "Say who or what, which point, and what is true.",
        }

    try:
        await db_client.remember_organisation_facts(
            organization_id=organization_id,
            facts={key: value},
            status="confirmed",
            subject_type=kind,
            subject_key=subject_key(subject),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not record a correction for org {}: {}", organization_id, exc
        )
        return {"status": "error", "error": "Could not save that just now."}

    line = changed_line(subject, key, value, was)
    # The graph hears the correction as a dated episode of its own, so the
    # edge it contradicts is closed there too. No graph, nothing lost: the
    # record is what recall reads first.
    await feed.remember_correction(
        organization_id=organization_id,
        subject=subject,
        key=key,
        value=value,
        was=was,
        at=datetime.now(UTC),
    )
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.AGENT_ACTED.value,
        actor=AgentEventActor.AGENT.value,
        summary=f"Corrected: {line}",
        payload={"body": f"Corrected: {line}", "from": "Decibyl", "ref": ref_id},
        in_channel=False,
    )
    return {"status": "success", "changed": line}


__all__ = [
    "KINDS",
    "TOOL_NAME",
    "changed_line",
    "correct",
    "subject_key",
    "tool_schema",
]
