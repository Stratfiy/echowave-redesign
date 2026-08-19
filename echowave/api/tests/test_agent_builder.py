"""The builder's pure parts: assembly, the daily cap, and three wire formats.

What is covered here is everything that can be wrong without a model or a
network. The vendor adapters are the highest-risk code in the feature — three
APIs that disagree about where the system prompt goes, what a tool result is,
and whether arguments arrive parsed — and every one of those differences is
pinned below rather than discovered against a live key.
"""

import json
from datetime import UTC, datetime

import pytest

from api.services.agent_builder import client as builder_client
from api.services.agent_builder.assemble import (
    RUNTIME_VARIABLES,
    AssemblyError,
    assemble,
    required_variables,
)
from api.services.agent_builder.limits import ist_day, next_ist_midnight
from api.services.agent_templates import get_template, list_templates

CLINIC = get_template("clinic_appointment")
LENDING = get_template("lending_payment_reminder")

ANSWERS = {
    "clinic_name": "Sunrise Dental",
    "doctor_names": "Dr Rao, Dr Iyer",
    "opening_hours": "Monday to Saturday, 9am to 7pm",
    "clinic_address": "12 MG Road, Bengaluru",
}


# --- assembly ---------------------------------------------------------------


def test_required_variables_come_from_the_prompts_not_the_declaration():
    """Derived from prompt text, so a template that forgot to declare one still
    gets it asked about rather than speaking literal braces to a caller."""
    needed = required_variables(CLINIC)
    assert set(needed) == set(ANSWERS)


def test_runtime_variables_are_never_asked_of_the_operator():
    """`first_name` and friends are filled by the pipeline per contact."""
    for template in list_templates():
        assert not (set(required_variables(template)) & RUNTIME_VARIABLES)


def test_answers_are_substituted_into_prompts():
    built = assemble(CLINIC, name="Sunrise front desk", variables=ANSWERS)
    start = next(n for n in built.definition["nodes"] if n["type"] == "startCall")

    assert "Sunrise Dental" in start["data"]["greeting"]
    assert "12 MG Road, Bengaluru" in start["data"]["prompt"]
    assert "{{clinic_name}}" not in json.dumps(built.definition)


def test_runtime_placeholders_survive_assembly():
    """`{{first_name}}` must reach the running agent intact — substituting it
    at build time would greet every caller by the same name."""
    built = assemble(
        LENDING,
        name="EMI reminders",
        variables={
            "lender_name": "Acme Finance",
            "payment_link_channel": "SMS",
            "grievance_number": "1800 111 222",
        },
    )
    assert "{{first_name}}" in json.dumps(built.definition)


def test_missing_answers_are_reported_rather_than_guessed():
    """The chat's cue to ask another question, not a failure."""
    built = assemble(CLINIC, name="Half built", variables={"clinic_name": "Sunrise"})

    assert set(built.missing_variables) == set(ANSWERS) - {"clinic_name"}
    # The unanswered placeholder is left visible rather than silently blanked.
    assert "{{opening_hours}}" in json.dumps(built.definition)


def test_blank_answers_count_as_unanswered():
    built = assemble(CLINIC, name="Blank", variables={**ANSWERS, "clinic_name": "   "})
    assert "clinic_name" in built.missing_variables


def test_guardrails_are_hoisted_into_a_global_node():
    """A guardrail on one node applies on one turn. Several of these are legal
    constraints for the vertical, so they have to apply on every turn."""
    built = assemble(
        LENDING,
        name="EMI reminders",
        variables={
            "lender_name": "Acme Finance",
            "payment_link_channel": "SMS",
            "grievance_number": "1800 111 222",
        },
    )

    globals_ = [n for n in built.definition["nodes"] if n["type"] == "globalNode"]
    assert len(globals_) == 1
    prompt = globals_[0]["data"]["prompt"].lower()
    assert "never disclose" in prompt
    assert "threaten" in prompt


