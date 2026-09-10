"""What day the agent thinks it is.

Asked outright, a live agent answered "June 13, 2024" — its training cutoff,
more than two years out, stated with the same confidence as everything else it
says. Harmless until something depends on it, and then not harmless at all:
every booking works from a date, so "tomorrow at five" resolves against a year
that has already gone, and "next Tuesday" lands on the wrong weekday too.

Nothing in the composed prompt carried a date, so the model had nothing to
correct itself against. These tests defend the line that fixes it, and the
position it holds — a model that has already read "book them in for Tuesday"
has started reasoning from the wrong year, so the date has to come first.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from api.constants import DEFAULT_ORGANIZATION_TIMEZONE
from api.services.workflow.pipecat_engine_context_composer import today_line


class TestItSaysWhatDayItIs:
    def test_the_real_date_in_the_business_timezone(self):
        line = today_line("Asia/Kolkata")
        now = datetime.now(ZoneInfo("Asia/Kolkata"))

        assert f"{now:%d %B %Y}" in line
        assert f"{now:%A}" in line

    def test_the_weekday_is_spelled_out(self):
        """Callers speak in weekdays — "Tuesday evening", not "the 15th"."""
        weekdays = (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        )

        assert any(day in today_line("Asia/Kolkata") for day in weekdays)

    def test_the_time_is_there_too(self):
        """Opening hours and "this evening" both depend on it."""
        now = datetime.now(ZoneInfo("Asia/Kolkata"))

        assert f"{now:%H:%M}" in today_line("Asia/Kolkata")

    def test_it_tells_the_model_not_to_use_its_own_memory(self):
        assert "never from anything you remember" in today_line("Asia/Kolkata")


class TestTheTimezoneItUses:
    def test_a_named_zone_is_honoured(self):
        assert "Asia/Tokyo" in today_line("Asia/Tokyo")

    def test_no_zone_falls_back_to_the_deployment_default(self):
        assert DEFAULT_ORGANIZATION_TIMEZONE in today_line(None)

    @pytest.mark.parametrize("bad", ["Mars/Olympus", "", "not a zone"])
    def test_a_zone_nobody_recognises_does_not_take_the_call_down(self, bad):
        """An operator typing a bad timezone should get the wrong offset at
        worst, never a call that fails to start."""
        line = today_line(bad)

        assert DEFAULT_ORGANIZATION_TIMEZONE in line


class TestWhereItSits:
    def test_the_date_comes_before_the_operators_own_prompt(self):
        """A model that has already read "book them in for Tuesday" has begun
        reasoning from the wrong year; the correction has to arrive first."""
        from api.services.workflow.pipecat_engine_context_composer import (
            compose_system_prompt_for_node,
        )

        class _Node:
            prompt = "OPERATOR PROMPT MARKER"
            add_global_prompt = False
            document_uuids = None

        class _Workflow:
            global_node_id = None
            nodes: dict = {}

        composed = compose_system_prompt_for_node(
            node=_Node(),
            workflow=_Workflow(),
            format_prompt=lambda text: text,
            has_recordings=False,
        )

        assert composed.index("Right now it is") < composed.index(
            "OPERATOR PROMPT MARKER"
        )
