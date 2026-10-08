"""Drive each case through the real API as a test person, then grade it.

One case is: put the account in the state the case needs (an earlier thread
by either member, a preference), open a fresh thread, say each line and wait
for each reply, read the thread once more so every card is seen in its final
state, run the exact checks, and ask the judge only if they all hold.
Afterwards every card the case left waiting is declined and every preference
is put back, so the test accounts end each case as they began it.

Nothing here presses Confirm. That is the point of the ``no_send`` check:
with nobody confirming, any card that ran is a send without a Confirm.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from evals.decibyl import api, checks
from evals.decibyl import judge as judging
from evals.decibyl.cases import Case, new_marker
from evals.decibyl.cost import Spend
from evals.decibyl.report import ERROR, FAILED, PASSED, SKIPPED, Result


class QuotaReached(RuntimeError):
    """A test account has used its turns for today: every later case would
    fail for the same reason, so the run stops and says so."""


@dataclass
class Context:
    transport: api.Transport
    accounts: dict[str, api.Account]
    state: api.AccountState
    model: judging.Model | None
    spend: Spend = field(default_factory=Spend)
    wait: dict[str, Any] = field(default_factory=dict)
    #: Turns left today per account label; None is "not limited".
    left: dict[str, int | None] = field(default_factory=dict)
    marker: str = field(default_factory=new_marker)
    log: Callable[[str], None] = print


def _today() -> str:
    """The date the judge resolves "next Friday" against: India's, where the
    test accounts live, written out with the weekday."""
    from datetime import datetime, timedelta, timezone

    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("Asia/Kolkata")
    except Exception:  # noqa: BLE001 - a runner without tzdata
        zone = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(zone).strftime("%A %d %B %Y (Asia/Kolkata)")


def unmet(case: Case, ctx: Context) -> str | None:
    """Why this account cannot run the case, or None. Said, never guessed."""
    need = case.requires
    if need.get("two_accounts") and (
        "b" not in ctx.accounts or not ctx.state.same_workspace
    ):
        return "needs two members of one workspace (STAGING_EMAIL_B, invited by A)"
    if need.get("apps") and not ctx.state.apps:
        return "connecting outside apps is not switched on here (no Composio key)"
    for flag in need.get("features") or []:
        if not ctx.state.features.get(flag):
            return f"switch {flag!r} is off here"
    for app in need.get("connected") or []:
        if app.lower() not in ctx.state.connected:
            return f"{app} is not connected on the test account"
    for app in need.get("not_connected") or []:
        if app.lower() in ctx.state.connected:
            return (
                f"{app} is connected on the test account; the case needs it not to be"
            )
    for helper in need.get("helpers") or []:
        state = ctx.state.helpers.get(helper)
        if state != "available":
            return f"helper {helper!r} is {state or 'not offered'}"
    return None


def speaker_for(case: Case, ctx: Context) -> str:
    if case.speaker != "any":
        return case.speaker
    if "b" not in ctx.accounts:
        return "a"
    a, b = ctx.left.get("a"), ctx.left.get("b")
    if a is None or b is None:
        return "a"
    return "b" if b > a else "a"


def _spend_turns(ctx: Context, label: str, thread: api.Thread) -> None:
    for turn in thread.turns:
        if turn.quota:
            continue
        ctx.spend.add_turn(
            turn.model,
            len(
                [
                    e
                    for e in turn.events
                    if e.get("kind") in api.CARD_KINDS | api.CHIP_KINDS
                ]
            ),
        )
        if ctx.left.get(label) is not None:
            ctx.left[label] = max(int(ctx.left[label]) - 1, 0)


def _final_states(ctx: Context, account: api.Account, thread: api.Thread) -> None:
    """Re-read the thread so each card is graded as it stands after the
    turn, not as it was when the reply landed."""
    latest = {
        e.get("id"): e
        for e in api.read_thread(ctx.transport, account, thread.thread_id)
    }
    for turn in thread.turns:
        turn.events = [latest.get(e.get("id"), e) for e in turn.events]


def run_case(case: Case, ctx: Context) -> Result:
    started = time.monotonic()
    why = unmet(case, ctx)
    if why:
        return Result(id=case.id, category=case.category, status=SKIPPED, note=why)
    case = case.with_marker(ctx.marker)
    label = speaker_for(case, ctx)
    account = ctx.accounts[label]
    restore: dict[str, Any] | None = None
    threads: list[tuple[api.Account, api.Thread]] = []
    thread: api.Thread | None = None
    try:
        if case.preferences:
            restore = api.set_preferences(ctx.transport, account, case.preferences)
        context = []
        for step in case.setup:
            who = ctx.accounts[step.speaker]
            earlier = api.converse(ctx.transport, who, list(step.turns), **ctx.wait)
            threads.append((who, earlier))
            _spend_turns(ctx, step.speaker, earlier)
            if any(t.quota for t in earlier.turns):
                raise QuotaReached(step.speaker)
            context.append(
                f"Account {step.speaker.upper()}"
                + (
                    " (the same person)"
                    if step.speaker == label
                    else " (another member of the workspace)"
                )
                + " said in a separate, private conversation: "
                + " / ".join(step.turns)
            )
        context.append(f"The conversation ran on {_today()}.")
        if case.context:
            context.append(case.context)
        if case.preferences:
            context.append(
                f"The speaker's settings for this conversation: {case.preferences}"
            )
        thread = api.converse(
            ctx.transport,
            account,
            list(case.turns),
            **ctx.wait,
            **({"helper": case.helper} if case.helper else {}),
        )
        threads.append((account, thread))
        _spend_turns(ctx, label, thread)
        if any(t.quota for t in thread.turns):
            raise QuotaReached(label)
        _final_states(ctx, account, thread)
        failures = checks.run(thread, case.expect)
        verdict: dict[str, Any] | None = None
        if not failures and case.judge and ctx.model is not None:
            graded = judging.grade(
                ctx.model, case.category, case.good, thread, "\n".join(context)
            )
            ctx.spend.add_judge(
                ctx.model.name, graded.input_tokens, graded.output_tokens
            )
            verdict = {
                "passed": graded.judgement.passed,
                "reason": graded.judgement.reason,
            }
        ok = not failures and (verdict is None or verdict["passed"])
        return Result(
            id=case.id,
            category=case.category,
            status=PASSED if ok else FAILED,
            checks=failures,
            judge=verdict,
            transcript=judging.render(thread),
            cards=[
                {
                    "kind": c.get("kind"),
                    "action": (c.get("payload") or {}).get("action"),
                    "label": (c.get("payload") or {}).get("label"),
                    "state": (c.get("payload") or {}).get("state"),
                }
                for c in thread.cards
            ],
            seconds=round(time.monotonic() - started, 1),
        )
    except (QuotaReached, judging.JudgeUnavailable):
        raise
    except api.ApiError as exc:
        return Result(
            id=case.id,
            category=case.category,
            status=ERROR,
            note=str(exc),
            transcript=judging.render(thread) if thread else "",
            seconds=round(time.monotonic() - started, 1),
        )
    finally:
        for who, t in threads:
            try:
                api.decline_open_cards(ctx.transport, who, t)
            except api.ApiError:
                ctx.log(f"  could not decline the cards left by {case.id}")
        if restore is not None:
            try:
                api.set_preferences(ctx.transport, account, restore)
            except api.ApiError:
                ctx.log(f"  could not put back the preferences {case.id} changed")


def run_all(cases: list[Case], ctx: Context) -> tuple[list[Result], str | None]:
    """Every case in order. Returns the results and, when the run stopped
    early, why. The cases it never got to are listed as skipped with that
    reason -- never dropped from the report, and never scored: running out
    of turns says nothing about the assistant -- and the report leads with
    the reason."""
    results: list[Result] = []
    stopped: str | None = None
    for i, case in enumerate(cases, 1):
        if stopped:
            results.append(
                Result(
                    id=case.id,
                    category=case.category,
                    status=SKIPPED,
                    note=f"not run: {stopped}",
                )
            )
            continue
        try:
            result = run_case(case, ctx)
        except QuotaReached as exc:
            stopped = (
                f"test account {str(exc).upper()} used its turns for today; grant it "
                "a temporary allowance in the staff console or run a slice"
            )
            result = Result(
                id=case.id,
                category=case.category,
                status=SKIPPED,
                note=f"not run: {stopped}",
            )
        except judging.JudgeUnavailable as exc:
            stopped = f"the judge stopped answering: {exc}"
            result = Result(
                id=case.id,
                category=case.category,
                status=SKIPPED,
                note=f"not run: {stopped}",
            )
        results.append(result)
        mark = {PASSED: "PASS", FAILED: "FAIL", SKIPPED: "SKIP", ERROR: "ERR "}[
            result.status
        ]
        ctx.log(
            f"[{i}/{len(cases)}] {mark} {case.id}"
            + (f" -- {result.reason}" if result.reason else "")
        )
    return results, stopped
