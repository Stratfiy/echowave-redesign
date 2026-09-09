import asyncio
from datetime import UTC, datetime

from loguru import logger

from api.db import db_client
from api.enums import PostHogEvent, WorkflowRunState
from api.services.billing.turn_metrics import record_turn_metrics
from api.services.campaign.circuit_breaker import circuit_breaker
from api.services.integrations import IntegrationRuntimeSession
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_playback import play_audio_loop
from api.services.pipecat.in_memory_buffers import (
    InMemoryLogsBuffer,
    InMemoryRecordingBuffers,
)
from api.services.pipecat.pipeline_metrics_aggregator import PipelineMetricsAggregator
from api.services.pipecat.tracing_config import get_trace_url
from api.services.pipecat.transcript_log_coordinator import TranscriptLogCoordinator
from api.services.posthog_client import capture_event
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow_run_artifacts import upload_workflow_run_artifacts
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames
from pipecat.frames.frames import (
    Frame,
)
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.utils.enums import EndTaskReason


#: How long a call may run with a broken component and a silent agent before we
#: end it ourselves.
#:
#: Long enough that a slow first token, a long pre-call fetch or a ringer does
#: not trip it, short enough that nobody sits through a minute of nothing. The
#: watchdog is only ever armed *after* a provider has already reported an
#: error, so this timer never runs on a healthy call.
MUTE_AGENT_GRACE_SECONDS = 20


async def _capture_call_event(
    workflow_run_id: int,
    user_provider_id: str | None,
    event: str,
    extra_properties: dict | None = None,
) -> None:
    """Look up workflow_run for call metadata and fire a PostHog event.
    Meant to be run via asyncio.create_task() so it never blocks the pipeline."""
    try:
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
        properties = {
            "workflow_run_id": workflow_run_id,
            "workflow_id": workflow_run.workflow_id if workflow_run else None,
            "call_type": workflow_run.mode if workflow_run else None,
            "call_direction": (
                (workflow_run.initial_context or {}).get("direction", "outbound")
                if workflow_run
                else None
            ),
        }
        if extra_properties:
            properties.update(extra_properties)
        capture_event(
            distinct_id=user_provider_id,
            event=event,
            properties=properties,
        )
    except Exception:
        logger.exception(f"Background PostHog capture failed for '{event}'")


