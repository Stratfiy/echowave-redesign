"""Decibyl can read the task board (KAN-186).

It could file a task and could not say what was on the board -- "what is
blocked?" was answered with "I have no tool for that". One read tool, no
card: a list with the board's own filters, or one task with its comments.
Same rows the /tasks screen shows, same organisation boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client as client
from api.services.workflow import decibyl, tasks_board

pytestmark = pytest.mark.asyncio


def _task(**overrides):
    base = {
        "id": 12,
        "number": 12,
        "title": "Follow up Mrs Lakshmi",
        "brief": "Call in 3 days.",
        "status": "todo",
        "priority": "medium",
        "assignee_user_id": None,
        "parent_id": None,
        "blocked_by": [],
        "labels": [],
        "from_workflow_id": 3,
        "assignee_workflow_id": 4,
        "created_by": None,
        "workflow_run_id": None,
        "depth": 0,
        "due_at": None,
        "result": None,
        "created_at": datetime(2026, 9, 15, tzinfo=UTC),
        "started_at": None,
        "finished_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


ROWS = [
    _task(
        id=1,
        number=1,
        title="Chase AWS invoice",
        status="blocked",
        assignee_workflow_id=None,
        assignee_user_id=32,
        labels=["finance"],
    ),
    _task(id=2, number=2, title="Draft NL/003", status="in_progress"),
    _task(id=3, number=3, title="Old thing", status="done"),
    _task(
        id=4,
        number=4,
        title="Renew domain",
        status="todo",
        assignee_workflow_id=None,
        labels=["ops"],
    ),
]
NAMES = {3: "Reception", 4: "Retention"}
PEOPLE = {32: "Nithish"}


def _patched(rows=ROWS, comments=None):
    return (
        patch.object(
            client, "tasks_for_organization", new=AsyncMock(return_value=rows)
        ),
        patch.object(
            client,
            "get_task",
            new=AsyncMock(return_value=next((r for r in rows if r.id == 1), None)),
        ),
        patch.object(
            client, "comments_for_task", new=AsyncMock(return_value=comments or [])
        ),
        patch(
            "api.services.workflow.tasks_board.board_context",
            new=AsyncMock(
                return_value={
                    "roster": [],
                    "names": NAMES,
                    "handles": {3: "reception", 4: "retention"},
                    "people": PEOPLE,
                    "prefix": "NL",
                }
            ),
        ),
    )


class TestTheSchema:
    def test_it_is_a_read_beside_create_task(self):
        names = [t["name"] for t in decibyl.office_tools()]
        assert tasks_board.READ_TOOL_NAME in names
        assert tasks_board.TOOL_NAME in names
        schema = tasks_board.read_tool_schema()
        assert set(schema["parameters"]["properties"]) == {
            "status",
            "assignee",
            "label",
            "task_id",
        }
        assert schema["parameters"].get("required", []) == []

    def test_the_system_prompt_names_it_as_a_read(self):
        text = "".join(decibyl.SYSTEM)
        assert tasks_board.READ_TOOL_NAME in text
        line = next(l for l in text.split("\n") if tasks_board.READ_TOOL_NAME in l)
        assert "runs now" in line or "read" in line.lower()


class TestListing:
    async def test_open_work_by_default_newest_first_with_names(self):
        with _patched()[0], _patched()[3]:
            out = await tasks_board.read_board(organization_id=7, arguments={})
        assert out["status"] == "success"
        assert [t["identifier"] for t in out["tasks"]] == ["NL-1", "NL-2", "NL-4"]
        blocked = out["tasks"][0]
        assert blocked["status"] == "blocked"
        assert blocked["assignee"] == "Nithish"
        assert out["tasks"][1]["assignee"] == "@retention"
        assert out["counts"] == {"blocked": 1, "in_progress": 1, "todo": 1}

    async def test_filters_by_status_assignee_and_label(self):
        p = _patched()
        with p[0], p[3]:
            done = await tasks_board.read_board(
                organization_id=7, arguments={"status": "done"}
            )
            mine = await tasks_board.read_board(
                organization_id=7, arguments={"assignee": "Nithish"}
            )
            ops = await tasks_board.read_board(
                organization_id=7, arguments={"label": "ops"}
            )
            bot = await tasks_board.read_board(
                organization_id=7, arguments={"assignee": "@retention"}
            )
        assert [t["id"] for t in done["tasks"]] == [3]
        assert [t["id"] for t in mine["tasks"]] == [1]
        assert [t["id"] for t in ops["tasks"]] == [4]
        assert [t["id"] for t in bot["tasks"]] == [2]

    async def test_an_unknown_status_is_said_not_guessed(self):
        p = _patched()
        with p[0], p[3]:
            out = await tasks_board.read_board(
                organization_id=7, arguments={"status": "urgent"}
            )
        assert out["status"] == "error"
        assert "blocked" in out["error"]

    async def test_the_list_is_capped_and_says_so(self):
        rows = [_task(id=i, number=i, title=f"t{i}") for i in range(1, 80)]
        p = _patched(rows)
        with p[0], p[3]:
            out = await tasks_board.read_board(organization_id=7, arguments={})
        assert len(out["tasks"]) == tasks_board.READ_MAX
        assert out["more"] == 79 - tasks_board.READ_MAX


class TestOneTask:
    async def test_a_task_comes_back_with_its_comments_in_order(self):
        comments = [
            SimpleNamespace(
                id=1,
                body="Called, no answer",
                author_user_id=None,
                author_workflow_id=4,
                created_at=datetime(2026, 9, 16, tzinfo=UTC),
            ),
            SimpleNamespace(
                id=2,
                body="Try Tuesday",
                author_user_id=32,
                author_workflow_id=None,
                created_at=datetime(2026, 9, 17, tzinfo=UTC),
            ),
        ]
        p = _patched(comments=comments)
        with p[1], p[2], p[3]:
            out = await tasks_board.read_board(
                organization_id=7, arguments={"task_id": 1}
            )
        assert out["status"] == "success"
        assert out["task"]["identifier"] == "NL-1"
        assert [c["body"] for c in out["task"]["comments"]] == [
            "Called, no answer",
            "Try Tuesday",
        ]
        assert out["task"]["comments"][0]["by"] == "Retention"

    async def test_another_workspaces_task_is_not_found(self):
        with patch.object(client, "get_task", new=AsyncMock(return_value=None)):
            out = await tasks_board.read_board(
                organization_id=7, arguments={"task_id": 999}
            )
        assert out["status"] == "error" and "999" in out["error"]


class TestDecibylRuns:
    async def test_the_turn_runs_it_without_a_card(self):
        p = _patched()
        with p[0], p[3]:
            result = await decibyl._tool(
                7,
                SimpleNamespace(
                    name=tasks_board.READ_TOOL_NAME, arguments={"status": "blocked"}
                ),
            )
        assert result["tasks"][0]["title"] == "Chase AWS invoice"
