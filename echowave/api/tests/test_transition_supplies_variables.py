"""The whole round trip: the model hands a value over as it moves.

`test_transition_arguments.py` pins the schema and the reading of it in
isolation. This drives the real engine over a real pipeline, because the two
things worth proving are both about what happens *around* the transition: the
value reaches the gathered context, and the extraction pass that used to go and
find it does not run a second model to find it again.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.frames.frames import LLMContextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
)
from pipecat.tests.mock_transport import MockTransport
from pipecat.transports.base_transport import TransportParams

from api.services.pipecat.worker_runner import run_pipeline_worker
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_variable_extractor import (
    VariableExtractionManager,
)
from api.services.workflow.workflow_graph import WorkflowGraph
from pipecat.tests import MockLLMService, MockTTSService


async def _run(workflow: WorkflowGraph, transition_arguments: dict):
    """Drive one call: start node transitions with these arguments, then ends.

    Returns (engine, extraction_mock).
    """
    llm = MockLLMService(
        mock_steps=[
            MockLLMService.create_function_call_chunks(
                function_name="collect_info",
                arguments=transition_arguments,
                tool_call_id="call_1",
            ),
            MockLLMService.create_function_call_chunks(
                function_name="end_call", arguments={}, tool_call_id="call_2"
            ),
            MockLLMService.create_text_chunks("Goodbye!"),
        ],
        chunk_delay=0.001,
    )
    tts = MockTTSService(mock_audio_duration_ms=40, frame_delay=0)
    transport = MockTransport(
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
        ),
    )
    context = LLMContext()
    aggregators = LLMContextAggregatorPair(
        context, assistant_params=LLMAssistantAggregatorParams()
    )

    engine = PipecatEngine(
        llm=llm,
        context=context,
        workflow=workflow,
        call_context_vars={},
        workflow_run_id=1,
    )
    task = PipelineWorker(
        Pipeline([llm, tts, transport.output(), aggregators.assistant()]),
        params=PipelineParams(),
        enable_rtvi=False,
    )
    engine.set_task(task)

    with patch(
        "api.db:db_client.get_organization_id_by_workflow_run_id",
        new_callable=AsyncMock,
        return_value=1,
    ):
        with patch.object(
            VariableExtractionManager,
            "_perform_extraction",
            new_callable=AsyncMock,
            return_value={"user_name": "Extracted By The Second Call"},
        ) as extraction:

            async def initialize():
                await asyncio.sleep(0.01)
                await engine.initialize()
                await engine.set_node(engine.workflow.start_node_id)
                await engine.llm.queue_frame(LLMContextFrame(engine.context))

            await asyncio.gather(run_pipeline_worker(task), initialize())
            await engine._await_pending_extractions(timeout=5.0)

    return engine, extraction


class TestTheModelSuppliesTheValue:
    @pytest.mark.asyncio
    async def test_the_value_lands_in_the_gathered_context(
        self, three_node_workflow_extraction_start_only: WorkflowGraph
    ):
        engine, _ = await _run(
            three_node_workflow_extraction_start_only, {"user_name": "Priya"}
        )
        assert engine._gathered_context["user_name"] == "Priya"
        assert engine._gathered_context["extracted_variables"]["user_name"] == "Priya"

    @pytest.mark.asyncio
    async def test_the_second_model_call_is_not_made(
        self, three_node_workflow_extraction_start_only: WorkflowGraph
    ):
        """The point of the whole change. The start node collects one variable;
        the model supplied it, so there is nothing left to go and extract."""
        _, extraction = await _run(
            three_node_workflow_extraction_start_only, {"user_name": "Priya"}
        )
        assert extraction.await_count == 0

    @pytest.mark.asyncio
    async def test_what_the_model_said_is_not_overwritten(
        self, three_node_workflow_extraction_start_only: WorkflowGraph
    ):
        """The extraction mock returns a different name. If it ran and won,
        this is how we would find out."""
        engine, _ = await _run(
            three_node_workflow_extraction_start_only, {"user_name": "Priya"}
        )
        assert engine._gathered_context["user_name"] == "Priya"


class TestTheModelSuppliesNothing:
    """Every agent running today, unchanged."""

    @pytest.mark.asyncio
    async def test_extraction_still_runs(
        self, three_node_workflow_extraction_start_only: WorkflowGraph
    ):
        _, extraction = await _run(three_node_workflow_extraction_start_only, {})
        assert extraction.await_count >= 1

    @pytest.mark.asyncio
    async def test_the_extracted_value_still_lands(
        self, three_node_workflow_extraction_start_only: WorkflowGraph
    ):
        engine, _ = await _run(three_node_workflow_extraction_start_only, {})
        assert engine._gathered_context["user_name"] == "Extracted By The Second Call"

    @pytest.mark.asyncio
    async def test_a_blank_value_is_treated_as_nothing(
        self, three_node_workflow_extraction_start_only: WorkflowGraph
    ):
        """An empty string is not an answer, so the extraction pass must still
        go and look rather than record the blank."""
        engine, extraction = await _run(
            three_node_workflow_extraction_start_only, {"user_name": "   "}
        )
        assert extraction.await_count >= 1
        assert engine._gathered_context["user_name"] == "Extracted By The Second Call"
