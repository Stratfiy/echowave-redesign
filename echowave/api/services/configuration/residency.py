"""Whether a call's speech and language stay inside India.

A doctor asking "where does my patient's voice go?" is asking one narrow
question, and it deserves a narrow answer. This module computes it from the
stack a call will actually run on rather than from a label somebody typed.

**Computed, never declared.** A badge stored as a flag is a claim that goes
stale the first time a tier moves — and moving a tier is now a superadmin
action taken without a release. Deriving it means the guarantee cannot drift
from the configuration it describes: point the Lite brain at a US vendor and
the badge disappears by itself, on the same request.

**Speech and language only, and the name says so.** Carriage is a separate
question with a separate answer: an Indian call is carried by Indian licensed
carriers whoever the aggregator is, and stretching this to "nothing leaves
India" would be claiming something about network paths we do not control.
What a clinic is actually worried about is the consultation *content* reaching
a foreign company, and that is exactly what this covers.

**A knowledge base breaks it, and that is the interesting case.** Embeddings
run on OpenAI, so an agent with documents attached sends text abroad at ingest
even when every model on the call is Indian. That is the failure a declared
badge would miss, because nothing about the *call* configuration changed.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Vendors that process in India. Deliberately a short allow-list rather than a
#: block-list of foreign ones: a provider added to the platform must be
#: *proved* Indian to earn the badge, and the failure mode of forgetting to
#: classify one is a badge that does not appear rather than one that lies.
INDIA_PROCESSED = frozenset({"sarvam"})


@dataclass(frozen=True)
class Residency:
    """Where a stack processes, and what to say about it."""

    #: True only when every model on the call runs in India *and* nothing
    #: about the agent sends text abroad out of band.
    india_only: bool
    #: The components that leave India, named so the screen can say which.
    #: Empty when ``india_only`` is true.
    leaves_india: tuple[str, ...]
    #: Why, in the words a clinic owner would use. Empty when india_only.
    reason: str = ""


def _vendor_of(component: str, tier: str | None) -> str:
    from api.services.configuration import managed_tiers

    return managed_tiers.resolve(component, tier).provider.strip().lower()


def assess(
    *,
    architecture: str,
    llm_tier: str | None = None,
    stt_tier: str | None = "default",
    tts_tier: str | None = "default",
    realtime_tier: str | None = None,
    has_knowledge_base: bool = False,
) -> Residency:
    """Where this stack processes speech and language.

    Takes tiers rather than vendors so the caller does not have to resolve
    them — and so a tier moved in the superadmin screen changes the answer
    everywhere at once, which is the whole point of computing it.
    """
    leaves: list[str] = []

    if architecture == "realtime":
        # One model hears and speaks, so there is one vendor to check. No
        # Indic speech-to-speech model is good enough yet, which is why both
        # realtime tiers are foreign and this branch almost always fails.
        if _vendor_of("realtime", realtime_tier) not in INDIA_PROCESSED:
            leaves.append("the speech-to-speech model")
    else:
        checks = (
            ("stt", stt_tier, "transcription"),
            ("llm", llm_tier, "the language model"),
            ("tts", tts_tier, "the voice"),
        )
        for component, tier, label in checks:
            if _vendor_of(component, tier) not in INDIA_PROCESSED:
                leaves.append(label)

    if has_knowledge_base:
        # The case a declared badge would miss: nothing about the call
        # configuration changed, and text still goes abroad at ingest.
        leaves.append("knowledge base indexing")

    if not leaves:
        return Residency(india_only=True, leaves_india=())

    if len(leaves) == 1:
        which = leaves[0]
    else:
        which = ", ".join(leaves[:-1]) + " and " + leaves[-1]
    return Residency(
        india_only=False,
        leaves_india=tuple(leaves),
        reason=f"Runs outside India: {which}.",
    )
