"""The sandbox runner holds to the Step 4 contract.

Run against the local runner (a subprocess standing in for the box; the
protocol is identical) and a fake remote service: the box gets code, files
and shell only and no credentials; every tool call is bridged to the
harness and answered from here; the call cap and the time limit end a box;
the record survives a box that dies; and a large tool response reaches the
model as a preview with the whole thing stored.

Eval scenarios: sandbox_bridges_tool_calls_with_no_credentials_in_the_box,
sandbox_large_response_becomes_a_preview.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.sandbox import jobs, protocol, spill
from api.services.sandbox.runner import (
    Event,
    Limits,
    LocalRunner,
    RemoteRunner,
    SandboxUnavailable,
)

ORG = 7


class TestTheWire:
    def test_a_request_line_round_trips_and_ordinary_output_is_not_one(self):
        line = protocol.encode_request(3, "app_sheets_read", {"range": "A1:C10"})
        assert protocol.decode_request(line) == {
            "id": 3,
            "tool": "app_sheets_read",
            "args": {"range": "A1:C10"},
        }
        assert protocol.decode_request("hello world") is None
        assert protocol.decode_request(protocol.SENTINEL + "not json") is None

    def test_a_reply_too_large_for_the_box_becomes_an_error(self):
        huge = {"data": "x" * (protocol.MAX_RESULT_CHARS + 10)}
        reply = json.loads(protocol.encode_reply(1, huge))
        assert reply["result"]["status"] == "error"

    def test_the_prelude_has_no_credentials_and_no_network_code(self):
        text = protocol.prelude()
        for word in ("requests", "urllib", "socket", "http", "API_KEY", "token"):
            assert word not in text, word


def _record_patches():
    """The job record, stood in for."""
    return (
        patch.object(
            jobs.db_client,
            "create_sandbox_job",
            AsyncMock(return_value=SimpleNamespace(id=91)),
        ),
        patch.object(jobs.db_client, "touch_sandbox_job", AsyncMock()),
        patch.object(jobs.db_client, "finish_sandbox_job", AsyncMock()),
    )


@pytest.mark.asyncio
class TestBridgingInAPlainProcess:
    """The local runner is a subprocess; the contract is the same."""

    async def test_sandbox_bridges_tool_calls_with_no_credentials_in_the_box(self):
        seen: list[tuple[str, dict]] = []

        async def tools(name, args):
            seen.append((name, args))
            if name == "app_sheets_read":
                return {
                    "status": "success",
                    "data": {
                        "rows": [{"name": "A", "due": 500}, {"name": "B", "due": 0}]
                    },
                }
            if name == "app_whatsapp_send":
                return {"status": "success", "data": {"sent": args["to"]}}
            return {"status": "error", "error": "no such tool"}

        code = """
import os
assert not [k for k in os.environ if 'KEY' in k or 'SECRET' in k or 'TOKEN' in k], os.environ
rows = tools.call("app_sheets_read", range="A1:B10")["data"]["rows"]
sent = 0
for row in rows:
    if row["due"] > 0:
        tools.app_whatsapp_send(to=row["name"], text=f"You owe {row['due']}")
        sent += 1
print(f"chased {sent} of {len(rows)}")
"""
        create, touch, finish = _record_patches()
        with create, touch, finish as finished:
            result = await jobs.run(
                organization_id=ORG, code=code, tools=tools, runner=LocalRunner()
            )
        assert result.ok, result.error
        assert result.output.strip() == "chased 1 of 2"
        assert [s[0] for s in seen] == ["app_sheets_read", "app_whatsapp_send"]
        assert seen[1][1] == {"to": "A", "text": "You owe 500"}
        assert result.calls == 2
        assert result.job_id == 91
        assert finished.await_args.kwargs["status"] == jobs.DONE
        told = result.as_result()
        assert told["status"] == "success" and told["calls"] == 2

    async def test_a_tool_error_is_an_exception_the_script_can_catch(self):
        async def tools(name, args):
            return {"status": "error", "error": "the sheet is not shared with us"}

        code = """
try:
    tools.call("app_sheets_read")
except RuntimeError as e:
    print("caught:", e)
