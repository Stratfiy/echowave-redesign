"""The office: one thread, named colleagues, the addressee decides.

Design: ``docs/product/coordinator-interaction-model.md`` (KAN-129,
KAN-140). Decibyl is the manager; bots are colleagues with handles;
customers are never in the room. There is no Build mode and no Work mode,
because a mode is a state a person can hold a false belief about. Instead
the thing you address decides what happens:

- A line to nobody is to Decibyl.
- A line that *starts* with ``@handle`` is work for that bot: it is handed
  to the bot's own chat, as it always was.
- A handle anywhere *else* in the line is a **subject**, not an addressee.
  "Decibyl, edit @reception: be shorter" is about @reception, and waking
  @reception with it would have the bot try to carry out an instruction
  meant for its manager.

That last rule is the whole router, and it is the only change to how a line
on Decibyl's thread is read. Everything else here is what Decibyl can *do*
about a subject: describe a template so it can propose a build, read a
bot's steps so it can propose an edit, and offer the two test verbs.
Every one of those ends in a card a person presses, never in an action on
a model's say-so.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import agent_timeline, mentions, self_edit

#: A handle at the very start of the line, allowing "@handle," and "@handle:".
_LEADING = re.compile(r"^\s*@([A-Za-z0-9][\w.-]*)")

#: What a test verb opens. The tester is one panel with two modes; the
#: card says which one, and the page opens it in that mode.
HEAR = "hear"
TRY = "try"
TEST_MODES = (HEAR, TRY)
TEST_TOOL_NAME = "test_bot"
CHECK_TOOL_NAME = "check_bot"
#: Marks an eval result asked for from the thread, so the verdict comes back
#: to the thread as a card rather than only to the evals screen.
CHECK_ORIGIN = "office"

MAX_BRIEF_CHARS = 300
#: Steps shown for a subject bot. One bot's steps at a time; the block is
#: already bounded inside self_edit.
MAX_SUBJECTS_WITH_STEPS = 2


class Addressee(NamedTuple):
    """Who a line on Decibyl's thread is for, and who it is about."""

    #: ``bot`` when the line opens with a handle; ``decibyl`` otherwise.
    to: str
    #: The bot addressed, when ``to == "bot"``.
    leading: mentions.Mention | None
    #: Bots named elsewhere in the line: what Decibyl is being asked about.
    subjects: list[mentions.Mention]
    #: Handles that matched nothing, or more than one bot, for the screen.
    unknown: list[str]
    ambiguous: list[str]


def addressee(text: str, roster: Iterable[Mapping[str, object]]) -> Addressee:
    """Apply the leading-handle rule.

    ``mentions.resolve`` already refuses to guess; this only decides which of
    its answers is the addressee and which are subjects.
    """
    resolution = mentions.resolve(text, roster)
    match = _LEADING.match(text or "")
    leading: mentions.Mention | None = None
    if match:
        wanted = match.group(1).casefold()
        for mention in resolution.mentioned:
            if mention.handle.casefold() == wanted:
                leading = mention
                break
    subjects = [m for m in resolution.mentioned if m != leading]
    return Addressee(
        to="bot" if leading else "decibyl",
        leading=leading,
        subjects=subjects,
        unknown=list(resolution.unknown),
        ambiguous=list(resolution.ambiguous),
    )


# --- what Decibyl can read about a subject ----------------------------------


def templates_block() -> str:
    """Every template, one line each, so Decibyl can propose a build.

    Ids and the answers each needs, because a build card is only as good as
    the answers on it, and the model has to know what to ask before it can
    ask rather than guess.
    """
    from api.services.agent_builder.assemble import required_variables
    from api.services.agent_templates import list_templates

    lines = ["## Templates a new bot can be built from"]
    for template in list_templates():
        needs = required_variables(template)
        asks = ", ".join(
            f"{key} ({template.template_variables.get(key, key)})" for key in needs
        )
        lines.append(
            f"- {template.id}: {template.name} [{template.direction.value}] -- "
            f"{template.summary}" + (f". Needs: {asks}" if asks else "")
        )
    if len(lines) == 1:
        lines.append("(none installed)")
    return "\n".join(lines)


async def subjects_block(
    organization_id: int, subjects: Iterable[mentions.Mention]
) -> str:
    """The steps of each bot the line is about, so an edit names a real step."""
    parts: list[str] = []
    for mention in list(subjects)[:MAX_SUBJECTS_WITH_STEPS]:
        workflow = await db_client.get_workflow(
            mention.workflow_id, organization_id=organization_id
        )
        if workflow is None:
            continue
        steps = self_edit.steps_block(workflow.workflow_definition)
        parts.append(
            f"## @{mention.handle} ({workflow.name}, id {workflow.id})\n{steps}"
        )
    return "\n\n".join(parts)


# --- the test verbs ---------------------------------------------------------


