"""Whether the deployment is actually meeting its privacy obligations.

The point of this module is to be *distrustful*. Every other privacy test
checks that a control works; these check that the control is switched on in
this particular deployment, because the two are entirely different questions
and only the second one is asked in a security review.

Two properties matter more than the individual checks:

* **A fresh install must not read as compliant.** Nothing has happened yet, so
  there is no evidence either way, and reporting a pass on absence of evidence
  is the specific failure this exists to prevent.
* **The obligations code cannot discharge must never read ready.** Consent,
  signed processing agreements, DPO appointment. A green tick beside those is
  worse than not listing them, because somebody would rely on it.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from api.services.privacy import readiness
from api.services.privacy.readiness import (
    ACTION_REQUIRED,
    NEEDS_A_HUMAN,
    READY,
    UNKNOWN,
    assess,
)


def _by_key(assessment):
    return {check.key: check for check in assessment.checks}


@pytest.mark.asyncio
class TestTheGrievanceOfficer:
    """DPDP s13 wants a named individual. This is the check most likely to be
    silently unset, because nothing else in the system fails without it — the
    endpoint just serves null."""

    async def test_an_unset_name_is_flagged_with_a_remedy(self, async_session):
        with patch.object(readiness, "GRIEVANCE_OFFICER_NAME", ""):
            checks = _by_key(await assess(async_session))

        officer = checks["grievance_officer_named"]
        assert officer.status == ACTION_REQUIRED
        # A finding without an instruction is a finding nobody acts on.
        assert "GRIEVANCE_OFFICER_NAME" in officer.remedy

    async def test_whitespace_is_not_a_name(self, async_session):
        with patch.object(readiness, "GRIEVANCE_OFFICER_NAME", "   "):
            checks = _by_key(await assess(async_session))

        assert checks["grievance_officer_named"].status == ACTION_REQUIRED

    async def test_a_named_officer_passes(self, async_session):
        with patch.object(readiness, "GRIEVANCE_OFFICER_NAME", "A Real Person"):
            checks = _by_key(await assess(async_session))

        assert checks["grievance_officer_named"].status == READY

    async def test_an_email_alone_does_not_satisfy_the_requirement(self, async_session):
        """A role mailbox is not a person. If this ever passes on email alone,
        every deployment reads compliant while naming nobody."""
        with (
            patch.object(readiness, "GRIEVANCE_OFFICER_NAME", ""),
            patch.object(readiness, "GRIEVANCE_OFFICER_EMAIL", "privacy@decibyl.ai"),
        ):
            checks = _by_key(await assess(async_session))

        assert checks["grievance_officer_named"].status == ACTION_REQUIRED


@pytest.mark.asyncio
class TestDeploymentConfiguration:
    async def test_a_public_recordings_bucket_is_action_required(self, async_session):
        """One environment variable away from anonymous read, write and delete
        on a bucket of recorded human voices."""
        with patch.object(readiness, "MINIO_PUBLIC_BUCKET", True):
            checks = _by_key(await assess(async_session))

        bucket = checks["recordings_not_public"]
        assert bucket.status == ACTION_REQUIRED
        assert "MINIO_PUBLIC_BUCKET" in bucket.remedy

    async def test_a_private_bucket_passes(self, async_session):
        with patch.object(readiness, "MINIO_PUBLIC_BUCKET", False):
            checks = _by_key(await assess(async_session))

        assert checks["recordings_not_public"].status == READY

    async def test_disclosure_switched_off_is_flagged(self, async_session):
        with patch.object(readiness, "RECORDING_DISCLOSURE_ENABLED", False):
            checks = _by_key(await assess(async_session))

        assert checks["recording_disclosure"].status == ACTION_REQUIRED


@pytest.mark.asyncio
class TestEvidenceRatherThanConfiguration:
    """The checks that would catch a deployment where the worker is dead. A
    configured retention window with nothing running deletes exactly as much as
    no window at all."""

    async def test_an_empty_access_log_is_unknown_not_ready(self, async_session):
        """The distinction the whole module rests on. Nothing logged on a fresh
        install is honest ignorance; calling it a pass would mean a deployment
        that never wrote the log reports green forever."""
        checks = _by_key(await assess(async_session))

        assert checks["access_log_written"].status == UNKNOWN

    async def test_no_overdue_recordings_passes(self, async_session):
        checks = _by_key(await assess(async_session))

        assert checks["no_overdue_recordings"].status == READY

    async def test_an_erasure_request_past_its_deadline_is_action_required(
        self, async_session
    ):
        from api.db.models import ErasureRequestModel, OrganizationModel

        org = OrganizationModel(provider_id="org-late", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()

        async_session.add(
            ErasureRequestModel(
                organization_id=org.id,
                subject_type="phone_number",
                subject_hash="a" * 64,
                requested_at=datetime.now(UTC) - timedelta(days=45),
                completed_at=None,
                status="pending",
            )
        )
        await async_session.flush()

        checks = _by_key(await assess(async_session))

        assert checks["erasure_within_deadline"].status == ACTION_REQUIRED


@pytest.mark.asyncio
class TestWhatCodeCannotDo:
    async def test_the_human_obligations_can_never_read_ready(self, async_session):
        """No configuration, no amount of passing tests, and no future change
        should be able to flip these. They are documents and decisions."""
        assessment = await assess(async_session)

        human = [c for c in assessment.checks if c.status == NEEDS_A_HUMAN]
        assert {c.key for c in human} == {
            "notice_and_consent",
            "published_policies",
            "significant_data_fiduciary",
            "cross_border",
        }
        for check in human:
            assert check.remedy

    async def test_consent_is_listed_as_the_customers_obligation(self, async_session):
        """The legal structure the whole product rests on: on outbound calling
        our customer is the Data Fiduciary and we are the Processor. If that is
        ever quietly restated, the liability moves to us."""
        checks = _by_key(await assess(async_session))

        assert "Processor" in checks["notice_and_consent"].detail

    async def test_they_are_counted_separately_from_the_fixable_ones(
        self, async_session
    ):
        """Folding them into the action list would make the count permanently
        non-zero, and a checklist that can never reach zero gets ignored."""
        assessment = await assess(async_session)

        assert len(assessment.unresolvable) == 4
        assert not set(assessment.blocking) & set(assessment.unresolvable)


@pytest.mark.asyncio
class TestTheWholeAssessment:
    async def test_every_finding_carries_a_legal_reference(self, async_session):
        """A finding a reviewer cannot follow up is a finding they have to take
        on trust, which is the opposite of the point."""
        for check in (await assess(async_session)).checks:
            assert check.reference

    async def test_every_unmet_check_says_what_to_do(self, async_session):
        with (
            patch.object(readiness, "GRIEVANCE_OFFICER_NAME", ""),
            patch.object(readiness, "GRIEVANCE_OFFICER_ADDRESS", ""),
            patch.object(readiness, "MINIO_PUBLIC_BUCKET", True),
            patch.object(readiness, "RECORDING_DISCLOSURE_ENABLED", False),
        ):
            assessment = await assess(async_session)

        assert assessment.blocking
        for check in assessment.blocking:
            assert check.remedy, f"{check.key} has no remedy"

    async def test_a_correctly_configured_deployment_has_nothing_blocking(
        self, async_session
    ):
        with (
            patch.object(readiness, "GRIEVANCE_OFFICER_NAME", "A Real Person"),
            patch.object(readiness, "GRIEVANCE_OFFICER_EMAIL", "privacy@decibyl.ai"),
            patch.object(readiness, "GRIEVANCE_OFFICER_ADDRESS", "Somewhere"),
            patch.object(readiness, "MINIO_PUBLIC_BUCKET", False),
            patch.object(readiness, "RECORDING_DISCLOSURE_ENABLED", True),
        ):
            assessment = await assess(async_session)

        assert assessment.blocking == ()
        # But still not compliant, and the module must keep saying so.
        assert assessment.unresolvable
