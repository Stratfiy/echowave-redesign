"""What the AWS gateway is configured to do, and whether each part can.

Read at call time from ``api.constants`` so a test can monkeypatch a value
and an operator's change needs only a restart. Nothing has a vendor default:
a model id or a region the environment does not name is a "needs setup"
with the variable to set, never a guess.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

from loguru import logger

from api import constants

ANTHROPIC = "anthropic"
AWS_PLATFORM = "aws_platform"
BEDROCK = "bedrock"
BACKENDS = (ANTHROPIC, AWS_PLATFORM, BEDROCK)

#: The provider names usage is recorded and priced under, so a receipt says
#: which door a token went through. Claude Platform on AWS is billed through
#: AWS, Bedrock is AWS's own service; neither is Anthropic's first-party API.
USAGE_PROVIDER = {
    ANTHROPIC: "anthropic",
    AWS_PLATFORM: "anthropic_aws",
    BEDROCK: "aws_bedrock",
}

AVAILABLE = "available"
NEEDS_SETUP = "needs_setup"
DISABLED = "disabled"

#: The vector column holds this many numbers (db/models.py, ``Vector(1536)``).
VECTOR_DIMENSIONS = 1536

#: Speech-to-speech on Nova Sonic is offered for these languages only.
#: Sarvam stays the default for every Indian language, these two included.
NOVA_SONIC_LANGUAGES = frozenset({"hi", "hi-in", "en-in"})


@dataclass(frozen=True)
class Status:
    """One capability state, with the reason a person can act on."""

    state: str
    reason: str = ""
    #: Whether the operator has configured this at all. A choice that is
    #: configured but still waiting on model access is listed (as needs
    #: setup); one nobody has configured is not offered anywhere.
    configured: bool = True

    @property
    def available(self) -> bool:
        return self.state == AVAILABLE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _available() -> Status:
    return Status(AVAILABLE)


def _needs(reason: str) -> Status:
    return Status(NEEDS_SETUP, reason)


def _unset(reason: str) -> Status:
    return Status(NEEDS_SETUP, reason, configured=False)


def _disabled(reason: str) -> Status:
    return Status(DISABLED, reason, configured=False)


# --- Claude's backend ---------------------------------------------------------


def claude_backend() -> str:
    """The configured backend. An unknown value is Anthropic, said loudly."""
    value = (constants.CLAUDE_BACKEND or ANTHROPIC).strip().lower()
    if value not in BACKENDS:
        logger.error(
            "CLAUDE_BACKEND={!r} is not one of {}; Claude stays on Anthropic's API.",
            value,
            ", ".join(BACKENDS),
        )
        return ANTHROPIC
    return value


def _pairs(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (raw or "").split(","):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key.strip() and value.strip():
            out[key.strip()] = value.strip()
    return out


def bedrock_claude_models() -> dict[str, str]:
    """Claude model -> the Bedrock model id that serves it, from configuration."""
    return _pairs(constants.BEDROCK_CLAUDE_MODEL_IDS)


def bedrock_model_for(model: str) -> str | None:
    """The Bedrock id for a Claude model, or None when none is configured.

    An id that is already a Bedrock id (``anthropic.`` or an inference-profile
    prefix) is taken as given, so a tier pinned to one keeps working."""
    if not model:
        return None
    mapped = bedrock_claude_models().get(model)
    if mapped:
        return mapped
    if "anthropic." in model:
        return model
    return None


def aws_platform_status() -> Status:
    if not constants.CLAUDE_AWS_REGION:
        return _unset(
            "Set CLAUDE_AWS_REGION (or AWS_REGION) for Claude Platform on AWS."
        )
    if not constants.ANTHROPIC_AWS_WORKSPACE_ID:
        return _unset("Set ANTHROPIC_AWS_WORKSPACE_ID to the Claude workspace on AWS.")
    return _available()


def claude_backend_status(model: str | None = None) -> Status:
    """Whether the configured backend can serve Claude (``model`` if given)."""
    backend = claude_backend()
    if backend == ANTHROPIC:
        return _available()
    if backend == AWS_PLATFORM:
        return aws_platform_status()
    if not constants.BEDROCK_REGION:
        return _unset("Set BEDROCK_REGION (or AWS_REGION) for Claude on Bedrock.")
    if model is None:
        mapped = bedrock_claude_models()
        if not mapped:
            return _unset(
                "Set BEDROCK_CLAUDE_MODEL_IDS to map each Claude model to its "
                "Bedrock model id."
            )
        waiting = [
            bedrock_id
            for bedrock_id in mapped.values()
            if not bedrock_model_status(bedrock_id).available
        ]
        if waiting:
            return _needs(
                f"Model access is not confirmed for {', '.join(waiting)}. Enable "
                "it in the Bedrock console, then add each to BEDROCK_ENABLED_MODELS."
            )
        return _available()
    bedrock_id = bedrock_model_for(model)
    if bedrock_id is None:
        return _unset(f"No Bedrock model id is configured for {model}.")
    return bedrock_model_status(bedrock_id)


# --- Bedrock model access -------------------------------------------------------

#: Models AWS refused at runtime (AccessDenied: access not granted or the
#: agreement not accepted), until when. A refusal outranks the operator's
#: list: a model listed as enabled that AWS still refuses is "needs setup",
#: because that is what a person trying it will find.
_REFUSED: dict[str, tuple[float, str]] = {}
REFUSED_FOR_SECONDS = 10 * 60


def mark_not_authorized(model_id: str, why: str = "") -> None:
    """AWS refused this model; show it as needs setup for a while, loudly."""
    logger.error(
        "Bedrock refused {} ({}). Model access is not enabled for this account "
        "or region; it shows as needs setup until access is granted.",
        model_id,
        why or "access denied",
    )
    _REFUSED[model_id] = (time.monotonic() + REFUSED_FOR_SECONDS, why)


def clear_refusals() -> None:
    """For tests, and for an operator who has just granted access."""
    _REFUSED.clear()


def enabled_models() -> frozenset[str]:
    return frozenset(
        m.strip()
        for m in (constants.BEDROCK_ENABLED_MODELS or "").split(",")
        if m.strip()
    )


def bedrock_model_status(model_id: str, *, region: str | None = None) -> Status:
    """Whether this account may invoke ``model_id`` on Bedrock right now.

    ``region`` is for a model served from its own region (Nova Sonic);
    everything else runs in ``BEDROCK_REGION``."""
    if not model_id:
        return _unset("No Bedrock model id is configured.")
    if not (region or constants.BEDROCK_REGION):
        return _unset("Set BEDROCK_REGION (or AWS_REGION).")
    refused = _REFUSED.get(model_id)
    if refused is not None:
        until, _ = refused
        if time.monotonic() < until:
            return _needs(
                f"AWS refused {model_id}: enable model access for it in the "
                "Bedrock console (and accept the model's agreement)."
            )
        _REFUSED.pop(model_id, None)
    if model_id not in enabled_models():
        return _needs(
            f"Model access for {model_id} is not confirmed. Enable it in the "
            "Bedrock console, then add it to BEDROCK_ENABLED_MODELS."
        )
    return _available()


# --- the gateway's other choices ------------------------------------------------


def _flagged(flag: str) -> Status | None:
    from api.services import features

    if not features.is_on(flag):
        return _disabled(f"Switched off ({flag}).")
    return None


def fallback_status() -> Status:
    off = _flagged("aws_fallback_brain")
    if off is not None:
        return off
    if not constants.BEDROCK_FALLBACK_MODEL:
        return _unset("Set BEDROCK_FALLBACK_MODEL to the Bedrock model that stands in.")
    return bedrock_model_status(constants.BEDROCK_FALLBACK_MODEL)


def cheap_status() -> Status:
    off = _flagged("aws_cheap_tier")
    if off is not None:
        return off
    if not constants.BEDROCK_CHEAP_MODEL:
        return _unset("Set BEDROCK_CHEAP_MODEL to a small Bedrock model.")
    return bedrock_model_status(constants.BEDROCK_CHEAP_MODEL)


def embeddings_status() -> Status:
    off = _flagged("aws_embeddings")
    if off is not None:
        return off
    model = constants.BEDROCK_EMBEDDING_MODEL
    if not model:
        return _unset("Set BEDROCK_EMBEDDING_MODEL to a Bedrock embeddings model.")
    if constants.BEDROCK_EMBEDDING_DIMENSIONS != VECTOR_DIMENSIONS:
        return _needs(
            f"Knowledge search stores {VECTOR_DIMENSIONS} numbers per passage; "
            f"BEDROCK_EMBEDDING_DIMENSIONS is {constants.BEDROCK_EMBEDDING_DIMENSIONS}."
        )
    if not model.startswith("cohere.embed-v4"):
        # Titan stops at 1024 and Cohere's v3 models return 1024, so neither
        # can fill the column; only a model that returns 1536 is offered.
        return _needs(
            f"{model} cannot return {VECTOR_DIMENSIONS} numbers per passage; "
            "use a Cohere Embed v4 model."
        )
    return bedrock_model_status(model)


def nova_sonic_status() -> Status:
    off = _flagged("aws_nova_sonic")
    if off is not None:
        return off
    missing = [
        name
        for name, value in (
            ("NOVA_SONIC_MODEL", constants.NOVA_SONIC_MODEL),
            ("NOVA_SONIC_REGION", constants.NOVA_SONIC_REGION),
            ("NOVA_SONIC_VOICE", constants.NOVA_SONIC_VOICE),
        )
        if not value
    ]
    if missing:
        return _unset(f"Set {', '.join(missing)}.")
    return bedrock_model_status(
        constants.NOVA_SONIC_MODEL, region=constants.NOVA_SONIC_REGION
    )


def nova_sonic_allows(language: str | None) -> bool:
    """Nova Sonic is offered for Hindi and Indian English only."""
    return (language or "").strip().lower() in NOVA_SONIC_LANGUAGES


def overview() -> dict[str, dict[str, Any]]:
    """Every part of the gateway and its state, for the staff and models screens."""
    backend = claude_backend()
    return {
        "claude_backend": {
            "backend": backend,
            **claude_backend_status().as_dict(),
            # Per Claude model, on Bedrock: each is enabled on its own.
            "models": (
                {
                    model: claude_backend_status(model).as_dict()
                    for model in bedrock_claude_models()
                }
                if backend == BEDROCK
                else {}
            ),
        },
        "fallback_brain": {
            "model": constants.BEDROCK_FALLBACK_MODEL,
            **fallback_status().as_dict(),
        },
        "cheap_tier": {
            "model": constants.BEDROCK_CHEAP_MODEL,
            **cheap_status().as_dict(),
        },
        "embeddings": {
            "model": constants.BEDROCK_EMBEDDING_MODEL,
            **embeddings_status().as_dict(),
        },
        "nova_sonic": {
            "model": constants.NOVA_SONIC_MODEL,
            **nova_sonic_status().as_dict(),
        },
    }
