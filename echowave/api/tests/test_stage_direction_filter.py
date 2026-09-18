"""Stage directions never reach the speaker, and speech always does.

Two halves, and the second matters more. Cutting a direction is the feature;
cutting the caller's answer is a worse bug than the one being fixed, so most
of this file is about what must survive.
"""

from __future__ import annotations

import pytest

from api.services.pipecat.stage_direction_filter import (
    StageDirectionFilter,
    strip_stage_directions,
)


class TestDirectionsAreRemoved:
    @pytest.mark.parametrize(
        "written,spoken",
        [
            ("*pauses warmly* Your order is ready.", "Your order is ready."),
            ("*clears throat* Right, so.", "Right, so."),
            ("_softly_ Good morning.", "Good morning."),
            ("(softly) Good morning.", "Good morning."),
            ("[laughs] That is right.", "That is right."),
            ("<sighs> Fine.", "Fine."),
            ("Hello. [long pause] Are you there?", "Hello. Are you there?"),
            # The one that ends a demo: a whole reply that is nothing else.
            ("*smiling warmly*", ""),
        ],
    )
    def test_it_is_not_spoken(self, written: str, spoken: str) -> None:
        assert strip_stage_directions(written) == spoken

    def test_the_gap_does_not_become_an_audible_pause(self) -> None:
        # A removal leaves a space before the full stop, which some engines
        # read as a longer pause than the sentence meant.
        assert strip_stage_directions("Yes *nodding* .") == "Yes."


class TestSpeechSurvives:
    @pytest.mark.parametrize(
        "written",
        [
            # The first draft of the filter cut this qualifier off a balance,
            # because four words with no digits looked like a direction.
            "Your balance is 1240 rupees (as of this morning).",
            "Let me check (one moment).",
            "Delivery is Tuesday (24 Sep).",
            "Sure! (Let me check that for you right now, one moment.)",
            # A bare asterisk is arithmetic, not markup.
            "The total is 5 * 3 rupees.",
            # An underscore inside an identifier is not emphasis.
            "Call user_name_here now.",
            "Nothing to remove here at all.",
            "",
        ],
    )
    def test_it_is_left_alone(self, written: str) -> None:
        assert strip_stage_directions(written) == written.strip()


class TestTheFilterInThePipeline:
    async def test_it_filters(self) -> None:
        assert await StageDirectionFilter().filter("*sighs* Hello.") == "Hello."

    async def test_extra_patterns_are_applied(self) -> None:
        # An escape hatch for a shape the built-in rules do not cover, without
        # anybody having to edit this module to get it.
        f = StageDirectionFilter(extra_patterns=[r"\{\{[^}]*\}\}"])
        assert await f.filter("{{beat}} Hello.") == "Hello."

    async def test_it_holds_no_state_across_an_interruption(self) -> None:
        f = StageDirectionFilter()
        await f.handle_interruption()
        await f.reset_interruption()
        assert await f.filter("(laughing) Still fine.") == "Still fine."
