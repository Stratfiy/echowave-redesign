"""Phone agents start noisy (founder's call, October 2026).

A test call from a busy place kept stopping whenever people nearby spoke. An
agent that never chose a setting now gets ``noisy`` on a phone line and
``normal`` in the browser; a chosen setting always wins.
"""

from __future__ import annotations

import pytest

from api.schemas.workflow_configurations import WorkflowConfigurationDefaults
from api.services.pipecat import vad_sensitivity
from api.services.pipecat.run_pipeline import PHONE_RUN_MODES


@pytest.mark.parametrize(
    "stored", [None, {}, {"caller_environment": "auto"}, {"caller_environment": "?"}]
)
def test_unchosen_is_noisy_on_a_phone_and_normal_in_the_browser(stored):
    assert vad_sensitivity.resolve(stored, phone=True) == "noisy"
    assert vad_sensitivity.resolve(stored, phone=False) == "normal"


@pytest.mark.parametrize("chosen", ["quiet", "normal", "noisy"])
def test_a_chosen_setting_wins_on_both(chosen):
    stored = {"caller_environment": chosen}
    assert vad_sensitivity.resolve(stored, phone=True) == chosen
    assert vad_sensitivity.resolve(stored, phone=False) == chosen


def test_the_phone_thresholds_are_the_noisy_ones():
    p = vad_sensitivity.params({}, phone=True)
    assert (p.confidence, p.min_volume) == vad_sensitivity.SETTINGS["noisy"]


def test_the_schema_default_is_auto():
    assert WorkflowConfigurationDefaults().caller_environment == "auto"


def test_phone_modes_are_the_carriers_not_the_browser():
    assert {"plivo", "twilio", "ari"} <= PHONE_RUN_MODES
    assert not {"webrtc", "smallwebrtc", "textchat"} & PHONE_RUN_MODES
