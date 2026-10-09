"""The huddle's audio: Talk's pipeline with the agent's brain in it.

``services/voice/pipeline.run_connection`` takes the processor where the LLM sits;
this builds the agent's (``turn.HuddleState`` plugged into the same
``DecibylVoiceBrain``) and hands it over. Transport, speech, turn-taking,
interruption, captions, the session's moves and its minutes are Talk's.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.services.huddle import context, record
from api.services.huddle import session as huddle_session
from api.services.huddle.turn import HuddleState
from api.services.voice import sessions
from api.services.voice.brain import DecibylVoiceBrain, TurnLedger


async def prepare(
    *, session: dict[str, Any], organization_id: int, user_id: int, tell: Any
) -> HuddleState | None:
    """Everything a connection needs before its first word, or None when
    the agent is gone or no longer this person's to see."""
    workflow_id = huddle_session.workflow_of(session)
    if workflow_id is None:
        return None
    try:
        workflow = await huddle_session.agent_for(
            organization_id=organization_id, user_id=user_id, workflow_id=workflow_id
        )
    except huddle_session.NotFound:
        return None
    notes = await record.notes_for(
        organization_id=organization_id, user_id=user_id, workflow_id=workflow_id
    )
    system = await context.system_prompt(
        organization_id=organization_id, workflow=workflow, notes=notes
    )
    found = await record.find(
        organization_id=organization_id,
        workflow_id=workflow_id,
        session_id=session["id"],
    )
    event_id, payload = (
        found
        if found
        else (None, record.new_payload(session_id=session["id"], user_id=user_id))
    )
    return HuddleState(
        organization_id=organization_id,
        user_id=user_id,
        workflow_id=workflow_id,
        session_id=session["id"],
        agent_name=workflow.name or "Agent",
        system=system,
        payload=payload,
        event_id=event_id,
        tell=tell,
    )


async def run_huddle_voice(
    webrtc_connection: Any,
    *,
    session_id: int,
    user_id: int,
    organization_id: int,
    ws_sender: Any,
) -> None:
    """Run one connection of a huddle until it ends. Never raises."""
    from api.services.voice import pipeline

    ledger: TurnLedger | None = None
    try:
        session = await huddle_session.get(
            organization_id=organization_id, user_id=user_id, session_id=session_id
        )
        if session is None or session["state"] in sessions.TERMINAL:
            return
        state = await prepare(
            session=session,
            organization_id=organization_id,
            user_id=user_id,
            tell=ws_sender,
        )
        ledger = TurnLedger(
            session_id=session_id,
            organization_id=organization_id,
            user_id=user_id,
            thread_id=None,
            language=session.get("language"),
            send=ws_sender,
        )
        if state is None:
            await ledger.tell(
                {
                    "type": "error",
                    "payload": {
                        "error_type": "session_ended",
                        "message": "This agent is not here any more.",
                    },
                }
            )
            await sessions.end(
                organization_id=organization_id,
                user_id=user_id,
                session_id=session_id,
                reason="failed",
            )
            return
        ledger.on_heard = state.heard
        brain = DecibylVoiceBrain(ledger, answer=state.answer)
        await pipeline.run_connection(webrtc_connection, session, ledger, brain=brain)
    except Exception as exc:  # noqa: BLE001 - the session record must still move
        logger.error("Huddle {} failed: {}", session_id, exc)
        if ledger is not None:
            await ledger.tell(
                {
                    "type": "error",
                    "payload": {
                        "error_type": "voice_failed",
                        "message": "The huddle stopped working. You can continue in text.",
                    },
                }
            )
        await sessions.server_lost_audio(session_id)
    finally:
        if ledger is not None:
            await ledger.drain()
