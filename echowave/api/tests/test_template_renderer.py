import pytest

from api.utils.template_renderer import render_spoken_template, render_template


def test_initial_context_prefix_resolves_against_flat_context():
    context = {
        "first_name": "Abhishek",
        "runtime_configuration": {
            "realtime_model": "gpt-realtime-2",
        },
    }

    assert (
        render_template("Hi {{initial_context.first_name | there}}", context)
        == "Hi Abhishek"
    )
    assert (
        render_template(
            "Model {{initial_context.runtime_configuration.realtime_model}}", context
        )
        == "Model gpt-realtime-2"
    )


def test_initial_context_prefix_prefers_explicit_initial_context():
    context = {
        "first_name": "Flat",
        "initial_context": {
            "first_name": "Nested",
        },
    }

    assert render_template("Hi {{initial_context.first_name}}", context) == "Hi Nested"


def test_initial_context_prefix_uses_fallback_when_missing_from_both_contexts():
    assert (
        render_template("Hi {{initial_context.first_name | there}}", {}) == "Hi there"
    )


# --- Spoken text: an unset variable leaves no stranded punctuation ----------
#
# Run 23 on staging opened with "Namaste, . How may I help you today?" because
# clinic_name was unset. The voice reads ", ." as a pause where a name should
# be, and it was the first thing the caller heard.


@pytest.mark.parametrize(
    "template,expected",
    [
        (
            "Namaste, {{clinic_name}}. How may I help you today?",
            "Namaste. How may I help you today?",
        ),
        ("Hello {{name}}!", "Hello!"),
        ("Hello {{name}}, how are you?", "Hello, how are you?"),
        ("Hi {{first}} {{last}}, welcome.", "Hi, welcome."),
        ("Your order {{order_id}} is ready.", "Your order is ready."),
        ("{{name}}, welcome back.", "Welcome back."),
        ("Namaste, {{clinic_name}}.", "Namaste."),
    ],
)
def test_spoken_text_closes_the_hole_an_unset_variable_leaves(template, expected):
    assert render_spoken_template(template, {}) == expected


def test_an_empty_string_is_unset_too():
    assert (
        render_spoken_template("Namaste, {{clinic_name}}.", {"clinic_name": " "})
        == "Namaste."
    )


def test_a_set_variable_renders_exactly_as_before():
    """What must appear: the name, with its comma, untouched."""
    template = "Namaste, {{clinic_name}}. How may I help you today?"
    context = {"clinic_name": "Smile Dental"}
    assert render_spoken_template(template, context) == render_template(
        template, context
    )
    assert render_spoken_template(template, context) == (
        "Namaste, Smile Dental. How may I help you today?"
    )


def test_a_fallback_still_wins_over_the_hole():
    assert (
        render_spoken_template("Namaste, {{clinic_name | our clinic}}.", {})
        == "Namaste, our clinic."
    )


def test_text_with_no_empty_variable_is_left_alone():
    """No tidying of text the operator wrote, only of holes we made."""
    text = "Hello  there , {{name}}"
    assert render_spoken_template(text, {"name": "Asha"}) == render_template(
        text, {"name": "Asha"}
    )


def test_prompts_are_not_touched():
    """A model is better served seeing the empty slot than not knowing the
    line was there, so render_template keeps its behaviour."""
    assert render_template("Clinic: {{clinic_name}}.", {}) == "Clinic: ."


def test_none_stays_none():
    assert render_spoken_template(None, {}) is None
