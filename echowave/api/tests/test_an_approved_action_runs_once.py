"""An approved action runs once, however many presses or retries reach it.

Settling a card read the payload and wrote it back unconditionally, so two
Confirms pressed together both armed it, and the job that fires it acted
before marking it done, so a retried job sent the same email twice. Every
move is now a compare-and-swap on the card's state, and the job claims the
card before it acts.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client
from api.db.models import OrganizationModel
from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import actions


async def _card(state: str = actions.PROPOSED) -> tuple[int, int]:
    async with db_client.async_session() as session:
        org = OrganizationModel(
            provider_id=f"org-runs-once-{asyncio.get_running_loop().time()}",
            quota_decibyl_tokens=0,
        )
        session.add(org)
        await session.flush()
        organization_id = int(org.id)
        await session.commit()
    event_id = await db_client.record_agent_event(
        organization_id=organization_id,
        kind=AgentEventKind.ACTION_PROPOSED.value,
        actor=AgentEventActor.AGENT.value,
        summary="Send the quote to Mr Rao",
        payload={"state": state, "label": "Send the quote", "action": "run_tool"},
    )
    return organization_id, int(event_id)


async def _state(organization_id: int, event_id: int) -> str:
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    return (event.payload or {}).get("state")


@pytest.mark.asyncio
class TestTwoPresses:
    async def test_two_confirms_arm_it_once(self):
        organization_id, event_id = await _card()

        async def slow_check(*_args, **_kwargs):
            # Both presses have read "proposed" before either writes.
            await asyncio.sleep(0.05)

        with (
            patch.object(
                actions.approvals, "check", new=AsyncMock(side_effect=slow_check)
            ),
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
            patch.object(actions, "_audit", new=AsyncMock()),
        ):
            results = await asyncio.gather(
                *(
                    actions.settle(
                        organization_id=organization_id,
                        event_id=event_id,
                        verb="confirm",
                        user_id=1,
                    )
                    for _ in range(2)
                ),
                return_exceptions=True,
            )
        armed = [r for r in results if isinstance(r, dict)]
        refused = [r for r in results if isinstance(r, actions.ActionError)]
        assert len(armed) == 1 and len(refused) == 1
        assert enqueue.await_count == 1
        assert await _state(organization_id, event_id) == actions.ARMED

    async def test_a_decline_after_a_confirm_is_refused(self):
        organization_id, event_id = await _card()
        with (
            patch.object(actions.approvals, "check", new=AsyncMock()),
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
            patch.object(actions, "_audit", new=AsyncMock()),
        ):
            await actions.settle(
                organization_id=organization_id,
                event_id=event_id,
                verb="confirm",
                user_id=1,
            )
            with pytest.raises(actions.ActionError):
                await actions.settle(
                    organization_id=organization_id,
                    event_id=event_id,
                    verb="decline",
                    user_id=2,
                )
        assert await _state(organization_id, event_id) == actions.ARMED


@pytest.mark.asyncio
class TestTheJobFiresOnce:
    async def test_two_jobs_for_one_card_act_once(self):
        organization_id, event_id = await _card(actions.ARMED)

        async def slow_execute(*_args, **_kwargs):
            await asyncio.sleep(0.05)
            return "Sent."

        with (
            patch.object(
                actions, "_execute", new=AsyncMock(side_effect=slow_execute)
            ) as execute,
            patch.object(actions, "_say", new=AsyncMock()),
        ):
            await asyncio.gather(
                actions.run(event_id, organization_id),
                actions.run(event_id, organization_id),
            )
        assert execute.await_count == 1
        assert await _state(organization_id, event_id) == actions.DONE

    async def test_a_retry_after_it_ran_does_nothing(self):
        organization_id, event_id = await _card(actions.ARMED)
        with (
            patch.object(
                actions, "_execute", new=AsyncMock(return_value="Sent.")
            ) as execute,
            patch.object(actions, "_say", new=AsyncMock()),
        ):
            await actions.run(event_id, organization_id)
            await actions.run(event_id, organization_id)
        assert execute.await_count == 1

    async def test_a_job_that_died_mid_action_is_never_fired_again(self):
        organization_id, event_id = await _card(actions.RUNNING)
        with (
            patch.object(actions, "_execute", new=AsyncMock()) as execute,
            patch.object(actions, "_say", new=AsyncMock()),
        ):
            await actions.run(event_id, organization_id)
        execute.assert_not_awaited()
        assert await _state(organization_id, event_id) == actions.RUNNING
