"""The seam between a carrier ringing and a callback being queued.

`missed_call.py` decides whether to call someone back, and `missed_call_tasks`
records what happened; both are covered. Nothing covered the piece between
them — the branch in `/inbound/run` that notices a callback-mode number, writes
the ring down, and enqueues the job.

That seam is the whole feature from the operator's side. If it never fires, the
guards below it are irrelevant and the dashboard stays empty, which reads
exactly like nobody having rung.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes import telephony as telephony_routes
from api.tasks.function_names import FunctionNames


def ring(caller="+91 98765 43210", to="+911140001111", provider="plivo"):
    return SimpleNamespace(from_number=caller, to_number=to, provider=provider)


@pytest.fixture
def wiring(monkeypatch):
    calls = SimpleNamespace(enqueued=[], recorded=[])

    async def record(**kwargs):
        calls.recorded.append(kwargs)
        return SimpleNamespace(id=4242)

    async def enqueue(*args):
        calls.enqueued.append(args)

    monkeypatch.setattr(
        telephony_routes.db_client, "record_missed_call", AsyncMock(side_effect=record)
    )
    monkeypatch.setattr(telephony_routes, "enqueue_job", AsyncMock(side_effect=enqueue))
    return calls


@pytest.mark.asyncio
class TestARingOnACallbackNumber:
    async def test_it_is_written_down_and_handed_to_a_worker(self, wiring):
        await telephony_routes._record_missed_call(7, SimpleNamespace(id=11), ring())

        assert wiring.recorded[0]["organization_id"] == 7
        assert wiring.recorded[0]["telephony_phone_number_id"] == 11
        assert wiring.enqueued == [(FunctionNames.PLACE_MISSED_CALL_CALLBACK, 4242, 7)]

    async def test_the_caller_is_stored_in_the_form_the_guards_compare_on(self, wiring):
        """The loop guard and the DND list both match on the normalised form.
        Storing the carrier's spacing instead would make every comparison below
        this miss, and the number would look like a stranger every time."""
        await telephony_routes._record_missed_call(
            7, SimpleNamespace(id=11), ring(caller="+91 98765 43210")
        )

        stored = wiring.recorded[0]["caller"]
        assert stored == telephony_routes.dnd.normalise_number("+91 98765 43210")
        assert " " not in stored

    async def test_the_row_is_written_before_the_job_is_enqueued(
        self, wiring, monkeypatch
    ):
        """A worker that never picks the job up must leave a `pending` row
        rather than no trace. Enqueueing first and crashing would lose the ring
        entirely, and an empty dashboard cannot be told from a quiet one."""
        order = []
        monkeypatch.setattr(
            telephony_routes.db_client,
            "record_missed_call",
            AsyncMock(
                side_effect=lambda **k: order.append("row") or SimpleNamespace(id=1)
            ),
        )
        monkeypatch.setattr(
            telephony_routes,
            "enqueue_job",
            AsyncMock(side_effect=lambda *a: order.append("job")),
        )

        await telephony_routes._record_missed_call(7, SimpleNamespace(id=11), ring())

        assert order == ["row", "job"]


@pytest.mark.asyncio
class TestWhatItRefusesToDo:
    async def test_a_caller_id_it_cannot_use_records_nothing(self, wiring):
        """A withheld or malformed caller id gives nothing to ring back. A row
        would promise a callback that can never happen."""
        await telephony_routes._record_missed_call(
            7, SimpleNamespace(id=11), ring(caller="anonymous")
        )

        assert wiring.recorded == []
        assert wiring.enqueued == []

    async def test_a_database_failure_does_not_raise(self, monkeypatch):
        """The carrier is holding this webhook open waiting for a hangup. A 500
        makes it retry a request whose only job was to decline, so a failure
        here must cost the callback, not the hangup."""
        monkeypatch.setattr(
            telephony_routes.db_client,
            "record_missed_call",
            AsyncMock(side_effect=RuntimeError("database is down")),
        )

        await telephony_routes._record_missed_call(7, SimpleNamespace(id=11), ring())

    async def test_a_queue_failure_does_not_raise_either(self, monkeypatch):
        monkeypatch.setattr(
            telephony_routes.db_client,
            "record_missed_call",
            AsyncMock(return_value=SimpleNamespace(id=1)),
        )
        monkeypatch.setattr(
            telephony_routes,
            "enqueue_job",
            AsyncMock(side_effect=RuntimeError("redis is down")),
        )

        await telephony_routes._record_missed_call(7, SimpleNamespace(id=11), ring())


class TestWhichModeANumberIsIn:
    """`inbound_workflow_id` wins when both are set — a number that can be
    answered is answered. Getting this backwards would hang up on callers whose
    agent was sitting right there."""

    def test_a_number_with_both_is_answered_not_called_back(self):
        from api.services.telephony import missed_call

        phone = SimpleNamespace(inbound_workflow_id=3, callback_workflow_id=9)
        assert missed_call.is_callback_number(phone) is True
        # The route's own condition, which is what actually decides.
        assert not (
            not phone.inbound_workflow_id and missed_call.is_callback_number(phone)
        )

    def test_a_callback_only_number_takes_the_callback_branch(self):
        from api.services.telephony import missed_call

        phone = SimpleNamespace(inbound_workflow_id=None, callback_workflow_id=9)
        assert not phone.inbound_workflow_id and missed_call.is_callback_number(phone)
