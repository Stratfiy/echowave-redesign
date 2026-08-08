"""Deterministic routing.

The point of a branch node is that it does the same thing twice. So these tests
are the specification: if the behaviour below changes, routing that somebody
demonstrated to a customer has changed with it.

Three things carry most of the weight, and each has its own class:

* **A missing variable never matches.** Everything else follows from this.
* **Order decides.** Overlapping rules are how bands are written, not a mistake.
* **Nothing is stranded.** A call reaching a branch always leaves it.
"""

import pytest

from api.services.workflow.branching import (
    NUMERIC_OPERATORS,
    OPERATORS,
    UNARY_OPERATORS,
    Decision,
    Rule,
    decide,
    evaluate_rule,
    validate_rule,
)


def rule(variable="x", operator="equals", value="", label="matched") -> Rule:
    return Rule(label=label, variable=variable, operator=operator, value=value)


def matches(r: Rule, **variables) -> bool:
    matched, _ = evaluate_rule(r, variables)
    return matched


class TestAMissingVariableNeverMatches:
    """The most consequential rule in the module.

    A pre-call fetch that timed out, a CSV column nobody filled in, an
    extraction that found nothing — all three arrive as an absent key and all
    three mean "we do not know". Routing on an unknown sends the caller down a
    path chosen by an outage rather than by their situation.
    """

    @pytest.mark.parametrize(
        "operator,value",
        [
            ("equals", "yes"),
            ("not_equals", "yes"),
            ("contains", "yes"),
            ("not_contains", "yes"),
            ("starts_with", "y"),
            ("ends_with", "s"),
            ("matches_regex", ".*"),
            ("greater_than", "5"),
            ("less_than", "5"),
            ("in_list", "a,b"),
            ("not_in_list", "a,b"),
            ("is_true", ""),
            ("is_false", ""),
        ],
    )
    def test_an_absent_variable_matches_nothing(self, operator, value):
        assert not matches(rule(operator=operator, value=value))

    def test_not_equals_does_not_match_an_absent_variable(self):
        """The trap. `not_equals` reads as "anything but this", and a missing
        value is certainly not "yes" — but a caller whose language we failed to
        capture is not a caller who chose English. Absence is not a value."""
        assert not matches(rule(operator="not_equals", value="yes"))
        assert matches(rule(operator="not_equals", value="yes"), x="no")

    def test_an_empty_string_counts_as_absent(self):
        """A CSV with an empty cell and a CSV missing the column are the same
        state of knowledge."""
        assert not matches(rule(operator="equals", value=""), x="")
        assert not matches(rule(operator="contains", value="a"), x="")

    def test_is_empty_is_the_one_operator_that_asks_about_absence(self):
        assert matches(rule(operator="is_empty"))
        assert matches(rule(operator="is_empty"), x="")
        assert matches(rule(operator="is_empty"), x=None)
        assert not matches(rule(operator="is_empty"), x="something")

    def test_is_not_empty_is_its_mirror(self):
        assert not matches(rule(operator="is_not_empty"))
        assert matches(rule(operator="is_not_empty"), x="something")

    def test_zero_is_a_value_not_an_absence(self):
        """`0` is falsy in Python and meaningful in a workflow — an order worth
        nothing, a count of zero previous calls."""
        assert not matches(rule(operator="is_empty"), x=0)
        assert matches(rule(operator="is_not_empty"), x=0)
        assert matches(rule(operator="less_than", value="1"), x=0)


class TestNumbersAsTheyActuallyArrive:
    def test_a_number_compares_as_a_number(self):
        assert matches(rule(operator="greater_than", value="50000"), x=50001)
        assert not matches(rule(operator="greater_than", value="50000"), x=50000)
        assert matches(rule(operator="greater_or_equal", value="50000"), x=50000)

    def test_a_numeric_string_compares_as_a_number(self):
        """Everything that reaches here from a CSV or JSON body is a string.
        Comparing "9" to "10" as text would say nine is larger."""
        assert matches(rule(operator="greater_than", value="9"), x="10")
        assert not matches(rule(operator="greater_than", value="10"), x="9")

    def test_thousands_separators_and_rupee_signs_parse(self):
        """Someone typing a threshold into a form types it the way they say it."""
        assert matches(rule(operator="greater_than", value="50,000"), x="60000")
        assert matches(rule(operator="greater_than", value="₹50000"), x="₹60,000")

    def test_prose_does_not_parse_and_does_not_match(self):
        """A rule that guessed at "fifty thousand" would be worse than one that
        declines: it would route confidently on an invention."""
        matched, reason = evaluate_rule(
            rule(operator="greater_than", value="50000"), {"x": "about fifty thousand"}
        )
        assert not matched
        assert "not a number" in reason

    def test_a_boolean_is_not_a_number(self):
        """True == 1 in Python. Letting that through would make `> 0` match a
        flag, which is never what the author meant."""
        assert not matches(rule(operator="greater_than", value="0"), x=True)


