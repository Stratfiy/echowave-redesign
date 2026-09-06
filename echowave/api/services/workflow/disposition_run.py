"""Classifying a finished call, using the machinery QA already set up.

Kept apart from ``disposition.py`` for the same reason ``spoken_digits`` is
kept apart from its frame processor: the rules — what the taxonomy is, what a
valid answer looks like — are worth being able to test without a model, a
database or a pipeline. This module is only the part that talks to the model.

It deliberately reuses QA's LLM resolution, correlation id and token
accumulator rather than building its own. A second way of choosing which model
runs post-call work is a second thing to keep in step with billing, and the
first one to fall out of step would be this one, because it is the newer.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.db.models import WorkflowRunModel
from api.services.gen_ai.json_parser import parse_llm_json
from api.services.managed_model_services import get_mps_correlation_id
from api.services.pipecat.service_factory import create_llm_service_from_provider
from api.services.workflow.disposition import (
    UNCLEAR,
    build_prompt,
    coerce_result,
    parse_taxonomy,
)
from api.services.workflow.qa.analysis import _run_llm_inference
from api.services.workflow.qa.llm_config import resolve_user_llm_config


async def classify_call(
    *,
    workflow_run: WorkflowRunModel,
    transcript: str,
    disposition_codes: Any,
) -> dict[str, Any]:
    """Decide what this call achieved.

    Always returns a result. A model that errors, times out or answers
    nonsense produces ``[unclear]`` with the reason recorded, because a call
    with no disposition is invisible in the one view this feature exists to
    populate — and "we could not tell" is a fact somebody can act on, while a
    blank is not.
    """
    taxonomy = parse_taxonomy(disposition_codes)

    if not (transcript or "").strip():
        # Nothing was said. Classifying silence would be inventing an outcome.
        return {
            "dispositions": [UNCLEAR],
            "reason": "no_transcript",
            "taxonomy": [entry["code"] for entry in taxonomy],
        }

    spend: dict = {}

    try:
        # The workflow's own model, not a QA node's override: disposition is
        # a property of the call, not of a QA step, and an agent with no QA
        # node still needs classifying.
        provider, model, api_key, service_kwargs = await resolve_user_llm_config(
            workflow_run
        )
        llm = create_llm_service_from_provider(
            provider,
            model,
            api_key,
            correlation_id=get_mps_correlation_id(
                getattr(workflow_run, "initial_context", None)
            ),
            **service_kwargs,
        )
        answer = await _run_llm_inference(
            llm,
            [{"role": "user", "content": transcript}],
            build_prompt(taxonomy),
            spend,
        )
    except Exception as error:  # noqa: BLE001 - post-call work must not fail the run
        logger.warning(f"Disposition classification failed: {error}")
        return {
            "dispositions": [UNCLEAR],
            "reason": "classification_failed",
            "taxonomy": [entry["code"] for entry in taxonomy],
        }

    # Reported so the caller can put it on the run's usage_info. These are real
    # tokens on our own key for a managed account; QA's were charged to nobody
    # until somebody noticed, and a second post-call LLM call is exactly the
    # place that mistake gets made twice.
    return {
        "dispositions": coerce_result(parse_llm_json(answer), taxonomy),
        "taxonomy": [entry["code"] for entry in taxonomy],
        "provider": provider,
        "model": model,
        "token_usage": spend,
    }
