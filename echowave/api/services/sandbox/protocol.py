"""The contract between the harness and a box (Step 4, in code).

Five things, and every module in this package holds to them:

1. **The harness keeps the loop and every credential.** A box never sees a
   key, a token or a connection string. When a script needs an outside
   app, it asks the harness, and the harness makes the call.
2. **A box is created on demand** for one job and thrown away after it.
3. **A box runs code, files and shell only.** No network. Nothing inside it
   can reach anything but the harness, and only through the lines below.
4. **Tool calls from inside are bridged back to the harness** over the
   box's own stdout and stdin: a request line out, a result line in.
5. **State survives a box crash.** The job's record, its calls and its
   output live in the harness's own database, written as they happen.

The wire is deliberately dumb. A request is one line on stdout starting
with :data:`SENTINEL`; a reply is one JSON line on stdin. Anything else the
script prints is its output. That is the whole protocol, and it is why a
test can stand in a plain subprocess for the box and a production box can
be a Docker container with its network removed: neither side knows which.
"""

from __future__ import annotations

import json
from typing import Any

#: The marker a box puts in front of a tool request. Chosen so no ordinary
#: print collides with it.
SENTINEL = "@@decibyl-tool@@"

#: How much of a script's output the harness keeps, and how much of one
#: tool result travels into the box. Both bounded; a box is not a place to
#: keep things.
MAX_OUTPUT_CHARS = 64_000
MAX_RESULT_CHARS = 2_000_000
#: A script is a job, not a service.
MAX_CODE_CHARS = 64_000

#: Defaults a job runs under. Everyday-and-above accounts get these; the
#: limits themselves are the product's, not a box's, so they live here.
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_CALLS = 200
DEFAULT_MEMORY_MB = 512
DEFAULT_CPUS = 1.0


def encode_request(call_id: int, tool: str, args: dict[str, Any]) -> str:
    return SENTINEL + json.dumps(
        {"id": call_id, "tool": tool, "args": args}, separators=(",", ":")
    )


def decode_request(line: str) -> dict[str, Any] | None:
    """The request on a line, or None when the line is ordinary output."""
    if not line.startswith(SENTINEL):
        return None
    try:
        body = json.loads(line[len(SENTINEL) :])
    except ValueError:
        return None
    if not isinstance(body, dict) or "id" not in body or "tool" not in body:
        return None
    body.setdefault("args", {})
    if not isinstance(body["args"], dict):
        body["args"] = {}
    return body


def encode_reply(call_id: int, result: Any) -> str:
    text = json.dumps({"id": call_id, "result": result}, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = json.dumps(
            {
                "id": call_id,
                "result": {
                    "status": "error",
                    "error": "result too large to hand into the box",
                },
            }
        )
    return text


#: What runs in the box before the script. Written as one string so the box
#: image stays a bare Python and the harness owns the contract end to end.
#: It defines ``tools.call(name, **args)`` and ``tools.calls`` (how many so
#: far), redirects nothing, and runs the script from the CODE variable with
#: ``__name__ == "__main__"`` so an ordinary script works unchanged.
PRELUDE = r'''
import json as _json, os as _os, sys as _sys

_SENTINEL = %(sentinel)r
_out = _sys.stdout
_in = _sys.stdin
# Isolated mode ignores PYTHONUNBUFFERED, and a box that is killed on its
# time limit must not take its printed lines with it.
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass


class _Tools:
    """The apps this workspace has connected, reachable by name."""

    def __init__(self):
        self.calls = 0

    def call(self, name, **args):
        self.calls += 1
        _out.write(_SENTINEL + _json.dumps({"id": self.calls, "tool": name, "args": args}) + "\n")
        _out.flush()
        line = _in.readline()
        if not line:
            raise RuntimeError("the harness closed the line")
        reply = _json.loads(line)
        result = reply.get("result")
        if isinstance(result, dict) and result.get("status") == "error":
            raise RuntimeError(str(result.get("error") or "tool failed"))
        return result

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda **args: self.call(name, **args)


tools = _Tools()
_code = _os.environ.get("CODE", "")
_globals = {"__name__": "__main__", "tools": tools, "json": _json}
try:
    exec(compile(_code, "<script>", "exec"), _globals)
except SystemExit as _exit:
    raise
except BaseException as _error:  # noqa: BLE001 - reported, then the box ends
    import traceback as _tb
    _sys.stderr.write("".join(_tb.format_exception(_error)))
    _sys.stderr.flush()
    _sys.exit(1)
'''


def prelude() -> str:
    return PRELUDE % {"sentinel": SENTINEL}


__all__ = [
    "DEFAULT_CPUS",
    "DEFAULT_MAX_CALLS",
    "DEFAULT_MEMORY_MB",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_CODE_CHARS",
    "MAX_OUTPUT_CHARS",
    "MAX_RESULT_CHARS",
    "SENTINEL",
    "decode_request",
    "encode_reply",
    "encode_request",
    "prelude",
]
