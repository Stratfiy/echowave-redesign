"""Main QA analysis orchestrator — per-node and whole-call fallback."""

import json
import re
from typing import Any

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext

from api.db.models import WorkflowRunModel
from api.services.gen_ai.json_parser import parse_llm_json
from api.services.managed_model_services import get_mps_correlation_id
from api.services.pipecat.service_factory import create_llm_service_from_provider
from api.services.workflow.dto import QANodeData
from api.services.workflow.node_specs.constants import DEFAULT_QA_SYSTEM_PROMPT
from api.services.workflow.qa.conversation import (
    build_conversation_structure,
    format_transcript,
    split_events_by_node,
)
from api.services.workflow.qa.llm_config import (
    accumulate_token_usage,
    resolve_llm_config,
)
from api.services.workflow.qa.metrics import compute_call_metrics
from api.services.workflow.qa.node_summary import (
    CONVERSATION_SUMMARY_SYSTEM_PROMPT,
    ensure_node_summaries,
    get_node_summary_text,
)
from api.services.workflow.qa.tracing import (
    add_qa_span_to_trace,
    setup_langfuse_parent_context,
)
from api.utils.template_renderer import render_template


async def _run_llm_inference(
    llm, messages: list[dict], system_prompt: str, usage_total: dict | None = None
) -> str | None:
    """Run a one-shot LLM inference using the pipecat service.

    ``usage_total`` accumulates what every inference on this run spent, so the
    caller can hand one figure to billing. Post-call analysis is real model
    usage on our own key for a managed account — the same tokens at the same
    price as the call itself — and until it was recorded here it was charged to
    nobody.
    """
    context = LLMContext()
    context.set_messages(messages)
    text = await llm.run_inference(context, system_instruction=system_prompt)
    if usage_total is not None:
        accumulate_token_usage(usage_total, getattr(llm, "last_inference_usage", None))
    return text


#: What the QA response is contractually parsed into, regardless of what an
#: operator's system prompt asks the model for. Anything else the model
#: returns used to be silently discarded — an operator who added their own
#: extraction instructions to ``qa_system_prompt`` got an LLM call that ran,
#: was billed, and threw its own answer away.
_RESERVED_QA_KEYS = frozenset(
    {"tags", "summary", "call_quality_score", "overall_sentiment"}
)


def _extracted_data(parsed: dict[str, Any]) -> dict[str, Any]:
    """Whatever the model returned beyond the fixed QA fields.

    This is the whole of what makes a custom extraction — a lead score, an
    appointment time, a sentiment label with the operator's own categories —
    possible today without new runtime machinery: the QA LLM call already
    runs once per call on a prompt the operator already controls
    (``qa_system_prompt``/``QANodeData``), and the JSON it returns already
    carries anything asked for. This function is the one place that used to
    throw it away, and now doesn't.

    Empty rather than absent when there is nothing extra, so a reader can
    always index ``node_result["extracted_data"]`` without a KeyError — the
    same guarantee every other usage_info-shaped dict in this codebase gives.
    """
    return {k: v for k, v in parsed.items() if k not in _RESERVED_QA_KEYS}


def _system_prompt(qa_data: QANodeData) -> str:
    """The reviewer's instructions, falling back to the platform default.

    A review node created anywhere but the canvas editor carries no prompt: the
    default lives in the node *spec*, which is what pre-fills the textarea, and
    a node built in code never passes through a textarea. Both analysis paths
    used to answer that with ``no_system_prompt`` and return nothing, so the
    switch read as on, the call was reviewed by nobody, and the Analysis tab
    was empty for exactly the reason it looked like it should not be.

    Falling back here rather than writing the prompt into every new node keeps
    one copy of it: an operator who never touches it gets improvements to the
    default, and one who does still overrides it, because their own text is
    what this returns.
    """
    return qa_data.qa_system_prompt or DEFAULT_QA_SYSTEM_PROMPT


def _extraction_key(name: str) -> str:
    """A named extraction's display name, made safe as a JSON object key.

    An operator types "Lead Score" in the node editor; the model is asked to
    return it as ``lead_score`` so it round-trips through ``json.loads``
    without a reader having to guess the exact casing and punctuation someone
    typed into a form field.
    """
    key = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return key or "field"