class TestTextComparison:
    def test_case_does_not_matter(self):
        """The same answer arrives as "Telugu" from a form, "telugu" from a
        partner's JSON and "TELUGU" from a legacy system."""
        assert matches(rule(operator="equals", value="telugu"), x="Telugu")
        assert matches(rule(operator="contains", value="TELU"), x="telugu")
        assert matches(rule(operator="in_list", value="TE, EN"), x="te")

    def test_a_list_tolerates_the_spaces_a_human_types(self):
        assert matches(rule(operator="in_list", value="te, en , hi"), x="en")
        assert not matches(rule(operator="in_list", value="te, en"), x="hi")

    def test_not_in_list_is_its_mirror(self):
        assert matches(rule(operator="not_in_list", value="te, en"), x="hi")
        assert not matches(rule(operator="not_in_list", value="te, en"), x="te")

    def test_starts_and_ends_with(self):
        assert matches(rule(operator="starts_with", value="+91"), x="+919876543210")
        assert matches(rule(operator="ends_with", value="210"), x="+919876543210")

    def test_regex(self):
        assert matches(
            rule(operator="matches_regex", value=r"^\+91\d{10}$"), x="+919876543210"
        )
        assert not matches(
            rule(operator="matches_regex", value=r"^\+91\d{10}$"), x="+9198765"
        )

    def test_a_broken_regex_saved_before_validation_existed_does_not_match(self):
        """It falls through to the default rather than raising into a live call."""
        matched, reason = evaluate_rule(
            rule(operator="matches_regex", value="([a"), {"x": "abc"}
        )
        assert not matched
        assert "pattern error" in reason


class TestBooleansFromEverySource:
    """A flag reaches here as a real bool from JSON, as "true" from a CSV, as
    "yes" from a form and as 1 from a legacy system. All four mean the same."""

    @pytest.mark.parametrize("value", [True, "true", "True", "yes", "Y", "1", "on", 1])
    def test_things_that_are_true(self, value):
        assert matches(rule(operator="is_true"), x=value)
        assert not matches(rule(operator="is_false"), x=value)

    @pytest.mark.parametrize("value", [False, "false", "no", "N", "0", "off", 0])
    def test_things_that_are_false(self, value):
        assert matches(rule(operator="is_false"), x=value)
        assert not matches(rule(operator="is_true"), x=value)

    def test_a_word_that_is_neither_is_neither(self):
        """ "maybe" is not true, and it is also not false. Matching either would
        be a guess."""
        assert not matches(rule(operator="is_true"), x="maybe")
        assert not matches(rule(operator="is_false"), x="maybe")


class TestOrderDecides:
    def test_the_first_match_wins(self):
        decision = decide(
            [
                rule(
                    variable="v",
                    operator="greater_than",
                    value="100000",
                    label="platinum",
                ),
                rule(
                    variable="v", operator="greater_than", value="50000", label="gold"
                ),
            ],
            {"v": 150000},
            default_label="standard",
        )
        assert decision.label == "platinum"
        assert decision.rule_index == 0

    def test_bands_written_top_down_read_correctly(self):
        """The reason overlapping rules are a feature. Written the other way
        round, everything above 50,000 would be gold and platinum unreachable —
        which is why the UI must never reorder these rows."""
        rules = [
            rule(
                variable="v", operator="greater_than", value="100000", label="platinum"
            ),
            rule(variable="v", operator="greater_than", value="50000", label="gold"),
        ]
        assert (
            decide(rules, {"v": 150000}, default_label="standard").label == "platinum"
        )
        assert decide(rules, {"v": 60000}, default_label="standard").label == "gold"
        assert decide(rules, {"v": 10000}, default_label="standard").label == "standard"

    def test_the_default_is_taken_when_nothing_matches(self):
        decision = decide([rule()], {}, default_label="fallback")
        assert decision == Decision(
            label="fallback",
            matched=False,
            reason="fallback: no rule matched",
            rule_index=None,
        )

    def test_no_rules_at_all_still_routes(self):
        """A branch node someone added but has not configured yet must not
        strand the call while they are still building."""
        assert decide([], {"a": 1}, default_label="onward").label == "onward"


