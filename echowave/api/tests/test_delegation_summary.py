"""The subagent summary rule: a delegate returns a summary, never its rows.

Step 10's check is the two-hop eval at the bottom: a coordinator files a
task, the delegate reads a connected app (forty rows) and replies with a
report that also pastes the rows, and what reaches the coordinator's chat
is the report alone. The rows stay on the delegate's run, and the
delegate's timeline says they are there.

Eval scenario: two_hop_delegation_returns_a_summary_not_the_rows.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import tasks_board
from api.tasks.function_names import FunctionNames


class TestTheSummariser:
    def test_short_prose_passes_through(self):
        text = (
            "Called Mrs Lakshmi. She confirmed Tuesday 5 pm and asked for a reminder."
        )
        assert tasks_board.summarise_for_asker(text) == text

    def test_a_code_block_is_dropped(self):
        text = 'Found the lead.\n```json\n{"id": 1, "phone": "+91"}\n```\nShe is warm.'
        out = tasks_board.summarise_for_asker(text)
        assert "phone" not in out and '{"id"' not in out
        assert "Found the lead." in out and "She is warm." in out

    def test_table_rows_are_dropped(self):
        rows = "\n".join(f"| {i} | Lead {i} | +9198765{i:05d} |" for i in range(30))
        text = f"Thirty leads matched.\n| id | name | phone |\n|---|---|---|\n{rows}\nMost are from Hosur."
        out = tasks_board.summarise_for_asker(text)
        assert "Lead 7" not in out and "+9198765" not in out
        assert out.startswith("Thirty leads matched.")
        assert "Most are from Hosur." in out

    def test_json_records_are_dropped(self):
        records = "\n".join(
            json.dumps({"order": f"A{i}", "amount": 100 + i, "status": "cod"}) + ","
            for i in range(20)
        )
        text = f"Pulled today's orders.\n[\n{records}\n]\nTwo are unconfirmed."
        out = tasks_board.summarise_for_asker(text)
        assert '"order"' not in out
        assert "Pulled today's orders." in out and "Two are unconfirmed." in out

    def test_a_run_of_key_value_lines_is_a_record_and_goes(self):
        text = (
            "Here is the contact.\n"
            "Name: Meera\nPhone: +91 98765 43210\nEmail: meera@x.in\n"
            "City: Hosur\nStage: warm\nOwner: Ravi\n"
            "She wants a call on Monday."
        )
        out = tasks_board.summarise_for_asker(text)
        assert "98765" not in out and "meera@x.in" not in out
        assert "She wants a call on Monday." in out

    def test_one_or_two_labelled_lines_are_a_report_and_stay(self):
        text = "Done.\nStatus: booked\nWhen: Tuesday 5 pm"
        out = tasks_board.summarise_for_asker(text)
        assert "Status: booked" in out and "When: Tuesday 5 pm" in out

    def test_a_long_report_is_cut_and_says_so(self):
        text = " ".join(
            f"Sentence number {i} says something useful." for i in range(60)
        )
        out = tasks_board.summarise_for_asker(text)
        assert len(out) <= tasks_board.MAX_SUMMARY + len(" (More on the task card.)")
        assert out.endswith("(More on the task card.)")

    def test_too_many_lines_are_cut_and_say_so(self):
        text = "\n".join(f"Point {i} about the visit." for i in range(12))
        out = tasks_board.summarise_for_asker(text)
        assert out.count("\n") + 1 == tasks_board.MAX_SUMMARY_LINES + 1 or out.endswith(
            "(More on the task card.)"
        )
        assert "Point 11" not in out

    def test_nothing_left_is_still_a_sentence(self):
        assert tasks_board.summarise_for_asker("```\nrows\n```") == (
            "Done; the details are on the task card."
        )

    def test_the_brief_carries_the_rule(self):
        text = tasks_board.run_message(title="x", brief="y", asker="@a")
        assert "Report, do not paste" in text
        assert "only your summary reaches them" in text


def _bot(id: int, name: str, handle: str):
    return SimpleNamespace(id=id, name=name, handle=handle)


ROSTER = [_bot(3, "Front desk", "reception"), _bot(4, "Retention", "retention")]

ROWS = [
    {"id": i, "name": f"Lead {i}", "phone": f"+9198765{i:05d}", "stage": "cold"}
    for i in range(40)
]


def _session_with(turns):
    return SimpleNamespace(session_data={"turns": turns}, revision=1)


@pytest.mark.asyncio
class TestTwoHopDelegation:
    """Coordinator -> delegate -> connected app, and back.

    The delegate's model is fooled into pasting: its reply is a real
    report followed by the forty rows it read. The coordinator gets the
    report. The rows are on the delegate's run and nowhere else.
    """

    async def test_two_hop_delegation_returns_a_summary_not_the_rows(self):
        pasted = "\n".join(
            f"| {r['id']} | {r['name']} | {r['phone']} | {r['stage']} |" for r in ROWS
        )
        delegate_reply = (
            "Searched the CRM for Hosur leads: 40 matched, all cold, none "
            "contacted this month. Three have a site visit booked.\n"
            "| id | name | phone | stage |\n|---|---|---|---|\n" + pasted
        )
        delegate_turns = [
            {
                "id": "turn_1",
                "status": "completed",
                "user_message": {"text": "A task from @reception: ..."},
                "assistant_message": {"text": delegate_reply},
                "events": [
                    {
                        "type": "tool_call_started",
                        "payload": {"function_name": "app_hubspot_search_contacts"},
                    },
                    {
                        "type": "tool_call_result",
                        "payload": {
                            "function_name": "app_hubspot_search_contacts",
                            "result": {"status": "success", "data": ROWS},
                        },
                    },
                ],
            }
        ]
        row = SimpleNamespace(
            id=12,
            title="Find cold Hosur leads",
            brief="Search the CRM for cold leads in Hosur.",
            status="todo",
            from_workflow_id=3,
            assignee_workflow_id=4,
            created_by=None,
            source_run_id=70,
            workflow_run_id=None,
            depth=0,
            due_at=None,
            result=None,
            created_at=None,
            started_at=None,
            finished_at=None,
            organization_id=7,
        )
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = AsyncMock(return_value=row)

        finished = SimpleNamespace(
            **{**row.__dict__, "status": "done", "workflow_run_id": 99}
        )
        update_task = AsyncMock(return_value=finished)
        record = AsyncMock()
        record_activity = AsyncMock()
        enqueue = AsyncMock()

        with (
            patch.object(tasks_board.db_client, "async_session", return_value=session),
            patch.object(
                tasks_board.db_client,
                "get_all_workflows_for_listing",
                AsyncMock(return_value=ROSTER),
            ),
            patch.object(tasks_board.db_client, "update_task", update_task),
            patch.object(
                tasks_board.db_client,
                "create_workflow_run",
                AsyncMock(return_value=SimpleNamespace(id=99)),
            ),
            patch.object(tasks_board.db_client, "update_workflow_run", AsyncMock()),
            patch.object(
                tasks_board.db_client,
                "ensure_workflow_run_text_session",
                AsyncMock(return_value=_session_with([])),
            ),
            patch(
                "api.services.quota_service.authorize_workflow_run_start",
                AsyncMock(
                    return_value=SimpleNamespace(has_quota=True, error_message=None)
                ),
            ),
            patch(
                "api.services.workflow.text_chat_session_service.initialize_text_chat_session",
                AsyncMock(return_value=_session_with([])),
            ),
            patch(
                "api.services.workflow.text_chat_session_service.append_text_chat_user_message",
                AsyncMock(return_value=_session_with([])),
            ),
            patch(
                "api.services.workflow.text_chat_session_service.execute_pending_text_chat_turn",
                AsyncMock(
                    side_effect=[_session_with([]), _session_with(delegate_turns)]
                ),
            ),
            patch("api.services.billing.events.charge_in_own_session", AsyncMock()),
            patch.object(tasks_board.agent_timeline, "record", record),
            patch.object(
                tasks_board.agent_timeline, "record_activity", record_activity
            ),
            patch("api.tasks.arq.enqueue_job", enqueue),
            patch("pipecat.utils.run_context.set_current_run_id"),
        ):
            run_id = await tasks_board.run_task(12)

        assert run_id == 99

        # Hop one, back: the coordinator hears the report, not the rows.
        handed = [
            c
            for c in enqueue.await_args_list
            if c.args[0] == FunctionNames.ANSWER_CHANNEL_MESSAGE
        ]
        assert len(handed) == 1
        assert handed[0].args[1] == 3
        report = handed[0].args[3]
        assert report.startswith("Task result from Retention (Find cold Hosur leads): ")
        assert "40 matched, all cold" in report
        assert "Three have a site visit booked." in report
        assert "(More on the task card.)" in report
        for r in ROWS:
            assert r["phone"] not in report
            assert r["name"] not in report
        assert "|" not in report
        assert len(report) < 800

        # The card keeps the whole result, for a person.
        final = [
            c for c in update_task.await_args_list if c.kwargs.get("status") == "done"
        ]
        assert final and ROWS[7]["phone"] in final[0].kwargs["result"]

        # Hop two stays where it happened: the delegate's own timeline says
        # it used a tool, and on which run.
        notes = [
            c.kwargs
            for c in record_activity.await_args_list
            if c.kwargs.get("workflow_id") == 4
            and c.kwargs.get("workflow_run_id") == 99
        ]
        assert notes and notes[0]["payload"] == {"task_id": 12, "tool_calls": 1}
        assert "full output stays on this run" in notes[0]["summary"]
        # And the coordinator's timeline never received the rows either.
        for call in record.await_args_list + record_activity.await_args_list:
            if call.kwargs.get("workflow_id") == 3:
                assert ROWS[0]["phone"] not in json.dumps(call.kwargs, default=str)