def render_extraction_instructions(extractions: list) -> str:
    """The prompt fragment asking for an operator's named extractions.

    Appended to the QA system prompt, never sent as a separate LLM call —
    that is the whole reason adding an extraction does not add a new charge:
    it rides on the QA pass's one inference instead of paying for its own.
    Written to fit after the QA prompt's own "Output format" section, which
    is why it says *"alongside"* rather than restating what a JSON object is.

    Empty string when there is nothing to add, so a caller can always
    concatenate the result onto a prompt without an ``if`` at the call site.
    """
    if not extractions:
        return ""

    lines = [
        "",
        "## Additional fields to extract",
        "",
        "Include these keys in the same JSON object, alongside the fields "
        "above. Use `null` for any you cannot determine from the transcript.",
        "",
    ]
    for spec in extractions:
        key = _extraction_key(getattr(spec, "name", "") or "")
        prompt = (getattr(spec, "prompt", "") or "").strip()
        answer_type = getattr(spec, "answer_type", "free_text")
        if answer_type == "predefined":
            options = (getattr(spec, "predefined_options", "") or "").strip()
            shape = (
                f"one of: {options}" if options else "one of your configured options"
            )
        else:
            shape = {
                "numeric": "a number",
                "boolean": "true or false",
                "timestamp": "a date or time, in the caller's own words if exact isn't stated",
                "email": "an email address",
            }.get(getattr(spec, "expected_format", "text"), "text")
        lines.append(f"- `{key}` ({shape}): {prompt}")

    return "\n".join(lines)


async def _generate_conversation_summary(
    llm,
    model: str,
    transcript: str,
    parent_ctx,
    node_name: str,
    previous_summary: str = "",
    usage_total: dict | None = None,
) -> str:
    """Summarise the conversation so far, folding in the summary we already had.

    Traced to Langfuse as conversation-summary-before-{node_name}.

    ``transcript`` is only the stretch since the last summary, and
    ``previous_summary`` is what we concluded before it. That is the whole
    optimisation: this used to
    be handed the entire conversation from the top on every node, so an n-node
    call re-read node 1's transcript n times and the input grew with every step
    — quadratic tokens, and a wait that got longer the further into the call the
    node was. Folding makes each step's input roughly constant: one summary plus
    one node.

    The trade is that later summaries are summaries-of-summaries rather than of
    the raw transcript. For "what happened before this node", which is the
    question the QA prompt actually asks, that is the same answer at a fraction
    of the tokens.
    """
    conversation = (
        f"## Summary of the conversation so far\n{previous_summary}\n\n"
        f"## Conversation since then\n{transcript}"
        if previous_summary
        else f"## Conversation\n{transcript}"
    )
    messages = [
        {"role": "user", "content": conversation},
    ]

    try:
        summary = (
            await _run_llm_inference(
                llm, messages, CONVERSATION_SUMMARY_SYSTEM_PROMPT, usage_total
            )
            or ""
        )

        span_name = f"conversation-summary-before-{node_name}"
        add_qa_span_to_trace(
            parent_ctx,
            model,
            messages,
            summary,
            span_name,
            CONVERSATION_SUMMARY_SYSTEM_PROMPT,
        )

        return summary
    except Exception as e:
        logger.warning(
            f"Failed to generate conversation summary before {node_name}: {e}"
        )
        return ""


