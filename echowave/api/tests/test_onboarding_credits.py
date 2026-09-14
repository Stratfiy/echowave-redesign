"""Free credit earned step by step (KAN-132).

What these pin: a step pays exactly once and only when its thing has really
happened; the six steps sum to the Free allowance and never pass it; our own
accounts earn nothing; and the first routine run is free of its charge.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from api.db.models import (
    AgentEventModel,
    CreditLedgerModel,
    KnowledgeBaseDocumentModel,
    OrganizationInvitationModel,
    OrganizationMembershipModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import (
    AgentEventActor,
    AgentEventKind,
    CreditLedgerKind,
    WorkflowRunMode,
    WorkflowRunState,
)
from api.services.billing import onboarding_credits as oc
from api.services.billing.costing import current_balance_paise

NOW = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def every_step_on(monkeypatch):
    monkeypatch.setattr(oc, "ENABLED", True)
    monkeypatch.setattr(oc, "DISABLED_STEPS", frozenset())
    # Local auth with mail: the address has to be proved.
    from api.services.auth import email_verification

    monkeypatch.setattr(email_verification, "verification_is_enforceable", lambda: True)


async def _org(session, slug: str, *, internal: bool = False, verified: bool = False):
    org = OrganizationModel(
        provider_id=f"org-{slug}", quota_decibyl_tokens=0, internal_billing=internal
    )
    user = UserModel(
        provider_id=f"user-{slug}",
        email=f"{slug}@example.com",
        email_verified_at=NOW if verified else None,
    )
    session.add_all([org, user])
    await session.flush()
    session.add(
        OrganizationMembershipModel(
            user_id=user.id, organization_id=org.id, role="owner"
        )
    )
    await session.flush()
    return org, user


async def _trial_credits(session, org) -> int:
    paise = await session.scalar(
        select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
            CreditLedgerModel.organization_id == org.id,
            CreditLedgerModel.kind == CreditLedgerKind.TRIAL.value,
        )
    )
    return int(paise or 0) // 50


async def _bot(session, org) -> WorkflowModel:
    workflow = WorkflowModel(name="bot", organization_id=org.id, user_id=None)
    session.add(workflow)
    await session.flush()
    return workflow


async def _call(session, workflow, *, mode: str, seconds: int = 60):
    session.add(
        WorkflowRunModel(
            name="call",
            workflow_id=workflow.id,
            mode=mode,
            state=WorkflowRunState.COMPLETED.value,
            created_at=NOW,
            billable_seconds=seconds,
            billed_seconds=seconds,
            costed_at=NOW,
        )
    )
    await session.flush()


async def _fired(session, org, workflow):
    session.add(
        AgentEventModel(
            organization_id=org.id,
            workflow_id=workflow.id,
            at=NOW,
            kind=AgentEventKind.ROUTINE_FIRED.value,
            actor=AgentEventActor.SYSTEM.value,
            summary="Morning report started its scheduled run",
        )
    )
    await session.flush()


class TestTheSteps:
    def test_the_six_steps_sum_to_free(self):
        assert [s.key for s in oc.STEPS] == [
            "verify_email",
            "first_bot",
            "first_channel",
            "first_conversation",
            "first_routine",
            "moved_in",
        ]
        assert sum(s.credits for s in oc.STEPS) == oc.FREE_CREDITS == 1_000
        assert oc.STEPS_BY_KEY["verify_email"].ref_type == "signup_bonus"
        assert oc.STEPS_BY_KEY["first_bot"].ref_type == "onboarding:first_bot"


@pytest.mark.asyncio
class TestEarning:
    async def test_nothing_is_paid_until_something_is_done(
        self, db_session, async_session
    ):
        org, _ = await _org(async_session, "fresh")

        assert await oc.settle(async_session, organization_id=org.id) == []
        state = await oc.state(async_session, organization_id=org.id)
        assert state["granted_credits"] == 0
        assert state["remaining_credits"] == 1_000
        assert not state["complete"]
        assert [s["done"] for s in state["steps"]] == [False] * 6

    async def test_each_step_pays_once_as_it_is_done(self, db_session, async_session):
        org, user = await _org(async_session, "steps")

        user.email_verified_at = NOW
        await async_session.flush()
        assert await oc.settle(async_session, organization_id=org.id) == [
            "verify_email"
        ]
        assert await _trial_credits(async_session, org) == 150

        workflow = await _bot(async_session, org)
        assert await oc.settle(async_session, organization_id=org.id) == ["first_bot"]
        # Settling again pays nothing: the steps are done, and paid.
        assert await oc.settle(async_session, organization_id=org.id) == []
        assert await _trial_credits(async_session, org) == 300

        await _call(async_session, workflow, mode=WorkflowRunMode.PLIVO.value)
        assert await oc.settle(async_session, organization_id=org.id) == [
            "first_conversation"
        ]
        await _fired(async_session, org, workflow)
        assert await oc.settle(async_session, organization_id=org.id) == [
            "first_routine"
        ]
        assert await _trial_credits(async_session, org) == 700
        assert (
            await current_balance_paise(async_session, organization_id=org.id)
            == 700 * 50
        )

    async def test_a_rehearsal_is_not_a_conversation(self, db_session, async_session):
        """Talking to your own bot in the browser proves nothing to a customer."""
        org, _ = await _org(async_session, "rehearsal")
        workflow = await _bot(async_session, org)
        await _call(async_session, workflow, mode=WorkflowRunMode.WEBRTC.value)
        await _call(async_session, workflow, mode=WorkflowRunMode.TEXTCHAT.value)

        granted = await oc.settle(async_session, organization_id=org.id)

        assert "first_conversation" not in granted

    async def test_a_channel_message_counts_for_channel_and_conversation(
        self, db_session, async_session
    ):
        org, _ = await _org(async_session, "channel")
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=-50,
                kind=CreditLedgerKind.USAGE.value,
                ref_type="text_reply",
                ref_id="turn-1",
                balance_after_paise=0,
            )
        )
        await async_session.flush()

        granted = await oc.settle(async_session, organization_id=org.id)

        assert set(granted) == {"first_channel", "first_conversation"}

    async def test_moving_in_is_a_document_or_a_teammate(
        self, db_session, async_session
    ):
        org, _ = await _org(async_session, "movein")
        async_session.add(
            OrganizationInvitationModel(
                organization_id=org.id,
                email="colleague@example.com",
                role="member",
                token_hash="t1",
                expires_at=NOW,
                accepted_at=NOW,
            )
        )
        await async_session.flush()
        assert await oc.settle(async_session, organization_id=org.id) == ["moved_in"]

        other, owner = await _org(async_session, "docs")
        async_session.add(
            KnowledgeBaseDocumentModel(
                organization_id=other.id,
                filename="menu.pdf",
                processing_status="completed",
                created_by=owner.id,
            )
        )
        await async_session.flush()
        assert await oc.settle(async_session, organization_id=other.id) == ["moved_in"]

    async def test_an_account_paid_the_old_bonus_is_not_paid_twice(
        self, db_session, async_session
    ):
        """Before KAN-132 the bonus was one row with ref ``signup_bonus``.
        That row is the first step, already done."""
        org, _ = await _org(async_session, "legacy", verified=True)
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=41_500,
                kind=CreditLedgerKind.TRIAL.value,
                ref_type="signup_bonus",
                ref_id=str(org.id),
                balance_after_paise=41_500,
                note="Signup bonus ($5.00)",
            )
        )
        await async_session.flush()

        assert await oc.settle(async_session, organization_id=org.id) == []
        state = await oc.state(async_session, organization_id=org.id)
        assert state["steps"][0]["paid"] is True
        assert state["steps"][0]["granted_credits"] == 830

    async def test_the_free_allowance_is_a_ceiling(self, db_session, async_session):
        """An account that already holds ₹415 of the old bonus can earn the
        rest of the six, but never past 1,000 credits in total."""
        org, _ = await _org(async_session, "ceiling", verified=True)
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=41_500,
                kind=CreditLedgerKind.TRIAL.value,
                ref_type="signup_bonus",
                ref_id=str(org.id),
                balance_after_paise=41_500,
            )
        )
        workflow = await _bot(async_session, org)
        await _call(async_session, workflow, mode=WorkflowRunMode.PLIVO.value)
        await async_session.flush()

        granted = await oc.settle(async_session, organization_id=org.id)

        # 830 held; first_bot pays 150 (980), first_conversation is clipped to 20.
        assert granted == ["first_bot", "first_conversation"]
        assert await _trial_credits(async_session, org) == 1_000

    async def test_our_own_accounts_earn_nothing(self, db_session, async_session):
        org, _ = await _org(async_session, "ours", internal=True, verified=True)
        await _bot(async_session, org)

        assert await oc.settle(async_session, organization_id=org.id) == []
        assert await _trial_credits(async_session, org) == 0

    async def test_a_held_back_step_neither_pays_nor_blocks_completion(
        self, db_session, async_session, monkeypatch
    ):
        monkeypatch.setattr(oc, "DISABLED_STEPS", frozenset({"first_routine"}))
        org, _ = await _org(async_session, "heldback")
        workflow = await _bot(async_session, org)
        await _fired(async_session, org, workflow)

        assert "first_routine" not in await oc.settle(
            async_session, organization_id=org.id
        )
        state = await oc.state(async_session, organization_id=org.id)
        routine = next(s for s in state["steps"] if s["key"] == "first_routine")
        assert routine["enabled"] is False


@pytest.mark.asyncio
class TestTheFirstRoutineIsFree:
    async def test_only_the_first_firing_is_on_the_house(
        self, db_session, async_session
    ):
        org, _ = await _org(async_session, "routine")
        workflow = await _bot(async_session, org)

        await _fired(async_session, org, workflow)
        assert await oc.first_routine_run_is_free(async_session, organization_id=org.id)

        await _fired(async_session, org, workflow)
        assert not await oc.first_routine_run_is_free(
            async_session, organization_id=org.id
        )

    async def test_never_for_our_own_accounts(self, db_session, async_session):
        org, _ = await _org(async_session, "ours-routine", internal=True)
        workflow = await _bot(async_session, org)
        await _fired(async_session, org, workflow)

        assert not await oc.first_routine_run_is_free(
            async_session, organization_id=org.id
        )
