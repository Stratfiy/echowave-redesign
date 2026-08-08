"""Google Calendar integration package.

Self-registers on import via ``register_package``. Auto-discovered by
``api/services/integrations/loader.py``.

Unlike paygent/tuner, this package registers no workflow graph node — a
Calendar booking is not a fixed step in the call flow, it is an LLM-invoked
tool the agent decides to call mid-conversation (same shape as the HTTP API
and MCP tool types in ``api/services/workflow/tools/``). This package
provides only the OAuth connect flow's HTTP routes; the tool schema and
execution logic live in ``client.py`` and are wired into the tool dispatcher
in ``api/services/workflow/pipecat_engine_custom_tools.py``, not through the
``nodes=`` seam other integrations use.
"""

from __future__ import annotations

from api.services.integrations.base import IntegrationPackageSpec
from api.services.integrations.registry import register_package

from .routes import router

PACKAGE = register_package(
    IntegrationPackageSpec(
        name="google_calendar",
        routers=(router,),
    )
)

__all__ = ["PACKAGE"]