async def run_per_node_qa_analysis(
    qa_data: QANodeData,
    workflow_run: WorkflowRunModel,
    workflow_run_id: int,
    workflow_definition: dict,
    definition_id: int | None,
) -> dict[str, Any]:
    """Run per-node QA analysis on a completed workflow run.

    Splits the call by node, generates per-node summaries and conversation
    context, then evaluates each node segment individually.

    Falls back to whole-call QA if events lack node_id.

    Returns:
        Dict with node_results, model
    """
    logs = workflow_run.logs or {}
    rtf_events = logs.get("realtime_feedback_events", [])
    if not rtf_events:
        logger.warning(f"No realtime_feedback_events for run {workflow_run_id}")
        return {"error": "no_transcript", "node_results": {}}

    # Try to split by node
    node_splits = split_events_by_node(rtf_events)
    if not node_splits:
        # Fall back to whole-call QA
        logger.info(
            f"Events lack node_id for run {workflow_run_id}, falling back to whole-call QA"
        )
        return await _run_whole_call_qa_analysis(qa_data, workflow_run, workflow_run_id)

    system_prompt = _system_prompt(qa_data)
    # Appended once, outside the per-node loop below: the instructions are the
    # same for every node's pass, and computing them per-iteration would just
    # rebuild the identical string once per node in the call.
    system_prompt += render_extraction_instructions(
        getattr(qa_data, "qa_extractions", None) or []
    )

    # Resolve LLM config
    provider, model, api_key, service_kwargs = await resolve_llm_config(
        qa_data, workflow_run
    )
    if not api_key:
        logger.warning(
            f"No LLM API key configured for QA analysis on run {workflow_run_id}"
        )
        return {"error": "no_api_key", "node_results": {}}

    # Tokens spent by this analysis, handed back for billing. Post-call QA runs
    # real inference on our own key for a managed account; recording it here is
    # what stops it being charged to nobody. Declared before the first
    # inference, which is the summary backfill below.
    usage_total: dict = {}

    # Ensure node summaries
    node_summaries = await ensure_node_summaries(
        workflow_definition, definition_id, workflow_run, qa_data, usage_total
    )

    # Set up Langfuse tracing
    parent_ctx = setup_langfuse_parent_context(workflow_run)

    # Build LLM service. Reuse the run's MPS correlation id (minted at run
    # start, persisted on initial_context) so managed-model-services calls carry
    # billing-v2 markers — orgs on billing v2 reject managed calls that lack them.
    mps_correlation_id = get_mps_correlation_id(
        getattr(workflow_run, "initial_context", None)
    )
    llm = create_llm_service_from_provider(
        provider, model, api_key, correlation_id=mps_correlation_id, **service_kwargs
    )

    node_results: dict[str, Any] = {}
    # Only the node most recently seen, plus the summary that already folded in
    # everything before it. The whole conversation used to be kept here and
    # re-summarised from the top at every node, which made the token bill and
    # the wait grow with the square of the node count.
    pending_conversation: list[dict] = []
    running_summary = ""

    for idx, (node_id, node_name, node_events) in enumerate(node_splits):
        # Build this node's conversation and transcript
        node_conversation = build_conversation_structure(node_events)
        node_transcript = format_transcript(node_conversation)
        if not node_transcript:
            continue

        # Compute per-node metrics
        node_metrics = compute_call_metrics(node_events)

        # Get node summary
        node_summary = get_node_summary_text(node_summaries, node_id)

        # Fold everything before this node into one running summary. The input
        # is one summary plus one node's transcript, whatever the node count.
        previous_conversation_summary = running_summary
        if idx > 0 and pending_conversation:
            running_summary = await _generate_conversation_summary(
                llm,
                model,
                format_transcript(pending_conversation),
                parent_ctx,
                node_name,
                previous_summary=running_summary,
                usage_total=usage_total,
            )
            pending_conversation = []
            previous_conversation_summary = running_summary

        # Substitute placeholders in the user's system prompt
        template_context = {
            "node_summary": node_summary,
            "previous_conversation_summary": previous_conversation_summary,
            "transcript": node_transcript,
            "metrics": json.dumps(node_metrics, indent=2),
        }
        system_content = render_template(system_prompt, template_context)

        messages = [
            {"role": "user", "content": f"## Transcript\n{node_transcript}"},
        ]

        # Call QA LLM
        try:
            raw_response = await _run_llm_inference(
                llm, messages, system_content, usage_total
            )
        except Exception as e:
            logger.error(
                f"QA LLM call failed for node '{node_name}' on run {workflow_run_id}: {e}"
            )
            node_results[node_id] = {
                "node_name": node_name,
                "error": str(e),
                "tags": [],
                "summary": "",
                "score": None,
                "extracted_data": {},
            }
            pending_conversation.extend(node_conversation)
            continue

        # Trace
        span_name = f"qa-node-{node_name}"
        add_qa_span_to_trace(
            parent_ctx, model, messages, raw_response, span_name, system_content
        )

        # Parse response
        node_result: dict[str, Any] = {
            "node_name": node_name,
            "raw_response": raw_response,
        }
        try:
            parsed = parse_llm_json(raw_response)
            # parse_llm_json can return a list (e.g. when the model emits a
            # top-level JSON array); coerce non-dict results so the .get()
            # lookups below don't raise AttributeError.
            if not isinstance(parsed, dict):
                logger.warning(
                    f"QA LLM returned non-object JSON for node '{node_name}' "
                    f"on run {workflow_run_id}; got {type(parsed).__name__}, "
                    "using empty QA result"
                )
                parsed = {}
            node_result["tags"] = parsed.get("tags", [])
            node_result["summary"] = parsed.get("summary", "")
            node_result["score"] = parsed.get("call_quality_score")
            node_result["overall_sentiment"] = parsed.get("overall_sentiment")
            node_result["extracted_data"] = _extracted_data(parsed)
        except (json.JSONDecodeError, ValueError):
            node_result["tags"] = []
            node_result["summary"] = ""
            node_result["score"] = None
            node_result["extracted_data"] = {}

        node_results[node_id] = node_result

        # Held until the next node folds it into the running summary.
        pending_conversation.extend(node_conversation)

    return {
        "node_results": node_results,
        # What this analysis actually spent. run_integrations writes it onto the
        # run's usage_info, where the cost engine prices it as an ordinary LLM
        # line at the same rate and markup as the call's own tokens.
        "token_usage": usage_total,
        # The provider travels with the model because the rate card is keyed by
        # both. Dropping it here is what made QA tokens unpriceable: the usage
        # key was built from a label, and a label is not a vendor with a rate.
        "provider": provider,
        "model": model,
    }


