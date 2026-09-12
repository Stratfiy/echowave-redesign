"""Recording what the agent did, and the answer rate that was measuring itself wrong.

Two things are under test. That every action an agent takes leaves a row
whatever happened to it -- success, error envelope, or an exception out of the
handler -- because a missing row and an action that never happened look
identical afterwards. And that answer rate divides by calls a carrier could
actually have answered.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import CARRIER_RUN_MODES, WorkflowRunMode, is_carrier_run_mode
from api.services.workflow.app_interactions import (
    STATUS_ERROR,
    STATUS_SUCCESS,
    classify_result,
    wrap_handler,
)
from api.services.workflow.pipecat_engine_custom_tools import _tool_app_slug


def _params():
    """A FunctionCallParams-shaped double: the wrapper only touches the one field."""
    return SimpleNamespace(result_callback=AsyncMock(), arguments={})


async def _context():
    return {"organization_id": 42, "workflow_run_id": 7, "workflow_id": 3}


class TestClassifyResult:
    def test_the_error_envelope_the_model_sees_is_the_one_recorded(self):
        """Reading the same field the model reads means this table can never
        disagree with what the agent was told -- which matters when the table
        is the evidence for whether the product worked."""
        status, error = classify_result({"status": "error", "error": "rejected"})
        assert (status, error) == (STATUS_ERROR, "rejected")

    def test_success_is_success(self):
        assert classify_result({"status": "success", "data": {}}) == (
            STATUS_SUCCESS,
            None,
        )

    @pytest.mark.parametrize("result", ["a string", None, 42, [], {"other": 1}])
    def test_anything_not_shaped_like_the_contract_counts_as_success(self, result):
        """A handler returning something else has done its job. Inventing a
        failure would be worse than recording a vague success."""
        assert classify_result(result)[0] == STATUS_SUCCESS

    def test_a_provider_returning_an_essay_cannot_fill_the_column(self):
        _, error = classify_result({"status": "error", "error": "x" * 5000})
        assert len(error) == 1000


class TestWrapHandler:
    @pytest.mark.asyncio
    async def test_a_successful_action_is_recorded_with_its_duration(self):
        async def handler(params):
            await params.result_callback({"status": "success"})

        with patch("api.services.workflow.app_interactions.record", AsyncMock()) as rec:
            await wrap_handler(
                handler,
                kind="composio",
                app="gmail",
                name="send_email",
                context=_context,
            )(_params())

        kwargs = rec.await_args.kwargs
        assert kwargs["status"] == STATUS_SUCCESS
        assert kwargs["app"] == "gmail"
        assert kwargs["organization_id"] == 42
        assert kwargs["workflow_run_id"] == 7
        assert kwargs["duration_ms"] is not None

    @pytest.mark.asyncio
    async def test_the_model_still_gets_its_result(self):
        """The substituted callback passes everything through untouched. The
        pipeline must not be able to tell the difference."""
        params = _params()

        async def handler(p):
            await p.result_callback({"status": "success", "data": {"id": 1}})

        with patch("api.services.workflow.app_interactions.record", AsyncMock()):
            await wrap_handler(
                handler, kind="composio", app="gmail", name="x", context=_context
            )(params)

        params.result_callback.assert_awaited_once_with(
            {"status": "success", "data": {"id": 1}}
        )

    @pytest.mark.asyncio
    async def test_an_error_envelope_is_recorded_as_a_failure(self):
        async def handler(params):
            await params.result_callback({"status": "error", "error": "timed out"})

        with patch("api.services.workflow.app_interactions.record", AsyncMock()) as rec:
            await wrap_handler(
                handler, kind="composio", app="gmail", name="x", context=_context
            )(_params())

        assert rec.await_args.kwargs["status"] == STATUS_ERROR
        assert rec.await_args.kwargs["error"] == "timed out"

    @pytest.mark.asyncio
    async def test_a_handler_that_raises_is_recorded_before_the_exception_leaves(self):
        """The most interesting row in the table and the easiest one to lose."""

        async def handler(params):
            raise RuntimeError("boom")

        with patch("api.services.workflow.app_interactions.record", AsyncMock()) as rec:
            with pytest.raises(RuntimeError):
                await wrap_handler(
                    handler,
                    kind="http_api",
                    app="api.acme.com",
                    name="x",
                    context=_context,
                )(_params())

        assert rec.await_args.kwargs["status"] == STATUS_ERROR
        assert "boom" in rec.await_args.kwargs["error"]

    @pytest.mark.asyncio
    async def test_a_failure_to_record_never_reaches_the_call(self):
        """A metric must not end a conversation. The caller is on the line."""

        async def handler(params):
            await params.result_callback({"status": "success"})

        with patch(
            "api.services.workflow.app_interactions.db_client.create_app_interaction",
            AsyncMock(side_effect=RuntimeError("database is on fire")),
        ):
            await wrap_handler(
                handler, kind="composio", app="gmail", name="x", context=_context
            )(_params())

    @pytest.mark.asyncio
    async def test_a_run_with_no_organization_writes_nothing(self):
        """Without a tenant the row cannot be read by anyone who should see it
        and could be read by someone who should not."""

        async def handler(params):
            await params.result_callback({"status": "success"})

        async def no_org():
            return {"organization_id": None, "workflow_run_id": 1, "workflow_id": 1}

        with patch(
            "api.services.workflow.app_interactions.db_client.create_app_interaction",
            AsyncMock(),
        ) as create:
            await wrap_handler(
                handler, kind="composio", app="gmail", name="x", context=no_org
            )(_params())
        create.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_original_callback_is_restored(self):
        """Handlers are registered once and called many times. A substitution
        left in place would nest a new wrapper on every call."""
        params = _params()
        original = params.result_callback

        async def handler(p):
            await p.result_callback({"status": "success"})

        with patch("api.services.workflow.app_interactions.record", AsyncMock()):
            await wrap_handler(
                handler, kind="composio", app="gmail", name="x", context=_context
            )(params)

        assert params.result_callback is original


class TestAppSlug:
    def test_a_composio_tool_names_its_toolkit(self):
        tool = SimpleNamespace(
            definition={"type": "composio", "config": {"toolkit": "GMAIL"}}
        )
        assert _tool_app_slug(tool) == "gmail"

    def test_an_http_tool_falls_back_to_its_host(self):
        """Not a slug, but the thing a reliability report groups by: "every
        call to api.acme.com is failing" is a sentence somebody acts on."""
        tool = SimpleNamespace(
            definition={
                "type": "http_api",
                "config": {"url": "https://API.acme.com/v1/x"},
            }
        )
        assert _tool_app_slug(tool) == "api.acme.com"

    @pytest.mark.parametrize(
        "definition",
        [None, {}, {"config": None}, {"config": {}}, {"config": {"url": "not a url"}}],
    )
    def test_a_tool_touching_nothing_outside_has_no_app(self, definition):
        assert _tool_app_slug(SimpleNamespace(definition=definition)) is None


