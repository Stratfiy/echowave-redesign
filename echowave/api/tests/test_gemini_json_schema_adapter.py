from unittest.mock import patch

from google.genai.types import GenerateContentConfig, LiveConnectConfig
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.gemini_json_schema_adapter import (
    DecibylGeminiJSONSchemaAdapter,
)
from api.services.pipecat.realtime.gemini_live import DecibylGeminiLiveLLMService
from api.services.pipecat.realtime.gemini_live_vertex import (
    DecibylGeminiLiveVertexLLMService,
)
from api.services.pipecat.service_factory import (
    DecibylGoogleLLMService,
    DecibylGoogleVertexLLMService,
    create_llm_service_from_provider,
)


def test_gemini_tools_use_json_schema_parameters_for_external_schemas():
    function_schema = FunctionSchema(
        name="customer_lookup",
        description="Look up a customer by email.",
        properties={
            "customerEmail": {
                "description": "Customer email address",
                "anyOf": [
                    {"anyOf": [{"not": {}}]},
                    {"const": ""},
                ],
            },
            "metadata": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        },
        required=["customerEmail"],
    )

    tools = DecibylGeminiJSONSchemaAdapter().to_provider_tools_format(
        ToolsSchema(standard_tools=[function_schema])
    )

    declaration = tools[0]["function_declarations"][0]
    assert "parameters" not in declaration
    assert (
        declaration["parameters_json_schema"]["properties"]["customerEmail"]["anyOf"][
            0
        ]["anyOf"][0]["not"]
        == {}
    )
    assert (
        declaration["parameters_json_schema"]["properties"]["customerEmail"]["anyOf"][
            1
        ]["const"]
        == ""
    )
    assert declaration["parameters_json_schema"]["properties"]["metadata"][
        "additionalProperties"
    ] == {"type": "string"}

    GenerateContentConfig(tools=tools)


def test_gemini_tools_use_json_schema_parameters_for_no_argument_tools():
    function_schema = FunctionSchema(
        name="refresh_context",
        description="Refresh the current context.",
        properties={},
        required=[],
    )

    tools = DecibylGeminiJSONSchemaAdapter().to_provider_tools_format(
        ToolsSchema(standard_tools=[function_schema])
    )

    declaration = tools[0]["function_declarations"][0]
    assert "parameters" not in declaration
    assert declaration["parameters_json_schema"] == {
        "type": "object",
        "properties": {},
        "required": [],
    }

    GenerateContentConfig(tools=tools)


def test_google_service_classes_use_decibyl_gemini_adapter_class():
    assert DecibylGoogleLLMService.adapter_class is DecibylGeminiJSONSchemaAdapter
    assert DecibylGoogleVertexLLMService.adapter_class is DecibylGeminiJSONSchemaAdapter


def test_google_llm_service_factory_uses_decibyl_service_class():
    with patch(
        "api.services.pipecat.service_factory.DecibylGoogleLLMService",
    ) as mock_service:
        result = create_llm_service_from_provider(
            provider=ServiceProviders.GOOGLE.value,
            model="gemini-2.5-flash",
            api_key="test-api-key",
        )

    assert result is mock_service.return_value
    assert mock_service.call_args.kwargs["api_key"] == "test-api-key"
    assert mock_service.call_args.kwargs["settings"].model == "gemini-2.5-flash"


def test_google_vertex_llm_service_factory_uses_decibyl_service_class():
    with patch(
        "api.services.pipecat.service_factory.DecibylGoogleVertexLLMService",
    ) as mock_service:
        result = create_llm_service_from_provider(
            provider=ServiceProviders.GOOGLE_VERTEX.value,
            model="gemini-2.5-pro",
            api_key=None,
            project_id="demo-project",
            location="us-central1",
            credentials='{"type":"service_account"}',
        )

    assert result is mock_service.return_value
    assert mock_service.call_args.kwargs["project_id"] == "demo-project"
    assert mock_service.call_args.kwargs["location"] == "us-central1"
    assert mock_service.call_args.kwargs["settings"].model == "gemini-2.5-pro"


def test_gemini_live_service_classes_use_decibyl_gemini_adapter_class():
    assert DecibylGeminiLiveLLMService.adapter_class is DecibylGeminiJSONSchemaAdapter
    # Vertex Live inherits adapter_class from DecibylGeminiLiveLLMService via MRO.
    assert (
        DecibylGeminiLiveVertexLLMService.adapter_class is DecibylGeminiJSONSchemaAdapter
    )


def test_vertex_live_inherits_decibyl_node_transition_lifecycle():
    assert (
        DecibylGeminiLiveVertexLLMService._requires_node_transition_context_aggregation
        is DecibylGeminiLiveLLMService._requires_node_transition_context_aggregation
    )
    assert (
        DecibylGeminiLiveVertexLLMService._run_or_defer_function_calls
        is DecibylGeminiLiveLLMService._run_or_defer_function_calls
    )
    assert (
        DecibylGeminiLiveVertexLLMService._reconnect_for_node_transition
        is DecibylGeminiLiveLLMService._reconnect_for_node_transition
    )


def test_gemini_live_config_accepts_json_schema_tools():
    function_schema = FunctionSchema(
        name="customer_lookup",
        description="Look up a customer by email.",
        properties={
            "customerEmail": {
                "description": "Customer email address",
                "anyOf": [{"not": {}}, {"const": ""}],
            },
        },
        required=["customerEmail"],
    )

    tools = DecibylGeminiJSONSchemaAdapter().to_provider_tools_format(
        ToolsSchema(standard_tools=[function_schema])
    )

    declaration = tools[0]["function_declarations"][0]
    assert "parameters" not in declaration
    assert "parameters_json_schema" in declaration

    # Gemini Live validates tools through LiveConnectConfig rather than
    # GenerateContentConfig; it must also accept the raw JSON Schema payload.
    LiveConnectConfig(tools=tools)
