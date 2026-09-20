"""Deleting call data once its purpose is served.

Storage limitation is the privacy obligation that no document can satisfy.
DPDP s8(7) and GDPR Art 5(1)(e) both say personal data must stop existing once
the purpose it was collected for is finished, and the only thing that satisfies
that is a job which deletes things.

**Audio and text are aged separately.** A recording is a person's voice — among
the most identifying data there is, and useless to the customer a week after the
call. A transcript is text, far less sensitive and far more useful: it is what
campaign reporting and quality review actually read. One window for both would
be either too short to run a business on or too long to defend.

**Storage first, then the database.** Deleting the row and leaving the object is
the failure mode that matters, because it looks exactly like success: the UI
shows nothing, the audit trail says purged, and the audio is still sitting in a
bucket. So the object is deleted first and the row is only cleared once that has
happened.

**Billing figures survive.** Duration, cost and the receipt stay; the recording,
transcript and gathered context go. This is not an oversight — GST records must
be kept for years after the conversation they describe should have been
forgotten, and both DPDP and GDPR carve out retention required by other law
(GDPR Art 17(3)(b)). What is kept is the arithmetic, not the conversation: how
long a call lasted and what it cost identifies nobody.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import (
    DEFAULT_RECORDING_RETENTION_DAYS,
    DEFAULT_TRANSCRIPT_RETENTION_DAYS,
)
from api.db.models import (
    DataRetentionPolicyModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.privacy.redaction import redact_annotations
from api.utils.recording_artifacts import get_recording_storage_key

#: The tracks a call can be recorded on. A purge that removed the mixed
#: recording and left the per-speaker ones would report success while the audio
#: was still there.
RECORDING_TRACKS = ("mixed", "user", "bot")

#: Marks a run whose personal data has been removed. Distinguishable from a run
#: that never had any, which matters when answering "was this erased or did it
#: never exist".
PURGED_MARKER = "__purged__"

#: Never purge anything younger than this, whatever a policy says. A retention
#: window set to a mistaken 0 would otherwise delete calls as they finish, and
#: the data is gone before anyone notices the typo.
MINIMUM_RETENTION_DAYS = 1


@dataclass(frozen=True)
class RetentionPolicy:
    """Resolved retention windows for one organization."""

    recording_days: int
    transcript_days: int
    is_default: bool


async def resolve_policy(
    session: AsyncSession, *, organization_id: int
) -> RetentionPolicy:
    """The windows in force for an account, falling back to the platform default."""
    row = await session.scalar(
        select(DataRetentionPolicyModel).where(
            DataRetentionPolicyModel.organization_id == organization_id
        )
    )
    if row is None:
        return RetentionPolicy(
            recording_days=DEFAULT_RECORDING_RETENTION_DAYS,
            transcript_days=DEFAULT_TRANSCRIPT_RETENTION_DAYS,
            is_default=True,
        )

    recording = (
        row.recording_retention_days
        if row.recording_retention_days is not None
        else DEFAULT_RECORDING_RETENTION_DAYS
    )
    transcript = (
        row.transcript_retention_days
        if row.transcript_retention_days is not None
        else DEFAULT_TRANSCRIPT_RETENTION_DAYS
    )
    return RetentionPolicy(
        recording_days=max(MINIMUM_RETENTION_DAYS, int(recording)),
        transcript_days=max(MINIMUM_RETENTION_DAYS, int(transcript)),
        is_default=False,
    )


async def set_policy(
    session: AsyncSession,
    *,
    organization_id: int,
    recording_retention_days: int | None,
    transcript_retention_days: int | None,
    updated_by: int | None = None,
) -> RetentionPolicy:
    """Set one account's retention windows. None means "use the default"."""
    for value in (recording_retention_days, transcript_retention_days):
        if value is not None and value < MINIMUM_RETENTION_DAYS:
            raise ValueError(
                f"Retention must be at least {MINIMUM_RETENTION_DAYS} day. Use the "
                "erasure tools to remove data immediately."
            )

    row = await session.scalar(
        select(DataRetentionPolicyModel).where(
            DataRetentionPolicyModel.organization_id == organization_id
        )
    )
    if row is None:
        row = DataRetentionPolicyModel(organization_id=organization_id)
        session.add(row)

    row.recording_retention_days = recording_retention_days
    row.transcript_retention_days = transcript_retention_days
    row.updated_by = updated_by
    await session.flush()

    logger.info(
        "Retention policy for org {}: recordings {} days, transcripts {} days",
        organization_id,
        recording_retention_days or "default",
        transcript_retention_days or "default",
    )
    return await resolve_policy(session, organization_id=organization_id)


