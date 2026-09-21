"""FD-2: an Indian calling number places an automated call only once declared.

TRAI's TCCCP Third Amendment requires a number used for automated voice
calls in India to be declared to the telecom provider. The account records
the declaration; the gate reads it where the calling number is chosen, on
every path that dials, before a slot or a run exists. Numbers outside India
pass; a verified test call to the account's own handset is exempt; a record
that cannot be read refuses rather than guesses. Off by default.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api import constants
from api.db.models import NumberPredeclarationModel, OrganizationModel
from api.services.compliance import predeclaration
from api.services.telephony import outbound

INDIAN = "+919876543210"
INDIAN_2 = "+919812345678"
US = "+14155550100"


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(constants, "TRAI_PREDECLARATION_ENFORCED", True)


@pytest.fixture
def relaxed(monkeypatch):
    monkeypatch.setattr(constants, "TRAI_PREDECLARATION_ENFORCED", False)


def _declared(monkeypatch, numbers):
    monkeypatch.setattr(
        predeclaration, "declared_numbers", AsyncMock(return_value=set(numbers))
    )


class TestWhichNumbersAreIndias:
    @pytest.mark.parametrize(
        "number, indian",
        [
            (INDIAN, True),
            ("9876543210", True),
            ("098765 43210", True),
            (US, False),
            ("+4420 7946 0000", False),
            ("", False),
            (None, False),
        ],
    )
    def test_by_country_code(self, number, indian):
        assert predeclaration.is_indian(number) is indian


@pytest.mark.asyncio
class TestTheGate:
    async def test_off_everything_passes_and_nothing_is_read(
        self, relaxed, monkeypatch
    ):
        read = AsyncMock()
        monkeypatch.setattr(predeclaration, "declared_numbers", read)
        assert await predeclaration.allowed_from_numbers(7, [INDIAN, US]) == [
            INDIAN,
            US,
        ]
        assert await predeclaration.choose(7, [INDIAN]) is None
        read.assert_not_awaited()

    async def test_an_undeclared_indian_number_is_refused_with_the_reason(
        self, enforced, monkeypatch
    ):
        _declared(monkeypatch, [])
        with pytest.raises(predeclaration.NotPredeclared) as refused:
            await predeclaration.allowed_from_numbers(7, [INDIAN])
        assert INDIAN in str(refused.value)
        assert "TRAI" in str(refused.value)
        assert "not placed" in str(refused.value)

    async def test_a_declared_one_is_used_and_an_undeclared_one_left_out(
        self, enforced, monkeypatch
    ):
        _declared(monkeypatch, [INDIAN])
        assert await predeclaration.allowed_from_numbers(7, [INDIAN, INDIAN_2]) == [
            INDIAN
        ]
        assert await predeclaration.choose(7, [INDIAN, INDIAN_2]) == INDIAN

    async def test_numbers_outside_india_pass_whatever_the_record_says(
        self, enforced, monkeypatch
    ):
        _declared(monkeypatch, [])
        assert await predeclaration.allowed_from_numbers(7, [US]) == [US]
        assert await predeclaration.choose(7, [US]) is None
        # Beside an undeclared Indian one, the US number is what remains.
        assert await predeclaration.allowed_from_numbers(7, [INDIAN, US]) == [US]

    async def test_a_verified_test_call_is_exempt(self, enforced, monkeypatch):
        _declared(monkeypatch, [])
        assert await predeclaration.allowed_from_numbers(
            7, [INDIAN], automated=False
        ) == [INDIAN]
        await predeclaration.assert_from_number(7, INDIAN, automated=False)

    async def test_a_record_that_cannot_be_read_refuses(self, enforced, monkeypatch):
        monkeypatch.setattr(
            predeclaration,
            "declared_numbers",
            AsyncMock(side_effect=RuntimeError("db")),
        )
        with pytest.raises(predeclaration.NotPredeclared):
            await predeclaration.allowed_from_numbers(7, [INDIAN])

    async def test_it_is_a_call_refusal_the_routes_already_surface(self):
        from api.services.compliance import dnd

        assert issubclass(predeclaration.NotPredeclared, dnd.CallRefused)


@pytest.mark.asyncio
class TestTheOutboundHelper:
    """dial_workflow refuses before a slot or a run exists, and hands the
    chosen number to the provider."""

    async def _dial(self, provider, monkeypatch):
        concurrency = MagicMock()
        concurrency.acquire_org_slot = AsyncMock(
            return_value=SimpleNamespace(slot_id="s")
        )
        concurrency.bind_workflow_run = AsyncMock()
        concurrency.release_slot = AsyncMock()
        concurrency.release_workflow_run_slot = AsyncMock()
        monkeypatch.setattr(outbound, "call_concurrency", concurrency)
        db = MagicMock()
        db.create_workflow_run = AsyncMock(return_value=SimpleNamespace(id=99))
        monkeypatch.setattr(outbound, "db_client", db)
        monkeypatch.setattr(
            outbound,
            "authorize_workflow_run_start",
            AsyncMock(return_value=SimpleNamespace(has_quota=True, error_message=None)),
        )
        monkeypatch.setattr(
            outbound,
            "get_backend_endpoints",
            AsyncMock(return_value=("https://api", None)),
        )
        run_id = await outbound.dial_workflow(
            workflow=SimpleNamespace(id=1, user_id=2, workflow_uuid="u"),
            organization_id=7,
            to_number="+919000000000",
            provider=provider,
            telephony_configuration_id=3,
            source="missed_call",
        )
        return run_id, concurrency, db

    async def test_undeclared_refuses_before_any_slot_or_run(
        self, enforced, monkeypatch
    ):
        _declared(monkeypatch, [])
        provider = MagicMock(
            PROVIDER_NAME="plivo", WEBHOOK_ENDPOINT="p", from_numbers=[INDIAN]
        )
        provider.initiate_call = AsyncMock()
        with pytest.raises(predeclaration.NotPredeclared):
            await self._dial(provider, monkeypatch)
        provider.initiate_call.assert_not_awaited()

    async def test_declared_dials_from_the_declared_number(self, enforced, monkeypatch):
        _declared(monkeypatch, [INDIAN])
        provider = MagicMock(
            PROVIDER_NAME="plivo", WEBHOOK_ENDPOINT="p", from_numbers=[INDIAN, INDIAN_2]
        )
        provider.initiate_call = AsyncMock()
        run_id, _, _ = await self._dial(provider, monkeypatch)
        assert run_id == 99
        assert provider.initiate_call.await_args.kwargs["from_number"] == INDIAN

    async def test_off_the_provider_picks_as_before(self, relaxed, monkeypatch):
        provider = MagicMock(
            PROVIDER_NAME="plivo", WEBHOOK_ENDPOINT="p", from_numbers=[INDIAN]
        )
        provider.initiate_call = AsyncMock()
        await self._dial(provider, monkeypatch)
        assert "from_number" not in provider.initiate_call.await_args.kwargs


class _Borrow:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
class TestTheRecord:
    async def test_declared_dated_listed_and_read_by_the_gate(
        self, async_session, monkeypatch, enforced
    ):
        from api.db import db_client

        org = OrganizationModel(provider_id="org-trai", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        monkeypatch.setattr(db_client, "async_session", lambda: _Borrow(async_session))

        row = await predeclaration.record(
            organization_id=org.id,
            number="98765 43210",
            status="declared",
            reference="JIO-2026-0917",
            user_id=None,
        )
        assert row.address_normalized == INDIAN
        assert row.declared_at is not None
        assert row.reference == "JIO-2026-0917"

        listed = await predeclaration.list_declarations(org.id)
        assert [d.address_normalized for d in listed] == [INDIAN]
        assert await predeclaration.status_by_number(org.id) == {INDIAN: "declared"}
        assert await predeclaration.declared_numbers(org.id) == {INDIAN}
        # The gate, end to end, on the real record.
        assert await predeclaration.allowed_from_numbers(org.id, [INDIAN]) == [INDIAN]

        # Withdrawn keeps the date, stops the calls; forgotten is gone.
        again = await predeclaration.record(
            organization_id=org.id, number=INDIAN, status="withdrawn"
        )
        assert again.declared_at is not None
        with pytest.raises(predeclaration.NotPredeclared):
            await predeclaration.allowed_from_numbers(org.id, [INDIAN])
        assert await predeclaration.forget(organization_id=org.id, number=INDIAN)
        assert await predeclaration.list_declarations(org.id) == []
        rows = (
            (
                await async_session.execute(
                    __import__("sqlalchemy")
                    .select(NumberPredeclarationModel)
                    .where(NumberPredeclarationModel.organization_id == org.id)
                )
            )
            .scalars()
            .all()
        )
        assert rows == []

    async def test_a_bad_status_or_number_is_refused(self, async_session, monkeypatch):
        from api.db import db_client

        monkeypatch.setattr(db_client, "async_session", lambda: _Borrow(async_session))
        with pytest.raises(ValueError):
            await predeclaration.record(
                organization_id=1, number=INDIAN, status="maybe"
            )
        with pytest.raises(ValueError):
            await predeclaration.record(
                organization_id=1, number="abc", status="declared"
            )