class TestTheDecisionExplainsItself:
    """A routing choice nobody can reconstruct afterwards is barely better than
    a model's. The reason lands on the run record."""

    def test_a_match_names_the_rule_and_the_value(self):
        decision = decide(
            [
                rule(
                    variable="order_value",
                    operator="greater_than",
                    value="50000",
                    label="high",
                )
            ],
            {"order_value": 60000},
            default_label="normal",
        )
        assert decision.matched
        assert "high" in decision.reason
        assert "order_value" in decision.reason
        assert "60000" in decision.reason

    def test_a_miss_says_so(self):
        decision = decide(
            [rule(variable="order_value", operator="greater_than", value="50000")],
            {},
            default_label="normal",
        )
        assert not decision.matched
        assert "no rule matched" in decision.reason

    def test_an_unset_variable_is_named_in_the_reason(self):
        """So the author can tell "the fetch returned nothing" from "the value
        was below the threshold" without re-running the call."""
        _, reason = evaluate_rule(
            rule(variable="order_value", operator="greater_than", value="50000"), {}
        )
        assert reason == "order_value is not set"


class TestValidationCatchesRulesThatCouldNeverMatch:
    """A malformed rule does not fail loudly at runtime — it silently never
    matches and every call takes the default. The workflow looks fine and the
    routing does not exist. That is why this runs at save time."""

    def test_a_valid_rule_has_no_problems(self):
        assert validate_rule(rule(operator="equals", value="yes")) == []

    def test_an_unknown_operator_is_named(self):
        problems = validate_rule(rule(operator="is_kind_of"))
        assert len(problems) == 1
        assert "is_kind_of" in problems[0]

    def test_a_missing_label_is_caught(self):
        problems = validate_rule(rule(label="", operator="equals", value="a"))
        assert any("label" in p for p in problems)

    def test_a_missing_variable_is_caught(self):
        problems = validate_rule(rule(variable="", operator="equals", value="a"))
        assert any("variable" in p for p in problems)

    def test_a_binary_operator_without_a_value_is_caught(self):
        problems = validate_rule(rule(operator="equals", value=""))
        assert any("needs a value" in p for p in problems)

    @pytest.mark.parametrize("operator", sorted(UNARY_OPERATORS))
    def test_a_unary_operator_with_a_value_is_caught(self, operator):
        """Half-finished editing: someone switched `equals yes` to `is_true`
        and left the value behind. Silent, and it changes nothing — but it
        misrepresents the rule to the next person reading it."""
        problems = validate_rule(rule(operator=operator, value="leftover"))
        assert any("takes no value" in p for p in problems)

    @pytest.mark.parametrize("operator", sorted(NUMERIC_OPERATORS))
    def test_comparing_a_number_to_prose_is_caught(self, operator):
        problems = validate_rule(rule(operator=operator, value="fifty thousand"))
        assert any("not a number" in p for p in problems)

    def test_a_broken_regex_is_caught(self):
        problems = validate_rule(rule(operator="matches_regex", value="([a"))
        assert any("not valid regex" in p for p in problems)

    def test_an_enormous_regex_is_refused(self):
        problems = validate_rule(rule(operator="matches_regex", value="a" * 500))
        assert any("longer than" in p for p in problems)

    def test_every_operator_in_the_list_validates(self):
        """Guards the list against an operator added to OPERATORS but never
        taught to validate_rule."""
        for operator in OPERATORS:
            value = (
                ""
                if operator in UNARY_OPERATORS
                else ("5" if operator in NUMERIC_OPERATORS else "a")
            )
            assert validate_rule(rule(operator=operator, value=value)) == [], operator

    def test_every_operator_in_the_list_evaluates(self):
        """And against one that validates but has no branch in evaluate_rule."""
        for operator in OPERATORS:
            value = (
                ""
                if operator in UNARY_OPERATORS
                else ("5" if operator in NUMERIC_OPERATORS else "a")
            )
            evaluate_rule(rule(operator=operator, value=value), {"x": "a"})