def test_every_node_opts_into_the_global_prompt():
    """A global node nothing references applies to nothing."""
    built = assemble(CLINIC, name="Sunrise", variables=ANSWERS)
    for node in built.definition["nodes"]:
        if node["type"] != "globalNode":
            assert node["data"]["add_global_prompt"] is True


def test_recording_disclosure_is_on_by_default():
    """Spoken disclosure is a DPDP obligation on a recorded call, and every one
    of these verticals records."""
    built = assemble(CLINIC, name="Sunrise", variables=ANSWERS)
    start = next(n for n in built.definition["nodes"] if n["type"] == "startCall")
    assert start["data"]["recording_disclosure_enabled"] is True


def test_edges_resolve_to_the_ids_of_real_nodes():
    built = assemble(CLINIC, name="Sunrise", variables=ANSWERS)
    ids = {n["id"] for n in built.definition["nodes"]}
    for edge in built.definition["edges"]:
        assert edge["source"] in ids
        assert edge["target"] in ids


def test_extraction_variables_are_carried_through():
    built = assemble(CLINIC, name="Sunrise", variables=ANSWERS)
    start = next(n for n in built.definition["nodes"] if n["type"] == "startCall")
    names = {v["name"] for v in start["data"]["extraction_variables"]}
    assert "intent" in names
    assert start["data"]["extraction_enabled"] is True


def test_an_unnamed_agent_is_refused():
    with pytest.raises(AssemblyError):
        assemble(CLINIC, name="   ", variables=ANSWERS)


@pytest.mark.parametrize("template", list_templates(), ids=lambda t: t.id)
def test_every_template_assembles(template):
    """The build path must work for all of them, not only the one demoed."""
    answers = {key: f"value for {key}" for key in required_variables(template)}
    built = assemble(template, name=f"Test {template.id}", variables=answers)

    assert not built.missing_variables
    assert built.definition["nodes"]
    # Exactly one start node and one global node, whatever the template.
    assert sum(n["type"] == "startCall" for n in built.definition["nodes"]) == 1
    assert sum(n["type"] == "globalNode" for n in built.definition["nodes"]) == 1
    assert "{{" not in json.dumps(
        [n for n in built.definition["nodes"] if n["type"] != "globalNode"]
    ).replace("{{first_name}}", "").replace("{{amount_due}}", "").replace(
        "{{due_date}}", ""
    ).replace("{{loan_ref}}", "").replace("{{order_summary}}", "").replace(
        "{{order_amount}}", ""
    )


# --- the assembled workflow must be one the platform accepts ----------------


@pytest.mark.parametrize("template", list_templates(), ids=lambda t: t.id)
def test_assembled_workflow_passes_the_real_schema(template):
    """The claim the whole feature rests on: what the chat builds, runs.

    Validated against the same `ReactFlowDTO` every other creation path uses,
    so a template that produced an unopenable agent fails here rather than in
    somebody's canvas. Node types, per-type data models and edge referential
    integrity are all checked by that model.
    """
    from api.services.workflow.dto import ReactFlowDTO

    answers = {key: f"value for {key}" for key in required_variables(template)}
    built = assemble(template, name=f"Test {template.id}", variables=answers)

    dto = ReactFlowDTO.model_validate(built.definition)
    assert sum(node.type == "startCall" for node in dto.nodes) == 1
    assert any(node.type == "endCall" for node in dto.nodes)


@pytest.mark.parametrize("template", list_templates(), ids=lambda t: t.id)
def test_assembled_workflow_passes_graph_validation(template):
    """The semantic pass the canvas applies — start-node count, edge shape.

    Separate from the schema test because it reaches into the pipecat
    submodule, so it is the one that fails first in a checkout without it.
    """
    from api.services.workflow.dto import ReactFlowDTO
    from api.services.workflow.workflow_graph import WorkflowGraph

    answers = {key: f"value for {key}" for key in required_variables(template)}
    built = assemble(template, name=f"Test {template.id}", variables=answers)

    WorkflowGraph(ReactFlowDTO.model_validate(built.definition))


