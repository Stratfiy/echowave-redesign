"""place_callback and the ARQ task around it.

Two things are worth testing here and the rest is plumbing: that a redelivered
job does not ring the caller twice, and that every failure ends up on the event
row instead of vanishing. Nobody is on the line to notice either one.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services.telephony import missed_call
from api.tasks import missed_call_tasks


class _FakeRedis:
    """Only what the guard uses, with the atomicity that matters."""

    def __init__(self):
        self.store = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def incr(self, key):
        self.store[key] = str(int(self.store.get(key, 0)) + 1)
        return int(self.store[key])

    async def expire(self, key, seconds):
        return True


def event(outcome="pending"):
    return SimpleNamespace(
        id=5,
        organization_id=7,
        telephony_phone_number_id=11,
        caller="919876543210",
        outcome=outcome,
    )


@pytest.fixture
def db(monkeypatch):
    d = MagicMock()
    d.get_missed_call = AsyncMock(return_value=event())
    d.resolve_missed_call = AsyncMock()
    monkeypatch.setattr(missed_call_tasks, "db_client", d)
    return d


@pytest.fixture
def place(monkeypatch):
    fn = AsyncMock(return_value=99)
    monkeypatch.setattr(missed_call_tasks.missed_call, "place_callback", fn)
    return fn


class TestTask:
    @pytest.mark.asyncio
    async def test_success_records_the_run(self, db, place):
        await missed_call_tasks.place_missed_call_callback(None, 5, 7)
        db.resolve_missed_call.assert_awaited_once()
        kwargs = db.resolve_missed_call.await_args.kwargs
        assert kwargs["outcome"] == "called_back"
        assert kwargs["workflow_run_id"] == 99

    @pytest.mark.asyncio
    async def test_a_redelivered_job_does_not_ring_twice(self, db, place):
        """ARQ redelivers a job whose worker died mid-run. The cooldown will
        not save us — the first attempt already spent it."""
        db.get_missed_call.return_value = event(outcome="called_back")
        await missed_call_tasks.place_missed_call_callback(None, 5, 7)
        place.assert_not_awaited()
        db.resolve_missed_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_refusal_is_recorded_with_its_reason(self, db, place):
        place.side_effect = missed_call.CallbackTooSoon("called back 2 min ago")
        await missed_call_tasks.place_missed_call_callback(None, 5, 7)
        kwargs = db.resolve_missed_call.await_args.kwargs
        assert kwargs["outcome"] == "refused"
        assert "2 min ago" in kwargs["refusal_reason"]

    @pytest.mark.asyncio
    async def test_an_unexpected_error_is_recorded_not_raised(self, db, place):
        """If this raised, ARQ would retry and ring the caller again. The
        failure belongs on the row, where the operator reads it."""
        place.side_effect = RuntimeError("carrier refused")
        await missed_call_tasks.place_missed_call_callback(None, 5, 7)
        kwargs = db.resolve_missed_call.await_args.kwargs
        assert kwargs["outcome"] == "failed"
        assert "carrier refused" in kwargs["refusal_reason"]

    @pytest.mark.asyncio
    async def test_a_purged_event_is_survivable(self, db, place):
        db.get_missed_call.return_value = None
        await missed_call_tasks.place_missed_call_callback(None, 5, 7)
        place.assert_not_awaited()


class TestPlaceCallback:
    """The guard order. Cheap local checks must run before anything that costs
    a slot, or a caller in a loop can make us do work by ringing repeatedly."""

    @pytest.fixture
    def wiring(self, monkeypatch):
        db = MagicMock()
        db.get_phone_number_for_org = AsyncMock(
            return_value=SimpleNamespace(
                id=11, callback_workflow_id=3, telephony_configuration_id=4
            )
        )
        db.list_normalized_addresses_for_organization = AsyncMock(return_value=[])
        db.get_workflow = AsyncMock(
            return_value=SimpleNamespace(id=3, user_id=2, workflow_uuid="u")
        )
        monkeypatch.setattr("api.db.db_client", db, raising=False)

        prefs = AsyncMock(return_value=SimpleNamespace(timezone="Asia/Kolkata"))
        monkeypatch.setattr(
            "api.services.organization_preferences.get_organization_preferences", prefs
        )
        monkeypatch.setattr(
            "api.services.compliance.dnd.assert_may_call",
            AsyncMock(return_value="+919876543210"),
        )
        monkeypatch.setattr(
            "api.services.telephony.factory.get_telephony_provider_by_id",
            AsyncMock(return_value=MagicMock(PROVIDER_NAME="plivo")),
        )
        dial = AsyncMock(return_value=99)
        monkeypatch.setattr("api.services.telephony.outbound.dial_workflow", dial)

        # A fresh guard per test. The module singleton talks to the real Redis
        # in this environment, so without this the first test to run leaves a
        # cooldown that refuses every later one — and the refusal tests would
        # then pass for the wrong reason.
        guard = missed_call._Guard()
        guard._redis = _FakeRedis()
        monkeypatch.setattr(missed_call, "_guard", guard)

        return SimpleNamespace(db=db, dial=dial, guard=guard)

    @pytest.mark.asyncio
    async def test_the_happy_path_actually_dials(self, wiring):
        """Without this, every refusal test below would pass just as well if
        place_callback always raised."""
        assert await missed_call.place_callback(event()) == 99
        kwargs = wiring.dial.await_args.kwargs
        assert kwargs["to_number"] == "+919876543210"
        assert kwargs["source"] == "missed_call"
        assert kwargs["extra_context"]["missed_call_event_id"] == 5

    @pytest.mark.asyncio
    async def test_a_number_out_of_callback_mode_is_refused(self, wiring):
        """The operator can turn callback mode off between the ring and the
        job. The queued job must not dial anyway."""
        wiring.db.get_phone_number_for_org.return_value = SimpleNamespace(
            id=11, callback_workflow_id=None, telephony_configuration_id=4
        )
        with pytest.raises(missed_call.CallbackRefused):
            await missed_call.place_callback(event())
        wiring.dial.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_deleted_number_is_refused(self, wiring):
        wiring.db.get_phone_number_for_org.return_value = None
        with pytest.raises(missed_call.CallbackRefused):
            await missed_call.place_callback(event())

    @pytest.mark.asyncio
    async def test_dnd_and_the_calling_window_become_a_refusal(
        self, wiring, monkeypatch
    ):
        """A CallRefused from the DND gate is an outcome, not a crash — the
        task records it on the row rather than retrying."""
        from api.services.compliance import dnd

        monkeypatch.setattr(
            "api.services.compliance.dnd.assert_may_call",
            AsyncMock(side_effect=dnd.OutsideCallingHours("window closed")),
        )
        with pytest.raises(missed_call.CallbackRefused):
            await missed_call.place_callback(event())
        wiring.dial.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_deleted_callback_agent_is_refused(self, wiring):
        wiring.db.get_workflow.return_value = None
        with pytest.raises(missed_call.CallbackRefused):
            await missed_call.place_callback(event())
        wiring.dial.assert_not_awaited()
