"""Composio integration package.

Registers no package spec, unlike its neighbours, because there is nothing of
ours to register yet: Composio owns the OAuth flow for every app it fronts, so
there are no redirect routes here to mount. Connecting an account is a Connect
Link Composio issues and the operator opens; our side begins after that, at the
tool call. The loader imports this package anyway, which is harmless and keeps
it visible where the other integrations are.

The tool schema and execution live in ``client.py`` and are wired into the tool
dispatcher in ``api/services/workflow/pipecat_engine_custom_tools.py``, the same
seam Google Calendar uses.
"""

from __future__ import annotations

__all__: list[str] = []
