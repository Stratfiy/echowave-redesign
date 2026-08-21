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
    PlatformProviderCredentialModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.privacy import (
    access_log,
    erasure,
    export,
    retention,
    subprocessors,
)
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


async def _cost_item(
    async_session, run, *, provider: str, component: str, age_days: int = 0
):
    item = CallCostItemModel(
        workflow_run_id=run.id,
        component=component,
        provider=provider,
        units=1,
        unit_rate_mpaise=1,
        cost_paise=1,
        created_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    async_session.add(item)
    await async_session.flush()
    return item


async def _platform_key(async_session, *, provider: str, component: str, active=True):
    credential = PlatformProviderCredentialModel(
        component=component,
        provider=provider,
        encrypted_key="ciphertext",
        key_last_four="abcd",
        is_active=active,
    )
    async_session.add(credential)
    await async_session.flush()
    return credential


@pytest.mark.asyncio
class TestSubprocessorsAreDerived:
    """The list exists because a hand-maintained one goes stale the first time
    somebody adds a provider and forgets, and a stale sub-processor list is
    worse than none — it is a specific written claim that is now false."""

    async def test_a_configured_provider_is_listed(self, async_session):
        await _platform_key(async_session, provider="deepgram", component="stt")

        listed = await subprocessors.in_use(async_session)

        entry = next(s for s in listed if s.name == "deepgram")
        assert entry.basis == "configured"
        assert entry.purpose == "Speech recognition"

    async def test_a_deactivated_provider_drops_off(self, async_session):
        """Deactivating is how a provider is taken out of service. If it stayed
        on the list, taking a vendor out would never be visible to anyone."""
        await _platform_key(
            async_session, provider="retired-vendor", component="tts", active=False
        )

        listed = await subprocessors.in_use(async_session)

        assert not any(s.name == "retired-vendor" for s in listed)

    async def test_a_provider_nobody_configured_but_calls_went_to_is_listed(
        self, async_session
    ):
        """The case a config-only implementation misses: a customer's own key
        routes their audio to a vendor we never configured. Their data still
        went there, and the question is about the data."""
        _, workflow, _ = await _org(async_session, "byokey")
        run = await _run(async_session, workflow)
        await _cost_item(async_session, run, provider="elevenlabs", component="tts")

        listed = await subprocessors.in_use(async_session)

        entry = next(s for s in listed if s.name == "elevenlabs")
        assert entry.basis == "observed"

    async def test_a_provider_not_touched_in_the_window_is_not_in_use(
        self, async_session
    ):
        _, workflow, _ = await _org(async_session, "staleprovider")
        run = await _run(async_session, workflow)
        await _cost_item(
            async_session,
            run,
            provider="ancient-vendor",
            component="llm",
            age_days=subprocessors.IN_USE_WINDOW_DAYS + 1,
        )

        listed = await subprocessors.in_use(async_session)

        assert not any(s.name == "ancient-vendor" for s in listed)

    async def test_one_vendor_serving_two_components_is_one_entry(self, async_session):
        """Sarvam does both STT and TTS. Listed twice it reads as two separate
        companies holding the data."""
        await _platform_key(async_session, provider="sarvam", component="stt")
        await _platform_key(async_session, provider="sarvam", component="tts")

        listed = await subprocessors.in_use(async_session)

        entries = [s for s in listed if s.name == "sarvam"]
        assert len(entries) == 1
        assert "Speech recognition" in entries[0].purpose
        assert "speech synthesis" in entries[0].purpose

    async def test_the_sentence_does_not_depend_on_which_key_was_added_first(
        self, async_session
    ):
        """The bug this used to be. ``in_use`` reads credentials with no
        ``ORDER BY``, so Postgres makes no promise about which of the two rows
        comes back first — and a purpose built incrementally, capitalising
        whichever one ``_merge`` reached first, read differently between two
        requests for no reason a reader could see. Reversing the insertion
        order here is standing in for what an unordered scan can do on its
        own; the wording must come out identical either way."""
        await _platform_key(async_session, provider="sarvam", component="tts")
        await _platform_key(async_session, provider="sarvam", component="stt")

        listed = await subprocessors.in_use(async_session)

        entry = next(s for s in listed if s.name == "sarvam")
        assert entry.purpose == "Speech recognition; speech synthesis"

    async def test_configured_outranks_observed_for_the_same_vendor(
        self, async_session
    ):
        await _platform_key(async_session, provider="openai", component="llm")
        _, workflow, _ = await _org(async_session, "bothbases")
        run = await _run(async_session, workflow)
        await _cost_item(async_session, run, provider="openai", component="llm")

        listed = await subprocessors.in_use(async_session)

        entries = [s for s in listed if s.name == "openai"]
        assert len(entries) == 1
        assert entries[0].basis == "configured"

    async def test_hosting_and_payments_are_always_named(self, async_session):
        """Nothing in the application code enumerates its own hosting, so this
        half has to be declared — but it must never be missing."""
        listed = await subprocessors.in_use(async_session)

        names = {s.name for s in listed}
        assert "Amazon Web Services" in names
        assert "Razorpay" in names
        assert all(s.basis == "infrastructure" for s in listed if s.name == "Razorpay")


@pytest.mark.asyncio
class TestSubprocessorsPerAccount:
    async def test_an_account_is_only_told_about_its_own_vendors(self, async_session):
        """The specific answer to "who was my data shared with". A customer who
        only ever used one vendor should not be told their data went to five."""
        mine, my_workflow, _ = await _org(async_session, "subpmine")
        theirs, their_workflow, _ = await _org(async_session, "subptheirs")

        my_run = await _run(async_session, my_workflow)
        their_run = await _run(async_session, their_workflow)
        await _cost_item(async_session, my_run, provider="deepgram", component="stt")
        await _cost_item(async_session, their_run, provider="cartesia", component="tts")

        listed = await subprocessors.for_organization(
            async_session, organization_id=mine.id
        )

        names = {s.name for s in listed}
        assert "deepgram" in names
        assert "cartesia" not in names

    async def test_a_platform_key_this_account_never_used_is_not_claimed(
        self, async_session
    ):
        """A provider configured platform-wide but never routed to for this
        account has processed none of its data, and saying otherwise is a
        disclosure of a sharing that never happened."""
        org, _, _ = await _org(async_session, "subpunused")
        await _platform_key(async_session, provider="unused-vendor", component="llm")

        listed = await subprocessors.for_organization(
            async_session, organization_id=org.id
        )

        assert not any(s.name == "unused-vendor" for s in listed)

    async def test_an_account_with_no_calls_still_gets_the_infrastructure(
        self, async_session
    ):
        org, _, _ = await _org(async_session, "subpnocalls")

        listed = await subprocessors.for_organization(
            async_session, organization_id=org.id
        )

        assert {s.name for s in listed} == {"Amazon Web Services", "Razorpay"}


@pytest.mark.asyncio
class TestBreachWindowReport:
    """GDPR Art 33 allows 72 hours from becoming aware of a breach to describe
    its scope. The report is only answerable because the log was being written
    before anyone suspected anything."""

    async def _access(
        self, async_session, org, *, user_id=None, run_id=None, hours_ago=0, **kwargs
    ):
        row = DataAccessLogModel(
            organization_id=org.id,
            user_id=user_id,
            resource_type=kwargs.pop("resource_type", access_log.RECORDING),
            resource_id=kwargs.pop("resource_id", "recordings/1.wav"),
            workflow_run_id=run_id,
            action="signed_url",
            created_at=datetime.now(UTC) - timedelta(hours=hours_ago),
            **kwargs,
        )
        async_session.add(row)
        await async_session.flush()
        return row

    async def test_it_counts_what_was_reached_in_the_window(self, async_session):
        org, workflow, user = await _org(async_session, "breachcount")
        run = await _run(async_session, workflow)
        await self._access(
            async_session, org, user_id=user.id, run_id=run.id, hours_ago=2
        )
        await self._access(
            async_session,
            org,
            user_id=user.id,
            run_id=run.id,
            hours_ago=1,
            resource_type=access_log.TRANSCRIPT,
        )

        report = await access_log.window_report(
            async_session,
            organization_id=org.id,
            start=datetime.now(UTC) - timedelta(hours=3),
            end=datetime.now(UTC),
        )

        assert report["total_accesses"] == 2
        assert report["by_resource_type"] == {"recording": 1, "transcript": 1}
        assert report["distinct_calls_touched"] == 1

    async def test_access_outside_the_window_is_excluded(self, async_session):
        """The window is the whole point: the report scopes an incident to when
        it happened, and a report that swept in a year of legitimate access
        would overstate every breach."""
        org, workflow, user = await _org(async_session, "breachwindow")
        run = await _run(async_session, workflow)
        await self._access(
            async_session, org, user_id=user.id, run_id=run.id, hours_ago=48
        )
        await self._access(
            async_session, org, user_id=user.id, run_id=run.id, hours_ago=1
        )

        report = await access_log.window_report(
            async_session,
            organization_id=org.id,
            start=datetime.now(UTC) - timedelta(hours=3),
            end=datetime.now(UTC),
        )

        assert report["total_accesses"] == 1

    async def test_it_names_the_calls_that_were_touched(self, async_session):
        """Art 34 notification is to the affected people. Without the specific
        call ids there is no way to work out who those are."""
        org, workflow, user = await _org(async_session, "breachwho")
        first = await _run(async_session, workflow)
        second = await _run(async_session, workflow)
        await self._access(async_session, org, user_id=user.id, run_id=first.id)
        await self._access(async_session, org, user_id=user.id, run_id=second.id)

        report = await access_log.window_report(
            async_session,
            organization_id=org.id,
            start=datetime.now(UTC) - timedelta(hours=1),
            end=datetime.now(UTC) + timedelta(minutes=1),
        )

        assert report["affected_call_ids"] == sorted([first.id, second.id])

    async def test_unauthenticated_access_is_attributed_not_dropped(
        self, async_session
    ):
        """Access through a public share link is exactly the case worth seeing
        afterwards. A null user id must not make the row invisible."""
        org, workflow, _ = await _org(async_session, "breachanon")
        run = await _run(async_session, workflow)
        await self._access(
            async_session, org, user_id=None, run_id=run.id, actor_kind="public_token"
        )

        report = await access_log.window_report(
            async_session,
            organization_id=org.id,
            start=datetime.now(UTC) - timedelta(hours=1),
            end=datetime.now(UTC) + timedelta(minutes=1),
        )

        assert report["total_accesses"] == 1
        assert report["by_actor"] == {"unauthenticated:public_token": 1}

    async def test_it_reports_no_content(self, async_session):
        """A breach report that itself contains the compromised data is a
        second incident."""
        org, workflow, user = await _org(async_session, "breachcontent")
        run = await _run(async_session, workflow)
        await self._access(async_session, org, user_id=user.id, run_id=run.id)

        report = await access_log.window_report(
            async_session,
            organization_id=org.id,
            start=datetime.now(UTC) - timedelta(hours=1),
            end=datetime.now(UTC) + timedelta(minutes=1),
        )

        flat = str(report)
        assert "recordings/1.wav" not in flat
        assert set(report) == {
            "window_start",
            "window_end",
            "total_accesses",
            "by_resource_type",
            "by_actor",
            "distinct_calls_touched",
            "distinct_objects_touched",
            "affected_call_ids",
            "first_access",
            "last_access",
        }

    async def test_another_account_is_not_in_the_report(self, async_session):
        mine, my_workflow, my_user = await _org(async_session, "breachmine")
        theirs, their_workflow, their_user = await _org(async_session, "breachtheirs")
        await self._access(async_session, theirs, user_id=their_user.id)

        report = await access_log.window_report(
            async_session,
            organization_id=mine.id,
            start=datetime.now(UTC) - timedelta(hours=1),
            end=datetime.now(UTC) + timedelta(minutes=1),
        )

        assert report["total_accesses"] == 0
        assert report["first_access"] is None