# --- the daily cap ----------------------------------------------------------


@pytest.mark.parametrize(
    ("utc", "expected_day"),
    [
        # 18:29 UTC is 23:59 IST — still the same day.
        ("2026-08-18T18:29:00Z", "2026-08-18"),
        # 18:30 UTC is midnight IST — the next day has begun.
        ("2026-08-18T18:30:00Z", "2026-08-19"),
        ("2026-08-18T00:00:00Z", "2026-08-18"),
    ],
)
def test_the_day_rolls_over_at_midnight_ist_not_utc(utc, expected_day):
    """An Indian account's day must not reset at 05:30 local."""
    moment = datetime.fromisoformat(utc.replace("Z", "+00:00"))
    assert ist_day(moment) == expected_day


def test_reset_time_is_the_next_ist_midnight():
    moment = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    resets = next_ist_midnight(moment)

    assert resets > moment
    # 18:30 UTC is 00:00 IST.
    assert resets.hour == 18
    assert resets.minute == 30


# --- the vendor adapters ----------------------------------------------------


def _conversation_with_a_tool_round_trip():
    conversation = builder_client.Conversation()
    conversation.add_user("I run a clinic")
    call = builder_client.ToolCall(
        id="call_1", name="list_agent_templates", arguments={"query": "clinic"}
    )
    conversation.add_assistant(
        builder_client.ModelReply(text="Let me look", tool_calls=(call,))
    )
    conversation.add_tool_result(call, {"templates": [{"id": "clinic_appointment"}]})
    return conversation, call


TOOLS = [
    {
        "name": "list_agent_templates",
        "description": "List templates",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
    }
]


def test_anthropic_puts_the_system_prompt_at_the_top_level():
    conversation, _ = _conversation_with_a_tool_round_trip()
    payload = builder_client._anthropic_request(
        model="claude", system="SYS", conversation=conversation, tools=TOOLS
    )

    assert payload["system"] == "SYS"
    assert all(m["role"] != "system" for m in payload["messages"])


def test_anthropic_returns_tool_results_as_a_user_turn():
    """The difference most likely to be got wrong when porting from OpenAI."""
    conversation, _ = _conversation_with_a_tool_round_trip()
    payload = builder_client._anthropic_request(
        model="claude", system="SYS", conversation=conversation, tools=TOOLS
    )

    last = payload["messages"][-1]
    assert last["role"] == "user"
    assert last["content"][0]["type"] == "tool_result"
    assert last["content"][0]["tool_use_id"] == "call_1"


def test_anthropic_names_the_schema_input_schema():
    payload = builder_client._anthropic_request(
        model="claude",
        system="SYS",
        conversation=builder_client.Conversation(),
        tools=TOOLS,
    )
    assert "input_schema" in payload["tools"][0]


def test_openai_puts_the_system_prompt_in_the_message_list():
    conversation, _ = _conversation_with_a_tool_round_trip()
    payload = builder_client._openai_request(
        model="gpt", system="SYS", conversation=conversation, tools=TOOLS
    )

    assert payload["messages"][0] == {"role": "system", "content": "SYS"}


def test_openai_sends_tool_arguments_back_as_a_json_string():
    """They arrive as a string and must go back as one."""
    conversation, _ = _conversation_with_a_tool_round_trip()
    payload = builder_client._openai_request(
        model="gpt", system="SYS", conversation=conversation, tools=TOOLS
    )

    assistant = next(m for m in payload["messages"] if m["role"] == "assistant")
    arguments = assistant["tool_calls"][0]["function"]["arguments"]
    assert isinstance(arguments, str)
    assert json.loads(arguments) == {"query": "clinic"}


def test_openai_uses_a_dedicated_tool_role():
    conversation, _ = _conversation_with_a_tool_round_trip()
    payload = builder_client._openai_request(
        model="gpt", system="SYS", conversation=conversation, tools=TOOLS
    )

    last = payload["messages"][-1]
    assert last["role"] == "tool"
    assert last["tool_call_id"] == "call_1"


