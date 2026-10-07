"""Seed one demo thread for the phone-size walkthrough (mobile/e2e/README.md).

Run against a LOCAL stack only (never staging or production), from echowave/
with the stack's environment loaded:

    PYTHONPATH=. python mobile/e2e/seed_walkthrough.py <user_id> <org_id> <thread_id>

Replies come from a model, and the walkthrough container has no model key,
so a message sent from the app gets the honest failure reply ("I could not
think that through just now"). To show the other states of a thread, this
writes rows with the server's own services, exactly as a turn would:

* the person's line (decibyl.ask path is replaced by a direct record),
* a Decibyl reply,
* a real action card through ``actions.propose`` (``track_commitment``:
  resolved, versioned and settled by the real code; confirming it really
  tracks the commitment, nothing is sent),
* a connect chip row as ``connector_offer`` writes it (Composio is not
  configured here, so ``offer`` itself would refuse).

Nothing here is used by the app or the API at runtime.
"""

from __future__ import annotations

import asyncio
import sys

from api.enums import AgentEventActor, AgentEventKind
from api.services import acting
from api.services.workflow import actions, agent_timeline


async def main(user_id: int, org_id: int, thread_id: str) -> None:
    with agent_timeline.in_thread(thread_id), acting.acting_as(user_id):
        await agent_timeline.record(
            organization_id=org_id,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.HUMAN.value,
            summary="Rahul still owes me for the print order. Can you keep track and remind him?",
            payload={
                "body": "Rahul still owes me for the print order. Can you keep track and remind him?",
                "author_id": user_id,
                "to": "Decibyl",
            },
            in_channel=False,
        )
        await agent_timeline.record(
            organization_id=org_id,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.AGENT.value,
            summary="I can track that.",
            payload={
                "body": (
                    "I can track that. Here is exactly what I will add to your "
                    "follow-ups -- nothing is sent to Rahul until you say so. "
                    "To remind him by email I need your Gmail connected."
                ),
                "from": "Decibyl",
            },
            in_channel=False,
        )
        out = await actions.propose(
            organization_id=org_id,
            workflow_id=None,
            workflow_run_id=None,
            arguments={
                "action": "track_commitment",
                "direction": "owed_to_me",
                "counterparty": "Rahul Mehta",
                "description": "Payment for the October print order",
                "amount": "4800",
                "currency": "INR",
                "due_on": "2026-10-15",
                "why": "You asked me to keep track of what Rahul owes you.",
            },
            in_channel=False,
        )
        print("card:", out)
        await agent_timeline.record(
            organization_id=org_id,
            kind=AgentEventKind.CONNECTOR_OFFERED.value,
            actor=AgentEventActor.AGENT.value,
            summary="Connect Gmail",
            payload={
                "app": "gmail",
                "name": "Gmail",
                "description": "Read and send email",
                "why": "To send Rahul a reminder from your own address.",
            },
            in_channel=False,
        )


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]))
