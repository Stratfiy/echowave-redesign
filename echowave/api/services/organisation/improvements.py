"""What this business could improve, derived only from what it actually did.

Every suggestion here names its evidence and the number behind it. That is the
whole discipline: a suggestion without a count is an opinion, and a screen of
opinions is a screen people stop opening. "Add your Saturday hours" is advice;
"twelve callers asked about Saturday hours and no agent could answer" is a
finding, and the second one gets acted on.

Three rules.

**Nothing is applied.** A suggestion opens the screen that fixes it or fills the
chat box. It never edits an agent, never confirms a fact, never reconnects
anything. The person decides, every time.

**Nothing is suggested from noise.** Each rule carries a minimum -- calls seen,
times asked, failures counted -- below which it says nothing at all. One caller
asking an odd question is not a gap in the business, and a screen that treats it
as one teaches people to ignore the screen.

**Nothing is invented.** Every rule reads a table: gaps counted from finished
calls, failures from the record of actions, outcome rates per published
version. There is no model in this file, so there is nothing here that can be
confidently wrong.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

#: Times a question must go unanswered before it is worth an operator's
#: attention. One caller asking something odd is not a gap in the business.
MIN_TIMES_ASKED = 3

#: Failures in the recent window before a connector is called out. Matches the
#: readiness checklist and the home chips: one screen must not call a connector
#: healthy while another calls it broken.
MIN_FAILURES = 3

#: Calls a published version needs before its outcome rate means anything.
#: Below this the rate is mostly the luck of who rang.
MIN_CALLS_FOR_VERSION_COMPARISON = 20

#: How much worse a new version has to be before we say so. Outcome rates move
#: a few points on their own; a fifth of the bookings is not noise.
VERSION_REGRESSION_DROP = 0.10

SEVERITY_URGENT = "urgent"
SEVERITY_WORTH_DOING = "worth_doing"

ACTION_ANSWER = "answer"
ACTION_OPEN = "open"


def _suggestion(
    *,
    key: str,
    title: str,
    detail: str,
    evidence: str,
    severity: str,
    action: str,
    href: Optional[str] = None,
    prompt: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        #: What to do, in a sentence.
        "detail": detail,
        #: The number this rests on. Shown, always -- a suggestion whose
        #: evidence is hidden is indistinguishable from a guess.
        "evidence": evidence,
        "severity": severity,
        "action": action,
        "href": href,
        "prompt": prompt,
    }


def from_gaps(gaps: Iterable[Any]) -> list[dict[str, Any]]:
    """Questions the business cannot answer, and handovers it keeps making.

    A gap counted often enough is the clearest improvement in the product:
    somebody rang, nobody could help, and it has happened repeatedly. The fix
    is usually one sentence of knowledge, which is why the action is to answer
    it rather than to open a settings page.
    """
    out: list[dict[str, Any]] = []
    for gap in gaps:
        times = int(getattr(gap, "times_seen", 0) or 0)
        if times < MIN_TIMES_ASKED:
            continue
        subject = getattr(gap, "subject_key", "") or ""
        value = getattr(gap, "value", "") or ""

        if subject == "not_understood":
            out.append(
                _suggestion(
                    key=f"answer:{getattr(gap, 'id', '')}",
                    title=f"Nobody could answer: {value}",
                    detail="Add the answer and every agent will know it from the next call.",
                    evidence=f"Asked {times} times",
                    severity=SEVERITY_URGENT if times >= 10 else SEVERITY_WORTH_DOING,
                    action=ACTION_ANSWER,
                    prompt=f"The answer to “{value}” is ",
                )
            )
        elif subject == "escalated":
            out.append(
                _suggestion(
                    key=f"escalated:{getattr(gap, 'id', '')}",
                    title=f"Handed to a person {times} times: {value}",
                    detail=(
                        "Handing over is correct when something is real. This "
                        "often means it is a job an agent could take."
                    ),
                    evidence=f"Escalated {times} times",
                    severity=SEVERITY_WORTH_DOING,
                    action=ACTION_ANSWER,
                    prompt=f"Handle this without transferring: {value}",
                )
            )
        elif subject == "app_failed":
            out.append(
                _suggestion(
                    key=f"app:{value}",
                    title=f"{value} would not respond",
                    detail="Reconnect it. Until then the work it does is not being filed.",
                    evidence=f"Failed on {times} calls",
                    severity=SEVERITY_URGENT,
                    action=ACTION_OPEN,
                    href="/integrations/apps",
                )
            )
    return out


def from_failures(failures_by_app: dict[str, int]) -> list[dict[str, Any]]:
    """Connectors failing often enough to be costing the customer money."""
    out: list[dict[str, Any]] = []
    for app, failures in sorted(
        failures_by_app.items(), key=lambda pair: pair[1], reverse=True
    ):
        if failures < MIN_FAILURES:
            continue
        out.append(
            _suggestion(
                key=f"connector:{app}",
                title=f"{app} is failing",
                detail="Reconnect it. Every failure is work the agent did that was never filed.",
                evidence=f"{failures} failures in the last 7 days",
                severity=SEVERITY_URGENT,
                action=ACTION_OPEN,
                href="/integrations/apps",
            )
        )
    return out


def from_versions(
    *, workflow_id: int, name: str, versions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Whether the last change to this agent made it worse.

    The question the whole of version attribution exists to answer, and the one
    thing no competing platform can tell a customer: not "your agent changed"
    but "bookings fell a fifth after the change you published on the 12th".

    Compared against the previous version that has enough calls to be worth
    comparing, not simply the one before it. A version published and pulled
    after four calls is not evidence of anything, and treating it as the
    baseline would produce a confident sentence about noise.
    """
    usable = [
        version
        for version in versions
        if (version.get("calls") or 0) >= MIN_CALLS_FOR_VERSION_COMPARISON
        and version.get("outcome_rate") is not None
    ]
    if len(usable) < 2:
        return []

    usable.sort(key=lambda version: version.get("version_number") or 0)
    latest = usable[-1]
    previous = usable[-2]

    drop = (previous["outcome_rate"] or 0) - (latest["outcome_rate"] or 0)
    if drop < VERSION_REGRESSION_DROP:
        return []

    return [
        _suggestion(
            key=f"regression:{workflow_id}:{latest.get('version_number')}",
            title=f"{name} got worse after the last change",
            detail=(
                "Compare the two versions and put back what worked, or publish a fix."
            ),
            evidence=(
                f"v{previous.get('version_number')} finished "
                f"{round((previous['outcome_rate'] or 0) * 100)}% of calls, "
                f"v{latest.get('version_number')} finishes "
                f"{round((latest['outcome_rate'] or 0) * 100)}%"
            ),
            severity=SEVERITY_URGENT,
            action=ACTION_OPEN,
            href=f"/workflow/{workflow_id}",
        )
    ]


def rank(suggestions: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    """Most urgent first, and never more than a screenful.

    A list longer than this is a backlog, and a backlog is something people
    stop opening. What is cut is always the least urgent, so nothing important
    is lost to the limit.
    """
    order = {SEVERITY_URGENT: 0, SEVERITY_WORTH_DOING: 1}
    ranked = sorted(
        suggestions,
        key=lambda suggestion: (
            order.get(suggestion["severity"], 9),
            suggestion["key"],
        ),
    )
    return ranked[:limit]
