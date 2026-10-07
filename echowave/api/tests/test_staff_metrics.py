"""Staff console metrics and evaluations (handoff 37; screens 34-37).

Done when: a known journey produces one activation and one weekly useful
user; a chat reply alone is not a useful outcome; repeat and retention wait
for their windows; task success excludes cancellations and shows unknowns;
a ratio with nothing under it is undefined, never zero; the free beta shows
zero revenue and a budget that needs setup; and evaluation runs use a fixed
approved set, sanitize support cases, refuse a self-review, never let a
judge decide, block on a permission failure and on a regression, and never
overwrite an earlier run.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from api import constants
from api.db.models import AgentEventModel, AgentTaskModel
from api.db.staff_models import QualityEvalResultModel
from api.services.staff import analytics, commands, evaluations, revenue, roles


@pytest.fixture
def staff_on(monkeypatch):
    for name in (
        "STAFF_CONSOLE_ENABLED",
        "STAFF_ROLES_ENABLED",
        "STAFF_EVALUATIONS_ENABLED",
    ):
        monkeypatch.setattr(constants, name, True)


async def _user(db, session, name: str, tier: str | None = None, created_at=None):
    user, _ = await db.get_or_create_user_by_provider_id(f"{name}-{uuid4().hex[:8]}")
    user.staff_role = tier
    if created_at:
        user.created_at = created_at
    session.add(user)
    await session.flush()
    return user


async def _org(db, user):
    org, _ = await db.get_or_create_organization_by_provider_id(
        org_provider_id=f"org-{uuid4().hex[:10]}", user_id=user.id
    )
    return org


def _task(org, user, *, at, ledger_state="completed", status="done", assignee=None):
    return AgentTaskModel(
        organization_id=org.id,
        title="t",
        brief="",
        status=status,
        ledger_state=ledger_state,
        created_by=user.id,
        created_at=at - timedelta(minutes=5),
        finished_at=at,
        assignee_workflow_id=assignee,
    )


@pytest.mark.asyncio
class TestDefinitions:
    async def test_a_known_journey_is_one_activation(self, db_session, async_session):
        now = datetime.now(UTC)
        before = await analytics.report(async_session, days=28, now=now)
        person = await _user(
            db_session, async_session, "an-person", created_at=now - timedelta(days=3)
        )
        org = await _org(db_session, person)
        async_session.add_all(
            [
                _task(org, person, at=now - timedelta(days=2)),
                _task(org, person, at=now - timedelta(days=1)),
            ]
        )
        await async_session.flush()
        after = await analytics.report(async_session, days=28, now=now)
        step = lambda r, s: next(x["count"] for x in r["funnel"] if x["step"] == s)
        assert step(after, "signed_up") - step(before, "signed_up") == 1
        assert step(after, "activated") - step(before, "activated") == 1
        assert (
            after["weekly_useful_users"]["value"]
            - before["weekly_useful_users"]["value"]
            == 1
        )
        # Activated two days ago: the seven-day window has not passed yet.
        assert (
            after["seven_day_repeat"]["not_yet_eligible"]
            - before["seven_day_repeat"]["not_yet_eligible"]
            == 1
        )
        assert (
            after["seven_day_repeat"]["denominator"]
            == before["seven_day_repeat"]["denominator"]
        )

    async def test_a_chat_reply_alone_is_not_a_useful_outcome(
        self, db_session, async_session
    ):
        person = await _user(db_session, async_session, "an-chatty")
        org = await _org(db_session, person)
        async_session.add(
            AgentEventModel(
                organization_id=org.id,
                kind="message",
                actor="agent",
                summary="hello",
                payload={},
                at=datetime.now(UTC),
            )
        )
        await async_session.flush()
        assert (
            await analytics.useful_outcomes(async_session, user_ids=[person.id]) == []
        )

    async def test_a_card_that_ran_counts_for_who_confirmed_it(
        self, db_session, async_session
    ):
        person = await _user(db_session, async_session, "an-confirmer")
        org = await _org(db_session, person)
        at = datetime.now(UTC) - timedelta(hours=1)
        async_session.add(
            AgentEventModel(
                organization_id=org.id,
                kind="action_proposed",
                actor="agent",
                summary="x",
                payload={
                    "state": "done",
                    "action": "callback",
                    "confirmed": {"by": person.id, "at": at.isoformat()},
                },
                at=at,
            )
        )
        await async_session.flush()
        found = await analytics.useful_outcomes(async_session, user_ids=[person.id])
        assert [(o.source, o.kind) for o in found] == [("card", "callback")]

    async def test_repeat_counts_only_mature_windows(self, db_session, async_session):
        now = datetime.now(UTC)
        before = await analytics.report(async_session, days=60, now=now)
        person = await _user(
            db_session, async_session, "an-repeat", created_at=now - timedelta(days=20)
        )
        org = await _org(db_session, person)
        async_session.add_all(
            [
                _task(org, person, at=now - timedelta(days=15)),
                _task(org, person, at=now - timedelta(days=12)),
            ]
        )
        await async_session.flush()
        after = await analytics.report(async_session, days=60, now=now)
        assert (
            after["seven_day_repeat"]["denominator"]
            - before["seven_day_repeat"]["denominator"]
            == 1
        )
        assert (
            after["seven_day_repeat"]["numerator"]
            - before["seven_day_repeat"]["numerator"]
            == 1
        )

    async def test_task_success_excludes_cancelled_and_shows_unknown(
        self, db_session, async_session
    ):
        person = await _user(db_session, async_session, "an-success")
        org = await _org(db_session, person)
        start = datetime.now(UTC) - timedelta(minutes=1)
        at = datetime.now(UTC)
        async_session.add_all(
            [
                _task(org, person, at=at, ledger_state="completed"),
                _task(org, person, at=at, ledger_state="failed", status="could_not"),
                _task(
                    org, person, at=at, ledger_state="outcome_unknown", status="doing"
                ),
                _task(org, person, at=at, ledger_state="cancelled", status="done"),
            ]
        )
        await async_session.flush()
        result = await analytics.task_success(
            async_session, start=start, end=at + timedelta(seconds=1)
        )
        row = next(r for r in result["breakdown"] if r["type"] == "person_task")
        assert (row["completed"], row["failed"], row["unknown"], row["excluded"]) == (
            1,
            1,
            1,
            1,
        )
        assert row["eligible"] == 3 and row["rate"] == round(1 / 3, 4)

    async def test_no_denominator_is_undefined_not_zero(self, async_session):
        far = datetime(2001, 1, 1, tzinfo=UTC)
        result = await analytics.task_success(
            async_session, start=far, end=far + timedelta(days=1)
        )
        assert result["rate"] is None and result["eligible"] == 0
        useful = await analytics.usefulness(
            async_session, start=far, end=far + timedelta(days=1)
        )
        assert useful["rate"] is None and useful["response_rate"] is None

    async def test_records_are_pseudonymous(
        self, db_session, async_session, monkeypatch
    ):
        monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "test-key")
        person = await _user(db_session, async_session, "an-pseudo")
        org = await _org(db_session, person)
        async_session.add(_task(org, person, at=datetime.now(UTC)))
        await async_session.flush()
        result = await analytics.records(async_session, days=1)
        assert result["state"] == "ok"
        assert all(r["person"].startswith("u_") for r in result["records"])
        assert str(person.id) not in [r["person"] for r in result["records"]]
        monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "")
        assert (await analytics.records(async_session, days=1))[
            "state"
        ] == "needs_setup"


@pytest.mark.asyncio
class TestBetaEconomics:
    async def test_free_beta_reads_honestly(self, async_session, monkeypatch):
        monkeypatch.setattr(constants, "FREE_MODE_ENABLED", True)
        monkeypatch.setattr(constants, "STAFF_PILOT_BUDGET_PAISE", None)
        result = await revenue.report(async_session, days=7)
        assert result["free_mode"] is True
        assert result["revenue"]["recurring_value_paise"] is None
        assert result["beta"]["budget"]["state"] == "needs_setup"
        assert result["costs"]["infrastructure"] is None
        if result["cost_per_success"]["successes"] == 0:
            assert result["cost_per_success"]["value_paise"] is None
            assert result["cost_per_success"]["state"] == "undefined"

    async def test_a_budget_reports_what_is_left(self, async_session, monkeypatch):
        monkeypatch.setattr(constants, "STAFF_PILOT_BUDGET_PAISE", 10_000_000)
        budget = (await revenue.report(async_session, days=7))["beta"]["budget"]
        assert budget["state"] == "ok"
        assert budget["remaining_paise"] == 10_000_000 - budget["spent_to_date_paise"]


class TestMatrix:
    def test_every_destination_names_real_capabilities(self):
        for d in roles.DESTINATIONS:
            assert all(c in roles.CAPABILITIES for c in d.capabilities)
        assert len(roles.DESTINATIONS) == 8

    def test_every_capability_has_a_label_and_a_holder(self):
        assert set(roles.CAPABILITY_LABELS) == set(roles.CAPABILITIES)
        assert all(roles.CAPABILITIES.values())

    def test_handoff_role_boundaries(self):
        assert roles.can({"owner"}, "roles.manage") and roles.can(
            {"owner"}, "policy.change"
        )
        assert roles.can({"finance"}, "refunds.approve") and not roles.can(
            {"support"}, "refunds.request"
        )
        assert roles.can({"operations"}, "incidents.manage") and not roles.can(
            {"support"}, "incidents.manage"
        )
        assert roles.can({"quality"}, "quality.manage") and not roles.can(
            {"finance"}, "quality.manage"
        )
        assert not roles.can(
            {"support"}, "policy.change"
        )  # support cannot raise the global budget
        assert not roles.can({"support"}, "providers.rotate")


class TestSanitize:
    def test_contact_details_are_removed(self):
        cleaned = evaluations.sanitize(
            {
                "text": "Mail ravi@example.com or call +91 98765 43210, order 123456789",
                "n": 3,
            }
        )
        assert cleaned["n"] == 3
        assert cleaned["text"].startswith("Mail [email] or call [phone], order [")
        assert not any(ch.isdigit() for ch in cleaned["text"])
        assert evaluations.sanitize(["ref 4455667"]) == ["ref [number]"]


@pytest.mark.asyncio
class TestEvaluations:
    async def _people(self, db, session):
        a = await _user(db, session, "q-a", "support")
        b = await _user(db, session, "q-b", "support")
        from api.db.staff_models import StaffRoleGrantModel

        for u in (a, b):
            session.add(
                StaffRoleGrantModel(
                    user_id=u.id,
                    role="quality",
                    reason="t",
                    granted_by=u.id,
                    created_at=datetime.now(UTC),
                )
            )
        await session.flush()
        return (
            roles.StaffContext(user=a, roles=await roles.effective_roles(a)),
            roles.StaffContext(user=b, roles=await roles.effective_roles(b)),
        )

    async def _cmd(self, session, ctx, name, target):
        return await commands.request(
            session,
            ctx=ctx,
            command=name,
            target=target,
            reason="quality work",
            idempotency_key=f"t-{uuid4().hex}",
        )

    async def _cases(
        self, session, author, reviewer, dataset, n, kind="routing", texts=None
    ):
        ids = []
        for i in range(n):
            text = (texts or {}).get(i, "hi")
            saved = await self._cmd(
                session,
                author,
                "eval.case.save",
                {
                    "dataset": dataset,
                    "case_key": f"c{i}",
                    "input": {"text": text},
                    "expected": {"kind": "quick"},
                    "subgroup": {"language": "en", "kind": kind},
                },
            )
            await self._cmd(
                session,
                reviewer,
                "eval.case.review",
                {"case_id": saved.result["case_id"], "decision": "approve"},
            )
            ids.append(saved.result["case_id"])
        return ids

    async def _run(self, session, ctx, dataset, baseline=None):
        target = {"dataset": dataset, "runner": "routing_rules"}
        if baseline:
            target["baseline_run_id"] = baseline
        view = await self._cmd(session, ctx, "eval.run", target)
        assert view.state == commands.QUEUED
        await session.flush()
        assert await commands.run(view.id) == commands.SUCCEEDED
        refreshed = await commands.get(session, view.id)
        return refreshed.result["run_id"], refreshed.result["gate"]

    async def test_the_author_cannot_approve_their_own_case(
        self, db_session, async_session, staff_on
    ):
        a, _ = await self._people(db_session, async_session)
        saved = await self._cmd(
            async_session,
            a,
            "eval.case.save",
            {
                "dataset": "ds-self",
                "case_key": "k",
                "input": {"text": "hi"},
                "expected": {"kind": "quick"},
            },
        )
        review = await self._cmd(
            async_session,
            a,
            "eval.case.review",
            {"case_id": saved.result["case_id"], "decision": "approve"},
        )
        assert (
            review.state == commands.FAILED
            and "Another quality person" in review.result["message"]
        )

    async def test_editing_makes_a_new_version_and_sanitizes(
        self, db_session, async_session, staff_on
    ):
        a, _ = await self._people(db_session, async_session)
        first = await self._cmd(
            async_session,
            a,
            "eval.case.save",
            {
                "dataset": "ds-ver",
                "case_key": "k",
                "input": {"text": "hi"},
                "expected": {"kind": "quick"},
            },
        )
        second = await self._cmd(
            async_session,
            a,
            "eval.case.save",
            {
                "dataset": "ds-ver",
                "case_key": "k",
                "input": {"text": "write to me at a@b.co"},
                "expected": {"kind": "steps"},
                "source": "support_case",
                "source_ref": "ticket-9",
            },
        )
        assert (first.result["version"], second.result["version"]) == (1, 2)
        cases = await evaluations.list_cases(async_session, "ds-ver")
        assert cases[0]["input"]["text"] == "write to me at [email]"
        assert cases[0]["review_state"] == "draft"

    async def test_too_few_cases_is_an_insufficient_sample(
        self, db_session, async_session, staff_on
    ):
        a, b = await self._people(db_session, async_session)
        await self._cases(async_session, a, b, "ds-small", 3)
        _, gate = await self._run(async_session, a, "ds-small")
        assert gate == "insufficient_sample"

    async def test_a_permission_failure_blocks_and_a_regression_is_caught(
        self, db_session, async_session, staff_on
    ):
        a, b = await self._people(db_session, async_session)
        await self._cases(async_session, a, b, "ds-gate", evaluations.MIN_SAMPLE)
        baseline, gate = await self._run(async_session, a, "ds-gate")
        assert gate == "passed"
        # A new version of one case now fails (expects the wrong kind).
        saved = await self._cmd(
            async_session,
            a,
            "eval.case.save",
            {
                "dataset": "ds-gate",
                "case_key": "c0",
                "input": {"text": "please draft an email"},
                "expected": {"kind": "quick"},
                "subgroup": {"kind": "approval", "language": "en"},
            },
        )
        await self._cmd(
            async_session,
            b,
            "eval.case.review",
            {"case_id": saved.result["case_id"], "decision": "approve"},
        )
        candidate, gate = await self._run(
            async_session, a, "ds-gate", baseline=baseline
        )
        assert gate == "regression"
        detail = await evaluations.run_detail(async_session, candidate)
        assert [c["failed_checks"] for c in detail["failed_cases"]] == [
            ["kind_matches"]
        ]
        compare = await evaluations.comparison(
            async_session, candidate, saved.result["case_id"]
        )
        assert (
            compare["state"] == "regression"
            or compare["state"] == "inconsistent_fixture"
        )
        assert compare["candidate"]["judge"] == {"state": "unavailable"}
        # The baseline's evidence was not overwritten by the re-run.
        base_results = (
            (
                await async_session.execute(
                    select(QualityEvalResultModel).where(
                        QualityEvalResultModel.run_id == baseline
                    )
                )
            )
            .scalars()
            .all()
        )
        assert all(r.outcome == "passed" for r in base_results)

    async def test_a_judge_cannot_decide_a_recorded_case(
        self, db_session, async_session, staff_on
    ):
        a, b = await self._people(db_session, async_session)
        ids = await self._cases(async_session, a, b, "ds-rec", 2)
        with pytest.raises(commands.CommandError, match="must follow"):
            await self._cmd(
                async_session,
                a,
                "eval.results.record",
                {
                    "dataset": "ds-rec",
                    "config": {"model": "candidate"},
                    "results": [
                        {
                            "case_id": ids[0],
                            "outcome": "passed",
                            "checks": [{"name": "permission", "passed": False}],
                            "judge": {"score": 1.0},
                        }
                    ],
                },
            )
        ok = await self._cmd(
            async_session,
            a,
            "eval.results.record",
            {
                "dataset": "ds-rec",
                "config": {"model": "candidate"},
                "results": [
                    {
                        "case_id": ids[0],
                        "outcome": "passed",
                        "checks": [{"name": "kind", "passed": True}],
                    }
                ],
            },
        )
        # The case with no recorded result is unknown, so the run is partial.
        assert ok.result["gate"] == "partial"
