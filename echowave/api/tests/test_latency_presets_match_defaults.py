"""The Balanced preset has to be exactly what every workflow already runs.

The presets live in the UI because they are a reading of three numbers rather
than stored state -- but that only stays safe while "Balanced" means the
committed backend defaults. If the two drift, picking Balanced silently
retunes an agent that was already on it, and every existing workflow reads as
"Custom" the first time somebody opens the screen.

Nothing in TypeScript can see these Python constants, so this test is the
join. It reads the values out of the TS source rather than trusting a comment.
"""

import re
from pathlib import Path

from api.schemas.workflow_configurations import (
    DEFAULT_SMART_TURN_STOP_SECS,
    DEFAULT_TURN_START_MIN_WORDS,
    DEFAULT_USER_SPEECH_TIMEOUT,
)

_PRESETS_TS = (
    Path(__file__).resolve().parents[2]
    / "ui"
    / "src"
    / "types"
    / "workflow-configurations.ts"
)

#: TS spells some values as its own mirrored constants; resolve them by name.
_TS_CONSTANTS = {
    "DEFAULT_USER_SPEECH_TIMEOUT": DEFAULT_USER_SPEECH_TIMEOUT,
    "DEFAULT_TURN_START_MIN_WORDS": DEFAULT_TURN_START_MIN_WORDS,
}


def _preset_values(name: str) -> dict[str, float]:
    source = _PRESETS_TS.read_text()
    block = re.search(
        rf"\n    {name}: \{{.*?values: \{{(?P<values>[^}}]*)\}}",
        source,
        re.DOTALL,
    )
    assert block, f"No {name!r} preset found in {_PRESETS_TS.name}"

    values = {}
    for key, raw in re.findall(r"(\w+):\s*([\w.]+)", block.group("values")):
        values[key] = float(_TS_CONSTANTS.get(raw, raw))
    return values


def test_balanced_is_the_committed_backend_defaults():
    assert _preset_values("balanced") == {
        "user_speech_timeout": float(DEFAULT_USER_SPEECH_TIMEOUT),
        "smart_turn_stop_secs": float(DEFAULT_SMART_TURN_STOP_SECS),
        "turn_start_min_words": float(DEFAULT_TURN_START_MIN_WORDS),
    }, (
        "Balanced must equal the defaults every stored workflow already runs, "
        "or applying it changes agents that were not asking to change."
    )


def test_the_presets_are_ordered_from_quickest_to_most_patient():
    """A named scale that is not monotonic is a lie about what the names mean."""
    rapid, balanced, patient = (
        _preset_values("rapid"),
        _preset_values("balanced"),
        _preset_values("patient"),
    )

    for field in (
        "user_speech_timeout",
        "smart_turn_stop_secs",
        "turn_start_min_words",
    ):
        assert rapid[field] < balanced[field] < patient[field], field


def test_every_preset_value_is_inside_the_range_the_schema_accepts():
    """A preset the server would reject is worse than no preset."""
    from api.schemas.workflow_configurations import WorkflowConfigurationDefaults

    for name in ("rapid", "balanced", "patient"):
        values = _preset_values(name)
        config = WorkflowConfigurationDefaults(
            user_speech_timeout=values["user_speech_timeout"],
            smart_turn_stop_secs=values["smart_turn_stop_secs"],
            turn_start_min_words=int(values["turn_start_min_words"]),
        )
        assert config.user_speech_timeout == values["user_speech_timeout"], name
