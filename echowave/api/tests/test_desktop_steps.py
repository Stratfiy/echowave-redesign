"""Work on my computer: a step that sends, pays, deletes or submits is a card.

The desktop app (echowave/desktop) holds such a step and proposes it here.
These tests hold the contract it relies on, which is the contract of every
card in ``actions.py`` plus two things only a person's own computer needs:

- the card is released, not done, when its undo window passes, and the
  computer takes it exactly once, and only for the step the person saw
  (the fingerprint);
- only the person whose computer it is can answer it or take it.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import constants
from api.db import db_client
from api.db.models import OrganizationModel
from api.enums import AgentEventKind
from api.services.workflow import actions, desktop_steps

FINGERPRINT = "a" * 64
OTHER = "b" * 64


def _arguments(**overrides):
    return {
        "kind": "send",
        "app": "Mail",
        "summary": "Send the reply to Asha Rao",
        "detail": 'To asha@example.com: "Thanks, see you Monday."',
        "fingerprint": FINGERPRINT,
        "step": {"name": "left_click"},
        "session_id": "s-1",
        "request_id": "r-1",
        "device": "Asha's MacBook",
        **overrides,
    }


async def _org() -> int:
    async with db_client.async_session() as session:
        org = OrganizationModel(
            provider_id=f"org-desktop-{asyncio.get_running_loop().time()}",
            quota_decibyl_tokens=0,
        )
        session.add(org)
        await session.flush()
        organization_id = int(org.id)
        await session.commit()
    return organization_id


async def _state(organization_id: int, event_id: int) -> str:
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    return (event.payload or {}).get("state")


async def _confirm_and_fire(organization_id: int, event_id: int, user_id: int):
    with (
        patch.object(actions.approvals, "check", new=AsyncMock()),
        patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
        patch.object(actions, "_audit", new=AsyncMock()),
    ):
        await actions.settle(
            organization_id=organization_id,
            event_id=event_id,
            verb="confirm",
            user_id=user_id,
        )
    await actions.run(event_id, organization_id)


class TestResolve:
    def test_the_card_carries_the_exact_preview(self):
        payload = desktop_steps.resolve({**_arguments(), "user_id": 7})
        assert payload["action"] == actions.DESKTOP_STEP
        assert payload["label"] == "Send the reply to Asha Rao"
        assert payload["preview"] == 'To asha@example.com: "Thanks, see you Monday."'
        assert payload["effect"].startswith("Send in Mail on your computer, once.")
        assert payload["reversible"] is False
        assert payload["args"]["fingerprint"] == FINGERPRINT
        assert payload["args"]["user_id"] == 7
        assert payload["state"] == actions.PROPOSED

    @pytest.mark.parametrize(
        "change",
        [
            {"kind": "browse"},
            {"fingerprint": "not-a-hash"},
            {"summary": ""},
            {"detail": "   "},
            {"user_id": 0},
        ],
    )
    def test_a_step_the_card_cannot_carry_is_refused(self, change):
        with pytest.raises(desktop_steps.DesktopStepError):
            desktop_steps.resolve({**_arguments(), "user_id": 7, **change})

    def test_it_is_internal_not_something_a_bot_can_propose(self):
        assert actions.DESKTOP_STEP in actions.INTERNAL_ACTIONS
        assert actions.DESKTOP_STEP not in actions.ACTIONS


@pytest.mark.asyncio
@pytest.mark.usefixtures("setup_test_database")
class TestRunOnce:
    async def test_released_then_claimed_once_for_the_approved_step(self):
        organization_id = await _org()
        event_id = await desktop_steps.propose(
            organization_id=organization_id,
            user_id=7,
            thread_id=None,
            arguments=_arguments(),
        )
        assert await _state(organization_id, event_id) == actions.PROPOSED

        # Nothing can be taken before the person answers.
        assert not await desktop_steps.claim(
            organization_id=organization_id,
            user_id=7,
            event_id=event_id,
            fingerprint=FINGERPRINT,
        )

        await _confirm_and_fire(organization_id, event_id, user_id=7)
        assert await _state(organization_id, event_id) == actions.RELEASED

        # A different step (one pixel, one character) is not what was approved.
        assert not await desktop_steps.claim(
            organization_id=organization_id,
            user_id=7,
            event_id=event_id,
            fingerprint=OTHER,
        )
        claims = await asyncio.gather(
            *(
                desktop_steps.claim(
                    organization_id=organization_id,
                    user_id=7,
                    event_id=event_id,
                    fingerprint=FINGERPRINT,
                )
                for _ in range(3)
            )
        )
        assert claims.count(True) == 1
        assert await _state(organization_id, event_id) == actions.RUNNING

        result = await desktop_steps.report(
            organization_id=organization_id,
            user_id=7,
            event_id=event_id,
            ok=True,
            note="Clicked Send",
        )
        assert result["state"] == actions.DONE
        event = await db_client.get_agent_event(
            event_id, organization_id=organization_id
        )
        assert event.payload["done"]["note"] == "Done on your computer: Clicked Send"
        with pytest.raises(desktop_steps.DesktopStepError):
            await desktop_steps.report(
                organization_id=organization_id,
                user_id=7,
                event_id=event_id,
                ok=False,
                note="again",
            )

    async def test_a_retried_proposal_is_the_same_card(self):
        organization_id = await _org()
        first = await desktop_steps.propose(
            organization_id=organization_id,
            user_id=7,
            thread_id=None,
            arguments=_arguments(),
        )
        again = await desktop_steps.propose(
            organization_id=organization_id,
            user_id=7,
            thread_id=None,
            arguments=_arguments(),
        )
        assert again == first

    async def test_a_declined_step_is_never_released(self):
        organization_id = await _org()
        event_id = await desktop_steps.propose(
            organization_id=organization_id,
            user_id=7,
            thread_id=None,
            arguments=_arguments(),
        )
        with patch.object(actions, "_audit", new=AsyncMock()):
            await actions.settle(
                organization_id=organization_id,
                event_id=event_id,
                verb="decline",
                user_id=7,
            )
        await actions.run(event_id, organization_id)
        assert await _state(organization_id, event_id) == actions.DECLINED
        assert not await desktop_steps.claim(
            organization_id=organization_id,
            user_id=7,
            event_id=event_id,
            fingerprint=FINGERPRINT,
        )


@pytest.mark.asyncio
@pytest.mark.usefixtures("setup_test_database")
class TestOnlyThePersonWhoseComputerItIs:
    async def test_a_teammate_cannot_answer_the_card(self):
        organization_id = await _org()
        event_id = await desktop_steps.propose(
            organization_id=organization_id,
            user_id=7,
            thread_id=None,
            arguments=_arguments(),
        )
        with patch.object(actions, "_audit", new=AsyncMock()):
            for verb in ("confirm", "decline"):
                with pytest.raises(actions.ActionError, match="whose computer"):
                    await actions.settle(
                        organization_id=organization_id,
                        event_id=event_id,
                        verb=verb,
                        user_id=8,
                    )
        assert await _state(organization_id, event_id) == actions.PROPOSED

    async def test_a_teammate_cannot_read_take_or_report_it(self):
        organization_id = await _org()
        event_id = await desktop_steps.propose(
            organization_id=organization_id,
            user_id=7,
            thread_id=None,
            arguments=_arguments(),
        )
        await _confirm_and_fire(organization_id, event_id, user_id=7)
        for call in (
            desktop_steps.state(
                organization_id=organization_id, user_id=8, event_id=event_id
            ),
            desktop_steps.claim(
                organization_id=organization_id,
                user_id=8,
                event_id=event_id,
                fingerprint=FINGERPRINT,
            ),
            desktop_steps.cancel(
                organization_id=organization_id, user_id=8, event_id=event_id
            ),
        ):
            with pytest.raises(desktop_steps.DesktopStepError):
                await call
        assert await _state(organization_id, event_id) == actions.RELEASED

    async def test_another_workspace_cannot_see_it(self):
        organization_id = await _org()
        other = await _org()
        event_id = await desktop_steps.propose(
            organization_id=organization_id,
            user_id=7,
            thread_id=None,
            arguments=_arguments(),
        )
        with pytest.raises(desktop_steps.DesktopStepError):
            await desktop_steps.state(
                organization_id=other, user_id=7, event_id=event_id
            )

    async def test_an_ordinary_card_is_not_a_desktop_step(self):
        organization_id = await _org()
        event_id = await db_client.record_agent_event(
            organization_id=organization_id,
            kind=AgentEventKind.ACTION_PROPOSED.value,
            actor="agent",
            summary="Send the quote",
            payload={"state": actions.RELEASED, "action": actions.RUN_TOOL},
        )
        with pytest.raises(desktop_steps.DesktopStepError):
            await desktop_steps.claim(
                organization_id=organization_id,
                user_id=7,
                event_id=int(event_id),
                fingerprint=FINGERPRINT,
            )


@pytest.mark.asyncio
@pytest.mark.usefixtures("setup_test_database")
class TestStop:
    @pytest.mark.parametrize(
        "before,after",
        [
            ("proposed", actions.DECLINED),
            ("armed", actions.CANCELLED),
            ("released", actions.CANCELLED),
        ],
    )
    async def test_stop_takes_a_waiting_card_off(self, before, after):
        organization_id = await _org()
        event_id = await desktop_steps.propose(
            organization_id=organization_id,
            user_id=7,
            thread_id=None,
            arguments=_arguments(),
        )
        with (
            patch.object(actions.approvals, "check", new=AsyncMock()),
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
            patch.object(actions, "_audit", new=AsyncMock()),
        ):
            if before in ("armed", "released"):
                await actions.settle(
                    organization_id=organization_id,
                    event_id=event_id,
                    verb="confirm",
                    user_id=7,
                )
            if before == "released":
                await actions.run(event_id, organization_id)
            assert await _state(organization_id, event_id) == before
            assert (
                await desktop_steps.cancel(
                    organization_id=organization_id, user_id=7, event_id=event_id
                )
                == after
            )
        assert await _state(organization_id, event_id) == after
        assert not await desktop_steps.claim(
            organization_id=organization_id,
            user_id=7,
            event_id=event_id,
            fingerprint=FINGERPRINT,
        )


class TestTheSwitch:
    def _client(self, organization_id):
        from api.routes import desktop
        from api.services.auth.depends import get_user

        app = FastAPI()
        app.include_router(desktop.router, prefix="/api/v1")

        class _User:
            id = 7
            selected_organization_id = organization_id

        app.dependency_overrides[get_user] = lambda: _User()
        return TestClient(app)

    def test_every_route_is_a_404_while_off(self, monkeypatch):
        monkeypatch.setattr(constants, "DESKTOP_COMPUTER_USE_ENABLED", False)
        client = self._client(1)
        assert client.get("/api/v1/desktop/status").status_code == 404
        assert (
            client.post("/api/v1/desktop/steps", json=_arguments()).status_code == 404
        )

    def test_status_answers_while_on(self, monkeypatch):
        monkeypatch.setattr(constants, "DESKTOP_COMPUTER_USE_ENABLED", True)
        client = self._client(1)
        response = client.get("/api/v1/desktop/status")
        assert response.status_code == 200
        assert response.json() == {"computer_use": True}

    def test_both_flags_are_off_by_default_and_registered(self, monkeypatch):
        import importlib

        from api.services import features

        monkeypatch.delenv("DESKTOP_APP_ENABLED", raising=False)
        monkeypatch.delenv("DESKTOP_COMPUTER_USE_ENABLED", raising=False)
        fresh = importlib.reload(constants)
        assert fresh.DESKTOP_APP_ENABLED is False
        assert fresh.DESKTOP_COMPUTER_USE_ENABLED is False

        assert features.FLAGS["desktop_computer_use"] == "DESKTOP_COMPUTER_USE_ENABLED"
        assert features.FLAGS["desktop_app"] == "DESKTOP_APP_ENABLED"
        assert "desktop_computer_use" in features.DESCRIPTIONS
        assert "desktop_app" in features.DESCRIPTIONS


# --- with the task ledger (controls) ------------------------------------------


@pytest.fixture
def ledger_on(monkeypatch):
    monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)


async def _payload(organization_id: int, event_id: int) -> dict:
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    return dict(event.payload or {})


async def _released(organization_id: int, user_id: int = 7, **overrides) -> int:
    event_id = await desktop_steps.propose(
        organization_id=organization_id,
        user_id=user_id,
        thread_id=None,
        arguments=_arguments(**overrides),
    )
    version = (await _payload(organization_id, event_id)).get("version")
    with (
        patch.object(actions.approvals, "check", new=AsyncMock()),
        patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
        patch.object(actions, "_audit", new=AsyncMock()),
    ):
        await actions.settle(
            organization_id=organization_id,
            event_id=event_id,
            verb="confirm",
            user_id=user_id,
            version=version,
        )
    await actions.run(event_id, organization_id)
    return event_id


@pytest.mark.asyncio
@pytest.mark.usefixtures("setup_test_database", "ledger_on")
class TestWithTheTaskLedger:
    async def test_the_card_is_bound_to_a_version_like_every_card(self):
        organization_id = await _org()
        event_id = await desktop_steps.propose(
            organization_id=organization_id,
            user_id=7,
            thread_id=None,
            arguments=_arguments(),
        )
        payload = await _payload(organization_id, event_id)
        assert payload["version"] == actions.payload_version(payload)
        # A Confirm that does not name the version on screen is refused.
        with (
            patch.object(actions.approvals, "check", new=AsyncMock()),
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()),
            patch.object(actions, "_audit", new=AsyncMock()),
            pytest.raises(actions.ActionError, match="changed since you looked"),
        ):
            await actions.settle(
                organization_id=organization_id,
                event_id=event_id,
                verb="confirm",
                user_id=7,
            )
        assert await _state(organization_id, event_id) == actions.PROPOSED

    async def test_released_reads_as_scheduled_and_claimed_as_running(self):
        organization_id = await _org()
        event_id = await _released(organization_id)
        payload = await _payload(organization_id, event_id)
        assert payload["state"] == actions.RELEASED
        assert payload["ledger_state"] == "scheduled"
        assert payload["confirmed"]["version"] == payload["version"]
        assert await desktop_steps.claim(
            organization_id=organization_id,
            user_id=7,
            event_id=event_id,
            fingerprint=FINGERPRINT,
        )
        assert (await _payload(organization_id, event_id))["ledger_state"] == "running"

    async def test_a_computer_that_does_not_know_reports_outcome_unknown(self):
        organization_id = await _org()
        event_id = await _released(organization_id)
        await desktop_steps.claim(
            organization_id=organization_id,
            user_id=7,
            event_id=event_id,
            fingerprint=FINGERPRINT,
        )
        result = await desktop_steps.report(
            organization_id=organization_id,
            user_id=7,
            event_id=event_id,
            ok=None,
            note="input device went away",
        )
        assert result["state"] == actions.OUTCOME_UNKNOWN
        payload = await _payload(organization_id, event_id)
        assert payload["ledger_state"] == "outcome_unknown"
        assert payload["reason_code"] == "desktop_unsure"

    async def test_a_claim_never_reported_is_swept_to_outcome_unknown(self):
        from datetime import UTC, datetime, timedelta

        organization_id = await _org()
        event_id = await _released(organization_id)
        await desktop_steps.claim(
            organization_id=organization_id,
            user_id=7,
            event_id=event_id,
            fingerprint=FINGERPRINT,
        )
        payload = await _payload(organization_id, event_id)
        payload["fires_at"] = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        await db_client.set_agent_event_payload(
            event_id, organization_id=organization_id, payload=payload
        )
        with patch.object(actions, "_say", new=AsyncMock()):
            await actions.sweep_stale_running()
        assert await _state(organization_id, event_id) == actions.OUTCOME_UNKNOWN
        # Never handed out again.
        assert not await desktop_steps.claim(
            organization_id=organization_id,
            user_id=7,
            event_id=event_id,
            fingerprint=FINGERPRINT,
        )

    async def test_a_released_step_no_computer_took_is_cancelled_and_says_so(
        self, monkeypatch
    ):
        from datetime import UTC, datetime, timedelta

        monkeypatch.setattr(constants, "DESKTOP_COMPUTER_USE_ENABLED", True)
        organization_id = await _org()
        stale = await _released(organization_id)
        fresh = await _released(organization_id, request_id="r-2")
        payload = await _payload(organization_id, stale)
        payload["released"]["at"] = (
            datetime.now(UTC) - timedelta(minutes=30)
        ).isoformat()
        await db_client.set_agent_event_payload(
            stale, organization_id=organization_id, payload=payload
        )
        assert await desktop_steps.sweep_unclaimed() >= 1
        swept = await _payload(organization_id, stale)
        assert swept["state"] == actions.CANCELLED
        assert "Nothing was done" in swept["error"]
        assert await _state(organization_id, fresh) == actions.RELEASED

    async def test_a_send_spends_the_outbound_quota(self, monkeypatch):
        from sqlalchemy import text

        from api.services import quotas

        monkeypatch.setattr(constants, "OPERATIONAL_QUOTAS_ENABLED", True)
        monkeypatch.setattr(constants, "OPERATIONAL_QUOTA_OUTBOUND_MESSAGES", 1)
        organization_id = await _org()
        person, _ = await db_client.get_or_create_user_by_provider_id(
            f"desktop-send-{asyncio.get_running_loop().time()}"
        )
        try:
            first = await _released(organization_id, user_id=person.id)
            second = await _released(
                organization_id, user_id=person.id, request_id="r-2"
            )
            # A delete reaches nobody: it does not count.
            third = await _released(
                organization_id, user_id=person.id, request_id="r-3", kind="delete"
            )
            assert await _state(organization_id, first) == actions.RELEASED
            payload = await _payload(organization_id, second)
            assert payload["state"] == actions.FAILED
            assert payload["reason_code"] == "quota_outbound_messages"
            assert await _state(organization_id, third) == actions.RELEASED
        finally:
            async with db_client.async_session() as session:
                await session.execute(
                    text("DELETE FROM operational_usage WHERE user_id = :u"),
                    {"u": person.id},
                )
                await session.commit()
        assert quotas.OUTBOUND_MESSAGES == "outbound_messages"
