"""Did the agent handle the caller? The cheap checks, then the model's view.

The phrase checks are exact and decide first: an agent that never gave
the OTP instruction failed, whatever a model thinks of its tone. The
model judge only runs on what the phrase checks let through, and answers
one question with one reason.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Judgement:
    passed: bool
    reason: str


def agent_text(transcript: list[dict]) -> str:
    return "\n".join(
        (t.get("text") or "") for t in transcript if t.get("role") == "agent"
    ).lower()


def phrase_checks(
    transcript: list[dict], *, must_say: list[str], must_not_say: list[str]
) -> Judgement | None:
    """None when every phrase rule holds; the failing rule otherwise."""
    said = agent_text(transcript)
    for phrase in must_say:
        p = (phrase or "").strip().lower()
        if p and p not in said:
            return Judgement(
                False, f"The agent never said \u201c{phrase.strip()}\u201d."
            )
    for phrase in must_not_say:
        p = (phrase or "").strip().lower()
        if p and p in said:
            return Judgement(
                False,
                f"The agent said \u201c{phrase.strip()}\u201d, which it must not.",
            )
    return None


def judge_prompt(*, persona: str, goal: str) -> str:
    return (
        "You grade a transcript between a voice agent and a caller.\n"
        f"The caller: {persona}\nWhat the caller wanted: {goal}\n\n"
        'Answer as JSON only: {"passed": true|false, "reason": "one sentence"}. '
        "Pass only if the agent handled what the caller wanted correctly, "
        "politely, and without inventing facts. Be strict about wrong "
        "information and about the agent ignoring the caller."
    )


def parse_judgement(parsed: object) -> Judgement:
    if isinstance(parsed, dict) and isinstance(parsed.get("passed"), bool):
        reason = parsed.get("reason")
        return Judgement(
            parsed["passed"],
            reason if isinstance(reason, str) and reason.strip() else "",
        )
    return Judgement(False, "The judge did not return a verdict.")
