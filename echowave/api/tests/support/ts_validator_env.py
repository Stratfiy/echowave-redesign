"""Whether the Node TS validator can be shelled out to here, and why not.

Two test files exercise the real validator subprocess. In an environment
where nobody has run ``npm install`` every one of them fails with a
subprocess error apiece, which reads as a broken workflow bridge rather
than a missing setup step -- and a suite whose red does not mean broken
is a suite people stop reading.

The guard lives here rather than in one of those files because it was in
one of them: ``test_ts_bridge.py`` skipped cleanly while
``test_mcp_save_workflow.py``, checking only for the ``node`` binary,
went on to fail six tests on the same missing directory. One answer, one
place.

CI installs the dependencies, so skipping costs no coverage where it
counts.
"""

from __future__ import annotations

import shutil

from api.mcp_server.ts_bridge import _VALIDATOR_ENTRY

NODE_MODULES = _VALIDATOR_ENTRY.parents[1] / "node_modules"


def why_the_validator_cannot_run() -> str | None:
    """The setup step that has not been done, named. None when it can."""
    if shutil.which("node") is None:
        return "node binary not available"
    if not NODE_MODULES.is_dir():
        return (
            "ts_validator dependencies are not installed — run: "
            "cd api/mcp_server/ts_validator && npm install"
        )
    return None
