"""Escalation on a live call: the glue between the engine and the policy.

One ``EscalationRuntime`` per call, built by ``run_pipeline`` when
``escalation_v2`` is on for the workspace and the call is on a phone. It

* reads every final transcription (``EscalationWatcher``) and asks the
  evaluator what to do, before the model sees the words;
* offers the model two tools: ``report_escalation_signal`` (what it noticed
  -- an input, never a verdict) and ``escalation_fallback`` (what the caller
  chose once nobody could be reached);
* takes over the agent's transfer tool, so a transfer the model asks for goes
  through the same record, card and ladder as one the policy decides;
* runs the escalation: tell the caller who is joining and why, post the card,
  ring people with spoken updates, bridge on a person, or come back and say so;
* writes the call's escalation outcome when the call ends.

Nothing here runs with the flag off: ``for_run`` returns None and the engine
behaves exactly as before.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Mapping, Optional

from loguru import logger
from pipecat.frames.frames import Frame, LLMMessagesAppendFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import FunctionCallResultProperties
from pipecat.utils.enums import EndTaskReason

from api.db import db_client
from api.enums import AgentEventKind
from api.services import escalation
from api.services.escalation import ReasonCode, fallbacks, ladder, record
from api.services.escalation import card as handoff_card
from api.services.escalation.evaluator import (
    Action,
    Decision,
    EscalationEvaluator,
)
from api.services.escalation.policy import (
    EscalationPolicy,
    Team,
    TransferTarget,
    from_configurations,
    humans_available,
)
from api.utils.phone_masking import last_four

SIGNAL_TOOL = "report_escalation_signal"
FALLBACK_TOOL = "escalation_fallback"
TOOL_NAMES = (SIGNAL_TOOL, FALLBACK_TOOL)

SIGNAL_DESCRIPTION = (
    "Tell the platform something about this call that may mean a person "
    "should take over: the caller asked for a human (explicit_request); they "
    "raised a sensitive topic (policy_topic, with topic); they seem "
    "frustrated (frustration, with level 1-3); the same step keeps failing "
    "(step_failed, with step); you could not understand them (no_match); a "
    "tool failed (tool_error); or you are unsure of a key detail such as an "
    "amount or an account number (low_confidence). You do not decide the "
    "handover -- the platform does. Do exactly what the result says."
)
FALLBACK_DESCRIPTION = (
    "Record what the caller chose after nobody from the team could be "
    "reached: callback (with the number, after reading it back), ticket, "
    "voicemail (with their message), or declined. Only offer what the "
    "platform told you to offer, in that order."
)

REPAIR_INSTRUCTION = (
    "[Note from the platform, not the caller] This conversation is not "
    "going well. Before anything else, repair it once: say briefly what you "
    "have understood so far, ask the caller to confirm or correct it, or "
    "offer them a simple choice. Do not mention a transfer."
)

_HOLD_LINES = (
    "Thanks for holding. I'm still connecting you.",
    "Still trying to reach them. It shouldn't be much longer.",
    "Thank you for waiting. I'm still on it.",
)


def tool_schemas() -> list[dict]:
    from api.services.escalation.policy import TOPICS
    from api.services.workflow.pipecat_engine_custom_tools import (
        get_function_schema,
    )

    return [
        get_function_schema(
            SIGNAL_TOOL,
            SIGNAL_DESCRIPTION,
            properties={
                "kind": {
                    "type": "string",
                    "enum": [
                        "explicit_request",
                        "policy_topic",
                        "frustration",
                        "step_failed",
                        "no_match",
                        "tool_error",
                        "low_confidence",
                    ],
                },
                "topic": {"type": "string", "enum": list(TOPICS)},
                "level": {"type": "integer", "minimum": 1, "maximum": 3},
                "step": {"type": "string"},
                "detail": {"type": "string"},
            },
            required=["kind"],
        ),
        get_function_schema(
            FALLBACK_TOOL,
            FALLBACK_DESCRIPTION,
            properties={
                "choice": {
                    "type": "string",
                    "enum": [
                        fallbacks.CALLBACK,
                        fallbacks.TICKET,
                        fallbacks.VOICEMAIL,
                        fallbacks.DECLINED,
                    ],
                },
                "number": {"type": "string"},
                "number_read_back": {"type": "boolean"},
                "window_minutes": {"type": "integer"},
                "message": {"type": "string"},
            },
            required=["choice"],
        ),
    ]


def _estimate_speech_seconds(text: str) -> float:
    return min(10.0, 0.8 + len((text or "").split()) / 2.6)


class EngineCallerLine:
    """The ladder's ``CallerLine`` on a live call: hold audio, and a spoken
    update in place of it every so often (music alone reads as a dropped
    line)."""

    def __init__(self, runtime: "EscalationRuntime"):
        self._runtime = runtime
        self._stop: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self._spoken = 0

    async def start_hold(self) -> None:
        engine = self._runtime.engine
        if self._task is not None or engine._transport_output is None:
            return
        from api.services.pipecat.audio_playback import play_audio_loop

        sample_rate = (
            engine._audio_config.transport_out_sample_rate
            if engine._audio_config
            else 8000
        )
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            play_audio_loop(
                stop_event=self._stop,
                sample_rate=sample_rate,
                queue_frame=engine._transport_output.queue_frame,
            )
        )

    async def stop_hold(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except Exception:  # noqa: BLE001
                pass
        self._task = None
        self._stop = None

    async def update(self, seconds_waited: int) -> None:
        line = _HOLD_LINES[self._spoken % len(_HOLD_LINES)]
        self._spoken += 1
        await self.stop_hold()
        await self._runtime.say(line, wait=True)
        await self.start_hold()


class EscalationRuntime:
    def __init__(
        self,
        *,
        engine: Any,
        policy: EscalationPolicy,
        organization_id: int,
        workflow_id: int | None,
        workflow_run: Any,
        owner_user_id: int | None = None,
        agent_schedule: Any = None,
        language: str | None = None,
        workspace_number: str | None = None,
        business_name: str | None = None,
    ):
        self.engine = engine
        self.policy = policy
        self.organization_id = organization_id
        self.workflow_id = workflow_id
        self.workflow_run = workflow_run
        self.workflow_run_id = getattr(workflow_run, "id", None)
        self.owner_user_id = owner_user_id
        self.agent_schedule = agent_schedule
        self.language = language
        self.workspace_number = workspace_number
        self.business_name = business_name
        self.evaluator = EscalationEvaluator(
            policy, humans_available=self.humans_available
        )
        self.resuming: dict | None = None
        initial = getattr(workflow_run, "initial_context", None) or {}
        if isinstance(initial.get("escalation_handback"), Mapping):
            self.resuming = dict(initial["escalation_handback"])
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.row: Any = None
        self.card: dict | None = None
        self.result: ladder.LadderResult | None = None
        self.fallback: fallbacks.FallbackLadder | None = None
        self.outcome: dict[str, Any] = {"outcome": "resolved_by_ai"}
        self._started_at: float | None = None
        self._finalised = False
        self._live_writes: set[asyncio.Task] = set()
        self._decision: Decision | None = None

    # --- construction ------------------------------------------------------

    @classmethod
    async def for_run(
        cls,
        *,
        engine: Any,
        run_configs: Mapping[str, Any] | None,
        organization_id: int | None,
        workflow: Any,
        workflow_run: Any,
        is_phone_call: bool,
        language: str | None = None,
    ) -> Optional["EscalationRuntime"]:
        """The runtime for this call, or None when escalation v2 does not
        apply (flag off for the workspace, or not a phone call)."""
        if not organization_id or not escalation.enabled(organization_id):
            return None
        if not is_phone_call:
            return None
        from api.services.workflow import agent_hours

        policy = from_configurations(run_configs)
        workspace_number = None
        try:
            from api.services.voice import appointments

            workspace_number = (await appointments.get_policy(organization_id)).get(
                "escalate_to"
            )
        except Exception as exc:  # noqa: BLE001 - a fallback number is optional
            logger.debug("No workspace escalation number: {}", exc)
        return cls(
            engine=engine,
            policy=policy,
            organization_id=organization_id,
            workflow_id=getattr(workflow, "id", None),
            workflow_run=workflow_run,
            owner_user_id=getattr(workflow, "user_id", None),
            agent_schedule=(run_configs or {}).get(agent_hours.CONFIG_KEY),
            language=language,
            workspace_number=workspace_number,
            business_name=getattr(workflow, "name", None),
        )

    def humans_available(self) -> bool:
        return humans_available(self.policy, self.agent_schedule)

    # --- speaking ------------------------------------------------------------

    @property
    def speaks_through_model(self) -> bool:
        """A fixed English line on a Tamil call is worse than a slightly slower
        one in Tamil: off English, the model says our words in its language."""
        language = (self.language or "en").lower()
        return not language.startswith("en")

    async def say(self, text: str, *, wait: bool = False) -> None:
        task = self.engine.task
        if task is None or not text:
            return
        if self.speaks_through_model:
            await task.queue_frame(
                LLMMessagesAppendFrame(
                    [
                        {
                            "role": "user",
                            "content": (
                                "[Note from the platform, not the caller] Say "
                                "this to the caller, in the language you have "
                                "been speaking with them, and nothing else. Do "
                                f'not call any tool: "{text}"'
                            ),
                        }
                    ],
                    run_llm=True,
                )
            )
        else:
            await task.queue_frame(
                TTSSpeakFrame(text, append_to_context=True, persist_to_logs=True)
            )
        if wait:
            await self._wait_spoken(text)

    async def _wait_spoken(self, text: str) -> None:
        deadline = time.monotonic() + _estimate_speech_seconds(text) + 4.0
        await asyncio.sleep(_estimate_speech_seconds(text))
        while self.engine._bot_is_speaking and time.monotonic() < deadline:
            await asyncio.sleep(0.2)

    async def instruct(self, text: str, *, run_llm: bool) -> None:
        if self.engine.task is None:
            return
        await self.engine.task.queue_frame(
            LLMMessagesAppendFrame(
                [
                    {
                        "role": "user",
                        "content": f"[Note from the platform, not the caller] {text}",
                    }
                ],
                run_llm=run_llm,
            )
        )

    # --- inputs ----------------------------------------------------------------

    def on_user_text(self, text: str) -> list[Frame]:
        """A final transcription, before the model sees it.

        Returns frames for the watcher to push ahead of the transcription (a
        repair note, so the model's very next reply is the repair). A
        transfer mutes the pipeline here, synchronously, so the words that
        asked for a person do not also start a model turn.
        """
        if self.row is not None and self.row.state in record.OPEN:
            self._note_live(text)
            return []
        decision = self.evaluator.observe_text(text)
        return self._act_now(decision)

    def _note_live(self, text: str) -> None:
        """What the caller says while a person is being found reaches the
        card's live transcript, so whoever opened the link sees it."""
        if self.card is None or self.row is None or not (text or "").strip():
            return
        lines = list(self.card.get("live_transcript") or [])
        lines.append(f"Caller: {' '.join(text.split())}")
        self.card["live_transcript"] = lines[-handoff_card.MAX_LIVE_LINES :]
        task = asyncio.create_task(
            db_client.update_escalation(
                self.row.id,
                organization_id=self.organization_id,
                handoff_card=dict(self.card),
            )
        )
        self._live_writes.add(task)
        task.add_done_callback(self._live_writes.discard)

    def on_idle(self) -> None:
        self.evaluator.observe_quiet("no_input")

    def _act_now(self, decision: Decision, *, trigger: str = "auto") -> list[Frame]:
        if decision.action == Action.REPAIR:
            return [
                LLMMessagesAppendFrame(
                    [{"role": "user", "content": REPAIR_INSTRUCTION}], run_llm=False
                )
            ]
        if decision.escalates:
            self.engine.set_mute_pipeline(True)
            self._start(decision, trigger=trigger)
        return []

    def _start(
        self,
        decision: Decision,
        *,
        trigger: str,
        extra: list[TransferTarget] | None = None,
    ) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self.escalate(decision, trigger=trigger, extra=extra)
        )

    # --- tools -----------------------------------------------------------------

    def register(self, llm: Any) -> None:
        llm.register_function(SIGNAL_TOOL, self._signal_handler)
        llm.register_function(FALLBACK_TOOL, self._fallback_handler, timeout_secs=30.0)

    async def _signal_handler(self, params: Any) -> None:
        args = dict(getattr(params, "arguments", None) or {})
        decision = self.evaluator.observe_signal(
            str(args.get("kind") or ""),
            topic=args.get("topic"),
            level=args.get("level"),
            step=args.get("step"),
            detail=str(args.get("detail") or "")[:200],
        )
        if decision.action == Action.REPAIR:
            await params.result_callback(
                {"status": "repair", "say": REPAIR_INSTRUCTION}
            )
            return
        if decision.escalates:
            await params.result_callback(
                {
                    "status": "handing_over",
                    "say": "Say nothing more; the platform is speaking to the caller.",
                },
                properties=FunctionCallResultProperties(run_llm=False),
            )
            self.engine.set_mute_pipeline(True)
            self._start(decision, trigger="auto")
            return
        await params.result_callback(
            {"status": "noted", "say": "Carry on helping the caller."}
        )

    async def handle_transfer_tool(self, tool: Any, params: Any) -> bool:
        """The agent's transfer tool, under the policy. Returns False to let
        the old handler run (a dynamic-destination tool, which this does not
        take over)."""
        config = (getattr(tool, "definition", None) or {}).get("config", {}) or {}
        if config.get("destination_source", "static") == "dynamic":
            return False
        extra: list[TransferTarget] = []
        destination = str(config.get("destination") or "").strip()
        if destination:
            try:
                extra.append(TransferTarget(number=destination))
            except Exception as exc:  # noqa: BLE001 - a template, not a number
                # Said, not swallowed: an overseas number lands here too.
                logger.warning(
                    "Transfer tool destination {} not rung: {}",
                    last_four(destination),
                    exc,
                )
        if self.evaluator.decided is not None or (
            self.row is not None and self.row.state in record.OPEN
        ):
            await params.result_callback(
                {"status": "in_progress", "say": "A handover is already under way."},
                properties=FunctionCallResultProperties(run_llm=False),
            )
            return True
        reason = (
            ReasonCode.EXPLICIT_REQUEST
            if self.evaluator.weak_mentions
            else ReasonCode.POLICY
        )
        detail = (
            "The caller asked for a person"
            if reason == ReasonCode.EXPLICIT_REQUEST
            else "The agent's own transfer rule"
        )
        decision = self.evaluator._escalate(reason, detail, topic="agent_rule")
        await params.result_callback(
            {
                "status": "handing_over",
                "say": "Say nothing more; the platform is speaking to the caller.",
            },
            properties=FunctionCallResultProperties(run_llm=False),
        )
        self.engine.set_mute_pipeline(True)
        self._start(decision, trigger="tool", extra=extra)
        return True

    async def _fallback_handler(self, params: Any) -> None:
        args = dict(getattr(params, "arguments", None) or {})
        result = await self.choose_fallback(
            str(args.get("choice") or ""),
            number=args.get("number"),
            number_read_back=bool(args.get("number_read_back")),
            message=args.get("message"),
            window_minutes=args.get("window_minutes"),
        )
        await params.result_callback(result)

    # --- the escalation ------------------------------------------------------

    def _team(self, decision: Decision) -> Team | None:
        return self.policy.team_for(decision.topic, decision.phrase)

    async def _targets(
        self, extra: list[TransferTarget] | None, team: Team | None = None
    ) -> list[TransferTarget]:
        """The team routed to first, then the general numbers, then the
        transfer tool's own; the workspace number when there is nobody."""
        targets = list(team.numbers) if team is not None else []
        for candidate in [*self.policy.transfer_numbers, *(extra or [])]:
            if candidate.number not in {t.number for t in targets}:
                targets.append(candidate)
        if not targets and self.workspace_number:
            try:
                targets.append(TransferTarget(number=self.workspace_number))
            except Exception:  # noqa: BLE001
                pass
        return targets

    def _messages(self) -> list[dict]:
        context = self.engine.context
        try:
            return list(context.get_messages()) if context is not None else []
        except Exception:  # noqa: BLE001
            return list(getattr(context, "messages", []) or [])

    async def _summarise(self, transcript: str) -> str | None:
        llm = self.engine.inference_llm
        if llm is None:
            return None
        context = LLMContext()
        context.set_messages(
            [{"role": "user", "content": handoff_card.summary_prompt(transcript)}]
        )
        return await llm.run_inference(
            context,
            system_instruction="You write two-sentence call summaries for colleagues.",
        )

    async def _summarise_in(self, transcript: str, language: str) -> str | None:
        """The summary in the language a person is briefed in."""
        llm = self.engine.inference_llm
        if llm is None:
            return None
        context = LLMContext()
        context.set_messages(
            [
                {
                    "role": "user",
                    "content": handoff_card.summary_prompt(transcript, language),
                }
            ]
        )
        return await llm.run_inference(
            context,
            system_instruction="You write two-sentence call summaries for colleagues.",
        )

    def _consent(self) -> dict[str, Any]:
        start = self.engine.workflow.start_node_id
        return {
            "recording_disclosed": bool(
                self.engine.resolve_recording_disclosure(start)
            ),
            "ai_disclosed": bool(self.engine.resolve_ai_disclosure(start)),
            "call_recorded": bool(self.engine._call_recorded),
        }

    async def _open(self, decision: Decision, trigger: str):
        async with self._lock:
            if self.row is not None and self.row.state in record.OPEN:
                return None
            self._sequence += 1
            row, created = await record.open_escalation(
                organization_id=self.organization_id,
                workflow_id=self.workflow_id,
                workflow_run_id=self.workflow_run_id,
                sequence=self._sequence,
                reason=decision.reason or ReasonCode.EXPLICIT_REQUEST,
                detail=decision.detail,
                trigger=trigger,
            )
            if not created:
                logger.info(
                    "Escalation {} already opened; not repeating it",
                    row.escalation_uuid,
                )
                return None
            self.row = row
            return row

    async def _post_card(self, row: Any, card: dict) -> None:
        from api.services.workflow import agent_timeline

        caller = card.get("caller") or {}
        who = caller.get("name") or caller.get("number_masked") or "A caller"
        event_id = await agent_timeline.record(
            organization_id=self.organization_id,
            kind=AgentEventKind.ESCALATED.value,
            summary=f"{who} needs a person: {card.get('reason')}",
            workflow_id=self.workflow_id,
            workflow_run_id=self.workflow_run_id,
            payload={
                "handoff": card,
                "escalation_uuid": row.escalation_uuid,
                "state": row.state,
            },
        )
        await db_client.update_escalation(
            row.id,
            organization_id=self.organization_id,
            handoff_card=card,
            timeline_event_id=event_id,
        )

    async def _refresh(self) -> None:
        if self.row is None:
            return
        from api.services.escalation.actions import sync_card

        fresh = await sync_card(self.row, organization_id=self.organization_id)
        if fresh is not None:
            self.row = fresh

    def _pre_dial_line(
        self, target: TransferTarget | None, team: Team | None = None
    ) -> str:
        if target and target.name:
            who = target.name
        elif team is not None:
            who = f"someone from our {team.name} team"
        else:
            who = "someone from the team"
        return (
            f"I'm going to bring in {who} who can help with this. It usually "
            "takes under a minute, and I'll stay with you while I connect you."
        )

    def _connect_line(self, target: TransferTarget | None) -> str:
        who = target.name if target and target.name else "my colleague"
        return f"Thanks for holding. I'm putting you through to {who} now, and I've passed on what you told me."

    async def escalate(
        self,
        decision: Decision,
        *,
        trigger: str = "auto",
        extra: list[TransferTarget] | None = None,
    ) -> None:
        try:
            await self._escalate(decision, trigger=trigger, extra=extra)
        except Exception as exc:  # noqa: BLE001 - the caller must never be left in silence
            logger.exception("Escalation failed: {}", exc)
            if self.row is not None:
                await record.fail(
                    self.row, "execution_error", organization_id=self.organization_id
                )
                await self._refresh()
            await self._come_back(reached_nobody=False, reason="execution_error")

    async def _escalate(
        self,
        decision: Decision,
        *,
        trigger: str,
        extra: list[TransferTarget] | None,
    ) -> None:
        row = await self._open(decision, trigger)
        if row is None:
            return
        self._decision = decision
        self._started_at = time.monotonic()
        self.outcome = {
            "outcome": "escalated",
            "reason_code": row.reason_code,
            "escalation_id": row.id,
            "caller_turn": decision.turn,
        }
        team = self._team(decision)
        targets = await self._targets(extra, team)
        card_task = asyncio.create_task(
            handoff_card.build(
                escalation_uuid=row.escalation_uuid,
                workflow_id=self.workflow_id,
                workflow_run_id=self.workflow_run_id,
                reason_code=row.reason_code,
                reason_detail=row.reason_detail or "",
                call_context=self.engine._call_context_vars,
                gathered=self.engine._gathered_context,
                messages=self._messages(),
                language=self.language,
                consent=self._consent(),
                summarise=self._summarise,
                team=team.name if team is not None else None,
                briefing_languages=[t.language for t in targets],
                summarise_in=self._summarise_in,
            )
        )

        if decision.action == Action.CALLBACK or not targets:
            self.card = await card_task
            await self._post_card(row, self.card)
            reason = (
                "no_human_available"
                if decision.action == Action.CALLBACK
                else "no_one_to_ring"
            )
            await record.fail(row, reason, organization_id=self.organization_id)
            self.outcome.update(
                transfer_result="callback"
                if decision.action == Action.CALLBACK
                else "failed",
                failure_reason=reason,
            )
            await self._refresh()
            await self._come_back(
                reached_nobody=False,
                outside_hours=decision.action == Action.CALLBACK,
                reason=decision.topic,
            )
            return

        from api.services.telephony.factory import get_telephony_provider_for_run

        provider = await get_telephony_provider_for_run(
            self.workflow_run, self.organization_id
        )
        call_sid = (self.engine._gathered_context or {}).get("call_id") or (
            getattr(self.workflow_run, "gathered_context", None) or {}
        ).get("call_id")
        if (
            not provider.supports_transfers()
            or not provider.validate_config()
            or not call_sid
        ):
            self.card = await card_task
            await self._post_card(row, self.card)
            await record.fail(
                row, "provider_cannot_transfer", organization_id=self.organization_id
            )
            self.outcome.update(
                transfer_result="failed", failure_reason="provider_cannot_transfer"
            )
            await self._refresh()
            await self._come_back(reached_nobody=False, reason=decision.topic)
            return

        await self.say(self._pre_dial_line(targets[0], team), wait=True)
        self.card = await card_task
        await self._post_card(row, self.card)

        from api.services.escalation.dialer import ProviderDialer

        dialer = ProviderDialer(
            provider=provider,
            original_call_sid=str(call_sid),
            workflow_run_id=self.workflow_run_id,
            escalation_uuid=row.escalation_uuid,
        )

        async def claim(expected: int, transfer_id: str, label: str) -> bool:
            return await record.claim_attempt(
                row,
                organization_id=self.organization_id,
                expected_count=expected,
                transfer_id=transfer_id,
                target=label,
            )

        async def on_attempt(attempt: ladder.Attempt) -> None:
            await db_client.set_escalation_attempt_outcome(
                row.id,
                organization_id=self.organization_id,
                transfer_id=attempt.transfer_id,
                outcome=attempt.outcome,
            )

        card = self.card

        def briefing(target: TransferTarget) -> str:
            # Their language when the carrier can speak it; else English.
            spoken = target.language if dialer.speaks(target.language) else "en"
            return handoff_card.spoken_briefing(card, spoken)

        result = await ladder.run(
            targets=targets,
            policy=self.policy,
            dialer=dialer,
            caller=EngineCallerLine(self),
            claim=claim,
            briefing=briefing,
            start_count=row.attempt_count or 0,
            on_attempt=on_attempt,
        )
        self.result = result
        await self._after_ladder(row, result)

    async def _after_ladder(self, row: Any, result: ladder.LadderResult) -> None:
        elapsed_ms = int(
            (time.monotonic() - (self._started_at or time.monotonic())) * 1000
        )
        if result.bridged:
            await record.move(
                row, record.BRIEFING, organization_id=self.organization_id
            )
            await record.move(
                row,
                record.BRIDGED,
                organization_id=self.organization_id,
                time_to_human_ms=elapsed_ms,
                current_transfer_id=result.transfer_id,
            )
            self.outcome.update(transfer_result="bridged", time_to_human_ms=elapsed_ms)
            await self._refresh()
            await self.say(self._connect_line(result.target), wait=True)
            await self._record_outcome()
            await self.engine.end_call_with_reason(
                EndTaskReason.TRANSFER_CALL.value, abort_immediately=False
            )
            return
        reason = result.failure_reason or "no_answer"
        await record.fail(row, reason, organization_id=self.organization_id)
        self.outcome.update(transfer_result="failed", failure_reason=reason)
        await self._refresh()
        # The topic, so a caller in an emergency nobody answered for is told
        # to ring 112.
        topic = self._decision.topic if self._decision is not None else None
        await self._come_back(reached_nobody=True, reason=topic)

    async def _come_back(
        self,
        *,
        reached_nobody: bool,
        outside_hours: bool = False,
        reason: str | None = None,
    ) -> None:
        """Back to the agent: unmute, say honestly what happened, and offer
        the first rung of what is left."""
        self.engine.set_mute_pipeline(False)
        self.engine._queued_speech_mute_state = "idle"
        await self.say(
            fallbacks.explanation(
                reached_nobody=reached_nobody,
                outside_hours=outside_hours,
                reason=reason,
            )
        )
        self.fallback = fallbacks.FallbackLadder(
            fallbacks.offers_for(self.policy, can_message=self._can_message())
        )
        await self.instruct(
            fallbacks.offer_instruction(self.fallback.current()), run_llm=True
        )

    def _can_message(self) -> bool:
        provider = str(getattr(self.workflow_run, "mode", "") or "").lower()
        if self.policy.ticket_channel == "sms":
            return provider in ("plivo", "twilio")
        if self.policy.ticket_channel == "whatsapp":
            return provider == "twilio"
        return False

    # --- after nobody answered -------------------------------------------------

    def _caller_number(self) -> str | None:
        caller = handoff_card.caller_identity(
            self.engine._call_context_vars, self.engine._gathered_context
        )
        return caller.get("number")

    async def choose_fallback(
        self,
        choice: str,
        *,
        number: str | None = None,
        number_read_back: bool = False,
        message: str | None = None,
        window_minutes: int | None = None,
    ) -> dict[str, Any]:
        ladder_ = self.fallback
        if ladder_ is None:
            return {"status": "not_available", "say": "Nothing to record; carry on."}
        current = ladder_.current()
        if choice == fallbacks.DECLINED:
            nxt = ladder_.decline()
            return {"status": "declined", "next": fallbacks.offer_instruction(nxt)}
        if current is None:
            return {"status": "done", "say": fallbacks.offer_instruction(None)}
        if choice != current:
            return {
                "status": "not_yet",
                "say": "Offer this first. " + fallbacks.offer_instruction(current),
            }
        card = self.card or {}
        row = self.row
        if choice == fallbacks.CALLBACK:
            dialable = fallbacks.normalise_callback_number(
                number or self._caller_number()
            )
            if not dialable:
                return {
                    "status": "not_filed",
                    "say": "Ask for the number to ring back.",
                }
            if not number_read_back:
                return {
                    "status": "not_filed",
                    "say": "Read the number back to the caller digit by digit and wait for a yes first.",
                }
            filed = await fallbacks.file_callback(
                organization_id=self.organization_id,
                workflow_id=self.workflow_id,
                workflow_run_id=self.workflow_run_id,
                owner_user_id=self.owner_user_id,
                number=dialable,
                card=card,
                window_minutes=window_minutes,
            )
            ladder_.take(choice)
            await self._set_fallback(choice)
            return {
                "status": "filed",
                "task_id": filed.get("task_id"),
                "say": (
                    "Tell the caller the team will call them back on the number "
                    "they confirmed, and ask if there is anything else."
                ),
            }
        if choice == fallbacks.TICKET:
            reference = fallbacks.ticket_reference(
                getattr(row, "id", None), getattr(row, "escalation_uuid", None)
            )
            sent = await self._send_ticket(reference, number or self._caller_number())
            if not sent:
                nxt = ladder_.decline()
                return {
                    "status": "not_sent",
                    "say": "The message could not be sent. "
                    + fallbacks.offer_instruction(nxt),
                }
            ladder_.take(choice)
            await self._set_fallback(choice)
            return {
                "status": "sent",
                "reference": reference,
                "say": f"Tell the caller a message is on its way with reference {reference}.",
            }
        if choice == fallbacks.VOICEMAIL:
            if not (message or "").strip():
                return {
                    "status": "not_left",
                    "say": "Ask the caller what they would like to say first.",
                }
            await fallbacks.leave_voicemail(
                organization_id=self.organization_id,
                workflow_id=self.workflow_id,
                workflow_run_id=self.workflow_run_id,
                message=str(message),
                card=card,
            )
            ladder_.take(choice)
            await self._set_fallback(choice)
            return {
                "status": "left",
                "say": "Tell the caller their message has been passed to the team.",
            }
        return {"status": "unknown_choice", "say": fallbacks.offer_instruction(current)}

    async def _set_fallback(self, choice: str) -> None:
        self.outcome["fallback"] = choice
        if self.row is not None:
            await db_client.update_escalation(
                self.row.id, organization_id=self.organization_id, fallback=choice
            )
            await self._refresh()

    async def _send_ticket(self, reference: str, to: str | None) -> bool:
        if not to:
            return False
        from api.services.messaging.send import send_message
        from api.services.telephony.factory import get_telephony_provider_for_run

        try:
            provider = await get_telephony_provider_for_run(
                self.workflow_run, self.organization_id
            )
            name = str(getattr(provider, "PROVIDER_NAME", "") or "")
            credentials = {
                "auth_id": getattr(provider, "auth_id", None),
                "account_sid": getattr(provider, "account_sid", None),
                "auth_token": getattr(provider, "auth_token", None),
            }
            senders = list(getattr(provider, "from_numbers", None) or [])
            channel = "whatsapp" if self.policy.ticket_channel == "whatsapp" else name
            result = await send_message(
                provider=channel,
                credentials=credentials,
                to=to,
                from_=senders[0] if senders else "",
                body=fallbacks.ticket_text(reference, self.business_name),
            )
            return bool(getattr(result, "ok", False))
        except Exception as exc:  # noqa: BLE001 - a missing message is a rung skipped
            logger.warning("Ticket message to {} not sent: {}", last_four(str(to)), exc)
            return False

    # --- hand back -------------------------------------------------------------

    async def announce_handback(self) -> bool:
        """On a call handed back by a person: tell the agent what was decided,
        and have it say it is back. Returns whether it did (and so whether the
        ordinary greeting should be skipped)."""
        if not self.resuming:
            return False
        note = str(self.resuming.get("note") or "").strip()
        summary = str(self.resuming.get("summary") or "").strip()
        who = str(self.resuming.get("human") or "my colleague")
        content = (
            f"[A colleague from the team, {who}, has handed this call back to "
            "you; this note is from them, not the caller] "
            f"Outcome: {note or 'no note'}. "
            + (f"What the call was about: {summary} " if summary else "")
            + "Tell the caller you are back, say in one line what was decided, "
            "and help with whatever remains (a follow-up, a reference number to "
            "read back). Do not greet them as if the call had just started."
        )
        if self.engine.task is not None:
            await self.engine.task.queue_frame(
                LLMMessagesAppendFrame(
                    [{"role": "user", "content": content}], run_llm=True
                )
            )
        return True

    # --- the end of the call -----------------------------------------------------

    async def _record_outcome(self) -> None:
        if not self.workflow_run_id:
            return
        try:
            await db_client.record_call_escalation_outcome(
                caller_turn=self.outcome.get("caller_turn"),
                caller_turns=self.evaluator.caller_turns,
                shadow_escalations=list(self.evaluator.shadow_hits) or None,
                organization_id=self.organization_id,
                workflow_run_id=self.workflow_run_id,
                workflow_id=self.workflow_id,
                outcome=self.outcome.get("outcome", "resolved_by_ai"),
                reason_code=self.outcome.get("reason_code"),
                transfer_result=self.outcome.get("transfer_result"),
                failure_reason=self.outcome.get("failure_reason"),
                fallback=self.outcome.get("fallback"),
                time_to_human_ms=self.outcome.get("time_to_human_ms"),
                escalation_id=self.outcome.get("escalation_id"),
            )
        except Exception as exc:  # noqa: BLE001 - metrics never fail a call
            logger.warning("Escalation outcome not recorded: {}", exc)

    async def finalise(self) -> None:
        """The call ended. A caller who hung up on hold gets a callback task;
        the call's outcome is written either way."""
        if self._finalised:
            return
        self._finalised = True
        if self._live_writes:
            await asyncio.gather(*list(self._live_writes), return_exceptions=True)
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except BaseException:  # noqa: BLE001
                pass
        if self.row is not None:
            fresh = await db_client.get_escalation_by_id(
                self.row.id, organization_id=self.organization_id
            )
            if fresh is not None and fresh.state in (record.REQUESTED, record.DIALLING):
                await record.fail(
                    fresh, "caller_hung_up", organization_id=self.organization_id
                )
                self.outcome.update(
                    transfer_result="failed", failure_reason="caller_hung_up"
                )
                number = fallbacks.normalise_callback_number(self._caller_number())
                if number:
                    try:
                        await fallbacks.file_callback(
                            organization_id=self.organization_id,
                            workflow_id=self.workflow_id,
                            workflow_run_id=self.workflow_run_id,
                            owner_user_id=self.owner_user_id,
                            number=number,
                            card={
                                **(self.card or fresh.handoff_card or {}),
                                "summary": ((self.card or {}).get("summary") or "")
                                + " The caller hung up while on hold; the number is "
                                "from caller ID and was not confirmed.",
                            },
                        )
                        self.outcome["fallback"] = fallbacks.CALLBACK
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Hang-up callback not filed: {}", exc)
                await self._refresh()
        await self._record_outcome()


__all__ = [
    "EngineCallerLine",
    "EscalationRuntime",
    "FALLBACK_TOOL",
    "REPAIR_INSTRUCTION",
    "SIGNAL_TOOL",
    "TOOL_NAMES",
    "tool_schemas",
]
