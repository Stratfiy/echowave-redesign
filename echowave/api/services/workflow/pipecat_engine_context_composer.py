"""System prompt and function schema composition for PipecatEngine nodes.

Extracts prompt and function composition logic from PipecatEngine into
reusable functions. Defines recording response mode markers and instructions.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional
from zoneinfo import ZoneInfo

from api.services.pipecat import agent_end_call

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
    from api.services.workflow.workflow_graph import Node, WorkflowGraph

from api.constants import DEFAULT_ORGANIZATION_TIMEZONE
from api.services.workflow.pipecat_engine_custom_tools import get_function_schema
from api.services.workflow.speaking_style import CODE_MIXED_INSTRUCTIONS
from api.services.workflow.tools.knowledge_base import get_knowledge_base_tool

# ---------------------------------------------------------------------------
# Recording response mode markers
# ---------------------------------------------------------------------------

RECORDING_MARKER = "●"  # Play pre-recorded audio
TTS_MARKER = "▸"  # Generate dynamic TTS text

# ---------------------------------------------------------------------------
# Recording response mode system prompt instructions
# ---------------------------------------------------------------------------

RECORDING_RESPONSE_MODE_INSTRUCTIONS = """\
RESPONSE MODE INSTRUCTIONS - MANDATORY FORMAT:
Every response you generate MUST begin with excatcly one response mode indicator.
You have two modes for responding:

1. DYNAMIC SPEECH (▸): Generate text that will be converted to speech by TTS.
   Format: ▸ followed by a space and your full spoken response. Nothing else.
   Example: ▸ Hello! How can I help you today?

2. PRE-RECORDED AUDIO (●): Play a pre-recorded audio message.
   Format: ● followed by a space followed by recording_id followed by provided transcript. Nothing else.
   Example: ● rec_greeting_01 [ Provided Transcript ]