def register_event_handlers(
    task: PipelineWorker,
    transport,
    workflow_run_id: int,
    engine: PipecatEngine,
    audio_buffer: AudioBufferProcessor,
    in_memory_logs_buffer: InMemoryLogsBuffer,
    transcript_log_coordinator: TranscriptLogCoordinator,
    pipeline_metrics_aggregator: PipelineMetricsAggregator,
    audio_config=AudioConfig,
    pre_call_fetch_task: asyncio.Task | None = None,
    user_provider_id: str | None = None,
    integration_runtime_sessions: list[IntegrationRuntimeSession] | None = None,
    include_transcript_end_timestamps: bool = False,
):
    """Register all event handlers for transport and task events.

    Returns:
        In-memory recording buffers for use by other handlers.
    """
    # Initialize in-memory buffers with proper audio configuration
    sample_rate = audio_config.pipeline_sample_rate if audio_config else 16000
    num_channels = 1  # Pipeline audio is always mono

    logger.debug(
        f"Initializing audio buffer for workflow {workflow_run_id} "
        f"with sample_rate={sample_rate}Hz, channels={num_channels}"
    )

    in_memory_audio_buffers = InMemoryRecordingBuffers(
        workflow_run_id=workflow_run_id,
        sample_rate=sample_rate,
        num_channels=num_channels,
    )
    # Track both events to ensure the initial response is only triggered after both occur
    ready_state = {
        "pipeline_started": False,
        "client_connected": False,
        "initial_response_triggered": False,
    }

    # The mute-agent watchdog. Armed by the first non-fatal pipeline error and
    # disarmed when the pipeline finishes; see _arm_mute_agent_watchdog.
    mute_watchdog: dict[str, asyncio.Task | None] = {"task": None}

    def agent_has_spoken() -> bool:
        """Did the caller hear anything from the agent?

        Three independent signals, OR-ed deliberately. A false "spoke" only
        costs us a watchdog that does not fire — the status quo. A false
        "silent" would hang up on a working call, which is the failure mode
        this whole handler was rewritten to stop, so every signal that could
        say yes gets a vote:

        * bot audio actually buffered for the recording;
        * TTS characters billed by the metrics aggregator, which is populated
          from metrics frames and so survives a transport that never emits
          per-track audio;
        * a bot text event on the timeline.
        """
        if not in_memory_audio_buffers.bot.is_empty:
            return True
        if any(pipeline_metrics_aggregator.get_tts_usage_metrics().values()):
            return True
        try:
            return in_memory_logs_buffer.contains_bot_speech()
        except Exception:  # noqa: BLE001 - a diagnostic must not end a call
            return True

    async def _mute_agent_watchdog() -> None:
        """End a call whose agent can no longer speak.

        A provider that fails to *connect* is not the recoverable grumble the
        non-fatal path was written for — it is dead for the rest of the call.
        pipecat reports both as `fatal=False` (``push_error`` defaults to it),
        so we cannot tell them apart from the frame. We can tell them apart by
        their consequence: after a connect failure the agent never says
        anything, and the caller sits in silence until they give up.

        That silence used to run to whatever the caller's patience was, get
        marked a completed run, and get billed. So: once a provider has
        errored, give the agent a grace period to prove it can still talk. If
        it cannot, end the call and say why.
        """
        try:
            await asyncio.sleep(MUTE_AGENT_GRACE_SECONDS)
            if agent_has_spoken():
                return

            logger.error(
                "Workflow run {}: a provider errored and the agent has said "
                "nothing after {}s. Ending the call rather than leaving the "
                "caller in silence.",
                workflow_run_id,
                MUTE_AGENT_GRACE_SECONDS,
            )
            # Promote the recorded error to fatal. It ended the call, so the
            # run detail screen should grade it as a failure rather than as a
            # service that grumbled and recovered.
            try:
                await _mark_pipeline_error_fatal(workflow_run_id)
            except Exception as exc:  # noqa: BLE001
                logger.error("Could not promote the pipeline error: {}", exc)

            asyncio.create_task(
                _capture_call_event(
                    workflow_run_id,
                    user_provider_id,
                    PostHogEvent.CALL_FAILED,
                    extra_properties={"error_reason": "mute_agent"},
                )
            )
            await engine.end_call_with_reason(
                EndTaskReason.PIPELINE_ERROR.value, abort_immediately=True
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let the watchdog kill the process
            logger.error("Mute-agent watchdog failed for run {}: {}", workflow_run_id, exc)

    def _arm_mute_agent_watchdog() -> None:
        """Start the watchdog once, and only while the agent is still silent."""
        if mute_watchdog["task"] is not None:
            return
        if agent_has_spoken():
            # The agent is talking, so whatever the provider complained about,
            # it recovered. Nothing to watch.
            return
        mute_watchdog["task"] = asyncio.create_task(_mute_agent_watchdog())

    def _disarm_mute_agent_watchdog() -> None:
        task = mute_watchdog["task"]
        if task is not None and not task.done():
            task.cancel()
        mute_watchdog["task"] = None

    async def maybe_trigger_initial_response():
        """Start the conversation after both pipeline_started and client_connected events.

        If a pre-call fetch is in progress, plays a ringer while waiting for the
        response, then merges the result into the call context before proceeding.
        """
        if (
            ready_state["pipeline_started"]
            and ready_state["client_connected"]
            and not ready_state["initial_response_triggered"]
        ):
            ready_state["initial_response_triggered"] = True

            asyncio.create_task(
                _capture_call_event(
                    workflow_run_id, user_provider_id, PostHogEvent.CALL_STARTED
                )
            )

            # Wait for pre-call fetch if in progress, playing ringer meanwhile
            if pre_call_fetch_task is not None:
                if not pre_call_fetch_task.done():
                    logger.info(
                        "Pre-call fetch still in progress, playing ringer while waiting"
                    )
                    stop_ringer = asyncio.Event()
                    sample_rate = audio_config.pipeline_sample_rate or 16000
                    ringer_task = asyncio.create_task(
                        play_audio_loop(
                            stop_event=stop_ringer,
                            sample_rate=sample_rate,
                            queue_frame=transport.output().queue_frame,
                        )
                    )
                    try:
                        fetch_result = await pre_call_fetch_task
                    finally:
                        stop_ringer.set()
                        await ringer_task
                else:
                    fetch_result = pre_call_fetch_task.result()

                if fetch_result:
                    engine._call_context_vars.update(fetch_result)
                    try:
                        await db_client.update_workflow_run(
                            workflow_run_id,
                            initial_context={**engine._call_context_vars},
                        )
                    except Exception as e:
                        logger.error(f"Failed to persist pre-call fetch context: {e}")
                    logger.info(
                        f"Pre-call fetch complete, merged keys: "
                        f"{list(fetch_result.keys())}"
                    )

            # Set the start node now (after pre-call fetch data is merged)
            # so that render_template() has the complete _call_context_vars.
            await engine.set_node(engine.workflow.start_node_id)
            await engine.queue_node_opening(
                node_id=engine.workflow.start_node_id,
                previous_node_id=None,
                generate_if_no_greeting=True,
            )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _participant):
        logger.debug("In on_client_connected callback handler")
        await audio_buffer.start_recording()
        ready_state["client_connected"] = True
        await maybe_trigger_initial_response()

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _participant):
        call_disposed = engine.is_call_disposed()

        logger.debug(
            f"In on_client_disconnected callback handler. Call disposed: {call_disposed}"
        )

        # Stop recordings
        await audio_buffer.stop_recording()

        await engine.end_call_with_reason(
            EndTaskReason.USER_HANGUP.value, abort_immediately=True
        )

    @task.event_handler("on_pipeline_started")
    async def on_pipeline_started(_task: PipelineWorker, _frame: Frame):
        logger.debug("In on_pipeline_started callback handler")
        ready_state["pipeline_started"] = True
        await maybe_trigger_initial_response()

    @task.event_handler("on_pipeline_error")
    async def on_pipeline_error(_task: PipelineWorker, frame: Frame):
        """Every ErrorFrame arrives here. Only a fatal one ends the call.

        Pipecat fires this handler for *every* ErrorFrame and then decides for
        itself: ``fatal`` cancels the pipeline, anything else is logged as a
        warning and the bot keeps talking. ``push_error`` defaults to
        ``fatal=False``, so most of what reaches us is a service reporting a
        problem it expects to survive.

        This used to hang up on all of them. A provider answering one message
        with a complaint — the shape of a keepalive, a flush it did not like —
        produced an ErrorFrame that pipecat would have shrugged off, and we
        ended the customer's call on it, at 0s, with the disposition
        ``pipeline_error``. The louder the provider, the shorter the call.

        So fatality is now the provider's decision, as it already was
        everywhere else in the pipeline. A non-fatal error is still recorded on
        the run and still shown on the timeline, because "the call worked but
        TTS complained twice" is worth seeing — it is just not a reason to cut
        somebody off mid-sentence.
        """
        fatal = bool(getattr(frame, "fatal", False))
        if fatal:
            logger.error(
                f"Fatal pipeline error for workflow run {workflow_run_id}: {frame}"
            )
        else:
            logger.warning(
                f"Non-fatal pipeline error for workflow run {workflow_run_id} "
                f"(the call continues): {frame}"
            )

        try:
            # Persist what actually went wrong. Until this existed the run
            # recorded the bare string "pipeline_error" and the cause lived only
            # in a log line, so a failed call was undiagnosable the moment the
            # logs rotated — which is exactly when someone comes asking.
            await _record_pipeline_error(workflow_run_id, frame)
        except Exception as exc:  # noqa: BLE001 - never let reporting kill the call
            logger.error("Could not record the pipeline error detail: {}", exc)

        if not fatal:
            # Not a failed call *yet*: no circuit-breaker strike, no
            # CALL_FAILED, and above all no immediate hangup. Counting these
            # would trip a campaign's breaker on calls that completed and were
            # charged for.
            #
            # But "the provider says it is fine" is not the same as "the caller
            # can hear us". A connect failure arrives here looking exactly like
            # a recovered grumble, and leaves the agent mute for the rest of
            # the call. So arm the watchdog: if the agent still has not spoken
            # a grace period from now, that call is over.
            _arm_mute_agent_watchdog()
            return

        try:
            workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
            if workflow_run and workflow_run.campaign_id:
                await circuit_breaker.record_and_evaluate(
                    campaign_id=workflow_run.campaign_id,
                    is_failure=True,
                    workflow_run_id=workflow_run_id,
                    reason="pipeline_error",
                )
            asyncio.create_task(
                _capture_call_event(
                    workflow_run_id,
                    user_provider_id,
                    PostHogEvent.CALL_FAILED,
                    extra_properties={"error_reason": "pipeline_error"},
                )
            )
        except Exception as e:
            logger.error(f"Error recording circuit breaker failure: {e}", exc_info=True)

        await engine.end_call_with_reason(
            EndTaskReason.PIPELINE_ERROR.value, abort_immediately=True
        )

    @task.event_handler("on_pipeline_finished")
    async def on_pipeline_finished(
        task: PipelineWorker,
        _frame: Frame,
    ):
        logger.debug("In on_pipeline_finished callback handler")

        # The call is over one way or another; nothing left to watch.
        _disarm_mute_agent_watchdog()

        # Turn and feedback observers run on independent queues. Drain them
        # before finalizing immutable transcripts and taking the DB snapshot.
        await task.wait_for_observers()
        await transcript_log_coordinator.flush()

        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)

        # Stop recordings
        await audio_buffer.stop_recording()

        gathered_context = await engine.get_gathered_context()

        # Add trace URL if available (must be done before conversation tracing ends)
        if task.turn_trace_observer:
            trace_id = task.turn_trace_observer.get_trace_id()
            if trace_id:
                trace_url = get_trace_url(trace_id)
                if trace_url:
                    gathered_context["trace_url"] = trace_url
                    logger.debug(f"Added trace URL to gathered_context: {trace_url}")

        # also consider existing gathered context in workflow_run
        gathered_context = {**workflow_run.gathered_context, **gathered_context}

        # Set user_speech call tag
        call_tags = gathered_context.get("call_tags", [])

        try:
            has_user_speech = in_memory_logs_buffer.contains_user_speech()
        except Exception:
            has_user_speech = False

        if has_user_speech and "user_speech" not in call_tags:
            call_tags.append("user_speech")

        # Append any keys from gathered_context that start with 'tag_' to call_tags
        for key in gathered_context:
            if key.startswith("tag_") and key not in call_tags:
                call_tags.append(gathered_context[key])

        gathered_context["call_tags"] = call_tags

        # Store disposition code in workflow for dynamic filtering
        disposition_code = gathered_context.get("mapped_call_disposition")
        if disposition_code and workflow_run:
            try:
                await db_client.add_call_disposition_code(
                    workflow_run.workflow_id, disposition_code
                )
            except Exception as e:
                logger.error(
                    f"Error storing disposition code in workflow: {e}",
                    exc_info=True,
                )

        # Clean up engine resources (including voicemail detector)
        integration_logs: dict[str, object] = {}
        for runtime_session in integration_runtime_sessions or []:
            try:
                session_logs = await runtime_session.on_call_finished(
                    gathered_context=gathered_context
                )
                if session_logs:
                    integration_logs.update(session_logs)
            except Exception as e:
                logger.error(
                    f"Error finalizing integration runtime session '{runtime_session.name}': {e}",
                    exc_info=True,
                )

        await engine.cleanup()

        # ------------------------------------------------------------------
        # Close Smart-Turn WebSocket if the transport's analyzer supports it
        # ------------------------------------------------------------------
        try:
            turn_analyzer = None

            # Most transports store their params (with turn_analyzer) directly.
            if hasattr(transport, "_params") and transport._params:
                turn_analyzer = getattr(transport._params, "turn_analyzer", None)

            # Fallback: some transports expose params through input() instance.
            if turn_analyzer is None and hasattr(transport, "input"):
                try:
                    input_transport = transport.input()
                    if input_transport and hasattr(input_transport, "_params"):
                        turn_analyzer = getattr(
                            input_transport._params, "turn_analyzer", None
                        )
                except Exception:
                    pass

            if turn_analyzer and hasattr(turn_analyzer, "close"):
                await turn_analyzer.close()
                logger.debug("Closed turn analyzer websocket")
        except Exception as exc:
            logger.warning(f"Failed to close Smart-Turn analyzer gracefully: {exc}")

        usage_info = pipeline_metrics_aggregator.get_all_usage_metrics_serialized()

        # Per-turn latency, the raw rows the Latency screen computes its
        # percentiles over. Best-effort: instrumentation must never cost the
        # call its recording, transcript or usage.
        await record_turn_metrics(
            workflow_run_id, pipeline_metrics_aggregator.get_turn_metrics()
        )

        logger.debug(
            f"Usage metrics: {usage_info}, Gathered context: {gathered_context}"
        )

        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            usage_info=usage_info,
            gathered_context=gathered_context,
            is_completed=True,
            state=WorkflowRunState.COMPLETED.value,
            # WebRTC and text-chat runs have no telephony callback, so this is
            # the only place they get an end stamp. Set once — a run that
            # already has one (from the telephony callback) keeps it.
            ended_at=datetime.now(UTC),
        )

        asyncio.create_task(
            _capture_call_event(
                workflow_run_id, user_provider_id, PostHogEvent.CALL_COMPLETED
            )
        )

        logs_update: dict[str, object] = {}
        if not in_memory_logs_buffer.is_empty:
            try:
                feedback_events = in_memory_logs_buffer.get_events()
                logs_update["realtime_feedback_events"] = feedback_events
                logger.debug(
                    f"Saved {len(feedback_events)} feedback events to workflow run logs"
                )
            except Exception as e:
                logger.error(f"Error saving realtime feedback logs: {e}", exc_info=True)
        else:
            logger.debug("Logs buffer is empty, skipping save")

        logs_update.update(integration_logs)

        if logs_update:
            try:
                await db_client.update_workflow_run(
                    run_id=workflow_run_id,
                    logs=logs_update,
                )
            except Exception as e:
                logger.error(f"Error saving workflow run logs: {e}", exc_info=True)

        # Upload artifacts straight from the in-memory buffers so nothing has
        # to cross a process/host boundary via temp files. Must complete
        # before the completion job is enqueued so QA and webhooks see the
        # artifacts in storage.
        try:
            mixed_audio_wav = None
            user_audio_wav = None
            bot_audio_wav = None

            if not in_memory_audio_buffers.mixed.is_empty:
                mixed_audio_wav = await in_memory_audio_buffers.mixed.to_wav_bytes()
            else:
                logger.debug("Audio buffer is empty, skipping upload")

            if not in_memory_audio_buffers.user.is_empty:
                user_audio_wav = await in_memory_audio_buffers.user.to_wav_bytes()
            else:
                logger.debug("User audio buffer is empty, skipping upload")

            if not in_memory_audio_buffers.bot.is_empty:
                bot_audio_wav = await in_memory_audio_buffers.bot.to_wav_bytes()
            else:
                logger.debug("Bot audio buffer is empty, skipping upload")

            transcript_text = in_memory_logs_buffer.generate_transcript_text(
                include_end_timestamps=include_transcript_end_timestamps
            )
            if not transcript_text:
                logger.debug("No transcript events in logs buffer, skipping upload")

            await upload_workflow_run_artifacts(
                workflow_run_id,
                mixed_audio_wav=mixed_audio_wav,
                user_audio_wav=user_audio_wav,
                bot_audio_wav=bot_audio_wav,
                transcript_text=transcript_text,
            )
        except Exception as e:
            logger.error(f"Error uploading call artifacts: {e}", exc_info=True)

        # Combined task: runs integrations (including QA), then calculates
        # cost (so QA token usage is captured in usage_info)
        await enqueue_job(
            FunctionNames.PROCESS_WORKFLOW_COMPLETION,
            workflow_run_id,
        )

    # Return the buffer so it can be passed to other handlers
    return in_memory_audio_buffers


