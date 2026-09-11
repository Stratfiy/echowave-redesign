"""A share link spends the owner's credits in a stranger's hands, so it has a
daily cap, an expiry and a switch; the cap counts calls still running."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import (
    EmbedSessionModel,
    EmbedTokenModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services import share_links


class TestTheArithmetic:
    def test_a_fresh_link_has_its_whole_day(self):
        usage = share_links.LinkUsage(cap_minutes=30, seconds_used_today=0)
        assert usage.remaining_seconds == 30 * 60
        assert not usage.is_exhausted
        assert usage.minutes_used_today == 0

    def test_a_spent_link_is_exhausted(self):
        usage = share_links.LinkUsage(cap_minutes=30, seconds_used_today=1_800)
        assert usage.is_exhausted
        assert usage.minutes_used_today == 30

    def test_partial_minutes_round_up_for_the_owner(self):
        usage = share_links.LinkUsage(cap_minutes=30, seconds_used_today=61)
        assert usage.minutes_used_today == 2

    def test_no_cap_means_no_ceiling(self):
        usage = share_links.LinkUsage(cap_minutes=None, seconds_used_today=99_999)
        assert usage.remaining_seconds is None
        assert not usage.is_exhausted

    def test_the_refusal_names_the_remedy(self):
        message = str(share_links.LinkCapReached(30))
        assert "used its minutes for today" in message
        assert "raise the limit" in message

    def test_defaults_are_modest(self):
        assert share_links.DEFAULT_DAILY_MINUTES == 30
        assert share_links.DEFAULT_EXPIRY_DAYS == 30
        assert share_links.MAX_DAILY_MINUTES == 24 * 60


async def _fixture(session):
    org = OrganizationModel(provider_id="org-share-caps", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    user = UserModel(provider_id="share-caps-owner", selected_organization_id=org.id)
    session.add(user)
    await session.flush()
    workflow = WorkflowModel(name="Shared", organization_id=org.id)
    session.add(workflow)
    await session.flush()
    token = EmbedTokenModel(
        token="emb_share_caps",
        workflow_id=workflow.id,
        organization_id=org.id,
        created_by=user.id,
        allowed_domains=[],
        settings={},
        is_active=True,
        usage_count=0,
        daily_minutes_cap=30,
    )
    session.add(token)
    await session.flush()
    return org, user, workflow, token


async def _session_with_run(
    session, org, user, workflow, token, *, created_at, billable=None, ended=True
):
    run = WorkflowRunModel(
        name="embed",
        workflow_id=workflow.id,
        mode="smallwebrtc",
        created_at=created_at,
        billable_seconds=billable,
        ended_at=created_at + timedelta(minutes=1) if ended else None,
    )
    session.add(run)
    await session.flush()
    session.add(
        EmbedSessionModel(
            session_token=f"sess_{run.id}",
            embed_token_id=token.id,
            workflow_run_id=run.id,
            created_at=created_at,
            expires_at=created_at + timedelta(hours=1),
        )
    )
    await session.flush()


class TestWhatCountsAgainstToday:
    async def test_ended_calls_add_their_billable_seconds(self, async_session):
        org, user, workflow, token = await _fixture(async_session)
        now = datetime.now(UTC).replace(hour=12)
        for billable in (120, 300):
            await _session_with_run(
                async_session,
                org,
                user,
                workflow,
                token,
                created_at=now - timedelta(minutes=30),
                billable=billable,
            )
        used = await share_links.seconds_used_today(
            async_session, embed_token_id=token.id, now=now
        )
        assert used == 420

    async def test_yesterday_does_not_count(self, async_session):
        org, user, workflow, token = await _fixture(async_session)
        now = datetime.now(UTC).replace(hour=12)
        await _session_with_run(
            async_session,
            org,
            user,
            workflow,
            token,
            created_at=now - timedelta(days=1),
            billable=1_800,
        )
        assert (
            await share_links.seconds_used_today(
                async_session, embed_token_id=token.id, now=now
            )
            == 0
        )

    async def test_a_call_still_running_counts_its_elapsed_time(self, async_session):
        """Ten tabs opened at once must not each see an empty day."""
        org, user, workflow, token = await _fixture(async_session)
        now = datetime.now(UTC).replace(hour=12)
        await _session_with_run(
            async_session,
            org,
            user,
            workflow,
            token,
            created_at=now - timedelta(minutes=5),
            billable=None,
            ended=False,
        )
        used = await share_links.seconds_used_today(
            async_session, embed_token_id=token.id, now=now
        )
        assert 295 <= used <= 305

    async def test_a_run_the_pipeline_never_closed_stops_counting(self, async_session):
        org, user, workflow, token = await _fixture(async_session)
        now = datetime.now(UTC).replace(hour=12)
        await _session_with_run(
            async_session,
            org,
            user,
            workflow,
            token,
            created_at=now - timedelta(hours=3),
            billable=None,
            ended=False,
        )
        assert (
            await share_links.seconds_used_today(
                async_session, embed_token_id=token.id, now=now
            )
            == 0
        )

    async def test_the_gate_refuses_a_spent_link_and_passes_a_fresh_one(
        self, async_session
    ):
        org, user, workflow, token = await _fixture(async_session)
        usage = await share_links.assert_within_cap(
            async_session, embed_token_id=token.id, cap_minutes=30
        )
        assert usage.remaining_seconds == 1_800
        now = datetime.now(UTC).replace(hour=12)
        await _session_with_run(
            async_session,
            org,
            user,
            workflow,
            token,
            created_at=now - timedelta(minutes=1),
            billable=1_800,
        )
        with pytest.raises(share_links.LinkCapReached):
            await share_links.assert_within_cap(
                async_session, embed_token_id=token.id, cap_minutes=30
            )

    async def test_an_uncapped_widget_token_never_reads_the_table(self, async_session):
        usage = await share_links.assert_within_cap(
            async_session, embed_token_id=999_999, cap_minutes=None
        )
        assert usage.remaining_seconds is None


class TestTheCapCanActuallyBeSet:
    """The write path, which silently dropped the cap.

    ``update_embed_token`` filters its kwargs against an allow-list, and
    ``daily_minutes_cap`` was not on it. Every path that sets a cap goes
    through there: the owner lowering it on the share dialog, and
    ``create_share_link`` restoring a default when it reactivates a switched
    off link. All of them returned 200 and changed nothing, so the only link
    anybody could make was an uncapped one -- the exact thing the cap exists
    to prevent, shipped as the default.

    Asserted against the allow-list itself rather than through a live update:
    the defect is entirely in that set, and a test of the set fails for the
    one reason it should.
    """

    def _allowed(self) -> set[str]:
        import ast
        import inspect

        from api.db.embed_token_client import EmbedTokenClient

        tree = ast.parse(
            inspect.getsource(EmbedTokenClient.update_embed_token).lstrip()
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "allowed_fields" for t in node.targets
            ):
                return {ast.literal_eval(e) for e in node.value.elts}
        raise AssertionError("update_embed_token no longer has an allow-list")

    def test_the_cap_is_writable(self):
        assert "daily_minutes_cap" in self._allowed()

    def test_the_other_settings_the_dialog_writes_are_too(self):
        allowed = self._allowed()
        for field in ("is_active", "expires_at", "allowed_domains", "settings"):
            assert field in allowed, field

    def test_nothing_a_caller_must_not_move_is_writable(self):
        """The token string and its owner are identity, not settings."""
        allowed = self._allowed()
        for field in ("token", "organization_id", "workflow_id", "created_by"):
            assert field not in allowed, field