async def _run_whole_call_qa_analysis(
    qa_data: QANodeData,
    workflow_run: WorkflowRunModel,
    workflow_run_id: int,
) -> dict[str, Any]:
    """Run whole-call QA analysis (fallback when events lack node_id).

    Returns results wrapped in the per-node format for consistency.
    """
    logs = workflow_run.logs or {}
    rtf_events = logs.get("realtime_feedback_events", [])
    if not rtf_events:
        logger.warning(f"No realtime_feedback_events for run {workflow_run_id}")
        return {"error": "no_transcript", "node_results": {}}

    conversation = build_conversation_structure(rtf_events)
    transcript = format_transcript(conversation)
    if not transcript:
        logger.warning(f"Empty transcript for run {workflow_run_id}")
        return {"error": "empty_transcript", "node_results": {}}

    # Compute call metrics
    usage_info = workflow_run.usage_info or {}
    call_duration = usage_info.get("call_duration_seconds")
    metrics = compute_call_metrics(rtf_events, call_duration)

    # Resolve LLM config
    system_prompt = _system_prompt(qa_data)
    system_prompt += render_extraction_instructions(
        getattr(qa_data, "qa_extractions", None) or []
    )

    provider, model, api_key, service_kwargs = await resolve_llm_config(
        qa_data, workflow_run
    )

    if not api_key:
        logger.warning(
            f"No LLM API key configured for QA analysis on run {workflow_run_id}"
        )
        return {"error": "no_api_key", "node_results": {}}

    # Build messages — substitute all placeholders with sensible defaults
    template_context = {
        "node_summary": "",
        "previous_conversation_summary": "",
        "transcript": transcript,
        "metrics": json.dumps(metrics, indent=2),
    }
    system_content = render_template(system_prompt, template_context)
    messages = [
        {"role": "user", "content": f"## Transcript\n{transcript}"},
    ]

    # Build LLM service. Reuse the run's MPS correlation id so managed-model
    # calls carry billing-v2 markers (see run_per_node_qa_analysis).
    mps_correlation_id = get_mps_correlation_id(
        getattr(workflow_run, "initial_context", None)
    )
    llm = create_llm_service_from_provider(
        provider, model, api_key, correlation_id=mps_correlation_id, **service_kwargs
    )

    try:
        # Whole-call QA is one inference, but it is the same real spend as the
        # per-node path and needs recording for the same reason.
        usage_total: dict = {}
        raw_response = await _run_llm_inference(
            llm, messages, system_content, usage_total
        )
    except Exception as e:
        logger.error(f"QA LLM call failed for run {workflow_run_id}: {e}")
        return {"error": str(e), "node_results": {}}

    # Parse response
    node_result: dict[str, Any] = {
        "node_name": "whole_call",
        "raw_response": raw_response,
    }
    try:
        parsed = parse_llm_json(raw_response)
        # parse_llm_json can return a list (e.g. when the model emits a
        # top-level JSON array); coerce non-dict results so the .get()
        # lookups below don't raise AttributeError.
        if not isinstance(parsed, dict):
            logger.warning(
                f"QA LLM returned non-object JSON for whole-call QA on run "
                f"{workflow_run_id}; got {type(parsed).__name__}, using empty "
                "QA result"
            )
            parsed = {}
        node_result["tags"] = parsed.get("tags", [])
        node_result["summary"] = parsed.get("summary", "")
        node_result["score"] = parsed.get("call_quality_score")
        node_result["overall_sentiment"] = parsed.get("overall_sentiment")
        node_result["extracted_data"] = _extracted_data(parsed)
    except (json.JSONDecodeError, ValueError):
        node_result["tags"] = []
        node_result["summary"] = ""
        node_result["score"] = None
        node_result["extracted_data"] = {}

    # Langfuse tracing
    parent_ctx = setup_langfuse_parent_context(workflow_run)
    add_qa_span_to_trace(
        parent_ctx, model, messages, raw_response, "qa-analysis", system_content
    )

    return {
        "node_results": {"whole_call": node_result},
        "provider": provider,
        "model": model,
        "token_usage": usage_total,
    }
