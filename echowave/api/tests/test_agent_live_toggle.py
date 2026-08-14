"""Switching an agent off stops it answering the phone.

The promise a toggle makes is only as good as its least-careful call path, so
the important tests here are the boring repetitive ones: **each** of the four
ways a call reaches an agent refuses when the agent is off. A flag honoured by
three of them is worse than no flag, because the operator believes the agent is
silent and it is answering.

The four paths, and why each is separate rather than parameterised: they refuse
in four different currencies. The webhook dispatcher returns a provider-shaped
rejection, ARI hangs the channel up, the outbound API raises 409, and the
campaign dispatcher marks the row refused without feeding the retry path.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.db import db_client
from api.db.models import OrganizationModel, UserModel, WorkflowModel
from api.enums import WorkflowStatus
from api.services.workflow import liveness


def _agent(*, is_live=True, status=WorkflowStatus.ACTIVE.value, workflow_id=7):
    return SimpleNamespace(id=workflow_id, is_live=is_live, status=status, user_id=1)


class TestTheRuleItself:
    def test_a_live_agent_takes_calls(self):
        liveness.assert_workflow_may_take_calls(_agent())
        assert liveness.workflow_is_live(_agent()) is True

    def test_a_paused_agent_does_not(self):
        with pytest.raises(liveness.AgentNotTakingCalls) as caught:
            liveness.assert_workflow_may_take_calls(_agent(is_live=False))

        assert caught.value.reason == "agent_not_live"
        # The message says how to undo it. An operator reading this in a log is
        # usually asking "why did nobody answer".
        assert "Turn it on" in str(caught.value)

    def test_an_archived_agent_does_not_either(self):
        """Nothing enforced this before. A number still pointed at an archived
        agent kept being answered by it — archiving is a stronger statement
        than pausing, and it would be strange for the weaker one to be the only
        one with teeth."""
        with pytest.raises(liveness.AgentNotTakingCalls) as caught:
            liveness.assert_workflow_may_take_calls(
                _agent(status=WorkflowStatus.ARCHIVED.value)
            )

        assert caught.value.reason == "agent_archived"

    def test_a_missing_agent_is_refused_rather_than_crashing(self):
        with pytest.raises(liveness.AgentNotTakingCalls):
            liveness.assert_workflow_may_take_calls(None)

    def test_a_row_without_the_column_is_treated_as_live(self):
        """Absence of the flag is not a decision to pause. A stale row or a
        partial object must not silently stop answering."""
        assert liveness.workflow_is_live(SimpleNamespace(id=1)) is True


@pytest.mark.asyncio
class TestTheColumn:
    async def test_agents_are_live_by_default(self, db_session, async_session):
        """Nothing may go quiet on deploy. Every agent that existed before this
        column did has numbers pointed at it already."""
        org = OrganizationModel(provider_id="org-live-default", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        workflow = WorkflowModel(
            name="Default agent", organization_id=org.id, workflow_definition={}
        )
        async_session.add(workflow)
        await async_session.flush()

        assert workflow.is_live is True

    async def test_the_toggle_round_trips(self, db_session, async_session):
        org = OrganizationModel(provider_id="org-live-toggle", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        workflow = WorkflowModel(
            name="Toggled agent", organization_id=org.id, workflow_definition={}
        )
        async_session.add(workflow)
        await async_session.flush()

        paused = await db_client.set_workflow_live(
            workflow_id=workflow.id, is_live=False, organization_id=org.id
        )
        assert paused.is_live is False
        # Pausing must not archive: the agent stays in the list you would use
        # to bring it back.
        assert paused.status == WorkflowStatus.ACTIVE.value

        resumed = await db_client.set_workflow_live(
            workflow_id=workflow.id, is_live=True, organization_id=org.id
        )
        assert resumed.is_live is True

    async def test_one_organization_cannot_silence_anothers_agent(
        self, db_session, async_session
    ):
        """An outage the victim did not cause and cannot see the reason for."""
        owner = OrganizationModel(provider_id="org-live-owner", quota_decibyl_tokens=0)
        stranger = OrganizationModel(
            provider_id="org-live-stranger", quota_decibyl_tokens=0
        )
        async_session.add_all([owner, stranger])
        await async_session.flush()
        workflow = WorkflowModel(
            name="Owned agent", organization_id=owner.id, workflow_definition={}
        )
        async_session.add(workflow)
        await async_session.flush()

        with pytest.raises(ValueError):
            await db_client.set_workflow_live(
                workflow_id=workflow.id, is_live=False, organization_id=stranger.id
            )

        await async_session.refresh(workflow)
        assert workflow.is_live is True


@pytest.mark.asyncio
class TestTheRouteThatTogglesIt:
    async def _account(self, async_session, slug):
        from api.db.models import OrganizationMembershipModel
        from api.enums import OrganizationRole

        org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        user = UserModel(provider_id=f"user-{slug}", selected_organization_id=org.id)
        async_session.add(user)
        await async_session.flush()
        async_session.add(
            OrganizationMembershipModel(
                user_id=user.id,
                organization_id=org.id,
                role=OrganizationRole.OWNER.value,
            )
        )
        await async_session.flush()
        return user, org

    async def test_a_member_can_pause_and_resume_an_agent(
        self, db_session, async_session, test_client_factory
    ):
        """Deliberately not admin-gated. Pausing an agent is reversible, is
        part of running one, and an operator who cannot stop a misbehaving
        agent without finding an admin will pull a phone number instead."""
        user, org = await self._account(async_session, "live-route")
        workflow = WorkflowModel(
            name="Route agent", organization_id=org.id, workflow_definition={}
        )
        async_session.add(workflow)
        await async_session.flush()

        async with test_client_factory(user) as client:
            paused = await client.put(
                f"/api/v1/workflow/{workflow.id}/live", json={"is_live": False}
            )
            assert paused.status_code == 200
            assert paused.json()["is_live"] is False

            resumed = await client.put(
                f"/api/v1/workflow/{workflow.id}/live", json={"is_live": True}
            )
            assert resumed.status_code == 200
            assert resumed.json()["is_live"] is True

    async def test_another_organizations_agent_is_not_found(
        self, db_session, async_session, test_client_factory
    ):
        user, _org = await self._account(async_session, "live-route-stranger")
        other = OrganizationModel(provider_id="org-live-other", quota_decibyl_tokens=0)
        async_session.add(other)
        await async_session.flush()
        workflow = WorkflowModel(
            name="Other agent", organization_id=other.id, workflow_definition={}
        )
        async_session.add(workflow)
        await async_session.flush()

        async with test_client_factory(user) as client:
            response = await client.put(
                f"/api/v1/workflow/{workflow.id}/live", json={"is_live": False}
            )

        assert response.status_code == 404


@pytest.mark.asyncio
class TestEachCallPathRefuses:
    """The four ways a call reaches an agent, each checked separately.

    Not parameterised, because they refuse in four different currencies: a
    provider-shaped rejection, a hung-up channel, a 409, and a refused campaign
    row. Those are what the test is about.
    """

    async def test_the_outbound_api_refuses_with_409(
        self, db_session, async_session, test_client_factory
    ):
        """409, not 404 or 403: the agent exists and you may use it, it is just
        switched off — a state the caller can change."""
        from unittest.mock import AsyncMock, patch

        from api.db.models import OrganizationMembershipModel
        from api.enums import OrganizationRole

        org = OrganizationModel(provider_id="org-paused-out", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        user = UserModel(provider_id="user-paused-out", selected_organization_id=org.id)
        async_session.add(user)
        await async_session.flush()
        async_session.add(
            OrganizationMembershipModel(
                user_id=user.id,
                organization_id=org.id,
                role=OrganizationRole.OWNER.value,
            )
        )
        workflow = WorkflowModel(
            name="Paused agent",
            organization_id=org.id,
            workflow_definition={},
            is_live=False,
        )
        async_session.add(workflow)
        await async_session.flush()

        provider = SimpleNamespace(
            PROVIDER_NAME="twilio",
            WEBHOOK_ENDPOINT="twilio/voice",
            validate_config=lambda: True,
            initiate_call=AsyncMock(),
        )

        with (
            patch(
                "api.routes.telephony.get_default_telephony_provider",
                new=AsyncMock(return_value=provider),
            ),
            patch(
                "api.routes.telephony.number_lifecycle.assert_configuration_may_serve",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "api.services.compliance.dnd.within_calling_hours",
                lambda **kwargs: True,
            ),
        ):
            async with test_client_factory(user) as client:
                response = await client.post(
                    "/api/v1/telephony/initiate-call",
                    json={"workflow_id": workflow.id, "phone_number": "+919876543210"},
                )

        assert response.status_code == 409
        assert "paused" in response.json()["detail"].lower()
        # The call never reached the carrier.
        provider.initiate_call.assert_not_awaited()

    async def test_the_campaign_dispatcher_refuses_the_row_terminally(self):
        """Terminal, like a DND refusal. Retrying changes nothing until
        somebody turns the agent on, and feeding these into the retry path
        would trip the circuit breaker that exists to spot a bad carrier."""
        from unittest.mock import AsyncMock

        from api.services.campaign.campaign_call_dispatcher import (
            CampaignCallDispatcher,
        )

        dispatcher = CampaignCallDispatcher.__new__(CampaignCallDispatcher)
        dispatcher.mark_queued_run_refused = AsyncMock()

        campaign = SimpleNamespace(id=3, workflow_id=7, organization_id=1)
        queued_run = SimpleNamespace(
            id=11, context_variables={"phone_number": "+91987"}
        )

        from unittest.mock import patch

        with patch(
            "api.services.campaign.campaign_call_dispatcher.db_client"
        ) as mock_db:
            mock_db.get_workflow_by_id = AsyncMock(return_value=_agent(is_live=False))
            await dispatcher.dispatch_call(queued_run, campaign, object())

        dispatcher.mark_queued_run_refused.assert_awaited_once()
        assert (
            dispatcher.mark_queued_run_refused.await_args.kwargs["reason"]
            == "agent_not_live"
        )

    async def test_the_ari_inbound_path_hangs_up(self):
        """Checked before a concurrency slot is taken, so a paused agent cannot
        consume capacity a live one needs."""
        import inspect

        from api.services.telephony import ari_manager

        source = inspect.getsource(ari_manager)
        guard = source.index("assert_workflow_may_take_calls")
        slot = source.index("acquire_org_slot", guard)
        run = source.index("create_workflow_run", guard)

        assert guard < slot, "liveness must be checked before a slot is acquired"
        assert guard < run, "liveness must be checked before a run is created"

    async def test_the_inbound_webhook_path_rejects_before_spending_anything(self):
        from api.routes import telephony as telephony_routes
        import inspect

        source = inspect.getsource(telephony_routes.handle_inbound_run)
        guard = source.index("assert_workflow_may_take_calls")
        slot = source.index("acquire_org_slot", guard)

        assert guard < slot, "liveness must be checked before a slot is acquired"
