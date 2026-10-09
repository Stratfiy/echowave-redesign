"""The assistant seed scenarios load, and stay the set they claim to be.

No model is called: this checks the data (evals/assistant/scenarios.py) so
that a malformed or quietly shrunk set fails here, not in a paid run.
"""

from __future__ import annotations

from datetime import date

import pytest

from evals.assistant import scenarios


def test_every_scenario_loads_and_checks_final_state():
    loaded = scenarios.load()
    assert len(loaded) >= 40
    assert all(s["expect"]["state"] for s in loaded)


def test_every_category_has_english_tamil_hindi_and_code_mixed():
    by = scenarios.summary(scenarios.load())
    assert set(by) == set(scenarios.CATEGORIES)
    for category, langs in by.items():
        assert {"en", "ta", "hi"} <= set(langs), category
        assert {"ta-en", "hi-en"} & set(langs), category


def test_a_bare_on_key_is_refused():
    bad = {
        "id": "mem-en-99",
        "category": "memory",
        "language": "en",
        "target": "current",
        "context": {"now": "2026-10-09T11:00:00+05:30"},
        "turns": ["x"],
        "expect": {"state": [{"store": "facts", "count": 0}]},
        True: "what YAML makes of `on:`",
    }
    with pytest.raises(scenarios.ScenarioError, match="not a string"):
        scenarios.check(bad)


def test_now_must_carry_a_zone():
    bad = {
        "id": "rem-en-99",
        "category": "reminders",
        "language": "en",
        "target": "stage2",
        "context": {"now": "2026-10-09T11:00:00"},
        "turns": ["x"],
        "expect": {"state": [{"store": "cards", "count": 0}]},
    }
    with pytest.raises(scenarios.ScenarioError, match="offset"):
        scenarios.check(bad)


def test_the_stated_dates_are_the_weekdays_the_scenarios_assume():
    """The set leans on Friday 9 October 2026; a typo in a due date would
    grade the assistant against the wrong day."""
    assert date(2026, 10, 9).strftime("%A") == "Friday"
    assert date(2026, 10, 12).strftime("%A") == "Monday"
    assert date(2026, 10, 14).strftime("%A") == "Wednesday"
    assert date(2026, 10, 15).strftime("%A") == "Thursday"
    assert date(2026, 10, 16).strftime("%A") == "Friday"
