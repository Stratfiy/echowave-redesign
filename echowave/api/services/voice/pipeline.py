"""The live voice pipeline for a conversation with Decibyl (screen 05).

The same transport, speech services, turn detection, interruption handling
and caption feed the workflow pipeline uses (``services/pipecat/``), with
Decibyl's brain where a workflow's LLM would be::

    transport in -> transcriber -> user turns -> DecibylVoiceBrain
                 -> voice -> transport out -> HeardTracker -> assistant turns

What it reuses, so behaviour matches the calls the product already makes:

* ``create_webrtc_transport``, ``create_stt_service_with_backups`` and
  ``create_tts_service_with_backups`` -- Sarvam for Indian languages through
  the existing pipeline;
* the non-realtime turn strategies (words, not volume, start a turn; the STT's
  own boundaries where it has them) and Silero VAD;
* ``RealtimeFeedbackObserver`` for captions: the person's words as they are
  transcribed, and Decibyl's words as they are spoken.

The session's configuration is fixed at its start: a reconnect that would
run on a different transcriber or voice than the session began with refuses
and asks for a new session, so a model change never lands mid-conversation.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.services.voice import catalogue, sessions
from api.services.voice.brain import DecibylVoiceBrain, HeardTracker, TurnLedger


def speech_choice(effective: Any) -> dict[str, Any]:
    """Which transcriber and voice a configuration runs, by name only."""
    out: dict[str, Any] = {}
    for slot in ("stt", "tts"):
        section = getattr(effective, slot, None)
        if section is not None:
            out[slot] = {
                "provider": getattr(section, "provider", None),
                "model": getattr(section, "model", None),
            }
    return out


def same_speech(snapshot: dict[str, Any], effective: Any) -> bool:
    """Whether ``effective`` runs what the session started on."""
    now = speech_choice(effective)
    return all(
        (snapshot.get(slot) or {}) == (now.get(slot) or {}) for slot in ("stt", "tts")
    )


def apply_person(effective: Any, session: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """The person's language, voice and speed on the session's speech, where
    the voice can honour them. Returns the configuration and what applied --
    a voice that cannot be honoured is said, never silently swapped."""
    config = session.get("config") or {}
    language = config.get("language")
    applied: dict[str, Any] = {"language": language, "voice_applied": False}
    stt = getattr(effective, "stt", None)
    tts = getattr(effective, "tts", None)
    updates: dict[str, Any] = {}
    if stt is not None and getattr(stt, "provider", None) == "sarvam" and language:
        updates["stt"] = stt.model_copy(update={"language": language})
    if tts is not None and getattr(tts, "provider", None) == "sarvam":
        tts_update: dict[str, Any] = {}
        if language:
            tts_update["language"] = language
        voice = session.get("voice")
        if catalogue.is_speaker(voice, getattr(tts, "model", None)):
            tts_update["voice"] = voice
            applied["voice_applied"] = True
        speed = config.get("speed")
        if isinstance(speed, (int, float)):
            tts_update["speed"] = float(speed)
        if tts_update:
            updates["tts"] = tts.model_copy(update=tts_update)
    if updates:
        effective = effective.model_copy(update=updates)
    return effective, applied


async def run_decibyl_voice(
    webrtc_connection: Any,
    *,
    session_id: int,
    user_id: int,
    organization_id: int,
    ws_sender: Any,
) -> None:
    """Run one connection of a voice session until it ends. Never raises."""
    ledger: TurnLedger | None = None
    try:
        session = await sessions.get(
            organization_id=organization_id, user_id=user_id, session_id=session_id
        )
        if session is None or session["state"] in sessions.TERMINAL:
            return
        ledger = TurnLedger(
            session_id=session_id,
            organization_id=organization_id,
            user_id=user_id,
            thread_id=session.get("thread_id"),
            language=session.get("language"),
            send=ws_sender,
        )
        await _run(webrtc_connection, session, ledger)
    except Exception as exc:  # noqa: BLE001 - the session record must still move
        logger.error("Decibyl voice session {} failed: {}", session_id, exc)
        if ledger is not None:
            await ledger.tell(
                {
                    "type": "error",
                    "payload": {
                        "error_type": "voice_failed",
                        "message": "Live voice stopped working. You can continue in text.",
                    },
                }
            )
        await sessions.server_lost_audio(session_id)
    finally:
        if ledger is not None:
            await ledger.drain()


async def _run(
    webrtc_connection: Any, session: dict[str, Any], ledger: TurnLedger
) -> None:
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    from api.enums import WorkflowRunMode
    from api.services import events
    from api.services.configuration.ai_model_configuration import (
        get_effective_ai_model_configuration_for_organization,
    )
    from api.services.pipecat import vad_sensitivity
    from api.services.pipecat.audio_config import create_audio_config
    from api.services.pipecat.pipeline_builder import create_pipeline_task
    from api.services.pipecat.realtime_feedback_observer import (
        RealtimeFeedbackObserver,
    )
    from api.services.pipecat.run_pipeline import (
        _create_non_realtime_user_turn_start_strategies,
        _create_non_realtime_user_turn_stop_strategies,
        _resolve_user_turn_stop_timeout,
    )
    from api.services.pipecat.service_factory import (
        create_stt_service_with_backups,
        create_tts_service_with_backups,
        stt_uses_external_turns,
    )
    from api.services.pipecat.transport_setup import create_webrtc_transport
    from api.services.pipecat.worker_runner import run_pipeline_worker

    session_id = session["id"]
    organization_id = ledger.organization_id
    effective = await get_effective_ai_model_configuration_for_organization(
        organization_id
    )
    if not same_speech(session.get("config") or {}, effective):
        await ledger.tell(
            {
                "type": "error",
                "payload": {
                    "error_type": "config_changed",
                    "message": (
                        "Voice settings changed since this session began. Start a "
                        "new session to use them."
                    ),
                },
            }
        )
        await sessions.end(
            organization_id=organization_id,
            user_id=ledger.user_id,
            session_id=session_id,
            reason="replaced",
        )
        return
    effective, applied = apply_person(effective, session)
    ledger.stt_provider = getattr(getattr(effective, "stt", None), "provider", None)
    ledger.tts_provider = getattr(getattr(effective, "tts", None), "provider", None)

    audio_config = create_audio_config(WorkflowRunMode.SMALLWEBRTC.value)
    transport = await create_webrtc_transport(
        webrtc_connection, session_id, audio_config
    )
    stt, _primary = create_stt_service_with_backups(effective, audio_config)
    tts = create_tts_service_with_backups(effective, audio_config)

    external = stt_uses_external_turns(effective)
    turn_config: dict[str, Any] = {}
    user_params = LLMUserAggregatorParams(
        user_turn_strategies=UserTurnStrategies(
            start=_create_non_realtime_user_turn_start_strategies(
                turn_config, uses_external_turns=external
            ),
            stop=_create_non_realtime_user_turn_stop_strategies(
                turn_config, uses_external_turns=external
            ),
        ),
        user_turn_stop_timeout=_resolve_user_turn_stop_timeout(
            turn_config, uses_external_turns=external
        ),
        vad_analyzer=SileroVADAnalyzer(params=vad_sensitivity.params(turn_config)),
    )
    aggregators = LLMContextAggregatorPair(LLMContext(), user_params=user_params)
    brain = DecibylVoiceBrain(ledger)
    tracker = HeardTracker(ledger)
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            brain,
            tts,
            transport.output(),
            tracker,
            aggregators.assistant(),
        ]
    )
    task = create_pipeline_task(
        pipeline, f"voice-{session_id}", audio_config, conversation_type="voice"
    )
    task.add_observer(RealtimeFeedbackObserver(ws_sender=ledger.send))

    @transport.event_handler("on_client_connected")
    async def _connected(_transport, _client):
        current = await sessions.get(
            organization_id=organization_id,
            user_id=ledger.user_id,
            session_id=session_id,
        )
        if current is None or current["state"] not in (
            sessions.CONNECTING,
            sessions.RECONNECTING,
        ):
            return
        try:
            moved = await sessions.move(
                organization_id=organization_id,
                user_id=ledger.user_id,
                session_id=session_id,
                expected_version=current["state_version"],
                to=sessions.LIVE,
                phase="listening",
            )
        except sessions.SessionError:
            return
        gap = None
        if current["state"] == sessions.RECONNECTING and moved["gaps"]:
            gap = moved["gaps"][-1]
            await events.emit(
                "voice_session_reconnected",
                user_id=ledger.user_id,
                organization_id=organization_id,
                task_id=f"voice:{session_id}",
                properties={"channel": "web", "lost_ms": int(gap.get("lost_ms") or 0)},
            )
        else:
            await events.emit(
                "voice_session_started",
                user_id=ledger.user_id,
                organization_id=organization_id,
                task_id=f"voice:{session_id}",
                properties={"channel": "web", "language": session.get("language")},
            )
        await ledger.tell(
            {
                "type": "voice-live",
                "payload": {
                    "session": moved,
                    "applied": applied,
                    "gap": gap,
                    "gap_sentence": sessions.gap_sentence(gap) if gap else None,
                },
            }
        )

    @transport.event_handler("on_client_disconnected")
    async def _disconnected(_transport, _client):
        await task.cancel()

    await run_pipeline_worker(task)
    # The audio ended. If the person pressed End the session is already
    # ended; otherwise the connection dropped and the screen may reconnect.
    await sessions.server_lost_audio(session_id)
