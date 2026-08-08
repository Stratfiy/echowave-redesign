"""The starter workflow is what a self-hosted user gets when MPS is absent.

Two things matter and are tested here: the definition it builds actually
validates (an invalid one would trade a 500 on create for a broken editor), and
the fallback fires on absence but not on a real rejection.
"""

import httpx
import pytest

from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.template_generation import (
    GLOBAL_PROMPT,
    build_starter_workflow,
    generate_workflow_definition,
)
from api.services.workflow.workflow_graph import WorkflowGraph


class TestBuildStarterWorkflow:
    def test_definition_passes_dto_validation(self):
        data = build_starter_workflow(
            "INBOUND", "Missed Call Callback", "Call them back."
        )
        ReactFlowDTO.model_validate(data["workflow_definition"])

    def test_definition_passes_graph_validation(self):
        data = build_starter_workflow("INBOUND", "Support", "Help the caller.")
        dto = ReactFlowDTO.model_validate(data["workflow_definition"])
        WorkflowGraph(dto)  # raises ValueError with a list of errors if invalid

    def test_outbound_definition_is_also_valid(self):
        data = build_starter_workflow(
            "OUTBOUND", "Payment Recovery", "Chase the payment."
        )
        dto = ReactFlowDTO.model_validate(data["workflow_definition"])
        WorkflowGraph(dto)

    def test_activity_description_becomes_the_agent_prompt(self):
        activity = "Ask which branch they visited and whether the issue is resolved."
        data = build_starter_workflow("INBOUND", "Feedback", activity)
        agent = next(
            n for n in data["workflow_definition"]["nodes"] if n["type"] == "agentNode"
        )
        assert agent["data"]["prompt"] == activity

    def test_use_case_names_the_workflow(self):
        data = build_starter_workflow("INBOUND", "Lead Qualification", "Qualify them.")
        assert data["name"] == "Lead Qualification - Inbound"

    def test_outbound_is_named_outbound(self):
        data = build_starter_workflow("OUTBOUND", "Lead Qualification", "Qualify them.")
        assert data["name"] == "Lead Qualification - Outbound"

    def test_outbound_greeting_says_why_it_is_calling(self):
        data = build_starter_workflow("OUTBOUND", "Renewals", "Ask about renewal.")
        start = next(
            n for n in data["workflow_definition"]["nodes"] if n["type"] == "startCall"
        )
        greeting = start["data"]["greeting"]
        # An outbound agent must name its reason and check it is a good time —
        # a cold call that opens with "how can I help you" is a wrong number.
        assert "Renewals" in greeting
        assert "good time" in greeting.lower()

    def test_inbound_greeting_asks_what_the_caller_wants(self):
        data = build_starter_workflow("INBOUND", "Support", "Help them.")
        start = next(
            n for n in data["workflow_definition"]["nodes"] if n["type"] == "startCall"
        )
        assert "help you" in start["data"]["greeting"].lower()

    def test_inbound_and_outbound_open_differently(self):
        inbound = build_starter_workflow("INBOUND", "Support", "Help them.")
        outbound = build_starter_workflow("OUTBOUND", "Support", "Help them.")

        def greeting_of(data):
            return next(
                n
                for n in data["workflow_definition"]["nodes"]
                if n["type"] == "startCall"
            )["data"]["greeting"]

        assert greeting_of(inbound) != greeting_of(outbound)

    def test_unknown_call_type_still_builds_a_valid_workflow(self):
        # An unrecognised call_type must not raise on the create path.
        data = build_starter_workflow("SIDEWAYS", "Support", "Help them.")
        dto = ReactFlowDTO.model_validate(data["workflow_definition"])
        WorkflowGraph(dto)

    def test_blank_inputs_still_build_a_valid_workflow(self):
        data = build_starter_workflow("INBOUND", "   ", "")
        dto = ReactFlowDTO.model_validate(data["workflow_definition"])
        WorkflowGraph(dto)
        agent = next(
            n for n in data["workflow_definition"]["nodes"] if n["type"] == "agentNode"
        )
        assert agent["data"]["prompt"].strip()

    def test_long_use_case_does_not_overflow_the_node_name(self):
        data = build_starter_workflow("INBOUND", "x" * 500, "Help them.")
        agent = next(
            n for n in data["workflow_definition"]["nodes"] if n["type"] == "agentNode"
        )
        assert len(agent["data"]["name"]) <= 60

    def test_persona_does_not_hardcode_a_language(self):
        # The platform supports eleven Indian languages plus auto-detect; naming
        # one here would silently pin every agent created this way to it.
        lowered = GLOBAL_PROMPT.lower()
        for language in ("telugu", "hindi", "tamil", "english", "kannada", "marathi"):
            assert language not in lowered

    def test_edges_connect_start_to_agent_to_end(self):
        data = build_starter_workflow("INBOUND", "Support", "Help them.")
        edges = data["workflow_definition"]["edges"]
        pairs = {(e["source"], e["target"]) for e in edges}
        assert pairs == {("start-1", "agent-1"), ("agent-1", "end-1")}

    def test_every_edge_carries_a_label_and_condition(self):
        data = build_starter_workflow("INBOUND", "Support", "Help them.")
        for edge in data["workflow_definition"]["edges"]:
            assert edge["data"]["label"].strip()
            assert edge["data"]["condition"].strip()


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.mark.asyncio
class TestGenerateWorkflowDefinition:
    async def test_uses_mps_when_it_answers(self, monkeypatch):
        generated = {
            "name": "From MPS",
            "workflow_definition": {"nodes": [], "edges": []},
        }

        async def fake_call(**kwargs):
            return generated

        monkeypatch.setattr(
            "api.services.workflow.template_generation.mps_service_key_client.call_workflow_api",
            fake_call,
        )
        monkeypatch.setattr(
            "api.services.workflow.template_generation.mps_is_configured", lambda: True
        )

        result = await generate_workflow_definition("INBOUND", "Support", "Help them.")
        assert result is generated

    async def test_falls_back_when_mps_is_not_configured(self, monkeypatch):
        monkeypatch.setattr(
            "api.services.workflow.template_generation.mps_is_configured", lambda: False
        )

        async def explode(**kwargs):
            raise AssertionError("MPS must not be called when it is not configured")

        monkeypatch.setattr(
            "api.services.workflow.template_generation.mps_service_key_client.call_workflow_api",
            explode,
        )

        result = await generate_workflow_definition("INBOUND", "Support", "Help them.")
        assert result["name"] == "Support - Inbound"

    async def test_falls_back_on_dns_failure(self, monkeypatch):
        # This is the production symptom: services.decibyl.ai does not resolve.
        async def fake_call(**kwargs):
            raise httpx.ConnectError("[Errno -2] Name or service not known")

        monkeypatch.setattr(
            "api.services.workflow.template_generation.mps_service_key_client.call_workflow_api",
            fake_call,
        )
        monkeypatch.setattr(
            "api.services.workflow.template_generation.mps_is_configured", lambda: True
        )

        result = await generate_workflow_definition("INBOUND", "Support", "Help them.")
        ReactFlowDTO.model_validate(result["workflow_definition"])

    async def test_falls_back_on_timeout(self, monkeypatch):
        async def fake_call(**kwargs):
            raise httpx.ReadTimeout("timed out")

        monkeypatch.setattr(
            "api.services.workflow.template_generation.mps_service_key_client.call_workflow_api",
            fake_call,
        )
        monkeypatch.setattr(
            "api.services.workflow.template_generation.mps_is_configured", lambda: True
        )

        result = await generate_workflow_definition("INBOUND", "Support", "Help them.")
        assert result["name"] == "Support - Inbound"

    @pytest.mark.parametrize("status", [404, 502, 503])
    async def test_falls_back_when_mps_is_absent_or_down(self, monkeypatch, status):
        async def fake_call(**kwargs):
            raise httpx.HTTPStatusError("nope", request=None, response=_Resp(status))

        monkeypatch.setattr(
            "api.services.workflow.template_generation.mps_service_key_client.call_workflow_api",
            fake_call,
        )
        monkeypatch.setattr(
            "api.services.workflow.template_generation.mps_is_configured", lambda: True
        )

        result = await generate_workflow_definition("INBOUND", "Support", "Help them.")
        assert result["name"] == "Support - Inbound"

    @pytest.mark.parametrize("status", [400, 401, 403, 422])
    async def test_surfaces_a_real_rejection(self, monkeypatch, status):
        # A wrong secret key must not degrade into a starter workflow forever —
        # the operator would never find out why every agent looks the same.
        async def fake_call(**kwargs):
            raise httpx.HTTPStatusError("nope", request=None, response=_Resp(status))

        monkeypatch.setattr(
            "api.services.workflow.template_generation.mps_service_key_client.call_workflow_api",
            fake_call,
        )
        monkeypatch.setattr(
            "api.services.workflow.template_generation.mps_is_configured", lambda: True
        )

        with pytest.raises(httpx.HTTPStatusError):
            await generate_workflow_definition("INBOUND", "Support", "Help them.")
