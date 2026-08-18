"""Starting agents for Indian verticals, ready to put on a phone number.

The authoring LLM is good at assembling a workflow and poor at two things a
template supplies: a prompt that survives a real caller, and a stack that suits
Indian traffic. Everything here exists to close that gap.

Public surface is deliberately small — list, get, search — because the callers
are an MCP tool and a REST route, and both only ever need to show a template or
hand one to a model.
"""

from api.services.agent_templates._base import (
    AgentTemplate,
    CallDirection,
    CallShape,
    RecommendedStack,
    TemplateEdge,
    TemplateNode,
)
from api.services.agent_templates.catalogue import (
    find_templates,
    get_template,
    list_templates,
)

__all__ = [
    "AgentTemplate",
    "CallDirection",
    "CallShape",
    "RecommendedStack",
    "TemplateEdge",
    "TemplateNode",
    "find_templates",
    "get_template",
    "list_templates",
]