def _storage_keys(run: WorkflowRunModel, *, include_transcript: bool) -> list[str]:
    """Every object belonging to one run that this purge is entitled to delete.

    Both mixed and per-speaker tracks: a purge that removed the combined
    recording and left the separated ones would be worse than none, because it
    would report success.

    **`include_transcript` is not a refinement, it is the correctness of the
    thing.** Recordings and transcripts have separate retention windows, and the
    recording one is shorter — that is the point of having two. So the ordinary
    case is a run whose audio has expired and whose transcript has not. Deleting
    every object belonging to the run on that sweep destroyed the transcript
    years early *and* left `transcript_url` pointing at it, so the row claimed a
    transcript that storage no longer had. Data loss that reports success, and
    the operator finds out when someone asks for a call that is gone.
    """
    keys: list[str] = []
    for track in RECORDING_TRACKS:
        key = get_recording_storage_key(run.extra, track)
        if key:
            keys.append(key)

    # The legacy columns hold bare storage keys on older rows.
    legacy_columns = [run.recording_url]
    if include_transcript:
        legacy_columns.append(run.transcript_url)
    for legacy in legacy_columns:
        if legacy and legacy != PURGED_MARKER and not legacy.startswith("http"):
            keys.append(legacy)

    # De-duplicated but order-preserving, so a failure log reads in a sensible
    # order rather than a set's arbitrary one.
    seen: set[str] = set()
    unique = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


async def _delete_objects(keys: list[str]) -> tuple[int, list[str]]:
    """Remove objects from storage. Returns (deleted, failed keys)."""
    if not keys:
        return 0, []

    from api.services.storage import get_storage

    filesystem = get_storage()
    deleted = 0
    failed: list[str] = []
    for key in keys:
        try:
            ok = await filesystem.adelete_file(key)
            if ok:
                deleted += 1
            else:
                failed.append(key)
        except Exception as exc:  # noqa: BLE001 - one bad key must not stop the rest
            logger.error("Could not delete object {}: {}", key, exc)
            failed.append(key)
    return deleted, failed


async def purge_run(
    session: AsyncSession, *, run: WorkflowRunModel, drop_transcript: bool
) -> tuple[int, bool]:
    """Strip one run's personal data. Returns (objects deleted, row cleared).

    Storage first. If an object cannot be deleted the row keeps its pointer, so
    the next sweep tries again rather than orphaning audio nobody can now find.
    """
    deleted, failed = await _delete_objects(
        _storage_keys(run, include_transcript=drop_transcript)
    )
    if failed:
        logger.warning(
            "Run {}: {} object(s) could not be deleted; leaving the row intact "
            "so the next sweep retries",
            run.id,
            len(failed),
        )
        return deleted, False

    run.recording_url = PURGED_MARKER
    run.extra = {
        k: v
        for k, v in (run.extra or {}).items()
        if k not in {"recording_artifacts", "recordings"}
    }

    if drop_transcript:
        run.transcript_url = PURGED_MARKER
        # The conversation itself, plus whatever the agent collected from the
        # person on the other end.
        run.gathered_context = {}
        run.initial_context = {}
        run.logs = {}
        # And what the post-call pass *learned* from it. The QA summary and
        # every extracted field lived on past this point for as long as the row
        # did — a transcript can expire while a tidy JSON object of the same
        # person's details does not. Outcome labels survive; see redaction.py
        # for why those identify nobody.
        run.annotations = redact_annotations(run.annotations)

    await session.flush()
    return deleted, True