def test_tool_schema() -> dict[str, Any]:
    return {
        "name": TEST_TOOL_NAME,
        "description": (
            "Offer to test a bot, when a person asks to hear, try or test one. "
            "'hear' is a real call in the browser; 'try' is a text chat. Both "
            "are marked TEST and never reach a customer. You will not see the "
            "result in this turn; say the test is ready on the card and end."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "bot": {
                    "type": "string",
                    "description": "The bot, by @handle or name.",
                },
                "how": {
                    "type": "string",
                    "enum": list(TEST_MODES),
                    "description": "'hear' for a call, 'try' for text. Default 'hear'.",
                },
                "brief": {
                    "type": "string",
                    "description": (
                        "What to test, in one line, e.g. 'a patient asking for "
                        "Saturday hours'. Shown on the card."
                    ),
                },
            },
            "required": ["bot"],
        },
    }


def test_url(workflow_id: int, how: str) -> str:
    mode = "call" if how == HEAR else "text"
    return f"/workflow/{workflow_id}?test={mode}"


def _match_bot(wanted: str, roster: list[Any]) -> Any | None:
    key = wanted.strip().lstrip("@").casefold()
    if not key:
        return None
    hits = [
        w
        for w in roster
        if (getattr(w, "handle", None) or "").casefold() == key
        or (getattr(w, "name", None) or "").casefold() == key
    ]
    return hits[0] if len(hits) == 1 else None


async def offer_test(
    *, organization_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Post the test card on Decibyl's thread. Returns what the model is told."""
    wanted = str(arguments.get("bot") or "").strip()
    how = str(arguments.get("how") or HEAR).strip().lower()
    if how not in TEST_MODES:
        how = HEAR
    brief = str(arguments.get("brief") or "").strip()[:MAX_BRIEF_CHARS]
    roster = await db_client.get_all_workflows_for_listing(
        organization_id=organization_id
    )
    bot = _match_bot(wanted, list(roster))
    if bot is None:
        return {
            "status": "not_offered",
            "reason": f"No bot called {wanted!r} here; use its exact @handle.",
        }
    verb = "Hear" if how == HEAR else "Try"
    line = f"{verb} {bot.name}" + (f": {brief}" if brief else "")
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.AGENT.value,
        summary=line,
        payload={
            "body": line,
            "from": "Decibyl",
            "test": {
                "workflow_id": bot.id,
                "bot_name": bot.name,
                "handle": getattr(bot, "handle", None),
                "how": how,
                "brief": brief,
                "url": test_url(bot.id, how),
            },
        },
        in_channel=False,
    )
    return {
        "status": "offered",
        "note": (
            f"A {verb.lower()}-it card for {bot.name} is on the thread. Say it "
            "is ready to press, then end your reply."
        ),
    }


# --- the check verb ---------------------------------------------------------


def check_tool_schema() -> dict[str, Any]:
    return {
        "name": CHECK_TOOL_NAME,
        "description": (
            "Check a bot: a scripted caller plays the scenario over text and "
            "a judge grades the transcript. Use when a person asks to check, "
            "verify or make sure a bot handles something, and offer it before "
            "an edit lands on a live bot. Marked TEST; reaches no customer. "
            "You will not see the verdict in this turn; say the check is "
            "running and the result will appear on this thread, then end."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "bot": {
                    "type": "string",
                    "description": "The bot, by @handle or name.",
                },
                "brief": {
                    "type": "string",
                    "description": (
                        "Who is calling and what they want, in one or two "
                        "lines, e.g. 'a patient asking for Saturday hours who "
                        "then wants to book'."
                    ),
                },
                "must_say": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Phrases the bot must say, if any.",
                },
                "must_not_say": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Phrases the bot must not say, if any.",
                },
            },
            "required": ["bot", "brief"],
        },
    }


async def check_bot(
    *, organization_id: int, arguments: dict[str, Any], user_id: int | None = None
) -> dict[str, Any]:
    """Queue a scripted check and note it on the thread. Returns what the
    model is told. The verdict arrives later, through ``post_check_result``."""
    from api.db.models import EvalCaseModel, EvalResultModel
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    wanted = str(arguments.get("bot") or "").strip()
    brief = " ".join(str(arguments.get("brief") or "").split())[:600]
    if not brief:
        return {"status": "not_checked", "reason": "Say what the caller wants."}
    roster = await db_client.get_all_workflows_for_listing(
        organization_id=organization_id
    )
    bot = _match_bot(wanted, list(roster))
    if bot is None:
        return {
            "status": "not_checked",
            "reason": f"No bot called {wanted!r} here; use its exact @handle.",
        }
    must_say = [
        str(p).strip() for p in (arguments.get("must_say") or []) if str(p).strip()
    ]
    must_not_say = [
        str(p).strip() for p in (arguments.get("must_not_say") or []) if str(p).strip()
    ]
    async with db_client.async_session() as session:
        case = EvalCaseModel(
            organization_id=organization_id,
            workflow_id=bot.id,
            name=brief[:120],
            persona=f"A caller: {brief}",
            goal=brief,
            must_say=must_say[:10],
            must_not_say=must_not_say[:10],
            max_turns=6,
            created_by=user_id,
        )
        session.add(case)
        await session.flush()
        result = EvalResultModel(
            case_id=case.id,
            organization_id=organization_id,
            workflow_id=bot.id,
            status="queued",
            origin=CHECK_ORIGIN,
        )
        session.add(result)
        await session.commit()
        result_id, case_id = result.id, case.id
    try:
        await enqueue_job(FunctionNames.RUN_EVAL_CASE, result_id)
    except Exception as exc:  # noqa: BLE001 - said, not lost
        logger.error("Check {} could not be queued: {}", result_id, exc)
        return {
            "status": "not_checked",
            "reason": "Could not start the check just now.",
        }
    await agent_timeline.record_activity(
        organization_id=organization_id,
        summary=f"Checking {bot.name}: {brief}",
        payload={
            "from": "Decibyl",
            "check": {
                "result_id": result_id,
                "case_id": case_id,
                "workflow_id": bot.id,
            },
        },
        in_channel=False,
    )
    return {
        "status": "checking",
        "note": (
            f"A scripted caller is checking {bot.name} now. The verdict will "
            "appear on this thread as a card. Say so, then end your reply."
        ),
    }


