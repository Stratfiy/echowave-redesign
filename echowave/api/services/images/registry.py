"""Which image providers exist, and which the platform itself can run.

The three are listed in the order the card shows them. A provider the
platform holds credentials for (``GEMINI_API_KEY``, ``OPENAI_API_KEY``, or
Bedrock on the box's own role with ``IMAGE_BEDROCK_ENABLED``) is ready for
every workspace without a key; any other needs the workspace's own.
"""

from __future__ import annotations

from dataclasses import dataclass

from api import constants
from api.services.images.providers.base import ImageProvider
from api.services.images.providers.bedrock import BedrockImages
from api.services.images.providers.gemini import GeminiImages
from api.services.images.providers.openai import OpenAIImages


@dataclass(frozen=True)
class ProviderInfo:
    """What the card says about one provider."""

    name: str
    label: str
    #: What a person pastes, in their words.
    key_label: str
    #: Where that key comes from, said in one line.
    key_hint: str
    blurb: str


PROVIDERS: dict[str, ImageProvider] = {
    "google": GeminiImages(),
    "openai": OpenAIImages(),
    "aws_bedrock": BedrockImages(),
}

INFO: dict[str, ProviderInfo] = {
    "google": ProviderInfo(
        name="google",
        label="Google Gemini",
        key_label="Gemini API key",
        key_hint="From Google AI Studio, under Get API key.",
        blurb="Gemini image models, by Google.",
    ),
    "openai": ProviderInfo(
        name="openai",
        label="OpenAI",
        key_label="OpenAI API key",
        key_hint="From the OpenAI platform, under API keys.",
        blurb="GPT Image models, by OpenAI.",
    ),
    "aws_bedrock": ProviderInfo(
        name="aws_bedrock",
        label="Amazon Bedrock",
        key_label="Bedrock API key",
        key_hint=(
            "From the Amazon Bedrock console, under API keys, with Nova "
            f"Canvas enabled in {constants.IMAGE_BEDROCK_REGION}."
        ),
        blurb="Amazon Nova Canvas, on an AWS account.",
    ),
}


def get(name: str | None) -> ImageProvider | None:
    return PROVIDERS.get((name or "").strip().lower())


def names() -> list[str]:
    return list(PROVIDERS)


def platform_key(name: str) -> str | None:
    """The platform's own key for ``name``, or None.

    Bedrock has no key on the platform path -- it signs with the box's role
    -- so it answers with an empty string when that path is switched on, and
    the caller reads "" as "ready, no key to send"."""
    if name == "google":
        return constants.IMAGE_GEMINI_API_KEY or None
    if name == "openai":
        return constants.IMAGE_OPENAI_API_KEY or None
    if name == "aws_bedrock":
        return "" if constants.IMAGE_BEDROCK_ENABLED else None
    return None


def platform_ready(name: str) -> bool:
    return platform_key(name) is not None
