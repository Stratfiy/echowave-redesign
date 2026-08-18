"""The builder's conversation: one user message in, one reply out.

Stateless on the server. The whole transcript arrives with each request and
goes back with each reply, so a restart, a deploy or a second browser tab does
not lose a half-built agent, and no session table has to be reaped. The cost is
bandwidth on a conversation that is at most a few dozen short turns.

The loop is the ordinary tool-calling shape: ask the model, run whatever tools
it asked for, feed the results back, repeat until it answers in prose or hits
the per-turn ceiling. The ceiling matters more than it looks — a model that
keeps re-reading the template list without converging would spend the account's
whole daily allowance on one message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.services.agent_builder import tools as builder_tools
from api.services.agent_builder.client import (
    BuilderClientError,
    Conversation,
    complete,
)
from api.services.agent_builder.settings import BuilderModel

SYSTEM_PROMPT = """\
You are the Decibyl agent builder. You help someone put a working voice agent \
on their phone line, in a chat, without them touching the canvas.

Assume the person is a business owner, not a developer. They know their \
business; they do not know what an STT model is and should never be asked.

## How to run the conversation

**Find the template first.** Call `list_agent_templates` before your first \
question. Then say what you think they need in their own words — "a front desk \
agent that answers your clinic's phone and books appointments" — and ask them \
to confirm. Naming something concrete is a far better opening than asking what \
they want built.

**Ask one question per message.** Never send a list of questions. Call \
`get_agent_template` to see `required_variables`, then work through them one at \
a time, in the order given. Use the `asks_for` text to phrase each question in \
plain language.

**Ask only what you cannot infer.** If they said "Sunrise Dental in Bandra", \
you have the clinic name — do not ask for it again. Repeat back what you \
inferred so they can correct it.

**Never invent an answer.** Opening hours, doctor names and addresses are \
things only they know, and a guessed value gets spoken to a real caller. If \
they do not know one yet, say it can be filled in later and move on.

**Tell them the cost without being asked.** Once the template is chosen, call \
`estimate_agent_cost` and give the per-minute and monthly figures. Knowing this \
before the first call is something no other platform offers, so say it plainly.

**Build as soon as you can.** The moment you have every required variable, call \
`create_agent`. Do not ask for confirmation of details you have already \
confirmed one at a time.

**Then say what is left.** After a successful build, tell them the agent exists \
and can be opened and tested now, and that it needs a phone number attached in \
Telephony before it can take real calls. Call `list_phone_numbers` to see \
whether they already have one. You cannot buy a number and must not imply you \
can.

## Tone

Short. Warm. No jargon, no bullet lists in your replies, no markdown headings. \
Write the way a helpful colleague talks — two or three sentences and a \
question. Match the user's language: if they write in Hindi or Hinglish, reply \
that way.

## Limits

You can only create agents from the templates. If someone wants something no \
template covers, say so honestly and suggest the closest one, or point them at \
the workflow editor. Never claim to have done something you have not done."""


@dataclass
class TurnResult:
    """What one user message produced."""

    reply: str
    #: The transcript to send back with the next message.
    conversation: list[dict[str, Any]]
    #: Tools that ran this turn, for the UI to show as activity.
    actions: list[str] = field(default_factory=list)
    #: Set when an agent was created, so the UI can offer a link to it.
    created_workflow_id: int | None = None


async def run_turn(
    *,
    session: AsyncSession,
    model: BuilderModel,
    organization_id: int,
    user_id: int,
    message: str,
    history: list[dict[str, Any]] | None = None,
) -> TurnResult:
    """Run one exchange and return the reply plus the updated transcript.

    Raises :class:`BuilderClientError` when the model cannot be reached — the
    caller turns that into a message for the user. Tool failures never raise;
    they come back to the model as text so it can recover.
    """
    conversation = Conversation(messages=list(history or []))
    conversation.add_user(message)

    schemas = builder_tools.tool_schemas()
    actions: list[str] = []
    created_workflow_id: int | None = None

    for _ in range(constants.AGENT_BUILDER_MAX_TOOL_CALLS_PER_TURN):
        reply = await complete(
            provider=model.provider,
            model=model.model,
            api_key=model.api_key,
            system=SYSTEM_PROMPT,
            conversation=conversation,
            tools=schemas,
        )
        conversation.add_assistant(reply)

        if not reply.wants_tools:
            return TurnResult(
                reply=reply.text,
                conversation=conversation.messages,
                actions=actions,
                created_workflow_id=created_workflow_id,
            )

        for call in reply.tool_calls:
            actions.append(call.name)
            result = await builder_tools.dispatch(
                call.name,
                call.arguments,
                session=session,
                organization_id=organization_id,
                user_id=user_id,
            )
            if call.name == "create_agent" and result.get("created"):
                created_workflow_id = result.get("workflow_id")
            conversation.add_tool_result(call, result)

    # The ceiling was reached without the model answering. Returning a usable
    # message rather than raising keeps the transcript intact, so the user can
    # nudge it and carry on rather than starting again.
    logger.warning(
        "Agent builder hit the tool-call ceiling for organization {}", organization_id
    )
    return TurnResult(
        reply=(
            "I got a bit stuck working that out. Could you tell me again what "
            "kind of business this agent is for?"
        ),
        conversation=conversation.messages,
        actions=actions,
        created_workflow_id=created_workflow_id,
    )


__all__ = ["BuilderClientError", "TurnResult", "run_turn", "SYSTEM_PROMPT"]
