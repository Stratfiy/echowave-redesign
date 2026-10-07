"""The cheap tier: a small Bedrock model for labelling and routing.

Off by default (``aws_cheap_tier``). When it is on and ``BEDROCK_CHEAP_MODEL``
(Amazon Nova Micro or Lite) is configured and enabled, Auto's "what kind of
work is this?" goes to it instead of Laya (``routing/decision.py``). It has
the same contract as Laya: a label from the fixed set or an abstention, and
the rules decide whenever it abstains. It never grants anything.

Only the message text is sent, trimmed, exactly as for Laya.
"""

from __future__ import annotations

import asyncio
import json
import re

from api import constants
from api.services.aws_gateway import bedrock, config

_INSTRUCTIONS = (
    "You sort a message into exactly one category. Reply with JSON only, "
    'in the form {"choice": "<category>", "confidence": <0 to 1>}. '
    "Categories:\n"
)
_JSON = re.compile(r"\{.*\}", re.DOTALL)


def available() -> bool:
    return config.cheap_status().available


async def choose(
    question: str, labels: dict[str, str], text: str, *, timeout_ms: int | None = None
) -> tuple[str | None, float | None, str | None]:
    """``(label, confidence, abstained)``; abstained names why there is no
    label: timeout, error or malformed."""
    from api.services.billing import model_usage

    model = constants.BEDROCK_CHEAP_MODEL
    system = _INSTRUCTIONS + "\n".join(f"- {k}: {v}" for k, v in labels.items())
    timeout = (timeout_ms or constants.BEDROCK_CHEAP_TIMEOUT_MS) / 1000
    try:
        response = await asyncio.wait_for(
            bedrock.converse(
                model_id=model,
                system=system,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"text": f"{question}\n\nMessage:\n{(text or '')[:4000]}"}
                        ],
                    }
                ],
                max_tokens=64,
                temperature=0.0,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return None, None, "timeout"
    except bedrock.BedrockError:
        return None, None, "error"
    with model_usage.labelled("routing"):
        await model_usage.record(
            provider=config.USAGE_PROVIDER[config.BEDROCK],
            model=model,
            usage=bedrock.converse_usage(response),
        )
    found = _JSON.search(bedrock.converse_text(response))
    try:
        answer = json.loads(found.group(0)) if found else {}
    except json.JSONDecodeError:
        answer = {}
    label = answer.get("choice")
    confidence = answer.get("confidence")
    if label not in labels or not isinstance(confidence, (int, float)):
        return None, None, "malformed"
    return label, float(confidence), None
