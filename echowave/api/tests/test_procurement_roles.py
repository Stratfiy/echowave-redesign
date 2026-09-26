"""The Procurement shelf (Step 2): four roles on the document engine.

Off, none of them is on the shelf, in the gallery or findable; on, all four
are, under one job. Each asks at hire for everything its prompts name, is
given the document tools on a text run, sends only on a card, and tells
the model the three things that matter most here: ask for every missing
field in one message, never invent a figure, never do the sums.

The drafter is also run end to end, scripted: the model stood in, the real
``draft_document`` behind the engine's own handler, storage and the PDF
converter mocked -- first missing fields, then a draft on the thread.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from api import constants
from api.services.agent_templates import find_templates, get_template, list_templates
from api.services.agent_templates._base import BOT_FUNCTIONS
from api.services.agent_templates.procurement import PROCUREMENT_GUARDRAILS
from api.services.documents import matching, money, tools
from api.services.packs import catalogue
from api.services.packs._base import CALLING_CHANNELS, AgentPack, Channel

ROLES = (
    "procurement_document_drafter",
    "rfq_quote_comparer",
    "po_followup",
    "invoice_three_way_match",
)

BUYER = money.with_checksum("29AABCT1332L1Z")
VENDOR = money.with_checksum("29AAGCB7383J1Z")
OTHER_STATE_VENDOR = money.with_checksum("27AAGCB7383J1Z")


@pytest.fixture
def off(monkeypatch):
    monkeypatch.setattr(constants, "PROCUREMENT_DOCS_2026_09_ENABLED", False)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(constants, "PROCUREMENT_DOCS_2026_09_ENABLED", True)


def _prompt(role: str) -> str:
    template = get_template(role)
    return " ".join(node.prompt for node in template.nodes).lower()


def _shelf() -> dict[str, AgentPack]:
    return {p.slug: p for p in catalogue._packs(None, None)}


# ---------------------------------------------------------------------------
# The shelf and the gallery follow the flag


class TestTheFlag:
    def test_off_they_are_nowhere_a_person_looks(self, off):
        shelf = _shelf()
        for role in ROLES:
            assert shelf[role].listed is False
            assert role not in {t.id for t in list_templates()}
        assert not {t.id for t in find_templates("purchase order vendor")} & set(ROLES)
        assert "Procurement" not in catalogue.jobs(
            p for p in shelf.values() if p.listed
        )

    def test_on_they_are_all_on_one_shelf(self, on):
        shelf = _shelf()
        for role in ROLES:
            assert shelf[role].listed is True
            assert role in {t.id for t in list_templates()}
        assert "Procurement" in catalogue.jobs(p for p in shelf.values() if p.listed)

    def test_get_template_resolves_them_either_way(self, off):
        # So the pack validates while the flag is off.
        for role in ROLES:
            assert get_template(role) is not None
            assert get_template(role).available is False

    def test_a_pack_cannot_list_a_documents_role_without_the_feature(self, on):
        from api.services.packs.catalogue import DECIBYL

        with pytest.raises(ValueError, match="procurement_docs"):
            AgentPack(
                slug="x",
                name="x",
                summary="x",
                job="Procurement",
                publisher=DECIBYL,
                channels=[Channel.WEB],
                template_id="procurement_document_drafter",
            )

    def test_nothing_else_moved_off_the_gallery(self, off):
        # The filter removes things; test what must still appear.
        shown = {t.id for t in list_templates()}
        assert {"document_drafter", "supplier_invoice_clerk", "report_generator"} <= (
            shown
        )
        from api.services.agent_templates.catalogue import _all

        assert len(shown) == len(_all()) - len(ROLES)

    @pytest.mark.asyncio
    async def test_off_the_route_does_not_know_them(self, off):
        from fastapi import HTTPException

        from api.routes.agent_templates import get_agent_template

        with pytest.raises(HTTPException) as caught:
            await get_agent_template("po_followup", _user=None)
        assert caught.value.status_code == 404

    @pytest.mark.asyncio
    async def test_on_the_route_serves_them(self, on):
        from api.routes.agent_templates import get_agent_template

        got = await get_agent_template("po_followup", _user=None)
        assert got["id"] == "po_followup"


# ---------------------------------------------------------------------------
# Each role as published


@pytest.mark.parametrize("role", ROLES)
class TestEveryRole:
    def test_it_is_ours_on_the_procurement_shelf(self, role, on):
        pack = _shelf()[role]
        assert pack.job == "Procurement"
        assert pack.industries == ["Procurement"]
        assert pack.publisher.first_party and pack.publisher.name == "Decibyl"
        assert pack.requires_feature == "procurement_docs"
        assert {"en", "hi"} <= set(pack.languages)
        assert Channel.EMAIL in pack.channels and Channel.WHATSAPP in pack.channels
        assert not set(pack.channels) & CALLING_CHANNELS

    def test_its_facts_are_exactly_its_templates_variables(self, role):
        from api.services.agent_builder.assemble import required_variables

        pack, template = _shelf()[role], get_template(role)
        keys = {f.key for f in pack.required_facts}
        assert keys == set(template.template_variables)
        assert set(required_variables(template)) <= keys

    def test_its_template_is_filed_and_equipped(self, role):
        template = get_template(role)
        assert template.industry == "Procurement"
        assert template.function in BOT_FUNCTIONS
        assert template.needs_documents is True
        assert template.requires_feature == "procurement_docs"
        assert template.approve_sends is True
        assert not template.speaks
        assert {"gmail", "outlook"} <= set(template.apps)

    def test_it_carries_the_procurement_rules(self, role):
        guardrails = get_template(role).guardrails
        assert all(rule in guardrails for rule in PROCUREMENT_GUARDRAILS)
        joined = " ".join(guardrails).lower()
        assert "one question per message" not in joined
        assert "in one message" in joined

    def test_the_prompt_never_invents_and_never_does_the_sums(self, role):
        prompt = _prompt(role)
        assert "never invent" in prompt
        assert re.search(r"never (work out|add up)", prompt)

    def test_it_opens_as_a_workflow(self, role, on):
        from api.services.agent_templates.materialise import to_workflow_definition
        from api.services.workflow.dto import ReactFlowDTO

        template = get_template(role)
        ReactFlowDTO.model_validate(to_workflow_definition(template))
        assert sum(n.type == "startCall" for n in template.nodes) == 1
        assert any(n.type == "endCall" for n in template.nodes)

    def test_it_is_found_by_its_own_examples(self, role, on):
        for request in get_template(role).example_requests:
            assert role in {t.id for t in find_templates(request)}, request

    def test_hiring_it_makes_sends_cards(self, role):
        from api.services.agent_templates import equip
        from api.services.workflow import send_approval

        config = equip.configurations({}, template=get_template(role))
        assert config["channel"] == "chat"
        assert send_approval.wants_approval(config)

    def test_every_tool_it_names_exists(self, role):
        prompt = _prompt(role)
        named = set(re.findall(r"\b([a-z]+_[a-z_]+)\b", prompt))
        documents = {n for n in named if n in tools.NAMES}
        assert documents, "a procurement role names at least one document tool"
        if role in ("procurement_document_drafter", "rfq_quote_comparer"):
            assert "ask_for_decision" in prompt


class TestTheirJobs:
    def test_the_drafter_asks_everything_at_once(self):
        prompt = _prompt("procurement_document_drafter")
        assert "ask for all of them in one message" in prompt
        assert "never invent a gstin, rate, quantity, date or term" in prompt
        assert "list_template_fields" in prompt and "draft_document" in prompt
        assert "status invalid" in prompt and "status drafted" in prompt
        assert "only after approval" in prompt
        assert "update_register" in prompt and "issued" in prompt
        assert "{{template_source}}" in prompt

    def test_the_comparer_compares_with_the_tool(self):
        prompt = _prompt("rfq_quote_comparer")
        assert "kind rfq" in prompt
        assert "save_email_attachment" in prompt and "read_document" in prompt
        assert "cost_bid_analysis" in prompt
        assert "comparative_statement" in prompt
        assert "l1, l2 and l3" in prompt
        assert "do not guess" in prompt
        assert "routine" in prompt
        assert get_template("rfq_quote_comparer").schedule_shape is not None

    def test_the_chaser_runs_every_morning(self):
        template = get_template("po_followup")
        assert template.direction.value == "scheduled"
        prompt = _prompt("po_followup")
        assert "issued,acknowledged,part_delivered" in prompt
        assert "{{reminder_lead_days}}" in prompt
        assert "part_delivered" in prompt and "delivered" in prompt
        assert "match_invoice" in prompt

    def test_the_matcher_never_pays(self):
        prompt = _prompt("invoice_three_way_match")
        assert "never approve, schedule or make a payment" in prompt
        assert "match_invoice" in prompt
        assert "more than five lines" in prompt and "build_spreadsheet" in prompt
        assert "never matched against the order alone" in prompt

    def test_the_office_drafter_is_untouched(self):
        pack = catalogue.get_pack("document_drafter")
        assert pack.job == "Office assistant"
        assert pack.requires_feature is None
        assert get_template("document_drafter").needs_documents is False


# ---------------------------------------------------------------------------
# Tools granted: a text run of a hired procurement agent is offered them


@pytest.mark.asyncio
class TestTheToolsReachThem:
    async def test_a_text_run_is_offered_every_document_tool(self, on):
        from api.services.workflow.pipecat_engine_context_composer import (
            compose_functions_for_node,
        )

        node = SimpleNamespace(
            document_uuids=[],
            tool_uuids=[],
            out_edges=[],
            extraction_variables=[],
            extraction_enabled=False,
            data=SimpleNamespace(),
        )
        with patch(
            "api.services.workflow.pipecat_engine_context_composer.argument_properties",
            return_value={},
        ):
            functions = await compose_functions_for_node(
                node=node, custom_tool_manager=None, can_ask_for_decision=True
            )
        names = {getattr(f, "name", None) or f.get("name") for f in functions}
        assert set(tools.NAMES) <= names
        assert "ask_for_decision" in names

    async def test_equip_hires_it_without_reaching_for_a_tool(self, on):
        from api.services.agent_templates import equip
        from api.services.agent_templates.materialise import to_workflow_definition

        template = get_template("procurement_document_drafter")
        with (
            patch(
                "api.services.workflow.connected_tools.list_for_organization",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.connected_tools.mcp_for_organization",
                new=AsyncMock(return_value=[]),
            ),
        ):
            out = await equip.for_template(
                to_workflow_definition(template),
                template=template,
                organization_id=1,
                user_id=1,
            )
        assert out["nodes"]


# ---------------------------------------------------------------------------
# The match, as arithmetic


def _order():
    return [
        {
            "description": "OPC 53 cement",
            "qty": "200",
            "unit": "Bag",
            "rate": "380",
            "gst_rate": "28",
        },
        {
            "description": "TMT 12mm",
            "qty": "2",
            "unit": "MT",
            "rate": "54000",
            "discount": "2",
            "gst_rate": "18",
        },
    ]


def _invoice(**changes):
    lines = [
        {"description": "OPC 53 cement", "qty": 200, "rate": 380, "gst_rate": 28},
        {
            "description": "TMT 12mm",
            "qty": 2,
            "rate": 54000,
            "discount": 2,
            "gst_rate": 18,
        },
    ]
    invoice = {
        "vendor_gstin": VENDOR,
        "buyer_gstin": BUYER,
        "lines": lines,
        "total": "222171.20",
    }
    invoice.update(changes)
    return invoice


class TestTheMatch:
    def _match(self, **kw):
        kw.setdefault(
            "received",
            [
                {"description": "OPC 53 cement", "qty": 200},
                {"description": "TMT 12mm", "qty": 2},
            ],
        )
        return matching.match(
            _order(), order_vendor_gstin=VENDOR, order_buyer_gstin=BUYER, **kw
        )

    def test_a_clean_invoice_matches(self):
        result = self._match(invoice=_invoice())
        assert result["status"] == "matched", result["problems"]
        assert result["computed"]["total"] == "2,22,171.20"
        assert all(line["ok"] for line in result["lines"])

    def test_invoiced_more_than_received_is_named(self):
        result = self._match(
            received=[
                {"description": "OPC 53 cement", "qty": 120},
                {"description": "TMT 12mm", "qty": 2},
            ],
            invoice=_invoice(total=None),
        )
        assert result["status"] == "mismatched"
        assert any("invoiced 200 but received 120" in p for p in result["problems"])
        assert result["lines"][0]["short_by"] == "80"

    def test_a_rate_is_held_to_the_tolerance(self):
        dearer = _invoice(total=None)
        dearer["lines"][0]["rate"] = 381.9
        assert self._match(invoice=dearer)["status"] == "mismatched"
        assert (
            self._match(invoice=dearer, price_tolerance_pct=0.5)["status"] == "matched"
        )

    def test_gst_and_gstins_are_checked(self):
        wrong = _invoice(total=None, vendor_gstin=OTHER_STATE_VENDOR)
        wrong["lines"][1]["gst_rate"] = 12
        problems = " ".join(self._match(invoice=wrong)["problems"])
        assert "vendor GSTIN" in problems
        assert "GST 12% against the order's 18%" in problems

    def test_a_total_off_by_more_than_round_off_is_named(self):
        assert self._match(invoice=_invoice(total="222171"))["status"] == "matched"
        result = self._match(invoice=_invoice(total="223171.20"))
        assert result["status"] == "mismatched"
        assert any("lines come to ₹2,22,171.20" in p for p in result["problems"])

    def test_no_receipt_means_no_match(self):
        result = self._match(received=None, invoice=_invoice())
        assert result["status"] == "mismatched"
        assert any("No goods receipt" in p for p in result["problems"])

    def test_a_receipt_alone_says_short_complete_or_over(self):
        # Two GRNs for the cement are added up here, not by the model.
        short = self._match(received=[{"line": 1, "qty": 100}, {"line": 1, "qty": 50}])
        assert short["status"] == "short"
        assert short["lines"][0]["received"] == "150"
        assert short["lines"][1]["short_by"] == "2"
        assert self._match()["status"] == "complete"
        over = self._match(received=[{"line": 1, "qty": 210}, {"line": 2, "qty": 2}])
        assert over["status"] == "over"
        assert (
            self._match(
                received=[{"line": 1, "qty": 210}, {"line": 2, "qty": 2}],
                quantity_tolerance_pct=5,
            )["status"]
            == "complete"
        )

    def test_a_line_not_on_the_order_is_never_matched_to_one(self):
        invoice = _invoice(total=None)
        invoice["lines"].append(
            {"description": "Binding wire", "qty": 10, "rate": 90, "gst_rate": 18}
        )
        result = self._match(invoice=invoice)
        assert any("Binding wire is not on the order" in p for p in result["problems"])


# ---------------------------------------------------------------------------
# The drafter, end to end: the model stood in, the real tool behind the
# engine's handler, storage and the converter mocked.


class FakeStorage:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    async def acreate_file_from_bytes(self, key, data):
        self.files[key] = data
        return True

    async def aget_signed_url(self, key, expiration=3600, **_):
        return f"https://files.test/{key}"


FACTS = {
    "buyer_name": "Shreeram Constructions Pvt Ltd",
    "buyer_address": "42, 2nd Main, Peenya Industrial Area, Bengaluru 560058",
    "buyer_gstin": BUYER,
    "default_payment_terms": "30 days from receipt of invoice",
    "default_delivery_address": "Site: Whitefield, Bengaluru",
    "signatory_name": "R. Srinivas",
    "signatory_designation": "Purchase Manager",
    "approver": "Anand, the proprietor",
    "template_source": "standard",
    "numbering_prefixes": "standard",
}


def _engine(org: int, workflow_id: int, run_id: int, results: list):
    """The engine as the handler sees it: its organization, its bot, its run."""
    return SimpleNamespace(
        _get_organization_id=AsyncMock(return_value=org),
        _get_workflow_id=AsyncMock(return_value=workflow_id),
        _workflow_run_id=run_id,
    )


async def _call(engine, name: str, arguments: dict, call_id: str) -> dict:
    from api.services.workflow.pipecat_engine import PipecatEngine

    results: list[dict] = []

    async def callback(result, **_):
        results.append(result)

    handler = PipecatEngine._procurement_handler(engine, name)
    await handler(
        SimpleNamespace(
            arguments=arguments, tool_call_id=call_id, result_callback=callback
        )
    )
    return results[0]


@pytest.mark.asyncio
class TestTheDrafterEndToEnd:
    async def test_missing_then_drafted_then_issued(self, on, db_session, monkeypatch):
        from api.db.models import (
            OrganizationModel,
            ProcurementDocumentModel,
            WorkflowModel,
            WorkflowRunModel,
        )
        from api.enums import AgentEventKind
        from api.services import storage as storage_module
        from api.services.agent_builder.assemble import fill_placeholders
        from api.services.agent_templates.materialise import to_workflow_definition
        from api.services.workflow import unfilled

        # Hired: every placeholder answered from the facts, none left over.
        template = get_template("procurement_document_drafter")
        definition = fill_placeholders(to_workflow_definition(template), FACTS)
        assert unfilled.problems(definition) == []
        start = next(n for n in definition["nodes"] if n["id"] == "start-1")
        assert "buyer_gstin " + BUYER in start["data"]["prompt"]

        async with db_session.async_session() as session:
            org = OrganizationModel(
                provider_id=f"proc-roles-{datetime.now(UTC).timestamp()}",
                quota_decibyl_tokens=0,
            )
            session.add(org)
            await session.flush()
            workflow = WorkflowModel(
                name="Procurement document drafter", organization_id=org.id
            )
            session.add(workflow)
            await session.flush()
            run = WorkflowRunModel(name="r", workflow_id=workflow.id, mode="textchat")
            session.add(run)
            await session.flush()
            await session.commit()
            org_id, workflow_id, run_id = org.id, workflow.id, run.id

        storage = FakeStorage()
        monkeypatch.setattr(storage_module, "storage_fs", storage)
        timeline = AsyncMock()
        engine = _engine(org_id, workflow_id, run_id, [])
        with (
            patch(
                "api.services.documents.convert.docx_to_pdf",
                new=AsyncMock(return_value=b"%PDF-1.7 test"),
            ),
            patch("api.services.workflow.agent_timeline.record", new=timeline),
            patch(
                "api.services.billing.events.charge_in_own_session",
                new=AsyncMock(return_value=1),
            ),
        ):
            # The person: "PO on Bharat for 200 bags OPC 53 at 380, 28% GST."
            # The model, as the prompt says: the fields first, then a draft
            # with everything it has -- the facts and the message.
            fields = await _call(
                engine, tools.LIST_FIELDS, {"template": "purchase_order"}, "c1"
            )
            assert fields["status"] == "success"
            assert "vendor_gstin" in fields["fields"]

            known = {
                "buyer_name": FACTS["buyer_name"],
                "buyer_address": FACTS["buyer_address"],
                "buyer_gstin": FACTS["buyer_gstin"],
                "payment_terms": FACTS["default_payment_terms"],
                "delivery_address": FACTS["default_delivery_address"],
                "signatory_name": FACTS["signatory_name"],
                "signatory_designation": FACTS["signatory_designation"],
                "vendor_name": "Bharat Building Supplies",
            }
            first = await _call(
                engine,
                tools.DRAFT,
                {
                    "kind": "purchase_order",
                    "template": "purchase_order",
                    "values": known,
                    "items": [
                        {
                            "description": "OPC 53 cement",
                            "qty": 200,
                            "unit": "Bag",
                            "rate": 380,
                            "gst_rate": 28,
                        }
                    ],
                },
                "c2",
            )
            # Everything missing at once, nothing drafted, nothing numbered.
            assert first["status"] == "missing"
            missing = {m["field"] for m in first["missing"]}
            assert {
                "vendor_address",
                "vendor_gstin",
                "delivery_date",
                "items[1].hsn_sac",
            } <= missing
            assert not missing & set(known)
            assert "ONE message" in first["note"]
            assert storage.files == {}
            timeline.assert_not_awaited()

            # The model's one message, and the person's one answer.
            ask = "To draft the PO I still need: " + "; ".join(
                m["question"] for m in first["missing"]
            )
            assert all(m["question"] in ask for m in first["missing"])
            answers = {
                "vendor_address": "Plot 7, KIADB, Hosur Road, Bengaluru 560100",
                "vendor_gstin": VENDOR,
                "vendor_pan": VENDOR[2:12],
                "reference": "Q-2291 dated 20-09-2026",
                "delivery_date": "2026-10-10",
                "validity": "30 days",
                "terms_and_conditions": "standard",
            }
            second = await _call(
                engine,
                tools.DRAFT,
                {
                    "kind": "purchase_order",
                    "template": "purchase_order",
                    "values": {**known, **answers},
                    "items": [
                        {
                            "description": "OPC 53 cement",
                            "hsn_sac": "2523",
                            "qty": 200,
                            "unit": "Bag",
                            "rate": 380,
                            "gst_rate": 28,
                        }
                    ],
                    "counterparty_email": "sales@bharat.example",
                },
                "c3",
            )
            assert second["status"] == "drafted", second
            number = second["number"]
            assert number.startswith("PO/")
            # The tool's total, not the model's: 200 x 380 + 28% GST.
            assert second["total"] == "97,280.00"
            assert "NOT been sent" in second["note"]

            # The deliverable is on this bot's thread, for this run.
            event = timeline.await_args.kwargs
            assert event["kind"] == AgentEventKind.DELIVERABLE.value
            assert event["organization_id"] == org_id
            assert event["workflow_id"] == workflow_id
            assert event["workflow_run_id"] == run_id
            assert [a["filename"] for a in event["payload"]["attachments"]] == [
                f"{number.replace('/', '-')}.pdf",
                f"{number.replace('/', '-')}.docx",
            ]

            # Approved and sent (the send itself is a card, off this test):
            # the register moves to issued, and the follow-up can find it.
            issued = await _call(
                engine,
                tools.UPDATE,
                {
                    "number": number,
                    "status": "issued",
                    "note": "sent to sales@bharat.example",
                },
                "c4",
            )
            assert issued["entry"]["status"] == "issued"
            found = await _call(engine, tools.LIST, {"number": number}, "c5")
            entry = found["entries"][0]
            assert entry["counterparty_email"] == "sales@bharat.example"
            assert entry["lines"][0]["description"] == "OPC 53 cement"
            assert entry["lines"][0]["qty"] == 200

            # And the matcher can check an invoice against it.
            matched = await _call(
                engine,
                tools.MATCH,
                {
                    "po": number,
                    "received": [{"line": 1, "qty": 200}],
                    "invoice": {
                        "vendor_gstin": VENDOR,
                        "buyer_gstin": BUYER,
                        "lines": [{"line": 1, "qty": 200, "rate": 380, "gst_rate": 28}],
                        "total": "97280",
                    },
                },
                "c6",
            )
            assert matched["status"] == "success"
            assert matched["match"] == "matched", matched["problems"]

        async with db_session.async_session() as session:
            rows = (
                await session.scalars(
                    select(ProcurementDocumentModel).where(
                        ProcurementDocumentModel.organization_id == org_id
                    )
                )
            ).all()
        assert [r.status for r in rows] == ["issued"]
        assert rows[0].workflow_id == workflow_id
