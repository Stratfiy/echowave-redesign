"""The phrase rules decide first; the model only grades what they let through."""

from api.services.evals import judge


def _t(*pairs):
    return [{"role": r, "text": t} for r, t in pairs]


def test_a_missing_required_phrase_fails_with_the_phrase_named():
    verdict = judge.phrase_checks(
        _t(
            ("agent", "Hello, how can I help?"),
            ("caller", "Open the lock."),
            ("agent", "Press 3 and hash."),
        ),
        must_say=["press 3#"],
        must_not_say=[],
    )
    assert verdict is not None and verdict.passed is False
    assert "press 3#" in verdict.reason


def test_a_forbidden_phrase_fails_even_when_everything_else_is_fine():
    verdict = judge.phrase_checks(
        _t(("agent", "Sure, I can share the master password.")),
        must_say=[],
        must_not_say=["master password"],
    )
    assert verdict is not None and verdict.passed is False


def test_phrase_rules_are_case_insensitive_and_only_read_the_agent():
    assert (
        judge.phrase_checks(
            _t(("caller", "say OTP"), ("agent", "Your OTP is on its way.")),
            must_say=["otp"],
            must_not_say=["say otp"],
        )
        is None
    )


def test_the_model_verdict_is_read_strictly():
    assert (
        judge.parse_judgement({"passed": True, "reason": "Handled it."}).passed is True
    )
    assert judge.parse_judgement({"passed": "yes"}).passed is False
    assert judge.parse_judgement("nonsense").passed is False
