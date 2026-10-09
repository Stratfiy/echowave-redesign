"""What a placed call's run proves (services/telephony/call_evidence).

The rule the reminder paths depend on: only the carrier's own word makes a
call "not connected"; silence is ``pending``.
"""

from types import SimpleNamespace

import pytest

from api.services.telephony import call_evidence as ev


def _run(**kw):
    base = {
        "is_completed": False,
        "answered_at": None,
        "billable_seconds": None,
        "ended_at": None,
        "gathered_context": {},
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _not_connected(status: str):
    return _run(
        is_completed=True,
        gathered_context={
            "call_tags": ["not_connected", f"telephony_{status}"],
            "mapped_call_disposition": status,
        },
    )


def test_no_run_is_missing():
    assert ev.classify(None) == ev.MISSING


def test_a_run_that_has_said_nothing_is_pending():
    assert ev.classify(_run()) == ev.PENDING


def test_a_completed_run_without_a_carrier_verdict_is_pending():
    """Completed, not answered by the rule, and no not-connected callback:
    the post-call report decides, not the sweep."""
    assert ev.classify(_run(is_completed=True)) == ev.PENDING


@pytest.mark.parametrize("status", ["no-answer", "busy", "canceled"])
def test_the_carriers_no_answer_is_not_connected(status):
    assert ev.classify(_not_connected(status)) == ev.NOT_CONNECTED


@pytest.mark.parametrize("status", ["failed", "error"])
def test_a_carrier_failure_is_its_own_verdict(status):
    assert ev.classify(_not_connected(status)) == ev.CARRIER_FAILED


def test_answered_wins_over_everything():
    assert ev.classify(_run(answered_at=object())) == ev.ANSWERED
    assert ev.classify(_run(billable_seconds=12, is_completed=True)) == ev.ANSWERED


def test_a_not_connected_disposition_on_a_run_still_in_flight_is_pending():
    run = _run(gathered_context={"mapped_call_disposition": "no-answer"})
    assert ev.classify(run) == ev.PENDING


@pytest.mark.asyncio
async def test_a_failed_read_is_pending_not_missing(monkeypatch):
    from unittest.mock import AsyncMock

    from api.db import db_client

    monkeypatch.setattr(
        db_client, "get_workflow_run", AsyncMock(side_effect=RuntimeError("db"))
    )
    assert (await ev.read(5, organization_id=1))[0] == ev.PENDING
    assert (await ev.read(None, organization_id=1))[0] == ev.MISSING