RULES:
- Your response MUST start with either ▸ or ● as the very first character.
- For ▸ (dynamic speech): Follow with a space and your response to be generated using TTS engine. Dont mix with ●
- For ● (pre-recorded audio): Follow with a space and recording_id of the audio clip with its transcript. Dont mix with ▸
- Use ● when a pre-recorded message matches the situation well.
- Use ▸ when you need to generate a dynamic, contextual response.
- *NEVER* mix modes in a single response, since we rely on the markers to decide whether to play using TTS or Pre-recorded audio."""


def compose_today_line(timezone: str | None = None) -> str:
    """What day it is, for a model whose idea of "today" is its training cutoff.

    Asked the date, an agent here answered "June 13, 2024" — off by more than
    two years, and stated with the same confidence as everything else it says.
    That is harmless until something depends on it, and then it is not: every
    booking works from a date, so "tomorrow at five" resolves against a year
    that has already been and gone, and "next Tuesday" lands on the wrong day
    of the week as well.

    Nothing in the composed prompt carried a date, so there was nothing for the
    model to correct itself against. One line at the top fixes it for every
    agent at once, which is the right level: an operator should not have to
    know their agent needs telling what day it is.

    The weekday is spelled out because callers speak in weekdays, and the time
    because opening hours and "this evening" both depend on it.
    """
    zone = timezone or DEFAULT_ORGANIZATION_TIMEZONE
    try:
        now = datetime.now(ZoneInfo(zone))
    except Exception:  # noqa: BLE001 - an unknown zone must not stop a call
        now = datetime.now(ZoneInfo(DEFAULT_ORGANIZATION_TIMEZONE))
        zone = DEFAULT_ORGANIZATION_TIMEZONE
    return (
        f"Right now it is {now:%A, %d %B %Y}, {now:%H:%M} ({zone}). "
        "Work every date and time out from this, and never from anything you "
        "remember. When you need a calendar date, count it from today."
    )


def compose_system_prompt_for_node(
    *,
    node: "Node",
    workflow: "WorkflowGraph",
    format_prompt: Callable[[str], str],
    has_recordings: bool,
    code_mixed_speech: bool = False,
    opening_notes: str | None = None,
    today_line: str | None = None,
) -> str:
    """Compose the full system prompt text for a workflow node.

    Combines the global prompt, node-specific prompt, and (when recordings
    are enabled anywhere in the workflow) the recording response mode
    instructions into a single string.

    Args:
        node: The workflow node to compose the prompt for.
        workflow: The full workflow graph (needed for global node prompt).
        format_prompt: Callable to render template variables in prompts.
        has_recordings: Whether any node in the workflow uses recordings.
        code_mixed_speech: Whether to tell the model to speak the way callers
            here actually do — mixing English into the local language — rather
            than in the formal register it reaches for by default.
        opening_notes: Extra instructions about how this node opens, for a
            start node that lets the caller speak first. Appended after the
            operator's prompts so they read as the latest instruction.
        today_line: What day and time it is, worked out once for the whole
            call by the engine so the prompt stays byte-identical across node
            transitions and therefore stays cacheable. Composed here from the
            deployment default when a caller does not supply it.

    Returns:
        The composed system prompt text.
    """
    global_prompt = ""
    if workflow.global_node_id and node.add_global_prompt:
        global_node = workflow.nodes[workflow.global_node_id]
        global_prompt = format_prompt(global_node.prompt)

    formatted_node_prompt = format_prompt(node.prompt)

    # First, before the operator's own words: everything after it may depend on
    # what day it is, and a model that has already read "book them in for
    # Tuesday" has started reasoning from the wrong year.
    dated = today_line if today_line is not None else compose_today_line()
    parts = [p for p in (dated, global_prompt, formatted_node_prompt) if p]

    # After the operator's own prompts, so it reads as the most recent
    # instruction, and before the recording block, which is a response *format*
    # and has to be the last thing the model is told.
    if code_mixed_speech:
        parts.append(CODE_MIXED_INSTRUCTIONS)

    if opening_notes:
        parts.append(opening_notes)

    if has_recordings and "RECORDING_ID:" in formatted_node_prompt:
        parts.append(RECORDING_RESPONSE_MODE_INSTRUCTIONS)

    return "\n\n".join(parts)


async def compose_functions_for_node(
    *,
    node: "Node",
    custom_tool_manager: Optional["CustomToolManager"],
    agent_can_end_call: bool = False,
) -> list[dict]:
    """Compose the function/tool schemas for a workflow node.

    Gathers knowledge-base tools, custom tools (including built-in
    categories like calculator), and transition function schemas
    into a single list.

    Args:
        node: The workflow node to compose functions for.
        custom_tool_manager: Manager for custom and built-in tools (may be None).
        agent_can_end_call: Whether this agent may hang up on its own. Off by
            default -- an agent that can end a call will sometimes end one it
            should not have, and an operator who never asked for that would
            rather a caller sat through a confused turn than be cut off.

    Returns:
        A list of function schemas to register with the LLM.
    """
    functions: list[dict] = []

    # Knowledge base retrieval tool
    if node.document_uuids:
        kb_tool_def = get_knowledge_base_tool(node.document_uuids)
        kb_schema = get_function_schema(
            kb_tool_def["function"]["name"],
            kb_tool_def["function"]["description"],
            properties=kb_tool_def["function"]["parameters"].get("properties", {}),
            required=kb_tool_def["function"]["parameters"].get("required", []),
        )
        functions.append(kb_schema)

    # Custom tools
    if node.tool_uuids and custom_tool_manager:
        custom_tool_schemas = await custom_tool_manager.get_tool_schemas(
            node.tool_uuids,
            mcp_tool_filters=getattr(node, "mcp_tool_filters", None),
        )
        functions.extend(custom_tool_schemas)

    # Hanging up, for agents allowed to. Offered on every node including an
    # end node: a caller who has gone quiet on the last step is exactly who
    # this is for, and an end node has no transitions to reach instead.
    if agent_can_end_call:
        functions.append(
            get_function_schema(
                agent_end_call.TOOL_NAME,
                agent_end_call.DESCRIPTION,
                properties=agent_end_call.tool_properties(),
                required=["reason"],
            )
        )

    # Transition function schemas
    for outgoing_edge in node.out_edges:
        function_schema = get_function_schema(
            outgoing_edge.get_function_name(), outgoing_edge.condition
        )
        functions.append(function_schema)

    return functions
