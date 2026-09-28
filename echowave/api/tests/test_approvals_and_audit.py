"""The approval matrix and the audit log (KAN-160, E-1)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api import constants
from api.db import db_client as client
from api.services.refused import Refused
from api.services.workflow import approvals, audit_log


@pytest.fixture
def approvals_on(monkeypatch):
    monkeypatch.setattr(constants, "APPROVALS_2026_09_ENABLED", True)


def _rule(**kw):
    base = dict(
        id=1,
        subject="purchase_order",
        min_amount_paise=None,
        max_amount_paise=None,
        approver_role="admin",
        approver_user_id=None,
        position=0,
    )
    base.update(kw)
    return approvals.Rule(**base)


class TestARule:
    def test_covers_its_subject_and_band(self):
        r = _rule(min_amount_paise=100_000_00, max_amount_paise=None)
        assert r.covers("purchase_order", 250_000_00) is True
        assert r.covers("purchase_order", 50_000_00) is False
        assert (
            r.covers("purchase_order", None) is False
        )  # an unpriced document is below every band
        assert r.covers("rfq", 250_000_00) is False
        assert _rule(subject="*").covers("card", None) is True
        assert (
            _rule(max_amount_paise=100_000_00).covers("purchase_order", 100_000_00)
            is False
        )

    def test_a_role_rule_ranks_and_a_named_rule_is_exact(self):
        r = _rule(approver_role="admin")
        assert r.allows(role="owner", user_id=1) is True
        assert r.allows(role="admin", user_id=1) is True
        assert r.allows(role="member", user_id=1) is False
        assert r.allows(role=None, user_id=1) is False
        named = _rule(approver_role=None, approver_user_id=32)
        assert named.allows(role="owner", user_id=1) is False
        assert named.allows(role="member", user_id=32) is True

    def test_the_first_matching_rule_wins_in_order(self):
        rules = [
            _rule(id=2, position=1, subject="*", approver_role="owner"),
            _rule(id=1, position=0, subject="purchase_order", approver_role="admin"),
        ]
        assert (
            approvals.first_match(rules, subject="purchase_order", amount_paise=10).id
            == 1
        )
        assert approvals.first_match(rules, subject="rfq", amount_paise=10).id == 2
        assert approvals.first_match([], subject="rfq", amount_paise=10) is None


class TestCleaning:
    def test_a_rule_needs_an_approver_and_a_sane_band(self):
        ok = approvals.clean(
            {
                "subject": "Purchase_Order",
                "min_amount_paise": "10000000",
                "approver_role": "Owner",
            },
            3,
        )
        assert (ok.subject, ok.min_amount_paise, ok.approver_role, ok.position) == (
            "purchase_order",
            10000000,
            "owner",
            3,
        )
        with pytest.raises(Refused):
            approvals.clean({"subject": "card"}, 0)
        with pytest.raises(Refused):
            approvals.clean({"approver_role": "member"}, 0)
        with pytest.raises(Refused):
            approvals.clean(
                {
                    "approver_role": "admin",
                    "min_amount_paise": 5,
                    "max_amount_paise": 5,
                },
                0,
            )
        with pytest.raises(Refused):
            approvals.clean({"approver_user_id": "nobody"}, 0)


class TestTheCheck:
    @pytest.mark.asyncio
    async def test_off_everything_is_allowed_and_nothing_is_read(self, monkeypatch):
        monkeypatch.setattr(constants, "APPROVALS_2026_09_ENABLED", False)
        with patch(
            "api.services.workflow.approvals.rules_of", new=AsyncMock()
        ) as rules:
            assert (
                await approvals.check(
                    7,
                    subject="purchase_order",
                    amount_paise=1,
                    user_id=5,
                    role="member",
                )
                is None
            )
        rules.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_member_is_refused_by_name_and_an_admin_passes(self, approvals_on):
        rules = [_rule(min_amount_paise=100_000_00)]
        with patch(
            "api.services.workflow.approvals.rules_of",
            new=AsyncMock(return_value=rules),
        ):
            with pytest.raises(approvals.ApprovalRequired) as exc:
                await approvals.check(
                    7,
                    subject="purchase_order",
                    amount_paise=200_000_00,
                    user_id=5,
                    role="member",
                )
            assert "an admin" in str(exc.value)
            assert (
                await approvals.check(
                    7,
                    subject="purchase_order",
                    amount_paise=200_000_00,
                    user_id=5,
                    role="admin",
                )
            ).id == 1
            assert (
                await approvals.check(
                    7,
                    subject="purchase_order",
                    amount_paise=50_000_00,
                    user_id=5,
                    role="member",
                )
                is None
            )

    @pytest.mark.asyncio
    async def test_the_role_is_looked_up_when_not_given(self, approvals_on):
        rules = [_rule(subject="card", approver_role="owner")]
        with (
            patch(
                "api.services.workflow.approvals.rules_of",
                new=AsyncMock(return_value=rules),
            ),
            patch.object(
                client,
                "get_membership",
                new=AsyncMock(return_value=SimpleNamespace(role="owner")),
            ),
        ):
            assert (
                await approvals.check(7, subject="card", amount_paise=None, user_id=5)
            ).subject == "card"


class TestTheAuditLog:
    @pytest.mark.asyncio
    async def test_a_row_is_bounded_json_safe_and_named(self):
        added = []

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def add(self, row):
                added.append(row)

            async def commit(self):
                pass

        with (
            patch.object(client, "async_session", new=lambda: _Session()),
            patch.object(
                client,
                "get_user_by_id",
                new=AsyncMock(
                    return_value=SimpleNamespace(name="Nithish", email="n@x")
                ),
            ),
        ):
            ok = await audit_log.record(
                7,
                action=audit_log.CARD_CONFIRMED,
                subject_kind="card",
                subject_id=682,
                subject="Schedule Morning board",
                actor_user_id=32,
                before={"state": "proposed", "deep": {"x": "y" * 5000}},
                after={"state": "armed"},
            )
        assert ok is True
        row = added[0]
        assert (row.actor, row.action, row.subject_id) == (
            "Nithish",
            "card_confirmed",
            "682",
        )
        assert len(row.before["deep"]["x"]) == 2000

    @pytest.mark.asyncio
    async def test_a_dead_database_is_a_warning_not_an_error(self):
        with patch.object(
            client,
            "async_session",
            new=lambda: (_ for _ in ()).throw(RuntimeError("down")),
        ):
            assert await audit_log.record(7, action="x", subject_kind="y") is False

    def test_the_csv_has_the_promised_columns(self):
        row = SimpleNamespace(
            id=1,
            at=None,
            actor="Nithish",
            actor_user_id=32,
            action="agent_live",
            subject_kind="agent",
            subject_id="3",
            subject="Meera",
            before={"is_live": True},
            after={"is_live": False},
            note=None,
        )
        text = audit_log.as_csv([row])
        head, line = text.strip().split("\n")[:2]
        assert head == ",".join(audit_log.COLUMNS)
        assert '"{""is_live"": true}"' in line and "Meera" in line


# --- where the check sits, and where the rows are written -------------------


class TestTheHooks:
    @pytest.mark.asyncio
    async def test_confirming_a_card_asks_the_matrix_first(self, approvals_on):
        from api.services.workflow import actions

        event = SimpleNamespace(
            id=682,
            organization_id=7,
            kind="action_proposed",
            payload={"state": "proposed", "label": "Send it"},
            workflow_id=None,
            folder_id=None,
        )
        rules = [_rule(subject="card", approver_role="owner")]
        with (
            patch.object(client, "get_agent_event", new=AsyncMock(return_value=event)),
            patch.object(
                client, "set_agent_event_payload", new=AsyncMock(return_value=True)
            ) as write,
            patch(
                "api.services.workflow.approvals.rules_of",
                new=AsyncMock(return_value=rules),
            ),
            patch.object(
                client,
                "get_membership",
                new=AsyncMock(return_value=SimpleNamespace(role="member")),
            ),
        ):
            with pytest.raises(approvals.ApprovalRequired) as exc:
                await actions.settle(
                    organization_id=7, event_id=682, verb="confirm", user_id=5
                )
        assert "the owner" in str(exc.value)
        write.assert_not_awaited()  # nothing was armed

    @pytest.mark.asyncio
    async def test_declining_a_card_writes_one_row(self):
        from api.services.workflow import actions

        event = SimpleNamespace(
            id=682,
            organization_id=7,
            kind="action_proposed",
            payload={"state": "proposed", "label": "Send it", "action": "run_tool"},
            workflow_id=None,
            folder_id=None,
        )
        with (
            patch.object(client, "get_agent_event", new=AsyncMock(return_value=event)),
            patch.object(
                client, "set_agent_event_payload", new=AsyncMock(return_value=True)
            ),
            patch(
                "api.services.workflow.actions.audit_log.record",
                new=AsyncMock(return_value=True),
            ) as rec,
            patch("api.services.workflow.send_approval.note_declined", new=AsyncMock()),
        ):
            out = await actions.settle(
                organization_id=7, event_id=682, verb="decline", user_id=5
            )
        assert out["state"] == "declined"
        rec.assert_awaited_once()
        kw = rec.await_args.kwargs
        assert (
            kw["action"],
            kw["subject_kind"],
            kw["subject_id"],
            kw["actor_user_id"],
        ) == ("card_declined", "card", 682, 5)
        assert (kw["before"], kw["after"]["state"]) == (
            {"state": "proposed"},
            "declined",
        )

    @pytest.mark.asyncio
    async def test_a_decision_is_checked_and_recorded(self, approvals_on):
        from api.services.workflow import decisions

        event = SimpleNamespace(
            id=90,
            organization_id=7,
            kind="needs_decision",
            payload={
                "question": "Which vendor?",
                "options": ["A", "B"],
                "mode": "single",
            },
            workflow_id=None,
            folder_id=None,
        )
        rules = [_rule(subject="decision", approver_role="admin")]
        with (
            patch.object(client, "get_agent_event", new=AsyncMock(return_value=event)),
            patch.object(
                client, "set_agent_event_payload", new=AsyncMock(return_value=True)
            ),
            patch(
                "api.services.workflow.approvals.rules_of",
                new=AsyncMock(return_value=rules),
            ),
            patch.object(
                client,
                "get_membership",
                new=AsyncMock(return_value=SimpleNamespace(role="member")),
            ),
        ):
            with pytest.raises(approvals.ApprovalRequired):
                await decisions.decide(
                    organization_id=7, event_id=90, choice=["A"], other=None, user_id=5
                )
        with (
            patch.object(client, "get_agent_event", new=AsyncMock(return_value=event)),
            patch.object(
                client, "set_agent_event_payload", new=AsyncMock(return_value=True)
            ),
            patch(
                "api.services.workflow.approvals.rules_of",
                new=AsyncMock(return_value=rules),
            ),
            patch.object(
                client,
                "get_membership",
                new=AsyncMock(return_value=SimpleNamespace(role="admin")),
            ),
            patch(
                "api.services.workflow.decisions.audit_log.record",
                new=AsyncMock(return_value=True),
            ) as rec,
            patch(
                "api.services.workflow.decisions.agent_timeline.record", new=AsyncMock()
            ),
        ):
            out = await decisions.decide(
                organization_id=7, event_id=90, choice=["A"], other=None, user_id=5
            )
        assert out["decided"]["choice"] == ["A"]
        assert rec.await_args.kwargs["action"] == "decision_made"
        assert rec.await_args.kwargs["after"] == {"choice": ["A"], "other": None}

    @pytest.mark.asyncio
    async def test_issuing_a_purchase_order_above_the_band_needs_the_owner(
        self, approvals_on
    ):
        from api.services.documents import register

        row = SimpleNamespace(
            id=3,
            kind="purchase_order",
            number="PO/26-27/0003",
            status="draft",
            amount_paise=250_000_00,
            data={},
            due_date=None,
        )
        session = SimpleNamespace(flush=AsyncMock())
        rules = [
            _rule(
                subject="purchase_order",
                min_amount_paise=100_000_00,
                approver_role="owner",
            )
        ]
        with (
            patch(
                "api.services.documents.register.get", new=AsyncMock(return_value=row)
            ),
            patch(
                "api.services.workflow.approvals.rules_of",
                new=AsyncMock(return_value=rules),
            ),
        ):
            # An agent (no person) cannot issue it.
            with pytest.raises(approvals.ApprovalRequired):
                await register.update(
                    session, organization_id=7, register_id=3, status="issued"
                )
            assert row.status == "draft"
            # A member cannot either.
            with patch.object(
                client,
                "get_membership",
                new=AsyncMock(return_value=SimpleNamespace(role="member")),
            ):
                with pytest.raises(approvals.ApprovalRequired):
                    await register.update(
                        session,
                        organization_id=7,
                        register_id=3,
                        status="issued",
                        user_id=5,
                    )
            # The owner can, and the row is recorded.
            with (
                patch.object(
                    client,
                    "get_membership",
                    new=AsyncMock(return_value=SimpleNamespace(role="owner")),
                ),
                patch(
                    "api.services.workflow.audit_log.record",
                    new=AsyncMock(return_value=True),
                ) as rec,
            ):
                out = await register.update(
                    session,
                    organization_id=7,
                    register_id=3,
                    status="issued",
                    user_id=5,
                )
        assert out.status == "issued"
        kw = rec.await_args.kwargs
        assert (kw["action"], kw["subject_kind"], kw["subject"], kw["before"]) == (
            "document_status",
            "purchase_order",
            "PO/26-27/0003",
            {"status": "draft"},
        )
        assert kw["after"]["amount_paise"] == 250_000_00

    @pytest.mark.asyncio
    async def test_below_the_band_anyone_issues_and_a_note_alone_is_no_row(
        self, approvals_on
    ):
        from api.services.documents import register

        row = SimpleNamespace(
            id=3,
            kind="purchase_order",
            number="PO/26-27/0003",
            status="draft",
            amount_paise=50_000_00,
            data={},
            due_date=None,
        )
        session = SimpleNamespace(flush=AsyncMock())
        rules = [
            _rule(
                subject="purchase_order",
                min_amount_paise=100_000_00,
                approver_role="owner",
            )
        ]
        with (
            patch(
                "api.services.documents.register.get", new=AsyncMock(return_value=row)
            ),
            patch(
                "api.services.workflow.approvals.rules_of",
                new=AsyncMock(return_value=rules),
            ),
            patch(
                "api.services.workflow.audit_log.record",
                new=AsyncMock(return_value=True),
            ) as rec,
        ):
            await register.update(
                session, organization_id=7, register_id=3, note="chased"
            )
            rec.assert_not_awaited()
            out = await register.update(
                session, organization_id=7, register_id=3, status="issued"
            )
        assert out.status == "issued"
        assert rec.await_args.kwargs["actor"] == "agent"


# --- the routes -------------------------------------------------------------


from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import organization as organization_routes
from api.services.auth.depends import get_user_with_selected_organization


def _app(role="admin"):
    from api.services.refused import Refused

    app = FastAPI()
    app.include_router(organization_routes.router)
    user = SimpleNamespace(id=5, selected_organization_id=7, provider_id="p", role=role)
    app.dependency_overrides[get_user_with_selected_organization] = lambda: user

    @app.exception_handler(Refused)
    async def _refused(_r, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


class TestTheRoutes:
    def test_off_the_routes_are_absent(self, monkeypatch):
        monkeypatch.setattr(constants, "APPROVALS_2026_09_ENABLED", False)
        client_ = TestClient(_app())
        assert client_.get("/organizations/approval-rules").status_code == 404
        assert client_.get("/organizations/audit").status_code == 404

    def test_an_admin_saves_rules_and_reads_them_back(self, approvals_on):
        client_ = TestClient(_app())
        saved = [
            _rule(
                id=1,
                subject="purchase_order",
                min_amount_paise=10_000_000,
                approver_role="owner",
            )
        ]
        with (
            patch.object(
                client,
                "get_membership",
                new=AsyncMock(return_value=SimpleNamespace(role="admin")),
            ),
            patch(
                "api.routes.organization.approvals.rules_of",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "api.routes.organization.approvals.save_rules",
                new=AsyncMock(return_value=saved),
            ) as save,
            patch(
                "api.routes.organization.audit_log.record",
                new=AsyncMock(return_value=True),
            ) as rec,
        ):
            response = client_.put(
                "/organizations/approval-rules",
                json={
                    "rules": [
                        {
                            "subject": "purchase_order",
                            "min_amount_paise": 10000000,
                            "approver_role": "owner",
                        }
                    ]
                },
            )
        assert response.status_code == 200, response.text
        assert response.json()["rules"][0]["approver_role"] == "owner"
        assert save.await_args.args[1][0]["subject"] == "purchase_order"
        assert rec.await_args.kwargs["action"] == "approval_rules_saved"

    def test_a_bad_rule_is_refused_with_the_reason(self, approvals_on):
        client_ = TestClient(_app())
        with (
            patch.object(
                client,
                "get_membership",
                new=AsyncMock(return_value=SimpleNamespace(role="admin")),
            ),
            patch(
                "api.routes.organization.approvals.rules_of",
                new=AsyncMock(return_value=[]),
            ),
        ):
            response = client_.put(
                "/organizations/approval-rules", json={"rules": [{"subject": "card"}]}
            )
        assert response.status_code == 400
        assert "approver" in response.json()["detail"]

    def test_a_member_may_not_read_the_matrix(self, approvals_on):
        client_ = TestClient(_app())
        with patch.object(
            client,
            "get_membership",
            new=AsyncMock(return_value=SimpleNamespace(role="member")),
        ):
            assert client_.get("/organizations/approval-rules").status_code == 403

    def test_the_audit_log_reads_as_json_and_csv(self, approvals_on):
        client_ = TestClient(_app())
        row = SimpleNamespace(
            id=1,
            at=None,
            actor="Nithish",
            actor_user_id=5,
            action="agent_live",
            subject_kind="agent",
            subject_id="3",
            subject="Meera",
            before=None,
            after={"is_live": False},
            note=None,
        )
        with (
            patch.object(
                client,
                "get_membership",
                new=AsyncMock(return_value=SimpleNamespace(role="admin")),
            ),
            patch(
                "api.routes.organization.audit_log.rows",
                new=AsyncMock(return_value=[row]),
            ) as rows,
        ):
            as_json = client_.get("/organizations/audit?since=2026-09-01&limit=10")
            as_csv = client_.get("/organizations/audit.csv")
            bad = client_.get("/organizations/audit?since=yesterday")
        assert as_json.status_code == 200
        assert as_json.json()["entries"][0]["action"] == "agent_live"
        assert rows.await_args_list[0].kwargs["since"].year == 2026
        assert as_csv.status_code == 200
        assert as_csv.headers["content-type"].startswith("text/csv")
        assert as_csv.text.splitlines()[0] == ",".join(audit_log.COLUMNS)
        assert bad.status_code == 400
