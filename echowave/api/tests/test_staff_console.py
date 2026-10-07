"""The staff console (launch stream `staff`; STAFF.md).

Done when: every route is closed while ``staff_console`` is off and to
anyone who is not staff; each destination answers only the roles the
matrix gives it (support cannot refund however the request is made); every
change is a typed command with a reason, an idempotency key, a second
person where required, a result and audit rows; invitations report per-item
outcomes and respect capacity; a suspension holds at sign-in while the flag
is on; a refund has one external effect and is ``refunded`` only after the
provider says so; incidents stay open until verified; and nothing shows
customer content.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from api import constants
from api.db.models import AdminActionLogModel, AgentTaskModel, PaymentModel
from api.db.shell_models import WaitlistRequestModel
from api.db.signup_invite_models import SignupInviteModel
from api.db.staff_models import StaffCommandModel, StaffRefundModel
from api.services import features
from api.services.staff import commands, operations, refunds, roles

ALL_FLAGS = (
    "STAFF_CONSOLE_ENABLED",
    "STAFF_ROLES_ENABLED",
    "STAFF_REFUNDS_ENABLED",
    "STAFF_EVALUATIONS_ENABLED",
    "STAFF_INCIDENTS_ENABLED",
)


@pytest.fixture
def staff_on(monkeypatch):
    for name in ALL_FLAGS:
        monkeypatch.setattr(constants, name, True)


async def _user(db, session, name: str, tier: str | None = None):
    run = uuid4().hex[:8]
    user, _ = await db.get_or_create_user_by_provider_id(f"{name}-{run}")
    user.email = f"{name}-{run}@example.test"
    user.staff_role = tier
    session.add(user)
    await session.flush()
    return user


async def _ctx(user) -> roles.StaffContext:
    return roles.StaffContext(user=user, roles=await roles.effective_roles(user))


def _key() -> str:
    return f"test-{uuid4().hex}"


@pytest.fixture
def signed_in(monkeypatch, test_client_factory):
    from api.services.auth import depends

    def _for(user):
        async def fake_get_user(*_args, **_kwargs):
            return user

        monkeypatch.setattr(depends, "get_user", fake_get_user)
        return test_client_factory(user)

    return _for


# --- flags -----------------------------------------------------------------------


class TestFlags:
    def test_every_flag_is_registered_off_and_described(self):
        for name, const in {
            "staff_console": "STAFF_CONSOLE_ENABLED",
            "staff_roles": "STAFF_ROLES_ENABLED",
            "staff_refunds": "STAFF_REFUNDS_ENABLED",
            "staff_evaluations": "STAFF_EVALUATIONS_ENABLED",
            "staff_incidents": "STAFF_INCIDENTS_ENABLED",
        }.items():
            assert features.FLAGS[name] == const
            assert name in features.DESCRIPTIONS
            assert features.env_global(name) is False

    def test_the_ui_knows_every_flag(self):
        ts = (
            Path(__file__).resolve().parents[2] / "ui/src/lib/features.ts"
        ).read_text()
        for name in (
            "staff_console",
            "staff_roles",
            "staff_refunds",
            "staff_evaluations",
            "staff_incidents",
        ):
            assert f'"{name}"' in ts

    def test_one_migration_with_the_stream_key(self):
        versions = Path(__file__).resolve().parents[1] / "alembic/versions"
        mine = [
            p
            for p in versions.glob("*.py")
            # A whole line: a later migration's `down_revision = "20261008staff"`
            # names this one, it is not another one.
            if re.search(r'^revision = "20261008staff', p.read_text(), re.M)
        ]
        assert len(mine) == 1


@pytest.mark.asyncio
class TestTheGate:
    async def test_off_every_route_is_a_404(self, signed_in, db_session, async_session):
        owner = await _user(db_session, async_session, "gate-owner", "superadmin")
        async with signed_in(owner) as client:
            for path in (
                "/api/v1/admin/staff/me",
                "/api/v1/admin/staff/overview",
                "/api/v1/admin/staff/users",
            ):
                assert (await client.get(path)).status_code == 404

    async def test_a_customer_is_refused(
        self, signed_in, db_session, async_session, staff_on
    ):
        customer = await _user(db_session, async_session, "gate-customer")
        async with signed_in(customer) as client:
            assert (await client.get("/api/v1/admin/staff/me")).status_code == 403

    async def test_an_owner_sees_all_eight_destinations(
        self, signed_in, db_session, async_session, staff_on
    ):
        owner = await _user(db_session, async_session, "gate-owner", "superadmin")
        async with signed_in(owner) as client:
            body = (await client.get("/api/v1/admin/staff/me")).json()
        assert [d["key"] for d in body["destinations"]] == [
            "overview",
            "users",
            "support",
            "quality",
            "analytics",
            "revenue",
            "operations",
            "controls",
        ]
        assert all(d["allowed"] for d in body["destinations"])
        assert body["roles"] == ["owner"]
        # Refunds are finance-only, even for an owner.
        assert "refunds.request" not in body["capabilities"]

    async def test_support_sees_only_its_destinations(
        self, signed_in, db_session, async_session, staff_on
    ):
        agent = await _user(db_session, async_session, "gate-support", "support")
        async with signed_in(agent) as client:
            body = (await client.get("/api/v1/admin/staff/me")).json()
            revenue = await client.get("/api/v1/admin/staff/revenue")
            roles_page = await client.get("/api/v1/admin/staff/roles")
            users_page = await client.get("/api/v1/admin/staff/users")
        allowed = {d["key"] for d in body["destinations"] if d["allowed"]}
        assert allowed == {"overview", "users", "support", "analytics", "operations"}
        assert revenue.status_code == 403
        assert roles_page.status_code == 403
        assert users_page.status_code == 200


# --- roles ------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRoles:
    async def test_grants_count_only_while_staff_roles_is_on(
        self, db_session, async_session, staff_on, monkeypatch
    ):
        owner = await _user(db_session, async_session, "r-owner", "superadmin")
        person = await _user(db_session, async_session, "r-support", "support")
        view = await commands.request(
            async_session,
            ctx=await _ctx(owner),
            command="role.grant",
            target={"user_id": person.id, "role": "finance"},
            reason="Runs refunds this month",
            idempotency_key=_key(),
        )
        assert view.state == commands.SUCCEEDED
        assert "refunds.request" in view.preview["capabilities_added"]
        assert await roles.effective_roles(person) == {"support", "finance"}
        monkeypatch.setattr(constants, "STAFF_ROLES_ENABLED", False)
        assert await roles.effective_roles(person) == {"support"}

    async def test_only_staff_can_hold_a_console_role(
        self, db_session, async_session, staff_on
    ):
        owner = await _user(db_session, async_session, "r-owner", "superadmin")
        customer = await _user(db_session, async_session, "r-customer")
        with pytest.raises(commands.CommandError, match="only for staff"):
            await commands.request(
                async_session,
                ctx=await _ctx(owner),
                command="role.grant",
                target={"user_id": customer.id, "role": "quality"},
                reason="should not work",
                idempotency_key=_key(),
            )

    async def test_support_cannot_grant_roles(
        self, db_session, async_session, staff_on
    ):
        agent = await _user(db_session, async_session, "r-support", "support")
        with pytest.raises(commands.NotPermitted):
            await commands.request(
                async_session,
                ctx=await _ctx(agent),
                command="role.grant",
                target={"user_id": agent.id, "role": "finance"},
                reason="self promotion",
                idempotency_key=_key(),
            )

    async def test_owner_is_not_grantable(self, db_session, async_session, staff_on):
        owner = await _user(db_session, async_session, "r-owner", "superadmin")
        person = await _user(db_session, async_session, "r-support", "support")
        with pytest.raises(commands.CommandError):
            await commands.request(
                async_session,
                ctx=await _ctx(owner),
                command="role.grant",
                target={"user_id": person.id, "role": "owner"},
                reason="not allowed",
                idempotency_key=_key(),
            )

    async def test_revoke_takes_it_away(self, db_session, async_session, staff_on):
        owner = await _user(db_session, async_session, "r-owner", "superadmin")
        person = await _user(db_session, async_session, "r-support", "support")
        ctx = await _ctx(owner)
        await commands.request(
            async_session,
            ctx=ctx,
            command="role.grant",
            target={"user_id": person.id, "role": "quality"},
            reason="evaluations",
            idempotency_key=_key(),
        )
        view = await commands.request(
            async_session,
            ctx=ctx,
            command="role.revoke",
            target={"user_id": person.id, "role": "quality"},
            reason="done with it",
            idempotency_key=_key(),
        )
        assert view.state == commands.SUCCEEDED
        assert "quality.manage" in view.preview["capabilities_removed"]
        assert await roles.effective_roles(person) == {"support"}


# --- the command contract ----------------------------------------------------------


@pytest.mark.asyncio
class TestCommands:
    async def test_the_same_key_is_the_same_request(
        self, db_session, async_session, staff_on
    ):
        owner = await _user(db_session, async_session, "c-owner", "superadmin")
        person = await _user(db_session, async_session, "c-support", "support")
        ctx, key = await _ctx(owner), _key()
        first = await commands.request(
            async_session,
            ctx=ctx,
            command="role.grant",
            target={"user_id": person.id, "role": "quality"},
            reason="evaluations",
            idempotency_key=key,
        )
        again = await commands.request(
            async_session,
            ctx=ctx,
            command="role.grant",
            target={"user_id": person.id, "role": "quality"},
            reason="evaluations",
            idempotency_key=key,
        )
        assert again.id == first.id
        with pytest.raises(commands.Conflict):
            await commands.request(
                async_session,
                ctx=ctx,
                command="role.grant",
                target={"user_id": person.id, "role": "finance"},
                reason="evaluations",
                idempotency_key=key,
            )

    async def test_a_preview_writes_nothing(self, db_session, async_session, staff_on):
        owner = await _user(db_session, async_session, "c-owner", "superadmin")
        person = await _user(db_session, async_session, "c-support", "support")
        before = await async_session.scalar(
            select(text("count(*)")).select_from(StaffCommandModel)
        )
        preview = await commands.dry_run(
            async_session,
            ctx=await _ctx(owner),
            command="role.grant",
            target={"user_id": person.id, "role": "operations"},
        )
        assert (
            preview["eligible"] is True
            and "operations.act" in preview["preview"]["capabilities_added"]
        )
        refused = await commands.dry_run(
            async_session,
            ctx=await _ctx(owner),
            command="role.revoke",
            target={"user_id": person.id, "role": "operations"},
        )
        assert refused["eligible"] is False and refused["preview"] is None
        assert (
            await async_session.scalar(
                select(text("count(*)")).select_from(StaffCommandModel)
            )
            == before
        )

    async def test_a_reason_is_required(self, db_session, async_session, staff_on):
        owner = await _user(db_session, async_session, "c-owner", "superadmin")
        with pytest.raises(commands.CommandError, match="reason"):
            await commands.request(
                async_session,
                ctx=await _ctx(owner),
                command="incident.open",
                target={
                    "title": "Calls drop",
                    "impact": "Some calls drop",
                    "severity": "sev2",
                },
                reason=" ",
                idempotency_key=_key(),
            )

    async def test_another_environment_is_refused(
        self, db_session, async_session, staff_on
    ):
        owner = await _user(db_session, async_session, "c-owner", "superadmin")
        with pytest.raises(commands.CommandError, match="runs in"):
            await commands.request(
                async_session,
                ctx=await _ctx(owner),
                command="incident.open",
                target={
                    "title": "Calls drop",
                    "impact": "Some calls drop",
                    "severity": "sev2",
                },
                reason="paging",
                idempotency_key=_key(),
                environment="production-other",
            )

    async def test_a_switched_off_command_is_not_offered(
        self, db_session, async_session, monkeypatch
    ):
        monkeypatch.setattr(constants, "STAFF_CONSOLE_ENABLED", True)
        owner = await _user(db_session, async_session, "c-owner", "superadmin")
        with pytest.raises(commands.NotSwitchedOn):
            await commands.request(
                async_session,
                ctx=await _ctx(owner),
                command="incident.open",
                target={
                    "title": "Calls drop",
                    "impact": "Some calls drop",
                    "severity": "sev2",
                },
                reason="paging",
                idempotency_key=_key(),
            )

    async def test_every_step_is_audited_with_the_command_id(
        self, db_session, async_session, staff_on
    ):
        owner = await _user(db_session, async_session, "c-owner", "superadmin")
        view = await commands.request(
            async_session,
            ctx=await _ctx(owner),
            command="incident.open",
            target={
                "title": "Calls drop",
                "impact": "Some calls drop",
                "severity": "sev2",
            },
            reason="paging the team",
            idempotency_key=_key(),
        )
        await async_session.flush()
        notes = (
            await async_session.execute(
                select(AdminActionLogModel.action, AdminActionLogModel.note).where(
                    AdminActionLogModel.actor_user_id == owner.id
                )
            )
        ).all()
        actions = [a for a, _ in notes]
        assert (
            "staff_command_requested" in actions
            and "staff_command_succeeded" in actions
        )
        assert all(f"#{view.id} incident.open" in n for _, n in notes)

    async def test_a_command_route_answers_accepted_not_succeeded(
        self, signed_in, db_session, async_session, staff_on, monkeypatch
    ):
        finance_a = await _user(db_session, async_session, "c-fin", "support")
        async_session.add_all([])
        await _grant(async_session, finance_a, "finance")
        payment = await _payment(db_session, async_session)
        refunds.use_provider(_FakeProvider())
        enqueued = []

        async def fake_enqueue(command_id):
            enqueued.append(command_id)

        monkeypatch.setattr(commands, "enqueue", fake_enqueue)
        try:
            async with signed_in(finance_a) as client:
                response = await client.post(
                    "/api/v1/admin/staff/commands",
                    json={
                        "command": "refund.request",
                        "target": {
                            "payment_id": payment.id,
                            "organization_id": payment.organization_id,
                            "amount_minor": 1000,
                        },
                        "reason": "Customer asked",
                        "idempotency_key": _key(),
                    },
                )
        finally:
            refunds.use_provider(None)
        assert response.status_code == 202
        assert response.json()["state"] == "awaiting_approval"
        assert enqueued == []  # nothing runs before the second person approves


async def _grant(session, user, role):
    from api.db.staff_models import StaffRoleGrantModel

    session.add(
        StaffRoleGrantModel(
            user_id=user.id,
            role=role,
            reason="test",
            granted_by=user.id,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def _org(db, user):
    org, _ = await db.get_or_create_organization_by_provider_id(
        org_provider_id=f"org-{uuid4().hex[:10]}", user_id=user.id
    )
    await db.add_user_to_organization(user.id, org.id)
    return org


async def _payment(db, session, *, gross=11800, currency="INR"):
    owner = await _user(db, session, "pay-customer")
    org = await _org(db, owner)
    payment = PaymentModel(
        organization_id=org.id,
        provider="razorpay",
        order_id=f"order_{uuid4().hex[:12]}",
        payment_id=f"pay_{uuid4().hex[:12]}",
        amount_paise=10000,
        gross_paise=gross,
        currency=currency,
        status="paid",
        paid_at=datetime.now(UTC),
    )
    session.add(payment)
    await session.flush()
    return payment


class _FakeProvider:
    name = "fake"

    def __init__(self, status="processed", raise_on_create=False):
        self.status = status
        self.raise_on_create = raise_on_create
        self.created: list[dict] = []

    async def create(self, *, payment_ref, amount_minor, currency, receipt):
        self.created.append(
            {
                "payment_ref": payment_ref,
                "amount_minor": amount_minor,
                "receipt": receipt,
            }
        )
        if self.raise_on_create:
            raise TimeoutError("provider timed out")
        return {"id": f"rfnd_{len(self.created)}", "status": "pending"}

    async def fetch(self, *, payment_ref, refund_id):
        return {"id": refund_id, "status": self.status}


# --- refunds -------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRefunds:
    async def _finance_pair(self, db, session):
        a = await _user(db, session, "fin-a", "support")
        b = await _user(db, session, "fin-b", "support")
        await _grant(session, a, "finance")
        await _grant(session, b, "finance")
        return a, b

    async def test_support_cannot_refund_through_the_api(
        self, signed_in, db_session, async_session, staff_on
    ):
        agent = await _user(db_session, async_session, "fin-support", "support")
        payment = await _payment(db_session, async_session)
        refunds.use_provider(_FakeProvider())
        try:
            async with signed_in(agent) as client:
                response = await client.post(
                    "/api/v1/admin/staff/commands",
                    json={
                        "command": "refund.request",
                        "target": {
                            "payment_id": payment.id,
                            "organization_id": payment.organization_id,
                            "amount_minor": 100,
                        },
                        "reason": "hidden call",
                        "idempotency_key": _key(),
                    },
                )
        finally:
            refunds.use_provider(None)
        assert response.status_code == 403

    async def test_an_owner_cannot_refund_without_finance(
        self, db_session, async_session, staff_on
    ):
        owner = await _user(db_session, async_session, "fin-owner", "superadmin")
        payment = await _payment(db_session, async_session)
        with pytest.raises(commands.NotPermitted):
            await commands.request(
                async_session,
                ctx=await _ctx(owner),
                command="refund.request",
                target={
                    "payment_id": payment.id,
                    "organization_id": payment.organization_id,
                    "amount_minor": 100,
                },
                reason="owner tries",
                idempotency_key=_key(),
            )

    async def test_needs_setup_without_a_provider(
        self, db_session, async_session, staff_on, monkeypatch
    ):
        monkeypatch.setattr(constants, "RAZORPAY_KEY_ID", None)
        a, _ = await self._finance_pair(db_session, async_session)
        payment = await _payment(db_session, async_session)
        assert refunds.provider_state()["state"] == "needs_setup"
        with pytest.raises(commands.CommandError, match="cannot be sent"):
            await commands.request(
                async_session,
                ctx=await _ctx(a),
                command="refund.request",
                target={
                    "payment_id": payment.id,
                    "organization_id": payment.organization_id,
                    "amount_minor": 100,
                },
                reason="customer asked",
                idempotency_key=_key(),
            )

    async def test_one_refund_one_external_effect_reconciled_before_refunded(
        self, db_session, async_session, staff_on
    ):
        a, b = await self._finance_pair(db_session, async_session)
        payment = await _payment(db_session, async_session)
        provider = _FakeProvider(status="pending")
        refunds.use_provider(provider)
        try:
            view = await commands.request(
                async_session,
                ctx=await _ctx(a),
                command="refund.request",
                target={
                    "payment_id": payment.id,
                    "organization_id": payment.organization_id,
                    "amount_minor": 5000,
                },
                reason="Charged twice",
                idempotency_key=_key(),
            )
            assert view.preview["eligible_minor"] == 11800
            with pytest.raises(commands.NotPermitted, match="second person"):
                await commands.approve(
                    async_session, ctx=await _ctx(a), command_id=view.id
                )
            approved = await commands.approve(
                async_session, ctx=await _ctx(b), command_id=view.id
            )
            assert approved.state == commands.QUEUED
            await async_session.flush()
            assert await commands.run(view.id) == commands.SUCCEEDED
            assert await commands.run(view.id) is None  # the second job claims nothing
            assert len(provider.created) == 1
            refund = (
                await async_session.execute(
                    select(StaffRefundModel).where(
                        StaffRefundModel.command_id == view.id
                    )
                )
            ).scalar_one()
            assert refund.state == "pending"  # the provider has not said processed
            provider.status = "processed"
            assert await refunds.reconcile_pending() >= 1
            await async_session.refresh(refund)
            assert refund.state == "refunded" and refund.reconciled_at is not None
        finally:
            refunds.use_provider(None)

    async def test_never_more_than_was_collected(
        self, db_session, async_session, staff_on
    ):
        a, b = await self._finance_pair(db_session, async_session)
        payment = await _payment(db_session, async_session, gross=1000)
        refunds.use_provider(_FakeProvider())
        try:
            first = await commands.request(
                async_session,
                ctx=await _ctx(a),
                command="refund.request",
                target={
                    "payment_id": payment.id,
                    "organization_id": payment.organization_id,
                    "amount_minor": 700,
                },
                reason="part one",
                idempotency_key=_key(),
            )
            second = await commands.request(
                async_session,
                ctx=await _ctx(a),
                command="refund.request",
                target={
                    "payment_id": payment.id,
                    "organization_id": payment.organization_id,
                    "amount_minor": 700,
                },
                reason="part two",
                idempotency_key=_key(),
            )
            for cmd in (first, second):
                await commands.approve(
                    async_session, ctx=await _ctx(b), command_id=cmd.id
                )
            await async_session.flush()
            assert await commands.run(first.id) == commands.SUCCEEDED
            assert await commands.run(second.id) == commands.FAILED
            with pytest.raises(
                commands.CommandError, match="more than can be refunded"
            ):
                await commands.request(
                    async_session,
                    ctx=await _ctx(a),
                    command="refund.request",
                    target={
                        "payment_id": payment.id,
                        "organization_id": payment.organization_id,
                        "amount_minor": 400,
                    },
                    reason="too much",
                    idempotency_key=_key(),
                )
        finally:
            refunds.use_provider(None)

    async def test_a_provider_that_does_not_answer_is_outcome_unknown(
        self, db_session, async_session, staff_on
    ):
        a, b = await self._finance_pair(db_session, async_session)
        payment = await _payment(db_session, async_session)
        provider = _FakeProvider(raise_on_create=True)
        refunds.use_provider(provider)
        try:
            view = await commands.request(
                async_session,
                ctx=await _ctx(a),
                command="refund.request",
                target={
                    "payment_id": payment.id,
                    "organization_id": payment.organization_id,
                    "amount_minor": 100,
                },
                reason="customer asked",
                idempotency_key=_key(),
            )
            await commands.approve(async_session, ctx=await _ctx(b), command_id=view.id)
            await async_session.flush()
            assert await commands.run(view.id) == commands.OUTCOME_UNKNOWN
            refund = (
                await async_session.execute(
                    select(StaffRefundModel).where(
                        StaffRefundModel.command_id == view.id
                    )
                )
            ).scalar_one()
            assert refund.state == "outcome_unknown"
            assert await commands.run(view.id) is None
            assert len(provider.created) == 1
        finally:
            refunds.use_provider(None)

    async def test_the_ledger_and_its_export_agree(
        self, db_session, async_session, staff_on
    ):
        payment = await _payment(db_session, async_session)
        view = await refunds.ledger(
            async_session, organization_id=payment.organization_id
        )
        csv = await refunds.export_csv(
            async_session, organization_id=payment.organization_id
        )
        assert [p["id"] for p in view["payments"]] == [payment.id]
        assert view["totals"]["INR"]["collected_minor"] == 11800
        assert "11800" in csv and str(payment.id) in csv
        # Provider references are masked.
        assert payment.payment_id not in str(view)


# --- invitations and suspension ------------------------------------------------------


@pytest.mark.asyncio
class TestUsers:
    async def _waitlisted(self, session, status="waitlisted"):
        row = WaitlistRequestModel(
            email=f"wait-{uuid4().hex[:8]}@example.test",
            language="hi",
            first_task="Remind me about rent",
            status=status,
            source="waitlist",
        )
        session.add(row)
        await session.flush()
        return row

    async def test_invites_report_each_item(
        self, db_session, async_session, staff_on, monkeypatch
    ):
        monkeypatch.setattr(constants, "STAFF_PILOT_USER_CAPACITY", None)
        agent = await _user(db_session, async_session, "inv-support", "support")
        fresh = await self._waitlisted(async_session)
        already = await self._waitlisted(async_session, status="invited")
        view = await commands.request(
            async_session,
            ctx=await _ctx(agent),
            command="invite.issue",
            target={"waitlist_ids": [fresh.id, already.id, 99999999]},
            reason="Pilot cohort two",
            idempotency_key=_key(),
        )
        assert view.state == commands.SUCCEEDED, view.result
        assert view.preview["capacity"]["state"] == "needs_setup"
        outcomes = {i["waitlist_id"]: i["outcome"] for i in view.result["items"]}
        assert outcomes == {
            fresh.id: "issued",
            already.id: "already_invited",
            99999999: "not_found",
        }
        assert view.result["partial"] is True
        invite = await async_session.get(
            SignupInviteModel, view.result["items"][0]["invite_id"]
        )
        assert invite.email == fresh.email and invite.max_uses == 1
        assert "invite_id" not in str(view.result["items"][1])

    async def test_capacity_exhausted_is_refused(
        self, db_session, async_session, staff_on, monkeypatch
    ):
        monkeypatch.setattr(constants, "STAFF_PILOT_USER_CAPACITY", 0)
        agent = await _user(db_session, async_session, "inv-support", "support")
        fresh = await self._waitlisted(async_session)
        with pytest.raises(commands.CommandError, match="Capacity exhausted"):
            await commands.request(
                async_session,
                ctx=await _ctx(agent),
                command="invite.issue",
                target={"waitlist_ids": [fresh.id]},
                reason="Pilot cohort two",
                idempotency_key=_key(),
            )

    async def test_a_suspension_needs_a_second_person_and_holds_at_sign_in(
        self, db_session, async_session, staff_on, monkeypatch
    ):
        from fastapi import HTTPException

        from api.services.auth import depends

        agent = await _user(db_session, async_session, "sus-support", "support")
        owner = await _user(db_session, async_session, "sus-owner", "superadmin")
        customer = await _user(db_session, async_session, "sus-customer")
        await _org(db_session, customer)
        view = await commands.request(
            async_session,
            ctx=await _ctx(agent),
            command="user.suspend",
            target={"user_id": customer.id},
            reason="Abuse report 42",
            idempotency_key=_key(),
        )
        assert view.state == commands.AWAITING_APPROVAL
        assert view.preview["workspaces"] == 1 and "effect" in view.preview
        assert customer.staff_suspended_at is None
        with pytest.raises(commands.NotPermitted):
            # support may ask, not approve
            other_agent = await _user(
                db_session, async_session, "sus-support2", "support"
            )
            await commands.approve(
                async_session, ctx=await _ctx(other_agent), command_id=view.id
            )
        approved = await commands.approve(
            async_session, ctx=await _ctx(owner), command_id=view.id
        )
        assert approved.state == commands.SUCCEEDED
        await async_session.refresh(customer)
        assert customer.staff_suspended_at is not None

        async def authenticate(*_a, **_k):
            return customer

        monkeypatch.setattr(depends, "_authenticate", authenticate)
        with pytest.raises(HTTPException) as refused:
            await depends.get_user()
        assert refused.value.status_code == 403
        monkeypatch.setattr(constants, "STAFF_CONSOLE_ENABLED", False)
        assert await depends.get_user() is customer  # off: today's behaviour

    async def test_staff_are_not_suspended(self, db_session, async_session, staff_on):
        owner = await _user(db_session, async_session, "sus-owner", "superadmin")
        agent = await _user(db_session, async_session, "sus-support", "support")
        with pytest.raises(commands.CommandError, match="Staff"):
            await commands.request(
                async_session,
                ctx=await _ctx(owner),
                command="user.suspend",
                target={"user_id": agent.id},
                reason="staff account",
                idempotency_key=_key(),
            )

    async def test_user_detail_shows_access_not_content(
        self, signed_in, db_session, async_session, staff_on
    ):
        agent = await _user(db_session, async_session, "det-support", "support")
        customer = await _user(db_session, async_session, "det-customer")
        org = await _org(db_session, customer)
        other = await _org(
            db_session, await _user(db_session, async_session, "det-other")
        )
        async_session.add(
            AgentTaskModel(
                organization_id=org.id,
                title="Pay Ravi's rent",
                brief="secret brief text",
                status="done",
                created_by=customer.id,
                finished_at=datetime.now(UTC),
            )
        )
        await async_session.flush()
        async with signed_in(agent) as client:
            detail = await client.get(f"/api/v1/admin/staff/users/{customer.id}")
            tasks = await client.get(
                f"/api/v1/admin/staff/users/{customer.id}/tasks",
                params={"organization_id": org.id},
            )
            elsewhere = await client.get(
                f"/api/v1/admin/staff/users/{customer.id}/tasks",
                params={"organization_id": other.id},
            )
        assert detail.status_code == 200
        body = detail.json()
        assert body["user"]["access_state"] == "active"
        assert body["user"]["last_useful_outcome_at"] is not None
        assert body["assisted_access"] == "off"
        assert [w["id"] for w in body["workspaces"]] == [org.id]
        assert tasks.status_code == 200 and tasks.json()["tasks"][0]["state"] == "done"
        assert "Ravi" not in tasks.text and "secret brief" not in tasks.text
        assert elsewhere.status_code == 404


# --- incidents ---------------------------------------------------------------------


@pytest.mark.asyncio
class TestIncidents:
    async def test_open_until_a_passed_verification(
        self, db_session, async_session, staff_on
    ):
        from api.services.staff import incidents

        owner = await _user(db_session, async_session, "inc-owner", "superadmin")
        ctx = await _ctx(owner)

        async def cmd(name, target):
            return await commands.request(
                async_session,
                ctx=ctx,
                command=name,
                target=target,
                reason="incident work",
                idempotency_key=_key(),
            )

        opened = await cmd(
            "incident.open",
            {
                "title": "Calls drop at answer",
                "impact": "One in five calls",
                "severity": "sev1",
            },
        )
        iid = opened.result["incident_id"]
        resolve = await cmd(
            "incident.state", {"incident_id": iid, "revision": 0, "state": "resolved"}
        )
        assert (
            resolve.state == commands.FAILED
            and "verification" in resolve.result["message"]
        )
        missing_cmd = await cmd(
            "incident.step",
            {
                "incident_id": iid,
                "revision": 0,
                "kind": "execution",
                "summary": "Drained workers",
            },
        )
        assert missing_cmd.state == commands.FAILED
        step = await cmd(
            "incident.step",
            {
                "incident_id": iid,
                "revision": 0,
                "kind": "execution",
                "summary": "Drained workers",
                "ops_command_id": 7,
            },
        )
        assert step.result["state"] == "running"
        stale = await cmd(
            "incident.step",
            {"incident_id": iid, "revision": 0, "kind": "note", "summary": "late"},
        )
        assert (
            stale.state == commands.FAILED
            and "changed since" in stale.result["message"]
        )
        failed = await cmd(
            "incident.step",
            {
                "incident_id": iid,
                "revision": 1,
                "kind": "verification",
                "summary": "Test call",
                "outcome": "failed",
            },
        )
        assert failed.result["state"] == "verification_failed"
        still = await cmd(
            "incident.state", {"incident_id": iid, "revision": 2, "state": "resolved"}
        )
        assert still.state == commands.FAILED
        await cmd(
            "incident.step",
            {
                "incident_id": iid,
                "revision": 2,
                "kind": "verification",
                "summary": "Test call again",
                "outcome": "passed",
            },
        )
        done = await cmd(
            "incident.state", {"incident_id": iid, "revision": 3, "state": "resolved"}
        )
        assert done.state == commands.SUCCEEDED
        record = await incidents.get(async_session, iid)
        assert record["state"] == "resolved"
        assert [s["kind"] for s in record["steps"]] == [
            "execution",
            "verification",
            "verification",
        ]
        assert record["links"]["ops_commands"] == [7]


# --- operations, overview -------------------------------------------------------------


class TestHonestHealth:
    def test_silence_is_not_healthy(self):
        snap = {
            "database": {"ok": True, "latency_ms": 2},
            "redis": {"ok": True, "latency_ms": 1},
            "worker": {"ok": False, "alive": None, "latency_ms": 1},
            "queue": {"ok": True, "latency_ms": 1},
        }
        health = operations.health_from_probes(snap)
        assert health["state"] == "unknown"
        assert {s["name"]: s["state"] for s in health["signals"]}[
            "provider_balances"
        ] == "unknown"

    def test_all_core_ok_is_healthy(self):
        snap = {
            n: {"ok": True, "latency_ms": 1, "alive": True}
            for n in ("database", "redis", "worker", "queue", "provider_balances")
        }
        assert operations.health_from_probes(snap)["state"] == "healthy"

    def test_a_timed_out_probe_is_unknown(self):
        snap = {
            "database": {"ok": False, "latency_ms": None},
            "redis": {"ok": True, "latency_ms": 1},
            "worker": {"ok": True, "alive": True, "latency_ms": 1},
        }
        assert operations.health_from_probes(snap)["state"] == "unknown"


@pytest.mark.asyncio
class TestOverviewAndTrace:
    async def test_the_attention_queue_counts_a_failed_task(
        self, db_session, async_session, staff_on, monkeypatch
    ):
        from api.services import system_status
        from api.services.staff import overview

        async def fake_snapshot():
            return {"database": {"ok": True, "latency_ms": 1}}

        monkeypatch.setattr(system_status, "snapshot", fake_snapshot)
        before = {
            i["key"]: i for i in (await overview.snapshot(async_session))["attention"]
        }
        customer = await _user(db_session, async_session, "ov-customer")
        org = await _org(db_session, customer)
        async_session.add(
            AgentTaskModel(
                organization_id=org.id,
                title="x",
                brief="",
                status="could_not",
                ledger_state="failed",
                created_by=customer.id,
                finished_at=datetime.now(UTC),
            )
        )
        await async_session.flush()
        snap = await overview.snapshot(async_session)
        after = {i["key"]: i for i in snap["attention"]}
        assert after["failed_tasks"]["count"] == before["failed_tasks"]["count"] + 1
        assert after["failed_tasks"]["href"].startswith("/superadmin/operations")
        # What must appear: support is shown as needs setup, never hidden.
        assert after["support_escalations"]["state"] == "needs_setup"
        assert snap["health"]["state"] == "unknown"  # redis and worker never answered
        assert snap["metrics"]["state"] == "ok"

    async def test_a_trace_has_states_and_no_content(
        self, signed_in, db_session, async_session, staff_on
    ):
        ops_person = await _user(db_session, async_session, "tr-support", "support")
        customer = await _user(db_session, async_session, "tr-customer")
        org = await _org(db_session, customer)
        task = AgentTaskModel(
            organization_id=org.id,
            title="Call Dr Mehta",
            brief="private",
            status="doing",
            ledger_state="running",
            created_by=customer.id,
        )
        async_session.add(task)
        await async_session.flush()
        await async_session.execute(
            text(
                "INSERT INTO agent_task_transitions (organization_id, task_id, sequence, from_state, to_state, occurred_at) VALUES (:o, :t, 1, 'queued', 'running', now())"
            ),
            {"o": org.id, "t": task.id},
        )
        async with signed_in(ops_person) as client:
            response = await client.get(
                f"/api/v1/admin/staff/operations/trace/{task.id}"
            )
        assert response.status_code == 200
        body = response.json()
        assert [s["state"] for s in body["stages"]] == ["queued", "running"]
        assert "Mehta" not in response.text and "private" not in response.text


@pytest.mark.asyncio
async def test_sweep_expires_and_marks_unknown(db_session, async_session, staff_on):
    owner = await _user(db_session, async_session, "sw-owner", "superadmin")
    old = datetime.now(UTC) - timedelta(days=2)
    waiting = StaffCommandModel(
        command="user.suspend",
        environment=constants.ENVIRONMENT,
        target={"user_id": 1},
        reason="x",
        idempotency_key=_key(),
        state="awaiting_approval",
        requested_by=owner.id,
        requested_roles=["owner"],
        approval_required=True,
        expires_at=old,
        created_at=old,
    )
    lost = StaffCommandModel(
        command="refund.request",
        environment=constants.ENVIRONMENT,
        target={},
        reason="x",
        idempotency_key=_key(),
        state="running",
        requested_by=owner.id,
        requested_roles=["finance"],
        created_at=old,
        started_at=old,
    )
    async_session.add_all([waiting, lost])
    await async_session.flush()
    counts = await commands.sweep()
    assert counts["expired"] >= 1 and counts["unknown"] >= 1
    await async_session.refresh(waiting)
    await async_session.refresh(lost)
    assert waiting.state == "expired"
    assert lost.state == "outcome_unknown"
