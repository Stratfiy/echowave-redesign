"""OP-6: the marketplace pack for Prospecting, and the replay that measures a
run off the rows the platform keeps.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.db.models import (
    AgentEventModel,
    CallCostItemModel,
    ContactListModel,
    ContactModel,
    CreditLedgerModel,
    DataLookupCostModel,
    OrganizationModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import AgentEventActor, AgentEventKind, CreditLedgerKind
from api.services.billing import replay
from api.services.billing.credits import PAISE_PER_CREDIT
from api.services.packs import get_pack
from api.services.packs._base import Channel


class TestThePack:
    def test_it_is_on_the_shelf_for_everybody_who_sells_to_businesses(self):
        pack = get_pack("outbound_prospecting")
        assert pack is not None and pack.listed
        assert pack.template_id == "outbound_prospecting"
        assert pack.industries == []
        assert Channel.SCHEDULED in pack.channels and Channel.EMAIL in pack.channels
        assert not (set(pack.channels) & {Channel.INBOUND_CALL, Channel.OUTBOUND_CALL})
        assert pack.demo_number is None and pack.creator_price_paise == 0

    def test_its_facts_are_the_templates_variables(self):
        from api.services.agent_templates import get_template

        pack = get_pack("outbound_prospecting")
        template = get_template("outbound_prospecting")
        assert {f.key for f in pack.required_facts} == set(template.template_variables)

    def test_it_asks_for_a_mailbox_without_gating_on_one_vendor(self):
        pack = get_pack("outbound_prospecting")
        apps = {c.app: c for c in pack.required_connectors}
        assert set(apps) == {"gmail", "outlook"}
        assert not any(c.required for c in apps.values())


async def _rows(session, *, when):
    org = OrganizationModel(provider_id="org-replay", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    wf = WorkflowModel(name="Prospecting", organization_id=org.id)
    session.add(wf)
    await session.flush()
    run = WorkflowRunModel(
        name="r", workflow_id=wf.id, mode="textchat", created_at=when
    )
    session.add(run)
    await session.flush()
    lst = ContactListModel(organization_id=org.id, name="Prospects")
    other = ContactListModel(organization_id=org.id, name="Customers")
    session.add_all([lst, other])
    await session.flush()

    def ledger(ref_type, paise, ref):
        return CreditLedgerModel(
            organization_id=org.id,
            workflow_id=wf.id,
            delta_paise=-paise,
            kind=CreditLedgerKind.USAGE.value,
            ref_type=ref_type,
            ref_id=ref,
            balance_after_paise=0,
            created_at=when,
        )

    session.add_all(
        [
            ledger("tool_call", 3 * PAISE_PER_CREDIT, "a"),
            ledger("text_reply", 4 * PAISE_PER_CREDIT, "b"),
            ledger("routine_run", 2 * PAISE_PER_CREDIT, "c"),
            ledger("data_lookup", 20, "d"),
            # Outside the window: not this replay's.
            CreditLedgerModel(
                organization_id=org.id,
                workflow_id=wf.id,
                delta_paise=-1000,
                kind=CreditLedgerKind.USAGE.value,
                ref_type="tool_call",
                ref_id="old",
                balance_after_paise=0,
                created_at=when - timedelta(days=30),
            ),
            # A top-up is not usage.
            CreditLedgerModel(
                organization_id=org.id,
                workflow_id=None,
                delta_paise=50_000,
                kind=CreditLedgerKind.TOPUP.value,
                ref_type="payment",
                ref_id="p",
                balance_after_paise=0,
                created_at=when,
            ),
            DataLookupCostModel(
                organization_id=org.id,
                workflow_id=wf.id,
                provider="serper",
                kind="search",
                requests=3,
                vendor_cost_paise=29,
                charged_paise=29,
                ref_id="d",
                created_at=when,
            ),
            CallCostItemModel(
                workflow_run_id=run.id,
                component="llm",
                provider="google",
                model="gemini",
                units=1000,
                unit_rate_mpaise=1,
                cost_paise=12,
                provider_cost_paise=9,
            ),
            ContactModel(
                organization_id=org.id,
                contact_list_id=lst.id,
                email="a@x.example",
                email_normalized="a@x.example",
                created_at=when,
            ),
            ContactModel(
                organization_id=org.id,
                contact_list_id=lst.id,
                email="b@x.example",
                email_normalized="b@x.example",
                created_at=when,
            ),
            ContactModel(
                organization_id=org.id,
                contact_list_id=other.id,
                email="c@x.example",
                email_normalized="c@x.example",
                created_at=when,
            ),
        ]
    )

    def card(state):
        return AgentEventModel(
            organization_id=org.id,
            workflow_id=wf.id,
            workflow_run_id=run.id,
            kind=AgentEventKind.ACTION_PROPOSED.value,
            actor=AgentEventActor.AGENT.value,
            summary="Send email via gmail",
            payload={"action": "run_tool", "state": state, "args": {}},
            visibility="always",
            at=when,
        )

    session.add_all([card("done"), card("done"), card("declined"), card("proposed")])
    session.add(
        AgentEventModel(
            organization_id=org.id,
            workflow_id=wf.id,
            kind=AgentEventKind.ACTION_PROPOSED.value,
            actor=AgentEventActor.AGENT.value,
            summary="Turn agent off",
            payload={"action": "turn_bot_off", "state": "done"},
            visibility="always",
            at=when,
        )
    )
    await session.flush()
    return org.id, wf.id


class TestTheReplay:
    async def test_it_sums_the_rows_and_only_the_rows(self, async_session):
        when = datetime.now(UTC) - timedelta(hours=1)
        org_id, wf_id = await _rows(async_session, when=when)
        out = await replay.measure(
            async_session,
            organization_id=org_id,
            workflow_id=wf_id,
            start=when - timedelta(days=1),
            end=when + timedelta(days=1),
        )
        d = out.as_dict()
        assert d["runs"] == 1
        assert d["prospects_found"] == 2
        assert d["emails"] == {"proposed": 4, "confirmed": 2, "declined": 1}
        assert d["charged"]["by_kind"] == {
            "data_lookup": 20,
            "routine_run": 100,
            "text_reply": 200,
            "tool_call": 150,
        }
        assert d["charged"]["credits"] == 9.4
        assert d["charged"]["per_lead_credits"] == 4.7
        assert d["vendor"]["by_kind"] == {"llm": 9, "serper search": 29}
        assert d["vendor"]["lookups"] == 3
        assert d["margin"]["paise"] == 470 - 38
        assert d["margin"]["share"] == round(432 / 470, 3)

    async def test_nothing_in_the_window_is_zero_and_said(self, async_session):
        when = datetime.now(UTC)
        org_id, wf_id = await _rows(async_session, when=when - timedelta(days=10))
        out = await replay.measure(
            async_session,
            organization_id=org_id,
            workflow_id=wf_id,
            start=when - timedelta(days=1),
            end=when,
        )
        assert out.credits == 0 and out.prospects_found == 0
        assert out.as_dict()["charged"]["per_lead_credits"] is None
        assert "| Credits per lead | — |" in out.as_markdown()

    async def test_the_markdown_carries_the_plan_figures(self, async_session):
        when = datetime.now(UTC) - timedelta(hours=1)
        org_id, wf_id = await _rows(async_session, when=when)
        out = await replay.measure(
            async_session,
            organization_id=org_id,
            workflow_id=wf_id,
            start=when - timedelta(days=1),
            end=when + timedelta(days=1),
        )
        text = out.as_markdown()
        assert "| Prospects found | 2 |" in text
        assert "| Credits per lead | 4.7 |" in text
        assert "- serper search: 0.29" in text
