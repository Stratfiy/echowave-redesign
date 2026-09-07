"""A managed tier is a different class wearing the vendor's name.

``managed_resolution`` rewrites a section's ``provider``, ``model`` and
``api_key`` in place and leaves the object as whatever it was -- deliberately,
so the pipeline has one configuration object rather than two representations of
the same thing. The consequence is that ``user_config.tts.provider ==
"elevenlabs"`` does *not* mean the section is an ``ElevenlabsTTSConfiguration``.
It can be a Decibyl tier class, which carries a voice and a speed and no
endpoint at all.

Reading a field that class does not have is an AttributeError at pipeline
build: the call connects and dies before its first frame, and the caller hears
the line go dead. That is the exact failure ``_carry`` was written to stop on
the LLM side; this file stops it on the TTS side, where the reads are inline.

Which providers a tier can reach is not fixed. ``managed_tiers._tier`` takes an
environment override -- ``MANAGED_TTS_DEFAULT=elevenlabs:eleven_flash_v2_5``
moves it -- so "the tier points at Sarvam today" is not a defence. Every branch
has to survive it, or an ops-level config change takes down every managed call.
"""

import ast
import re
from pathlib import Path

from api.services.configuration.registry import DecibylTTSService

_FACTORY = (
    Path(__file__).resolve().parents[1] / "services" / "pipecat" / "service_factory.py"
)

#: What a managed TTS section actually carries once resolution has run.
_TIER_FIELDS = set(DecibylTTSService.model_fields)


#: The branch shape this file reasons about.
_BRANCH = re.compile(
    r"(?:el)?if user_config\.tts\.provider == ServiceProviders\.(\w+)\.value:"
)


def _branches() -> dict[str, str]:
    """The provider branches, wherever they currently live.

    Found by looking for the function that *has* them rather than by name.
    `create_tts_service` used to hold them and now delegates to
    `_create_tts_service`, which set this parse to zero branches — and the two
    assertions below went on passing against an empty dict for as long as it
    took another failure to stop pytest before reaching this one. Anchoring on
    a name is what made a refactor able to switch off a safety check silently;
    anchoring on the content cannot.
    """
    source = _FACTORY.read_text()
    tree = ast.parse(source)
    holders = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        segment = ast.get_source_segment(source, node) or ""
        if _BRANCH.search(segment):
            holders.append((node.name, segment))

    assert holders, (
        "No function in service_factory.py dispatches on "
        "user_config.tts.provider. Either the branches moved somewhere this "
        "parse cannot see, or they are gone — both mean the checks below are "
        "testing nothing."
    )
    assert len(holders) == 1, (
        f"TTS provider branches are split across {[n for n, _ in holders]}. "
        "This file assumes one place to check; teach it about the others."
    )

    parts = _BRANCH.split(holders[0][1])
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def test_the_branches_are_discovered():
    """A parse that finds nothing would pass every assertion below."""
    branches = _branches()

    assert len(branches) >= 15, sorted(branches)


def test_no_branch_reads_a_field_a_managed_tier_would_not_have():
    unsafe: dict[str, list[str]] = {}

    for provider, branch in _branches().items():
        direct = set(re.findall(r"user_config\.tts\.(\w+)", branch))
        guarded = set(re.findall(r'getattr\(user_config\.tts,\s*"(\w+)"', branch))
        missing = sorted((direct - guarded) - _TIER_FIELDS)
        if missing:
            unsafe[provider] = missing

    assert not unsafe, (
        f"These branches read fields a managed tier section does not carry: "
        f"{unsafe}. Read them with getattr(user_config.tts, <field>, <default>) "
        "instead. A tier class answers provider == <vendor> while carrying only "
        f"{sorted(_TIER_FIELDS)}, so a direct read is an AttributeError at "
        "pipeline build -- a call that connects and then dies silently."
    )


def test_the_tier_class_still_carries_what_the_safe_branches_assume():
    """If a field is dropped from the tier class, the rule above changes."""
    assert {"voice", "model", "speed", "api_key"} <= _TIER_FIELDS