def test_openai_survives_unparseable_tool_arguments():
    """A truncated generation produces broken JSON. Recovering with an empty
    object lets the tool reject it with something the model can act on."""
    reply = builder_client._openai_parse(
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {"name": "x", "arguments": '{"a": '},
                            }
                        ],
                    }
                }
            ]
        }
    )
    assert reply.tool_calls[0].arguments == {}


def test_gemini_names_the_system_prompt_system_instruction():
    conversation, _ = _conversation_with_a_tool_round_trip()
    payload = builder_client._gemini_request(
        system="SYS", conversation=conversation, tools=TOOLS
    )

    assert payload["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert all(c["role"] != "system" for c in payload["contents"])


def test_gemini_tool_results_are_objects_not_strings():
    """Gemini rejects a bare string as a functionResponse payload."""
    conversation = builder_client.Conversation()
    call = builder_client.ToolCall(id="c", name="t", arguments={})
    conversation.add_assistant(builder_client.ModelReply(text="", tool_calls=(call,)))
    conversation.add_tool_result(call, "a plain string")

    payload = builder_client._gemini_request(
        system="SYS", conversation=conversation, tools=TOOLS
    )
    response = payload["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert isinstance(response, dict)


def test_gemini_uses_model_not_assistant_as_the_role():
    conversation, _ = _conversation_with_a_tool_round_trip()
    payload = builder_client._gemini_request(
        system="SYS", conversation=conversation, tools=TOOLS
    )
    assert any(c["role"] == "model" for c in payload["contents"])


def test_gemini_schema_strips_keywords_it_rejects():
    payload = builder_client._gemini_request(
        system="SYS", conversation=builder_client.Conversation(), tools=TOOLS
    )
    schema = payload["tools"][0]["functionDeclarations"][0]["parameters"]
    assert "additionalProperties" not in schema
    assert schema["properties"]["query"]["type"] == "string"


def test_gemini_schema_strip_is_recursive():
    """The offending keys appear on nested properties, not only at the top."""
    nested = {
        "type": "object",
        "properties": {
            "inner": {"type": "object", "additionalProperties": False, "title": "x"}
        },
    }
    stripped = builder_client._strip_for_gemini(nested)
    assert "additionalProperties" not in stripped["properties"]["inner"]
    assert "title" not in stripped["properties"]["inner"]


def test_gemini_synthesises_a_call_id():
    """Gemini assigns none, and the loop needs one to correlate results."""
    reply = builder_client._gemini_parse(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"functionCall": {"name": "look", "args": {"q": "1"}}}
                        ]
                    }
                }
            ]
        }
    )
    assert reply.tool_calls[0].id
    assert reply.tool_calls[0].name == "look"


@pytest.mark.parametrize(
    "parse",
    [builder_client._openai_parse, builder_client._gemini_parse],
)
def test_an_empty_reply_is_an_error_not_a_silent_blank(parse):
    with pytest.raises(builder_client.BuilderClientError):
        parse({})


def test_anthropic_parses_text_and_tool_calls_together():
    reply = builder_client._anthropic_parse(
        {
            "content": [
                {"type": "text", "text": "Looking now"},
                {"type": "tool_use", "id": "t1", "name": "look", "input": {"q": "1"}},
            ]
        }
    )
    assert reply.text == "Looking now"
    assert reply.tool_calls[0].name == "look"
    assert reply.wants_tools


def test_an_unsupported_provider_is_refused_before_any_request_is_made():
    """The guard has to fire on the provider name, not on a 404 from a URL
    built out of one. Run through asyncio rather than pytest-asyncio so this
    stays a plain unit test."""
    import asyncio

    with pytest.raises(builder_client.BuilderClientError):
        asyncio.run(
            builder_client.complete(
                provider="mistral",
                model="m",
                api_key="k",
                system="s",
                conversation=builder_client.Conversation(),
                tools=[],
            )
        )