async def purge_expired(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 500
) -> dict:
    """Delete call data that has outlived its retention window.

    Bounded per run so one sweep cannot hold a transaction open across a very
    large backlog; the cron simply catches up over subsequent passes.

    **The batch has to be spent on rows with work due, which is why the policy
    is in the query.** The candidate filter used to be "recording not purged OR
    transcript not purged" -- a correct description of unfinished work and the
    wrong description of work available *now*. Recordings expire at 90 days and
    transcripts at 365, so between those dates a row has had its audio deleted
    and its transcript is not yet due. It is finished for the time being, it
    still matched, and being the oldest it sorted first.

    Once ``limit`` runs sat in that window they filled the batch every night
    and the rows behind them crossing day 90 were never reached. Nothing
    failed: ``purge_run`` on an already-purged row finds no objects, reports
    the row cleared, and the nightly log said "purged 500 runs, deleted 0
    objects" indefinitely. The silent-absence shape from AGENTS.md, with a
    deletion as the thing that goes missing -- so what accumulated was personal
    data a privacy notice says was erased.

    So the predicate now asks what the loop below would actually do, against
    each organization's own windows rather than one platform-wide number:
    the recording window has passed, and either the audio is still there or the
    transcript is still there and its own window has passed too. Rows with
    nothing due are not selected, so every batch makes progress.
    """
    now = now or datetime.now(UTC)

    # Widest window first, so one query bounds the candidate set no matter which
    # organizations have which policies.
    widest = max(
        DEFAULT_RECORDING_RETENTION_DAYS,
        DEFAULT_TRANSCRIPT_RETENTION_DAYS,
    )
    policies = (await session.scalars(select(DataRetentionPolicyModel))).all()
    for policy in policies:
        widest = max(
            widest,
            policy.recording_retention_days or 0,
            policy.transcript_retention_days or 0,
        )

    def _window(column) -> Any:
        """One organization's window in days, as the DB sees it.

        Mirrors ``resolve_policy``: the policy's value, the platform default
        where the policy is absent or leaves it null, and never below
        ``MINIMUM_RETENTION_DAYS`` -- a window mistakenly set to 0 must not
        delete calls as they finish.
        """
        default = (
            DEFAULT_RECORDING_RETENTION_DAYS
            if column is DataRetentionPolicyModel.recording_retention_days
            else DEFAULT_TRANSCRIPT_RETENTION_DAYS
        )
        return func.greatest(MINIMUM_RETENTION_DAYS, func.coalesce(column, default))

    def _due(column) -> Any:
        """Whether a run is past that window, as at ``now``."""
        return WorkflowRunModel.created_at <= now - func.make_interval(
            0, 0, 0, _window(column)
        )

    recording_due = _due(DataRetentionPolicyModel.recording_retention_days)
    transcript_due = _due(DataRetentionPolicyModel.transcript_retention_days)

    # is_distinct_from, not !=, because a NULL url is "not purged" and SQL's
    # != would answer NULL to that.
    recording_present = WorkflowRunModel.recording_url.is_distinct_from(PURGED_MARKER)
    transcript_present = WorkflowRunModel.transcript_url.is_distinct_from(PURGED_MARKER)

    candidates = (
        await session.scalars(
            select(WorkflowRunModel)
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            # Outer, because most organizations have no policy row and take the
            # platform default. An inner join would quietly exempt every one of
            # them from retention -- an allowlist by accident.
            .outerjoin(
                DataRetentionPolicyModel,
                DataRetentionPolicyModel.organization_id
                == WorkflowModel.organization_id,
            )
            .where(
                WorkflowModel.organization_id.is_not(None),
                # The recording window gates the whole row, matching the loop:
                # purge_run always clears the audio, so it must not be called
                # for a run whose audio is still within its window even when
                # the transcript window is shorter.
                recording_due,
                or_(recording_present, and_(transcript_present, transcript_due)),
            )
            .order_by(WorkflowRunModel.created_at)
            .limit(limit)
        )
    ).all()

    runs_purged = 0
    objects_deleted = 0
    for run in candidates:
        workflow = await session.get(WorkflowModel, run.workflow_id)
        organization_id = getattr(workflow, "organization_id", None)
        if organization_id is None:
            continue

        policy = await resolve_policy(session, organization_id=organization_id)
        age_days = (now - run.created_at).days if run.created_at else 0

        # Kept although the query now applies the same rule. The two compute
        # the boundary differently -- whole days here, an interval there -- so
        # for a few hours either side of a window's edge they can disagree.
        # This is the direction that must not be wrong: a row the query offered
        # too early is skipped and collected on the next sweep, where deleting
        # audio a few hours early cannot be undone.
        if age_days < policy.recording_days:
            continue

        deleted, cleared = await purge_run(
            session,
            run=run,
            drop_transcript=age_days >= policy.transcript_days,
        )
        objects_deleted += deleted
        if cleared:
            runs_purged += 1

    if runs_purged:
        logger.info(
            "Retention sweep: purged {} run(s), deleted {} object(s)",
            runs_purged,
            objects_deleted,
        )
    return {
        "runs_purged": runs_purged,
        "objects_deleted": objects_deleted,
        "candidates_examined": len(candidates),
        "widest_window_days": widest,
    }