class TestAnswerRateDenominator:
    def test_a_browser_call_is_not_a_carrier_call(self):
        """It is a real call and it has no carrier, so nothing can ever set
        answered_at on it."""
        assert not is_carrier_run_mode(WorkflowRunMode.WEBRTC.value)
        assert not is_carrier_run_mode(WorkflowRunMode.SMALLWEBRTC.value)

    def test_a_text_chat_is_not_a_carrier_call(self):
        assert not is_carrier_run_mode(WorkflowRunMode.TEXTCHAT.value)

    @pytest.mark.parametrize(
        "mode",
        [
            WorkflowRunMode.PLIVO,
            WorkflowRunMode.TWILIO,
            WorkflowRunMode.VONAGE,
            WorkflowRunMode.TELNYX,
            WorkflowRunMode.ARI,
        ],
    )
    def test_every_telephony_provider_counts(self, mode):
        assert is_carrier_run_mode(mode.value)

    def test_an_unknown_mode_is_not_counted(self):
        assert not is_carrier_run_mode("some_future_thing")
        assert not is_carrier_run_mode(None)

    def test_the_asr_query_filters_by_carrier_mode(self):
        """The regression this fixes: an account's first day of dashboard test
        calls showed an answer rate near zero, because every test call counted
        as a call nobody picked up."""
        import inspect

        from api.services.reports import org_metrics

        source = inspect.getsource(org_metrics.answer_seizure_ratio)
        assert "CARRIER_RUN_MODES" in source

    def test_browser_and_text_modes_are_absent_from_the_allow_list(self):
        for mode in (
            WorkflowRunMode.WEBRTC,
            WorkflowRunMode.SMALLWEBRTC,
            WorkflowRunMode.TEXTCHAT,
            WorkflowRunMode.CHAT,
        ):
            assert mode.value not in CARRIER_RUN_MODES
