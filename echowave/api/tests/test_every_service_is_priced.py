"""Every model service the factory can build is priced, or declared unpriced.

The failure this prevents is the quietest one in billing. A service class whose
derived provider name has no rate row does not raise, does not fail a call and
does not appear on any screen. Its usage lands in ``CallCost.uncosted``, the
line contributes zero provider cost, and every margin figure downstream reads
better than it is — 100% margin on a call that cost us real money.

Two ways it happens, and this file catches both.

**A vendor nobody priced.** Straightforward, and often correct to leave: we do
not have to price a provider nobody is served by. What is not acceptable is
leaving it *silently* unpriced, so each one is named in ``_UNPRICED_BY_DESIGN``
with a reason somebody had to type.

**A vendor priced under a different name.** The subtler one.
``ElevenLabsRealtimeSTTService`` strips to ``elevenlabsrealtime``; the rate card
says ``elevenlabs``. Same vendor, same published price, no match — and nothing
says so. ``usage._PROVIDER_ALIASES`` closes those, and the point of enumerating
classes here rather than trusting the alias table is that a *new* class in the
factory fails this test on the day it is added.

The list is derived from the factory source rather than hand-maintained,
because a hand-maintained list of "every service we can build" is exactly the
thing that goes stale the first time somebody adds one.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from api.services.billing.default_rates import DEFAULT_RATES
from api.services.billing.usage import provider_from_processor

_FACTORY = pathlib.Path(__file__).resolve().parents[1] / (
    "services/pipecat/service_factory.py"
)

#: Providers with no rate row, and why. A reason is required — the whole value
#: of this file is that the gap was looked at rather than not noticed.
#:
#: Every one of these is reachable only by an account that names the vendor
#: explicitly. A component the customer runs on their own key produces no usage
#: item at all (see ``usage.usage_items_from_usage_info``), so the exposure is
#: an account running one of these on *our* platform key — which needs a
#: platform key we do not hold. Pricing them is a business decision that
#: follows a customer asking for one, not a prerequisite.
_UNPRICED_BY_DESIGN = {
    # --- self-hosted or bring-your-own-endpoint: no vendor price exists ------
    "speaches": "self-hosted; the operator's own hardware, no vendor invoice",
    "huggingface": "endpoint pricing depends on the customer's own deployment",
    "dograh": (
        "the Decibyl managed gateway itself. Reached only when a section still "
        "says 'decibyl' at pipeline build, which means managed resolution "
        "found no platform key — a misconfiguration to fix, not a rate to set"
    ),
    # --- vendors we hold no platform key for --------------------------------
    "awsbedrock": "no platform key; per-model Bedrock pricing varies by region",
    "azure": "no platform key; Azure pricing is per-deployment",
    "groq": "no platform key",
    "openrouter": "no platform key; OpenRouter prices per underlying model",
    "minimax": "no platform key",
    "minimaxownedsession": "no platform key",
    "assemblyai": "no platform key",
    "gladia": "no platform key",
    "speechmatics": "no platform key",
    "cartesiaturns": (
        "Cartesia transcription. Deliberately not aliased to 'cartesia', which "
        "prices synthesis — pricing a transcript at a TTS rate would be wrong "
        "silently, where no rate at least warns"
    ),
    "camb": "no platform key",
    "inworld": "no platform key",
    "rime": "no platform key",
    "xai": (
        "no platform key: xAI TTS is BYOK only, so the customer's own key "
        "pays xAI directly and Decibyl meters no cost for it. Keyed on "
        "'xai' rather than the former 'xaihttp' because the factory now "
        "builds XAITTSService, the websocket class, and the billing "
        "identity follows the class name"
    ),
    "cartesia_stt": (
        "Cartesia Ink transcription. Cartesia is priced for synthesis only, "
        "and the two are different products at different prices"
    ),
    "smallest_stt": "Smallest is priced for synthesis only",
    "deepgram_tts": "Deepgram Aura; we price Deepgram transcription only",
    "google_tts": "we price Google language models only",
    "google_stt": "we price Google language models only",
    "openai_stt": "Whisper; we price OpenAI language models and synthesis only",
    "decibylgoogle": "the managed gateway's Google path; see 'dograh'",
    "decibylgrokrealtime": "no platform key",
    "decibylultravoxrealtime": "no platform key",
}

#: Where one provider name is priced for one component but not another, the
#: key above carries a ``_<component>`` suffix. This maps those back.
_COMPONENT_SCOPED = {
    "cartesia_stt": ("cartesia", "stt"),
    "smallest_stt": ("smallest", "stt"),
    "deepgram_tts": ("deepgram", "tts"),
    "google_tts": ("google", "tts"),
    "google_stt": ("google", "stt"),
    "openai_stt": ("openai", "stt"),
}


def _component_of(class_name: str) -> str:
    if "STT" in class_name:
        return "stt"
    if "TTS" in class_name:
        return "tts"
    return "llm"


def _service_classes() -> list[str]:
    source = _FACTORY.read_text()
    return sorted(
        set(re.findall(r"\b([A-Z][A-Za-z0-9_]*(?:LLM|STT|TTS)Service)\b", source))
    )


def _priced() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for rate in DEFAULT_RATES:
        out.setdefault(rate.component.value, set()).add(rate.provider)
    return out


def _declared(provider: str, component: str) -> bool:
    if provider in _UNPRICED_BY_DESIGN:
        return True
    for key, (name, comp) in _COMPONENT_SCOPED.items():
        if name == provider and comp == component:
            return key in _UNPRICED_BY_DESIGN
    return False


class TestCoverage:
    def test_the_factory_still_has_services_to_check(self):
        """A regex that stops matching would make every test below vacuous."""
        assert len(_service_classes()) > 30

    def test_every_service_class_is_priced_or_declared(self):
        priced = _priced()
        undeclared = []
        for class_name in _service_classes():
            component = _component_of(class_name)
            provider = provider_from_processor(class_name)
            if provider in priced.get(component, set()):
                continue
            if _declared(provider, component):
                continue
            undeclared.append(f"{class_name} -> {component}:{provider}")

        assert not undeclared, (
            "these services have no rate row and are not declared unpriced: "
            f"{undeclared}. Usage on them reports zero provider cost and "
            "overstates margin on every call, silently. Price them, or name "
            "them in _UNPRICED_BY_DESIGN with a reason."
        )

    @pytest.mark.parametrize("provider", sorted(_UNPRICED_BY_DESIGN))
    def test_a_declared_exclusion_carries_a_reason(self, provider):
        assert _UNPRICED_BY_DESIGN[provider].strip()


class TestTheAliasesResolveToTheRightVendor:
    """An alias must land on a row that is genuinely the same vendor's price."""

    def test_elevenlabs_realtime_transcription_is_priced_as_elevenlabs(self):
        assert provider_from_processor("ElevenLabsRealtimeSTTService#0") == "elevenlabs"
        assert "elevenlabs" in _priced()["stt"]

    def test_google_vertex_is_priced_as_google(self):
        """The same models, billed through a different door."""
        assert provider_from_processor("GoogleVertexLLMService#1") == "google"
        assert "google" in _priced()["llm"]

    def test_cartesia_transcription_is_not_folded_into_cartesia_synthesis(self):
        """The alias table is not a prefix match, and this is why."""
        assert provider_from_processor("CartesiaTurnsSTTService") == "cartesiaturns"
        assert "cartesia" not in _priced().get("stt", set())


class TestNoRateIsNonsense:
    def test_no_rate_is_zero_or_negative(self):
        for rate in DEFAULT_RATES:
            assert rate.usd_per_unit > 0, (
                f"{rate.component.value}:{rate.provider}/{rate.model or '*'} is "
                "priced at zero, which reports as costed while contributing "
                "nothing"
            )

    def test_every_rate_records_how_it_was_arrived_at(self):
        for rate in DEFAULT_RATES:
            assert rate.basis.strip(), (
                f"{rate.component.value}:{rate.provider} has no basis. A price "
                "nobody can audit is a price nobody can correct."
            )
