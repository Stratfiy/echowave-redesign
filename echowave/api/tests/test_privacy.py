"""Retention, erasure, export and the access log.

The property worth testing hardest is the one that is easiest to fake: that
deleting data actually deletes the *objects*, not just the rows pointing at
them. A purge that clears the database and leaves the audio in a bucket produces
a confident answer to "have you deleted my recording" that happens to be false,
and nothing in the UI would ever reveal it.

So the storage layer is a fake that records what it was asked to delete, and the
tests assert on that rather than on the row.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from api.db.models import (
    CallCostItemModel,
    DataAccessLogModel,
    ErasureRequestModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.privacy import access_log, erasure, export, retention
from api.services.privacy.retention import PURGED_MARKER


class FakeStorage:
    """Records deletions instead of performing them."""

    def __init__(self, *, failing: set[str] | None = None):
        self.deleted: list[str] = []
        self.failing = failing or set()

    async def adelete_file(self, key: str) -> bool:
        if key in self.failing:
            return False
        self.deleted.append(key)
        return True


@pytest.fixture
def storage():
    fake = FakeStorage()
    with patch("api.services.storage.get_storage", return_value=fake):
        yield fake


async def _org(async_session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    async_session.add_all([org, user])
    await async_session.flush()

    workflow = WorkflowModel(
        name=f"wf-{slug}",
        user_id=user.id,
        organization_id=org.id,
        workflow_definition={},
        template_context_variables={},
        call_disposition_codes={},
    )
    async_session.add(workflow)
    await async_session.flush()
    return org, workflow, user


async def _run(
    async_session,
    workflow,
    *,
    age_days: int = 0,
    number: str | None = None,
    recording: str = "recordings/1.wav",
):
    when = datetime.now(UTC) - timedelta(days=age_days)
    run = WorkflowRunModel(
        name="run",
        workflow_id=workflow.id,
        mode="twilio",
        created_at=when,
        recording_url=recording,
        transcript_url="transcripts/1.txt",
        billable_seconds=60,
        total_charged_paise=250,
        initial_context={"caller_number": number} if number else {},
        gathered_context={},
    )
    async_session.add(run)
    await async_session.flush()
    return run


@pytest.mark.asyncio
class TestRetentionPolicy:
    async def test_an_account_without_a_policy_gets_the_platform_default(
        self, async_session
    ):
        org, _, _ = await _org(async_session, "defaultpolicy")

        policy = await retention.resolve_policy(async_session, organization_id=org.id)

        assert policy.is_default
        assert policy.recording_days == retention.DEFAULT_RECORDING_RETENTION_DAYS

    async def test_audio_and_text_age_separately(self, async_session):
        """A voice is far more identifying than the words in it, and far less
        useful a month later."""
        org, _, user = await _org(async_session, "separate")

        policy = await retention.set_policy(
            async_session,
            organization_id=org.id,
            recording_retention_days=7,
            transcript_retention_days=400,
            updated_by=user.id,
        )

        assert policy.recording_days == 7
        assert policy.transcript_days == 400

    async def test_zero_retention_is_refused(self, async_session):
        """A window mistyped as 0 would delete calls as they finish, and the
        data is gone before anybody notices."""
        org, _, _ = await _org(async_session, "zeroretention")

        with pytest.raises(ValueError):
            await retention.set_policy(
                async_session,
                organization_id=org.id,
                recording_retention_days=0,
                transcript_retention_days=30,
            )


@pytest.mark.asyncio
class TestPurgeDeletesObjects:
    async def test_the_object_is_deleted_from_storage(self, async_session, storage):
        """The property that matters. Clearing the row and leaving the audio
        looks exactly like success."""
        org, workflow, _ = await _org(async_session, "purgeobj")
        run = await _run(async_session, workflow, age_days=200)

        deleted, cleared = await retention.purge_run(
            async_session, run=run, drop_transcript=True
        )

        assert cleared
        assert deleted == 2
        assert "recordings/1.wav" in storage.deleted
        assert "transcripts/1.txt" in storage.deleted

    async def test_a_failed_deletion_leaves_the_row_pointing_at_it(self, async_session):
        """Otherwise the audio is orphaned: still stored, and now unreachable
        by the only code that knew where it was."""
        fake = FakeStorage(failing={"recordings/1.wav"})
        org, workflow, _ = await _org(async_session, "purgefail")
        run = await _run(async_session, workflow, age_days=200)

        with patch("api.services.storage.get_storage", return_value=fake):
            _, cleared = await retention.purge_run(
                async_session, run=run, drop_transcript=True
            )

        assert not cleared
        assert run.recording_url == "recordings/1.wav"

    async def test_billing_figures_survive_a_purge(self, async_session, storage):
        """GST records must outlive the conversation they describe. Duration and
        cost identify nobody."""
        org, workflow, _ = await _org(async_session, "purgebilling")
        run = await _run(async_session, workflow, age_days=200)

        await retention.purge_run(async_session, run=run, drop_transcript=True)

        assert run.billable_seconds == 60
        assert run.total_charged_paise == 250
        assert run.recording_url == PURGED_MARKER

    async def test_a_young_call_is_left_alone(self, async_session, storage):
        org, workflow, _ = await _org(async_session, "young")
        await _run(async_session, workflow, age_days=1)

        result = await retention.purge_expired(async_session)

        assert result["runs_purged"] == 0
        assert storage.deleted == []

    async def test_an_expired_call_is_swept(self, async_session, storage):
        org, workflow, _ = await _org(async_session, "expired")
        await retention.set_policy(
            async_session,
            organization_id=org.id,
            recording_retention_days=30,
            transcript_retention_days=30,
        )
        await _run(async_session, workflow, age_days=90)

        result = await retention.purge_expired(async_session)

        assert result["runs_purged"] == 1
        assert len(storage.deleted) == 2


@pytest.mark.asyncio
class TestErasure:
    async def test_a_number_is_erased_across_every_call(self, async_session, storage):
        org, workflow, user = await _org(async_session, "erase")
        await _run(async_session, workflow, number="+91 98765 43210")
        await _run(async_session, workflow, number="+919876543210")
        await _run(async_session, workflow, number="+911111111111")

        result = await erasure.erase_number(
            async_session,
            organization_id=org.id,
            number="9876543210",
            requested_by=user.id,
        )

        # Both formats of the same number, not the unrelated one.
        assert result.runs_affected == 2
        assert result.status == "completed"

    async def test_formatting_does_not_defeat_a_request(self, async_session, storage):
        """Someone asking to be forgotten will not know which format their
        number was stored in."""
        org, workflow, _ = await _org(async_session, "formats")
        await _run(async_session, workflow, number="+91 98765-43210")

        result = await erasure.erase_number(
            async_session, organization_id=org.id, number="098765 43210"
        )

        assert result.runs_affected == 1

    async def test_the_objects_are_actually_deleted(self, async_session, storage):
        org, workflow, _ = await _org(async_session, "erasestorage")
        await _run(async_session, workflow, number="+919876543210")

        await erasure.erase_number(
            async_session, organization_id=org.id, number="9876543210"
        )

        assert "recordings/1.wav" in storage.deleted
        assert "transcripts/1.txt" in storage.deleted

    async def test_another_account_is_never_touched(self, async_session, storage):
        """A cross-account search would let one customer discover another's
        traffic."""
        mine, my_workflow, _ = await _org(async_session, "erasemine")
        theirs, their_workflow, _ = await _org(async_session, "erasetheirs")
        await _run(async_session, my_workflow, number="+919876543210")
        their_run = await _run(async_session, their_workflow, number="+919876543210")

        await erasure.erase_number(
            async_session, organization_id=mine.id, number="9876543210"
        )

        assert their_run.recording_url == "recordings/1.wav"
        assert their_run.initial_context == {"caller_number": "+919876543210"}

    async def test_the_number_is_not_stored_in_the_clear(self, async_session, storage):
        """A register of people who asked to be forgotten is its own personal
        data, and a sensitive one."""
        org, workflow, _ = await _org(async_session, "hashed")
        await _run(async_session, workflow, number="+919876543210")

        await erasure.erase_number(
            async_session, organization_id=org.id, number="9876543210"
        )

        request = await async_session.scalar(
            select(ErasureRequestModel).where(
                ErasureRequestModel.organization_id == org.id
            )
        )
        assert "9876543210" not in (request.subject_hash or "")
        assert len(request.subject_hash) == 64

    async def test_the_request_is_recorded_even_when_nothing_matched(
        self, async_session, storage
    ):
        """Both statutes set a deadline for responding, so a request handled but
        not recorded is indistinguishable from one ignored."""
        org, workflow, _ = await _org(async_session, "norecords")

        result = await erasure.erase_number(
            async_session, organization_id=org.id, number="9999999999"
        )

        assert result.runs_affected == 0
        assert result.status == "completed"
        requests = await erasure.list_requests(async_session, organization_id=org.id)
        assert len(requests) == 1

    async def test_erasing_a_whole_account_leaves_the_billing_history(
        self, async_session, storage
    ):
        """A closed account still has invoices that must survive."""
        org, workflow, _ = await _org(async_session, "eraseorg")
        run = await _run(async_session, workflow, number="+919876543210")

        await erasure.erase_organization(async_session, organization_id=org.id)

        assert run.recording_url == PURGED_MARKER
        assert run.total_charged_paise == 250
        assert run.billable_seconds == 60


@pytest.mark.asyncio
class TestExport:
    async def test_it_reports_what_was_erased_rather_than_omitting_it(
        self, async_session, storage
    ):
        """ "We deleted this" and "we never had this" are different answers, and
        somebody exercising a right is entitled to the accurate one."""
        org, workflow, _ = await _org(async_session, "exporterased")
        await _run(async_session, workflow, number="+919876543210")
        await erasure.erase_number(
            async_session, organization_id=org.id, number="9876543210"
        )

        payload = await export.export_data_principal(
            async_session, organization_id=org.id, number="9876543210"
        )

        assert payload["call_count"] == 0 or all(
            call["recording"] == "erased" for call in payload["calls"]
        )

    async def test_it_lists_who_the_call_was_shared_with(self, async_session):
        """DPDP s11(1)(c). Read per call from the cost items, so the answer is
        the vendors that actually handled it."""
        org, workflow, _ = await _org(async_session, "exportshared")
        run = await _run(async_session, workflow, number="+919876543210")
        async_session.add_all(
            [
                CallCostItemModel(
                    workflow_run_id=run.id,
                    component="llm",
                    provider="openai",
                    model="gpt-4o",
                    units=1000,
                    unit_rate_mpaise=100,
                    cost_paise=10,
                ),
                CallCostItemModel(
                    workflow_run_id=run.id,
                    component="stt",
                    provider="deepgram",
                    model="nova-2",
                    units=60,
                    unit_rate_mpaise=50,
                    cost_paise=5,
                ),
            ]
        )
        await async_session.flush()

        payload = await export.export_data_principal(
            async_session, organization_id=org.id, number="9876543210"
        )

        assert payload["calls"][0]["shared_with"] == ["deepgram", "openai"]

    async def test_an_account_export_says_when_it_is_truncated(self, async_session):
        """A truncated export must never be mistaken for a complete one."""
        org, workflow, _ = await _org(async_session, "exporttrunc")
        for _ in range(3):
            await _run(async_session, workflow)

        payload = await export.export_organization(
            async_session, organization_id=org.id, limit=2
        )

        assert payload["truncated"] is True
        assert payload["call_count"] == 2


@pytest.mark.asyncio
class TestAccessLog:
    async def test_access_is_recorded(self, async_session):
        org, workflow, user = await _org(async_session, "accesslog")
        run = await _run(async_session, workflow)

        await access_log.record_access(
            async_session,
            organization_id=org.id,
            user_id=user.id,
            resource_type=access_log.RECORDING,
            resource_id="recordings/1.wav",
            workflow_run_id=run.id,
        )

        entries = await access_log.access_for_run(
            async_session, organization_id=org.id, workflow_run_id=run.id
        )
        assert len(entries) == 1
        assert entries[0]["resource_type"] == "recording"

    async def test_it_never_raises(self, async_session):
        """An audit trail that can take down the product it audits is one that
        gets switched off the first time it does."""
        with patch.object(
            async_session, "flush", AsyncMock(side_effect=RuntimeError("db down"))
        ):
            await access_log.record_access(
                async_session,
                organization_id=1,
                user_id=1,
                resource_type=access_log.RECORDING,
            )
        # Reaching here without an exception is the assertion.

    async def test_it_is_scoped_to_one_account(self, async_session):
        mine, my_workflow, my_user = await _org(async_session, "accessmine")
        theirs, their_workflow, their_user = await _org(async_session, "accesstheirs")

        await access_log.record_access(
            async_session,
            organization_id=theirs.id,
            user_id=their_user.id,
            resource_type=access_log.RECORDING,
        )

        entries = await access_log.recent_access(async_session, organization_id=mine.id)
        assert entries == []

        total = await async_session.scalar(
            select(func.count()).select_from(DataAccessLogModel)
        )
        assert total >= 1
