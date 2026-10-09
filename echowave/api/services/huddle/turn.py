"""One spoken turn of a huddle: the agent's brain where Decibyl's would sit.

``services/voice/brain.DecibylVoiceBrain`` takes the answerer it calls, so a
huddle is the same processor with this one plugged in: the person's line is
written to the huddle's transcript, the agent answers as a teammate from
its own prompt (``context``) with its own tools (``tools``), and the words
go to the voice as they form. What was actually heard comes back through
``heard`` and is written to the transcript, marked when cut off -- the same
rule Talk follows, so the next turn never assumes an answer it talked over.

The tool loop is Decibyl's shape, smaller: reads run and feed the answer; a
proposed edit ends the tool phase (propose it, say so, stop), and the last
round is given no tools so a loop of reads cannot run all afternoon.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from api.services.huddle import record, tools

#: Rounds of tool calls in one spoken turn.
MAX_ROUNDS = 4
#: Earlier lines of this huddle the model sees.
MAX_HISTORY = 24

YOU = "you"
AGENT = "agent"


@dataclass
class HuddleState:
    """One connection's huddle: who, which agent, and its transcript row."""

    organization_id: int
    user_id: int
    workflow_id: int
    session_id: int
    agent_name: str
    system: str
    payload: dict[str, Any]
    event_id: int | None = None
    #: Lines and their row are written one at a time: the reply's heard
    #: text arrives from a background task while the next line may start.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    model: Any = None
    #: Tells the huddle panel something happened (a card, a note).
    tell: Callable[[dict], Awaitable[None]] | None = None

    async def _save(self) -> None:
        self.event_id = await record.save(
            organization_id=self.organization_id,
            workflow_id=self.workflow_id,
            agent_name=self.agent_name,
            event_id=self.event_id,
            payload=self.payload,
        )

    async def line(self, who: str, text: str, *, interrupted: bool = False) -> None:
        async with self.lock:
            before = len(self.payload.get("turns") or [])
            record.add_turn(self.payload, who, text, interrupted=interrupted)
            if len(self.payload.get("turns") or []) != before or interrupted:
                await self._save()

    async def note(self, text: str) -> bool:
        async with self.lock:
            kept = record.add_note(self.payload, text)
            if kept:
                await self._save()
            return kept

    async def cards(self, event_ids: list[int]) -> None:
        if not event_ids:
            return
        async with self.lock:
            self.payload["cards"] = list(self.payload.get("cards") or []) + event_ids
            await self._save()

    def history(self) -> list[dict[str, str]]:
        """The lines so far as alternating turns, newest last."""
        turns = list(self.payload.get("turns") or [])[-MAX_HISTORY:]
        out: list[dict[str, str]] = []
        for entry in turns:
            role = "user" if entry.get("who") == YOU else "assistant"
            text = str(entry.get("text") or "")
            if entry.get("interrupted"):
                text += " (interrupted)"
            if out and out[-1]["role"] == role:
                out[-1]["content"] += "\n" + text
            else:
                out.append({"role": role, "content": text})
        # A conversation opens with the person.
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    async def _tell(self, message: dict) -> None:
        if self.tell is None:
            return
        try:
            await self.tell(message)
        except Exception:  # noqa: BLE001 - a closed panel is not an error
            pass

    # --- the two hooks the voice brain calls -------------------------------

    async def answer(self, ledger: Any, turn: Any, on_words) -> str:
        # Read before this line is written, so it is not sent twice.
        earlier = self.history()
        await self.line(YOU, turn.text)
        return await self._answer(turn, on_words, earlier)

    async def heard(self, ledger: Any, turn: Any) -> None:
        heard = turn.heard_text()
        body = heard or ("" if turn.interrupted else turn.said.strip())
        if not body and not turn.interrupted:
            return
        await self.line(
            AGENT, body or "(interrupted before speaking)", interrupted=turn.interrupted
        )

    # --- the turn ------------------------------------------------------------

    async def _resolve_model(self) -> Any:
        if self.model is None:
            from api.db import db_client
            from api.services.agent_builder import settings

            async with db_client.async_session() as session:
                self.model = await settings.resolve_for_organization(
                    session, None, organization_id=self.organization_id
                )
        return self.model

    async def _speak(self, conversation: Any, on_words, offer: bool) -> Any:
        """One model call, its words to the voice as they form. ``on_words``
        is the brain's: it keeps the turn's ``said`` and pushes the frame."""
        from api.services.agent_builder import client
        from api.services.billing import model_usage

        model = await self._resolve_model()
        seen = {"n": 0}

        async def on_text(text: str) -> None:
            # The stream hands the whole text so far; the voice wants the
            # new piece.
            piece = text[seen["n"] :]
            seen["n"] = len(text)
            if piece:
                await on_words(piece)

        feature = (
            "huddle:byok" if getattr(model, "key_source", "") == "byok" else "huddle"
        )
        with model_usage.scope(organization_id=self.organization_id, feature=feature):
            return await client.stream(
                provider=model.provider,
                model=model.model,
                api_key=model.api_key,
                system=self.system,
                conversation=conversation,
                on_text=on_text,
                tools=tools.schemas() if offer else None,
            )

    async def _answer(self, turn: Any, on_words, earlier: list[dict[str, str]]) -> str:
        from api.services.agent_builder import client

        conversation = client.Conversation()
        for entry in earlier:
            if entry["role"] == "user":
                conversation.add_user(entry["content"])
            else:
                conversation.messages.append(
                    {"role": "assistant", "content": entry["content"]}
                )
        if conversation.messages and conversation.messages[-1]["role"] == "user":
            # The last line went unanswered (cut off before a word): one
            # user turn, not two in a row.
            conversation.messages[-1]["content"] += "\n" + turn.text
        else:
            conversation.add_user(turn.text)

        spoken = {"any": False}

        async def speak(piece: str) -> None:
            spoken["any"] = True
            await on_words(piece)

        reply = await self._speak(conversation, speak, offer=True)
        rounds = 0
        while reply.wants_tools and rounds < MAX_ROUNDS:
            rounds += 1
            turn.tool_turn = True
            conversation.add_assistant(reply)
            offer_again = True
            for call in reply.tool_calls:
                result = await self.run_tool(call.name, call.arguments)
                conversation.add_tool_result(call, result)
                if call.name == tools.PROPOSE_EDIT:
                    # Propose it, say so, stop: no second card this turn.
                    offer_again = False
            reply = await self._speak(
                conversation, speak, offer=offer_again and rounds < MAX_ROUNDS
            )
        body = (reply.text or "").strip()
        if not body and not spoken["any"]:
            # Silence reads as a broken call; say so.
            body = "I have nothing to add on that."
            await speak(body)
        return body

    async def run_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One tool call. Never raises: the model is told what went wrong."""
        try:
            if name in tools.READS:
                return await tools.read(
                    name,
                    organization_id=self.organization_id,
                    workflow_id=self.workflow_id,
                    arguments=arguments,
                )
            if name == tools.REMEMBER:
                kept = await self.note(str((arguments or {}).get("note") or ""))
                if kept:
                    await self._tell({"type": "huddle-note", "payload": {}})
                    return {"status": "remembered"}
                return {
                    "status": "not_kept",
                    "reason": "Empty, already noted, or this huddle has enough notes.",
                }
            if name == tools.PROPOSE_EDIT:
                result, cards = await tools.propose_edit(
                    organization_id=self.organization_id,
                    workflow_id=self.workflow_id,
                    arguments=arguments,
                )
                await self.cards(cards)
                for event_id in cards:
                    await self._tell(
                        {"type": "huddle-card", "payload": {"event_id": event_id}}
                    )
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("Huddle tool {} failed: {}", name, exc)
            return {"status": "error", "error": "That did not work just now."}
        return {"status": "error", "error": f"{name} is not one of your tools."}
