from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from api.mcp_server.instructions import DECIBYL_MCP_INSTRUCTIONS
from api.mcp_server.tools.catalog import (
    list_credentials,
    list_documents,
    list_recordings,
    list_tools,
)
from api.mcp_server.tools.create_workflow import create_workflow
from api.mcp_server.tools.docs_search import list_docs, read_doc, search_docs
from api.mcp_server.tools.get_workflow_code import get_workflow_code
from api.mcp_server.tools.node_types import get_node_type, list_node_types
from api.mcp_server.tools.save_workflow import save_workflow
from api.mcp_server.tools.telephony_status import (
    get_billing_summary,
    get_telephony_verification,
    list_phone_numbers,
    search_available_numbers,
)
from api.mcp_server.tools.tool_creation import create_tool
from api.mcp_server.tools.voice_prompting_guide import get_voice_prompting_guide
from api.mcp_server.tools.workflows import get_workflow, list_workflows

mcp = FastMCP("decibyl", instructions=DECIBYL_MCP_INSTRUCTIONS)

for _tool in (
    create_workflow,
    create_tool,
    get_node_type,
    get_workflow,
    get_workflow_code,
    list_credentials,
    list_documents,
    list_node_types,
    list_recordings,
    list_tools,
    list_workflows,
    save_workflow,
):
    mcp.tool(_tool)

_GUIDE_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    idempotentHint=True,
    destructiveHint=False,
    openWorldHint=False,
)

mcp.tool(get_voice_prompting_guide, annotations=_GUIDE_TOOL_ANNOTATIONS)

_DOCS_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    idempotentHint=True,
    destructiveHint=False,
    openWorldHint=False,
)

for _tool in (list_docs, read_doc, search_docs):
    mcp.tool(_tool, annotations=_DOCS_TOOL_ANNOTATIONS)

# Telephony status. Read-only and annotated as such, so a client that respects
# hints knows none of these change anything.
#
# Provisioning and release are deliberately absent from this list. Buying a
# number spends money every month and a retry buys a second one; releasing one
# is irreversible at the carrier and the number may be printed on the
# customer's signage. Both stay behind the REST API, where a person makes the
# call. Reading is the part that is safe to automate.
_TELEPHONY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    idempotentHint=True,
    destructiveHint=False,
    # search_available_numbers reaches the carrier, so this set is not closed.
    openWorldHint=True,
)

for _tool in (
    get_telephony_verification,
    list_phone_numbers,
    search_available_numbers,
    get_billing_summary,
):
    mcp.tool(_tool, annotations=_TELEPHONY_TOOL_ANNOTATIONS)
