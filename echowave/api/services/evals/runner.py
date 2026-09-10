"""Run one scripted caller against an agent and grade the result.

The caller is a model playing the persona over the text-chat channel —
the same engine, prompts, tools and model as a real call, minus audio —
so a pass means the conversation logic holds, and a rerun costs a text
session rather than a phone minute. The agent's own model plays the
caller and the judge, resolved the way post-call QA resolves it, so an
account that can run the agent can run the eval.

Every step writes to the result row, so a run that dies mid-way reads as
an error with the turns it got through, never as a row stuck on
"running".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.db.models import EvalCaseModel, EvalResultModel
from api.enums import WorkflowRunMode
from api.services.evals import judge as judging
from api.services.gen_ai.json_parser import parse_llm_json
from api.services.pipecat.service_factory import create_llm_service_from_provider
from api.services.quota_service import authorize_workflow_run_start
from api.services.workflow.qa.llm_config import resolve_user_llm_config
from api.services.workflow.text_chat_runner import default_text_chat_checkpoint
from api.services.workflow.text_chat_session_service import (
    append_text_chat_user_message,
    default_text_chat_session_data,
    execute_pending_text_chat_turn,
    initialize_text_chat_session,
)

END = "<END>"


def caller_prompt(case: EvalCaseModel) -> str:
    return (
        "You are a caller on the phone with a business's voice agent. Stay in "
        "character, speak in short natural sentences as a caller would, one "
        "message per turn, in the language the agent uses.\n"
        f"Who you are: {case.persona}\nWhat you want: {case.goal}\n\n"
        f"When the conversation has reached its natural end, reply with exactly {END}."
    )


async def _llm_for(run):
    provider, model, api_key, kwargs = await resolve_user_llm_config(run)
    if not api_key:
        raise RuntimeError("No language model key is configured for this agent.")
    return create_llm_service_from_provider(provider, model, api_key, **kwargs)


async def _say(llm, system: str, messages: list[dict]) -> str:
    context = LLMContext()
    context.set_messages(messages)
    text = await llm.run_inference(context, system_instruction=system)
    return (text or "").strip()


def _last_assistant_text(text_session) -> str | None:
    turns = list((text_session.session_data or {}).get("turns") or [])
    if not turns:
        return None
    message = turns[-1].get("assistant_message") or {}
    return message.get("text")


async def _finish(
    result_id: int,
    *,
    status: str,
    verdict: str,
    transcript: list[dict],
    run_id: int | None,
) -> None:
    async with db_client.async_session() as session:
        row = await session.get(EvalResultModel, result_id)
        if row is None:
            return
        row.status = status
        row.verdict = verdict[:2000]
        row.transcript = transcript
        row.workflow_run_id = run_id
        row.finished_at = datetime.now(UTC)
        await session.commit()


async def run_case(result_id: int) -> None:
    """Drive the case to a verdict. Never raises: every exit writes the row."""
    async with db_client.async_session() as session:
        result = await session.get(EvalResultModel, result_id)
        if result is None:
            return
        case = await session.get(EvalCaseModel, result.case_id)
        if case is None:
            await _finish(
                result_id,
                status="error",
                verdict="The case was deleted.",
                transcript=[],
                run_id=None,
            )
            return
        result.status = "running"
        await session.commit()
        organization_id, workflow_id = case.organization_id, case.workflow_id
        created_by = case.created_by

    transcript: list[dict[str, Any]] = []
    run_id: int | None = None
    try:
        workflow_run = await db_client.create_workflow_run(
            name=f"EVAL-{case.name[:40]}",
            workflow_id=workflow_id,
            mode=WorkflowRunMode.TEXTCHAT.value,
            user_id=created_by,
            initial_context=None,
            use_draft=True,
            organization_id=organization_id,
        )
        run_id = workflow_run.id
        set_current_run_id(run_id)
        quota = await authorize_workflow_run_start(
            workflow_id=workflow_id,
            organization_id=organization_id,
            workflow_run_id=run_id,
        )
        if not quota.has_quota:
            await _finish(
                result_id,
                status="error",
                verdict=quota.error_message or "No credit for this run.",
                transcript=[],
                run_id=run_id,
            )
            return
        await db_client.update_workflow_run(
            run_id,
            annotations={
                "tester": {"source": "eval", "modality": "text"},
                "eval_case_id": case.id,
            },
        )
        text_session = await db_client.ensure_workflow_run_text_session(
            run_id,
            session_data=default_text_chat_session_data(),
            checkpoint=default_text_chat_checkpoint(),
        )
        text_session = await initialize_text_chat_session(
            run_id=run_id, text_session=text_session
        )
        text_session = await execute_pending_text_chat_turn(
            workflow_id=workflow_id, run_id=run_id, text_session=text_session
        )
        opening = _last_assistant_text(text_session)
        if opening:
            transcript.append({"role": "agent", "text": opening})

        run_with_context, _ = await db_client.get_workflow_run_with_context(run_id)
        llm = await _llm_for(run_with_context)
        system = caller_prompt(case)

        for _turn in range(max(1, int(case.max_turns or 6))):
            history = [
                {
                    "role": "assistant" if t["role"] == "caller" else "user",
                    "content": t["text"],
                }
                for t in transcript
            ] or [{"role": "user", "content": "(the line connects)"}]
            line = await _say(llm, system, history)
            if not line or END in line:
                break
            transcript.append({"role": "caller", "text": line})
            text_session = await append_text_chat_user_message(
                run_id=run_id,
                text_session=text_session,
                user_text=line,
                expected_revision=text_session.revision,
            )
            text_session = await execute_pending_text_chat_turn(
                workflow_id=workflow_id, run_id=run_id, text_session=text_session
            )
            reply = _last_assistant_text(text_session)
            if reply:
                transcript.append({"role": "agent", "text": reply})
            if getattr(text_session.workflow_run, "is_completed", False):
                break

        hard = judging.phrase_checks(
            transcript,
            must_say=list(case.must_say or []),
            must_not_say=list(case.must_not_say or []),
        )
        if hard is not None:
            verdict = hard
        else:
            rendered = "\n".join(
                f"{t['role'].upper()}: {t['text']}" for t in transcript
            )
            raw = await _say(
                llm,
                judging.judge_prompt(persona=case.persona, goal=case.goal),
                [{"role": "user", "content": f"## Transcript\n{rendered}"}],
            )
            try:
                verdict = judging.parse_judgement(parse_llm_json(raw))
            except Exception:  # noqa: BLE001 - a judge that cannot be read is a fail
                verdict = judging.Judgement(
                    False, "The judge did not return a verdict."
                )
        await _finish(
            result_id,
            status="passed" if verdict.passed else "failed",
            verdict=verdict.reason
            or ("Handled." if verdict.passed else "Not handled."),
            transcript=transcript,
            run_id=run_id,
        )
    except Exception as exc:  # noqa: BLE001 - written to the row, never raised
        logger.exception("Eval result {} could not run", result_id)
        await _finish(
            result_id,
            status="error",
            verdict=f"Could not run: {exc}"[:500],
            transcript=transcript,
            run_id=run_id,
        )