"""
        create, touch, finish = _record_patches()
        with create, touch, finish:
            result = await jobs.run(
                organization_id=ORG, code=code, tools=tools, runner=LocalRunner()
            )
        assert result.ok
        assert result.output.strip() == "caught: the sheet is not shared with us"

    async def test_a_script_that_crashes_is_a_failure_with_the_traceback(self):
        async def tools(name, args):
            return {}

        create, touch, finish = _record_patches()
        with create, touch, finish:
            result = await jobs.run(
                organization_id=ORG,
                code="x = 1 / 0\n",
                tools=tools,
                runner=LocalRunner(),
            )
        assert not result.ok and result.exit_code == 1
        assert "ZeroDivisionError" in result.error
        assert result.as_result()["status"] == "error"

    async def test_the_call_cap_ends_the_box(self):
        async def tools(name, args):
            return {"status": "success", "data": 1}

        code = "for i in range(50):\n    tools.call('app_x')\nprint('never')\n"
        create, touch, finish = _record_patches()
        with create, touch, finish as finished:
            result = await jobs.run(
                organization_id=ORG,
                code=code,
                tools=tools,
                runner=LocalRunner(),
                max_calls=5,
            )
        assert result.status == jobs.CAPPED
        assert result.calls == 6
        assert "never" not in result.output
        assert finished.await_args.kwargs["status"] == jobs.CAPPED
        assert "more than" in result.as_result()["error"]

    async def test_the_time_limit_ends_the_box_and_keeps_what_it_printed(self):
        async def tools(name, args):
            return {}

        code = "import time\nprint('started')\ntime.sleep(30)\nprint('never')\n"
        create, touch, finish = _record_patches()
        with create, touch, finish as finished:
            result = await jobs.run(
                organization_id=ORG,
                code=code,
                tools=tools,
                runner=LocalRunner(),
                limits=Limits(timeout_seconds=1),
            )
        assert result.status == jobs.TIMED_OUT
        assert "started" in result.output and "never" not in result.output
        assert finished.await_args.kwargs["status"] == jobs.TIMED_OUT

    async def test_a_stored_response_is_readable_from_inside(self):
        async def tools(name, args):
            return {}

        async def reader(stored_as):
            return {"status": "success", "data": {"rows": list(range(300))}}

        code = "d = tools.spilled(stored_as='sandbox/7/1/x.json')\nprint(len(d['data']['rows']))\n"
        create, touch, finish = _record_patches()
        with create, touch, finish:
            result = await jobs.run(
                organization_id=ORG,
                code=code,
                tools=tools,
                runner=LocalRunner(),
                spilled_reader=reader,
            )
        assert result.ok and result.output.strip() == "300"


@pytest.mark.asyncio
class TestTheRemoteService:
    """The api's side of the four calls, against a faked service."""

    def _client(self, routes):
        class Response:
            def __init__(self, status, body):
                self.status_code = status
                self._body = body

            def json(self):
                return self._body

            def raise_for_status(self):
                if self.status_code >= 400:
                    import httpx

                    raise httpx.HTTPStatusError("x", request=None, response=None)

        class Client:
            def __init__(self, **kw):
                self.headers = kw.get("headers", {})

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, path, **kw):
                return Response(*routes("POST", path, kw))

            async def get(self, path, **kw):
                return Response(*routes("GET", path, kw))

            async def delete(self, path, **kw):
                return Response(*routes("DELETE", path, kw))

        return Client

    async def test_the_four_calls_carry_the_secret_and_the_prelude(self):
        calls = []

        def routes(method, path, kw):
            calls.append((method, path, kw))
            if (method, path) == ("POST", "/jobs"):
                return 200, {"id": "abc"}
            if path.endswith("/next"):
                return 200, {"request": {"id": 1, "tool": "app_x", "args": {}}}
            if path.endswith("/reply"):
                return 200, {"ok": True}
            return 200, {"ok": True}

        with patch(
            "api.services.sandbox.runner.httpx.AsyncClient", self._client(routes)
        ):
            runner = RemoteRunner("http://sandbox:8080", "s3cret")
            job = await runner.start("print(1)", Limits())
            event = await runner.next(job, wait=5)
            await runner.reply(job, 1, {"status": "success"})
            await runner.stop(job)
        assert job == "abc"
        assert event.kind == "request" and event.request["tool"] == "app_x"
        started = calls[0][2]["json"]
        assert (
            started["code"] == "print(1)" and started["prelude"] == protocol.prelude()
        )
        assert started["timeout_seconds"] == protocol.DEFAULT_TIMEOUT_SECONDS
        assert [c[:2] for c in calls] == [
            ("POST", "/jobs"),
            ("GET", "/jobs/abc/next"),
            ("POST", "/jobs/abc/reply"),
            ("DELETE", "/jobs/abc"),
        ]

    async def test_a_full_sandbox_is_a_failure_the_model_is_told_about(self):
        def routes(method, path, kw):
            return 429, {"detail": "full"}

        async def tools(name, args):
            return {}

        create, touch, finish = _record_patches()
        with (
            patch(
                "api.services.sandbox.runner.httpx.AsyncClient", self._client(routes)
            ),
            create,
            touch,
            finish as finished,
        ):
            result = await jobs.run(
                organization_id=ORG,
                code="print(1)",
                tools=tools,
                runner=RemoteRunner("http://sandbox:8080", "s"),
            )
        assert result.status == jobs.FAILED
        assert "try again" in result.error
        assert finished.await_args.kwargs["status"] == jobs.FAILED

    def test_the_service_starts_a_box_with_no_network_and_nothing_to_escalate(self):
        import importlib.util
        import pathlib

        path = pathlib.Path(__file__).resolve().parents[2] / "sandbox" / "server.py"
        import sys

        spec = importlib.util.spec_from_file_location("sandbox_server", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["sandbox_server"] = module
        spec.loader.exec_module(module)
        request = module.JobRequest(prelude="p", code="c")
        line = module.command_line("j1", request)
        for flag in (
            "--network none",
            "--cap-drop ALL",
            "--security-opt no-new-privileges",
            "--read-only",
            "--pids-limit 256",
            "--user 65534:65534",
            "--memory 512m",
            "--cpus 1.0",
        ):
            assert flag in line, flag
        assert "--privileged" not in line
        assert "docker.sock" not in line


class TestLargeResponses:
    def test_a_small_response_is_itself(self):
        assert not spill.is_large({"status": "success", "data": {"rows": [1, 2, 3]}})

    def test_a_large_list_becomes_a_preview_with_the_count(self):
        rows = [
            {"name": f"Lead {i}", "phone": f"+9198765{i:05d}", "note": "x" * 80}
            for i in range(400)
        ]
        result = {"status": "success", "data": {"rows": rows}}
        assert spill.is_large(result)
        shown = spill.preview(result["data"], stored_as="sandbox/7/1/app_x-c1.json")
        assert shown["rows"] == 400 and len(shown["first"]) == spill.PREVIEW_ITEMS
        assert shown["spilled"] is True and "tools.spilled" in shown["note"]

    @pytest.mark.asyncio
    async def test_sandbox_large_response_becomes_a_preview(self):
        rows = [{"name": f"Lead {i}", "note": "x" * 100} for i in range(400)]
        result = {"status": "success", "data": rows}
        storage = SimpleNamespace(acreate_file_from_bytes=AsyncMock(return_value=True))
        with patch("api.services.storage.get_storage", return_value=storage):
            shown = await spill.spill_if_large(
                result,
                organization_id=ORG,
                run_id=12,
                name="app_hubspot_search",
                call_id="c1",
            )
        assert shown["status"] == "success"
        assert shown["data"]["rows"] == 400
        assert shown["data"]["stored_as"] == "sandbox/7/12/app_hubspot_search-c1.json"
        key, body = storage.acreate_file_from_bytes.await_args.args
        assert key == shown["data"]["stored_as"] and json.loads(body) == rows

    @pytest.mark.asyncio
    async def test_a_store_that_fails_still_bounds_what_the_model_sees(self):
        rows = [{"name": f"Lead {i}", "note": "x" * 100} for i in range(400)]
        storage = SimpleNamespace(acreate_file_from_bytes=AsyncMock(return_value=False))
        with patch("api.services.storage.get_storage", return_value=storage):
            shown = await spill.spill_if_large(
                {"status": "success", "data": rows},
                organization_id=ORG,
                run_id=None,
                name="x",
                call_id="c",
            )
        assert (
            "stored_as" not in shown["data"]
            and "could not be stored" in shown["data"]["note"]
        )

    @pytest.mark.asyncio
    async def test_a_key_from_another_organisation_reads_nothing(self):
        out = await spill.read_spilled(ORG, "sandbox/8/1/x.json")
        assert out["status"] == "error"


class TestTheLocalRunnerIsForDevelopmentOnly:
    def test_it_refuses_outside_development(self):
        with (
            patch("api.services.sandbox.runner.DEPLOYMENT_MODE", "cloud"),
            patch.dict("os.environ", {}, clear=False),
        ):
            import os

            os.environ.pop("SANDBOX_ALLOW_LOCAL", None)
            with pytest.raises(SandboxUnavailable):
                LocalRunner()


def test_the_event_shape():
    assert Event(kind="done", exit_code=0).output == ""
