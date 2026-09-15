"""A run is a test because a test verb started it (KAN-140, P0 item 5).

Arrival tests: the stamp is read in one place; a test call is said to be
one on the thread; the outcomes and the lessons are withheld from it; the
voice tester stamps the run it creates.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import agent_timeline, test_runs


class TestWhatCountsAsATest:
    def test_a_browser_call_is_one_by_mode(self):
        assert test_runs.is_test(SimpleNamespace(mode="webrtc", annotations={}))

    def test_a_stamped_run_is_one_whatever_its_mode(self):
        run = SimpleNamespace(
            mode="textchat", annotations=test_runs.stamp("workflow_editor", "text")
        )
        assert test_runs.is_test(run)

    def test_a_real_call_is_not(self):
        assert not test_runs.is_test(SimpleNamespace(mode="twilio", annotations={}))
        assert not test_runs.is_test(
            SimpleNamespace(mode="textchat", annotations={"routine": {"id": 1}})
        )
        assert not test_runs.is_test(None)

    def test_the_eval_runner_stamp_is_the_same_stamp(self):
        # evals/runner.py writes {"tester": {"source": "eval", ...}} already.
        assert test_runs.is_test_annotations({"tester": {"source": "eval"}})


@pytest.mark.asyncio
class TestTheThreadSaysSo:
    async def test_a_test_call_row_is_marked_and_says_test(self):
        run = SimpleNamespace(
            mode="webrtc",
            annotations=test_runs.stamp("workflow_editor", "voice"),
            gathered_context={},
            billable_seconds=42,
            answered_at=None,
            ended_at=None,
            recording_url=None,
            workflow_id=3,
            call_type=None,
        )
        with (
            patch.object(
                agent_timeline.db_client,
                "get_workflow_run",
                AsyncMock(return_value=run),
            ),
            patch.object(
                agent_timeline.db_client,
                "get_organization_id_by_workflow_run_id",
                AsyncMock(return_value=7),
            ),
            patch.object(agent_timeline, "record", AsyncMock()) as record,
        ):
            await agent_timeline.record_call_ended(11)
        row = record.await_args.kwargs
        assert row["payload"]["test"] is True
        assert row["summary"].startswith("Test call · ")

    async def test_a_real_call_row_is_not(self):
        run = SimpleNamespace(
            mode="twilio",
            annotations={},
            gathered_context={},
            billable_seconds=42,
            answered_at=None,
            ended_at=None,
            recording_url=None,
            workflow_id=3,
            call_type=None,
        )
        with (
            patch.object(
                agent_timeline.db_client,
                "get_workflow_run",
                AsyncMock(return_value=run),
            ),
            patch.object(
                agent_timeline.db_client,
                "get_organization_id_by_workflow_run_id",
                AsyncMock(return_value=7),
            ),
            patch.object(agent_timeline, "record", AsyncMock()) as record,
        ):
            await agent_timeline.record_call_ended(11)
        row = record.await_args.kwargs
        assert row["payload"]["test"] is False
        assert not row["summary"].startswith("Test call")


@pytest.mark.asyncio
class TestTheVoiceTesterStampsItsRun:
    async def test_the_run_route_writes_the_stamp(self):
        from api.routes import workflow as route

        created = SimpleNamespace(
            id=5,
            workflow_id=3,
            name="WR-1",
            mode="webrtc",
            created_at=None,
            definition_id=9,
            initial_context={},
            gathered_context={},
            annotations={},
        )
        stamped = SimpleNamespace(
            **{
                **created.__dict__,
                "annotations": {
                    "tester": {"source": "workflow_editor", "modality": "voice"}
                },
            }
        )
        with (
            patch.object(
                route.db_client, "create_workflow_run", AsyncMock(return_value=created)
            ),
            patch.object(
                route.db_client, "update_workflow_run", AsyncMock(return_value=stamped)
            ) as update,
        ):
            await route.create_workflow_run(
                3,
                SimpleNamespace(name="WR-1", mode="webrtc"),
                SimpleNamespace(id=42, selected_organization_id=7),
            )
        assert update.await_args.kwargs["annotations"]["tester"] == {
            "source": "workflow_editor",
            "modality": "voice",
        }
