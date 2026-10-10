"""The one place this package asks a model anything.

Three callers: proposing lesson deltas (``lessons``), running a case for the
evaluation gate (``evaluate``) and drafting a remembered skill
(``remember``). Each passes a system prompt and one user message and gets
back parsed JSON plus what the call used, so the cost of every candidate is
counted from the vendor's own usage rather than estimated.

Attributed to the workspace under ``evolve_skills`` in the model-usage
ledger, the same way Decibyl's own turns are. Tests replace :func:`ask_json`
with a stand-in; nothing in the suite reaches a provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from api.db import db_client
from api.services.billing import model_usage

FEATURE = "evolve_skills"


@dataclass
class Spend:
    """What proposing or testing one candidate has cost so far."""

    model_calls: int = 0
    tokens: int = 0
    notes: list[str] = field(default_factory=list)

    def add(self, usage: dict[str, int] | None) -> None:
        self.model_calls += 1
        usage = usage or {}
        self.tokens += int(usage.get("prompt_tokens") or 0) + int(
            usage.get("completion_tokens") or 0
        )

    def as_dict(self) -> dict[str, int]:
        return {"model_calls": self.model_calls, "tokens": self.tokens}


class ModelUnavailable(RuntimeError):
    """No model could be asked. The job records nothing and tries later."""


async def ask_json(
    organization_id: int, *, system: str, user: str, spend: Spend | None = None
) -> dict[str, Any]:
    """One question, one JSON answer. Raises :class:`ModelUnavailable`."""
    from api.services.agent_builder import client, settings
    from api.services.gen_ai.json_parser import parse_llm_json

    try:
        async with db_client.async_session() as session:
            model = await settings.resolve_for_organization(
                session, None, organization_id=organization_id
            )
        conversation = client.Conversation()
        conversation.add_user(user)
        with model_usage.scope(organization_id=organization_id, feature=FEATURE):
            reply = await client.complete(
                provider=model.provider,
                model=model.model,
                api_key=model.api_key,
                system=system,
                conversation=conversation,
                tools=[],
            )
    except Exception as exc:  # noqa: BLE001 - offline work; try again later
        logger.warning("evolve: no model answered for org {}: {}", organization_id, exc)
        raise ModelUnavailable(str(exc)) from exc
    if spend is not None:
        spend.add(reply.usage)
    try:
        parsed = parse_llm_json(reply.text or "")
    except Exception:  # noqa: BLE001 - an unparseable answer is no answer
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["FEATURE", "ModelUnavailable", "Spend", "ask_json"]
