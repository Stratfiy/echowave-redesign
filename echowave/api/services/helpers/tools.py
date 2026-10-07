"""Decibyl's tools from launch stream `agents`, each behind its own flag.

* ``save_report`` (research_reports) -- runs now; saving is not a send.
* ``trading_interests`` (trading_summaries) -- a read.
* ``track_commitment`` (follow_up_ledger) -- a card; confirming tracks it.
* ``who_owes_me`` (follow_up_ledger) -- a read.
* ``follow_up_commitment`` (follow_up_ledger) -- a ``run_tool`` card on a
  connected app's send, linked to the commitment.
* ``create_tracker`` (describe_builder) -- a card; ``add_to_tracker`` runs
  now; ``read_tracker`` is a read.

Every tool here has a rule in ``rules`` and is offered only while its flag
is on (``schemas``), the same contract as Decibyl's own
(``test_decibyl_knows_what_it_has``).
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.enums import AgentEventKind
from api.services.helpers import (
    catalogue,
    commitments,
    guard,
    interests,
    reports,
    sharing,
    trackers,
)

C = catalogue

#: Tool name -> the flag that offers it.
FLAGS: dict[str, str] = {
    C.SAVE_REPORT: reports.FLAG,
    C.TRADING_INTERESTS: interests.FLAG,
    C.TRACK_COMMITMENT: commitments.FLAG,
    C.WHO_OWES_ME: commitments.FLAG,
    C.FOLLOW_UP_COMMITMENT: commitments.FLAG,
    C.CREATE_TRACKER: trackers.FLAG,
    C.ADD_TO_TRACKER: trackers.FLAG,
    C.READ_TRACKER: trackers.FLAG,
}
NAMES = frozenset(FLAGS)
#: Reads keep Decibyl's tools open for the next step.
READS = frozenset({C.TRADING_INTERESTS, C.WHO_OWES_ME, C.READ_TRACKER})


def enabled_names(organization_id: int | None) -> set[str]:
    from api.services import features

    return {
        name for name, flag in FLAGS.items() if features.is_on(flag, organization_id)
    }


_REPORT_ITEM = {
    "type": "object",
    "properties": {
        "statement": {"type": "string"},
        "basis": {"type": "string", "enum": ["source", "inference"]},
        "sources": {"type": "array", "items": {"type": "integer"}},
        "as_of": {"type": "string", "description": "Date the fact is from, if dated."},
    },
    "required": ["statement", "basis"],
}

_COMMITMENT_PROPS = {
    "direction": {"type": "string", "enum": ["owed_to_me", "i_owe"]},
    "counterparty": {"type": "string", "description": "Who it is with."},
    "contact": {"type": "string", "description": "Their email or number, if known."},
    "description": {"type": "string", "description": "What was promised."},
    "amount": {"type": "string", "description": "Amount, e.g. 4800."},
    "currency": {"type": "string", "description": "Three letters; INR if unsaid."},
    "due_on": {"type": "string", "description": "YYYY-MM-DD, if a date was given."},
    "why": {"type": "string"},
}


def _schemas() -> dict[str, dict[str, Any]]:
    return {
        C.SAVE_REPORT: {
            "name": C.SAVE_REPORT,
            "description": (
                "Save a research report to the person, with its sources. Runs "
                "now. Each finding is marked source (cite source numbers) or "
                "inference; keep conflicts and unreadable sources."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "question": {"type": "string"},
                    "summary": {"type": "string"},
                    "findings": {"type": "array", "items": _REPORT_ITEM},
                    "conflicts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "statement": {"type": "string"},
                                "sources": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                },
                            },
                        },
                    },
                    "inaccessible": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                        },
                    },
                    "sources": {
                        "type": "array",
                        "description": "Numbered 1, 2, ... in this order.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string"},
                                "title": {"type": "string"},
                                "accessed": {"type": "string"},
                            },
                            "required": ["url"],
                        },
                    },
                },
                "required": ["title", "findings", "sources"],
            },
        },
        C.TRADING_INTERESTS: {
            "name": C.TRADING_INTERESTS,
            "description": (
                "What the person follows (tickers, sectors, topics) for a "
                "trading summary. Runs now."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        C.TRACK_COMMITMENT: {
            "name": C.TRACK_COMMITMENT,
            "description": (
                "Propose tracking a commitment (money or work owed either way). "
                "A card: nothing is tracked until the person confirms."
            ),
            "parameters": {
                "type": "object",
                "properties": _COMMITMENT_PROPS,
                "required": ["direction", "counterparty", "description"],
            },
        },
        C.WHO_OWES_ME: {
            "name": C.WHO_OWES_ME,
            "description": "Open commitments owed to the person, with totals. Runs now.",
            "parameters": {"type": "object", "properties": {}},
        },
        C.FOLLOW_UP_COMMITMENT: {
            "name": C.FOLLOW_UP_COMMITMENT,
            "description": (
                "Propose a follow-up message for a tracked commitment through a "
                "connected app's send tool (its app_ name and arguments). A "
                "card: nothing is sent until the person confirms."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "commitment_id": {"type": "integer"},
                    "tool": {"type": "string", "description": "The app_ tool name."},
                    "arguments": {"type": "object"},
                    "why": {"type": "string"},
                },
                "required": ["commitment_id", "tool", "arguments"],
            },
        },
        C.CREATE_TRACKER: {
            "name": C.CREATE_TRACKER,
            "description": (
                "Propose a tracker: a named list with columns. A card: it is "
                "created when the person confirms."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "columns": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "type": {
                                    "type": "string",
                                    "enum": list(trackers.TYPES),
                                },
                            },
                            "required": ["name"],
                        },
                    },
                    "why": {"type": "string"},
                },
                "required": ["name", "columns"],
            },
        },
        C.ADD_TO_TRACKER: {
            "name": C.ADD_TO_TRACKER,
            "description": "Add one row to a tracker, by its name. Runs now.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tracker": {"type": "string"},
                    "values": {"type": "object"},
                },
                "required": ["tracker", "values"],
            },
        },
        C.READ_TRACKER: {
            "name": C.READ_TRACKER,
            "description": "The latest rows of a tracker, by its name. Runs now.",
            "parameters": {
                "type": "object",
                "properties": {"tracker": {"type": "string"}},
                "required": ["tracker"],
            },
        },
    }


def schemas(organization_id: int | None) -> list[dict[str, Any]]:
    on = enabled_names(organization_id)
    return [s for name, s in _schemas().items() if name in on]


_RULES = {
    C.SAVE_REPORT: (
        "- save_report: keep a research report for the person, with numbered "
        "sources; each finding is source (cite numbers) or inference. Runs now; "
        "say it is saved.\n"
    ),
    C.TRADING_INTERESTS: (
        "- trading_interests: what the person follows. A trading summary is "
        "information only: moves, news and dates with sources; never buy, "
        "sell, hold, targets or what to do.\n"
    ),
    C.TRACK_COMMITMENT: (
        "- track_commitment: propose tracking a promise of money or work; a "
        "card the person confirms.\n"
    ),
    C.WHO_OWES_ME: "- who_owes_me: the tracked commitments owed to the person.\n",
    C.FOLLOW_UP_COMMITMENT: (
        "- follow_up_commitment: propose the follow-up message for a tracked "
        "commitment through a connected app's send tool; a card.\n"
    ),
    C.CREATE_TRACKER: (
        "- create_tracker: propose a tracker (a list with columns) the person "
        "described; a card.\n"
    ),
    C.ADD_TO_TRACKER: "- add_to_tracker: add a row to a tracker by name; runs now.\n",
    C.READ_TRACKER: "- read_tracker: read a tracker's latest rows; runs now.\n",
}


def rules(organization_id: int | None) -> str:
    on = enabled_names(organization_id)
    return "".join(rule for name, rule in _RULES.items() if name in on)


async def _record_activity(
    organization_id: int, summary: str, payload: dict[str, Any]
) -> None:
    from api.services.workflow import agent_timeline

    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.ACTIVITY.value,
        summary=summary[:500],
        payload=payload,
        in_channel=False,
    )


async def run(
    name: str,
    *,
    organization_id: int,
    arguments: dict[str, Any],
    author_id: int | None,
    thread_id: str | None,
    helper: str | None,
    request: str = "",
) -> dict[str, Any]:
    """Dispatch one call. Never raises: the thread must keep answering."""
    if name not in enabled_names(organization_id):
        return {"status": "unavailable", "reason": "That is not switched on here."}
    try:
        return await _run(
            name,
            organization_id=organization_id,
            arguments=arguments,
            author_id=author_id,
            thread_id=thread_id,
            helper=helper,
            request=request,
        )
    except sharing.Invalid as exc:
        return {"status": "not_proposed", "reason": str(exc)}
    except interests.Invalid as exc:
        return {"status": "not_proposed", "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("Helper tool {} failed: {}", name, exc)
        return {"status": "error", "error": "That did not work just now."}


async def _run(
    name: str,
    *,
    organization_id: int,
    arguments: dict[str, Any],
    author_id: int | None,
    thread_id: str | None,
    helper: str | None,
    request: str,
) -> dict[str, Any]:
    from api.services.workflow import actions

    needs_person = {
        C.SAVE_REPORT,
        C.TRADING_INTERESTS,
        C.WHO_OWES_ME,
        C.FOLLOW_UP_COMMITMENT,
        C.ADD_TO_TRACKER,
        C.READ_TRACKER,
        C.TRACK_COMMITMENT,
        C.CREATE_TRACKER,
    }
    if name in needs_person and not author_id:
        # A routine or a channel line with nobody signed in: these belong to
        # a person, so there is nobody to keep them for.
        return {
            "status": "unavailable",
            "reason": "This needs the person signed in; it cannot run from here.",
        }

    if name == C.SAVE_REPORT:
        kind = reports.RESEARCH
        if interests.enabled(organization_id) and interests.is_trading_turn(request):
            kind = reports.TRADING_SUMMARY
        draft = reports.clean(arguments, kind=kind)
        row = await reports.save(
            draft,
            organization_id=organization_id,
            user_id=author_id,
            thread_id=thread_id,
        )
        await _record_activity(
            organization_id,
            f"Saved report: {row.title}",
            {
                "saved_report": {
                    "uuid": row.uuid,
                    "title": row.title,
                    "kind": row.kind,
                    "content_hash": row.content_hash,
                }
            },
        )
        return {
            "status": "success",
            "note": f"Saved '{row.title}' to the person's reports. Say so.",
            "report": row.uuid,
        }

    if name == C.TRADING_INTERESTS:
        return await interests.for_tool(author_id)

    if name == C.TRACK_COMMITMENT:
        fields = commitments.clean(arguments)
        return await actions.propose(
            organization_id=organization_id,
            workflow_id=None,
            workflow_run_id=None,
            arguments={
                "action": actions.TRACK_COMMITMENT,
                **fields.as_args(),
                "why": str(arguments.get("why") or ""),
            },
            in_channel=False,
        )

    if name == C.WHO_OWES_ME:
        found = await commitments.who_owes_me(
            organization_id=organization_id, user_id=author_id
        )
        return {"status": "success", **found}

    if name == C.FOLLOW_UP_COMMITMENT:
        return await _follow_up(organization_id, arguments, author_id, request)

    if name == C.CREATE_TRACKER:
        spec = trackers.clean_spec(arguments)
        return await actions.propose(
            organization_id=organization_id,
            workflow_id=None,
            workflow_run_id=None,
            arguments={
                "action": actions.CREATE_TRACKER,
                **spec,
                "why": str(arguments.get("why") or ""),
            },
            in_channel=False,
        )

    if name in (C.ADD_TO_TRACKER, C.READ_TRACKER):
        tracker = await trackers.find_visible(
            organization_id=organization_id,
            user_id=author_id,
            name=str(arguments.get("tracker") or ""),
        )
        if tracker is None:
            have = await trackers.list_visible(
                organization_id=organization_id, user_id=author_id
            )
            return {
                "status": "not_found",
                "reason": "No tracker by that name.",
                "trackers": [t.name for t in have],
            }
        if name == C.READ_TRACKER:
            return {
                "status": "success",
                "tracker": trackers.describe(tracker, user_id=author_id),
                "rows": await trackers.entries(tracker),
            }
        values = trackers.clean_values(tracker, arguments.get("values"))
        await trackers.add_entry(tracker, values, user_id=author_id)
        return {"status": "success", "note": f"Added a row to {tracker.name}."}

    return {"status": "unavailable", "reason": "no such tool"}


async def _follow_up(
    organization_id: int,
    arguments: dict[str, Any],
    author_id: int,
    request: str,
) -> dict[str, Any]:
    from api.services.workflow import actions, connected_tools, draft_requests

    try:
        commitment_id = int(arguments.get("commitment_id"))
    except (TypeError, ValueError):
        return {"status": "not_proposed", "reason": "Say which commitment, by id."}
    row = await commitments.get_visible(
        organization_id=organization_id, user_id=author_id, commitment_id=commitment_id
    )
    if row is None:
        return {"status": "not_proposed", "reason": "No such commitment here."}
    if row.status != commitments.OPEN:
        return {"status": "not_proposed", "reason": f"That one is {row.status}."}
    current = await commitments.follow_up_state(row)
    if current and current["delivery"] in ("awaiting_approval", "scheduled", "sending"):
        return {
            "status": "already_proposed",
            "note": "A follow-up for this is already waiting. Say so; do not propose another.",
        }
    available = connected_tools.by_function_name(
        await connected_tools.list_for_organization(organization_id)
    )
    tool = available.get(str(arguments.get("tool") or ""))
    if tool is None:
        return {
            "status": "not_proposed",
            "reason": "That app is not connected here; offer it with offer_connector.",
        }
    if connected_tools.is_read(tool):
        return {"status": "not_proposed", "reason": "That tool reads; use a send."}
    refused = draft_requests.refusal(
        text=request, tool=tool, tools=list(available.values())
    )
    if refused is not None:
        return refused
    result = await actions.propose(
        organization_id=organization_id,
        workflow_id=None,
        workflow_run_id=None,
        arguments={
            "action": actions.RUN_TOOL,
            "tool_uuid": tool.tool_uuid,
            "arguments": dict(arguments.get("arguments") or {}),
            "commitment_id": row.id,
            "why": str(arguments.get("why") or f"Follow up with {row.counterparty}"),
        },
        in_channel=False,
    )
    if result.get("event_id"):
        await commitments.link_follow_up(
            organization_id=organization_id,
            commitment_id=row.id,
            card_id=int(result["event_id"]),
        )
    return result


def finish_reply(
    body: str, *, helper: str | None, request: str, organization_id: int
) -> str:
    """A Research reply that is a trading summary leaves as information only."""
    if (
        helper == C.RESEARCH
        and interests.enabled(organization_id)
        and interests.is_trading_turn(request)
    ):
        return guard.finish(body)
    return body