async def post_check_result(result: Any) -> None:
    """The verdict, as a Result card on Decibyl's thread. Called by the eval
    runner when a result it finishes carries the office origin. Never raises."""
    try:
        if getattr(result, "origin", None) != CHECK_ORIGIN:
            return
        organization_id = result.organization_id
        workflow = await db_client.get_workflow(
            result.workflow_id, organization_id=organization_id
        )
        bot_name = getattr(workflow, "name", None) or "the bot"
        handle = getattr(workflow, "handle", None)
        case = None
        async with db_client.async_session() as session:
            from api.db.models import EvalCaseModel

            case = await session.get(EvalCaseModel, result.case_id)
        brief = getattr(case, "goal", None) or getattr(case, "name", None) or ""
        status = str(result.status or "")
        passed = status == "passed"
        headline = (
            f"{bot_name} handled it: {brief}"
            if passed
            else f"{bot_name} did not handle it: {brief}"
            if status == "failed"
            else f"Could not check {bot_name}: {brief}"
        )
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.AGENT.value,
            summary=headline[:500],
            payload={
                "body": headline,
                "from": "Decibyl",
                "check": {
                    "result_id": result.id,
                    "case_id": result.case_id,
                    "workflow_id": result.workflow_id,
                    "bot_name": bot_name,
                    "handle": handle,
                    "brief": brief,
                    "status": status,
                    "passed": passed,
                    "verdict": result.verdict or "",
                    "run_id": result.workflow_run_id,
                    "evals_url": f"/workflow/{result.workflow_id}/evals",
                },
            },
            in_channel=False,
        )
    except Exception as exc:  # noqa: BLE001 - the row is written; the card is a courtesy
        logger.warning(
            "Could not post the check result {}: {}", getattr(result, "id", "?"), exc
        )


# --- the edit verb ----------------------------------------------------------


def edit_tool_schema() -> dict[str, Any]:
    """self_edit's tool, plus the bot it is for: Decibyl edits colleagues,
    not itself."""
    properties = dict(self_edit.tool_properties())
    properties["bot"] = {
        "type": "string",
        "description": "The bot to change, by @handle or name.",
    }
    return {
        "name": self_edit.TOOL_NAME,
        "description": (
            "Propose a change to one bot's behaviour, when a person asks. "
            "Name the bot, the step (from its steps in the context; 'Rules' "
            "for what applies on every step) and the complete new prompt. "
            "Say in one line why. It becomes a draft with a diff card on "
            "this thread; a person publishes it. Say you have proposed it "
            "and end your reply."
        ),
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": ["bot", "step", "new_prompt", "why"],
        },
    }


async def propose_edit(
    *, organization_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    wanted = str(arguments.get("bot") or "").strip()
    roster = await db_client.get_all_workflows_for_listing(
        organization_id=organization_id
    )
    bot = _match_bot(wanted, list(roster))
    if bot is None:
        return {
            "status": "not_proposed",
            "reason": f"No bot called {wanted!r} here; use its exact @handle.",
        }
    result = await self_edit.propose(
        organization_id=organization_id,
        workflow_id=bot.id,
        workflow_run_id=None,
        arguments=arguments,
        on_assistant_thread=True,
    )
    if result.get("status") == "proposed":
        logger.info("Decibyl proposed an edit to workflow {}", bot.id)
    return result


__all__ = [
    "HEAR",
    "TEST_MODES",
    "TEST_TOOL_NAME",
    "TRY",
    "Addressee",
    "addressee",
    "check_bot",
    "check_tool_schema",
    "edit_tool_schema",
    "offer_test",
    "post_check_result",
    "propose_edit",
    "subjects_block",
    "templates_block",
    "test_tool_schema",
    "test_url",
]