def register_audio_data_handler(
    audio_buffer: AudioBufferProcessor,
    workflow_run_id,
    in_memory_buffers: InMemoryRecordingBuffers,
):
    """Register event handler for audio data"""
    logger.info(f"Registering audio data handler for workflow run {workflow_run_id}")

    @audio_buffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        if not audio:
            return

        try:
            await in_memory_buffers.mixed.append(audio)
        except MemoryError as e:
            logger.error(f"Mixed audio buffer full: {e}")

    @audio_buffer.event_handler("on_track_audio_data")
    async def on_track_audio_data(
        buffer, user_audio, bot_audio, sample_rate, num_channels
    ):
        try:
            if user_audio:
                await in_memory_buffers.user.append(user_audio)
            if bot_audio:
                await in_memory_buffers.bot.append(bot_audio)
        except MemoryError as e:
            logger.error(f"Track audio buffer full: {e}")


def _processor_name(frame) -> str | None:
    """The name of the pipeline processor that raised this error, if known."""
    processor = getattr(frame, "processor", None)
    if processor is None:
        return None
    try:
        name = getattr(processor, "name", None) or type(processor).__name__
        return str(name)[:120]
    except Exception:  # noqa: BLE001 - a label must never cost us the error itself
        return None


async def _mark_pipeline_error_fatal(workflow_run_id: int) -> None:
    """Promote an already-recorded error to fatal.

    Used when the mute-agent watchdog ends the call: the provider called its
    own error non-fatal, but it is what stopped the conversation, and a run
    detail screen that grades it as a survivable grumble is telling the reader
    the opposite of what happened.
    """
    run = await db_client.get_workflow_run_by_id(workflow_run_id)
    extra = dict(getattr(run, "extra", None) or {}) if run else {}
    recorded = extra.get("pipeline_error")
    if not isinstance(recorded, dict):
        return
    extra["pipeline_error"] = {
        **recorded,
        "fatal": True,
        # Kept distinct from `fatal` so the reason survives: the provider did
        # not call this fatal, we did, because the agent went silent.
        "ended_call_reason": "mute_agent",
    }
    await db_client.update_workflow_run(workflow_run_id, extra=extra)


async def _record_pipeline_error(workflow_run_id: int, frame) -> None:
    """Write the pipeline failure onto the run so the UI can show it.

    Kept short and bounded: this is a diagnostic breadcrumb, not a log sink, and
    an unbounded frame repr on a JSON column is how a table becomes unqueryable.
    """
    detail = getattr(frame, "error", None) or str(frame)
    run = await db_client.get_workflow_run_by_id(workflow_run_id)
    extra = dict(getattr(run, "extra", None) or {}) if run else {}
    extra["pipeline_error"] = {
        "detail": str(detail)[:1000],
        "frame_type": type(frame).__name__,
        # Which service actually failed. Several providers raise the identical
        # bare string — "Error connecting: no close frame received or sent"
        # comes out of the Rime TTS and the OpenAI realtime LLM word for word —
        # so without this the message names no component and answering "which
        # one broke" means reading the pipeline source. pipecat stamps the
        # originating processor onto every ErrorFrame it pushes; we were
        # throwing it away.
        "component": _processor_name(frame),
        "at": datetime.now(UTC).isoformat(),
        # Whether this ended the call. Without it the reader cannot tell a call
        # that died from one that finished after a provider grumbled, and the
        # UI would tell somebody their working call had ended on an error.
        "fatal": bool(getattr(frame, "fatal", False)),
    }
    await db_client.update_workflow_run(workflow_run_id, extra=extra)
