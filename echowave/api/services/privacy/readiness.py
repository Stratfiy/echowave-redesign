"""Whether the privacy obligations are actually being met, right now.

Everything else in this package *does* the work: retention deletes, erasure
erases, the access log records. This module answers a different question, and
it is the one asked in a security review and on the morning of an incident —
**is any of it switched on in this deployment?**

That question is not answerable from the code, because every control here can
be configured off, left unconfigured, or silently fail. A nightly purge that
throws every night looks identical to one that has nothing to delete. A
grievance officer that was never named returns ``null`` from a live endpoint
and nobody notices until a Data Principal has a complaint and no one to send it
to.

So the checks split in two, and the distinction is the point:

* **Configuration** — is the setting present. Cheap to check, cheap to fix, and
  worth nothing on its own: a configured retention window with a dead worker
  deletes exactly as much as no window at all.
* **Evidence** — did the thing actually happen. Are there recordings past their
  window still sitting in storage; has the access log ever been written; is an
  erasure request past its deadline. These are the checks that would have caught
  a broken deployment, and they are the ones an auditor asks for.

A fresh install has no evidence either way, and this reports that as
``unknown`` rather than as a pass. Reporting "compliant" because nothing has
happened yet is the specific dishonesty this module exists to avoid.

**None of this makes anyone compliant.** DPDP obligations that no code can
discharge — lawful notice and consent, a signed processing agreement with each
customer, appointing a Data Protection Officer above the Significant Data
Fiduciary threshold — are listed here as unmet with no way to mark them met,
because a green tick beside them would be a lie a court would take seriously.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import (
    BACKUP_ENABLED,
    BACKUP_STALE_AFTER_HOURS,
    DEFAULT_RECORDING_RETENTION_DAYS,
    DEFAULT_TRANSCRIPT_RETENTION_DAYS,
    GRIEVANCE_OFFICER_ADDRESS,
    GRIEVANCE_OFFICER_EMAIL,
    GRIEVANCE_OFFICER_NAME,
    MINIO_PUBLIC_BUCKET,
    RECORDING_DISCLOSURE_ENABLED,
)
from api.db.models import DataAccessLogModel
from api.services.privacy.metrics import (
    ERASURE_DEADLINE_DAYS,
    erasure_turnaround,
    overdue_recordings,
)

#: A check that is satisfied.
READY = "ready"
#: A check that is not satisfied and can be fixed by whoever runs the platform.
ACTION_REQUIRED = "action_required"
#: Nothing has happened yet that would prove this either way. Not a pass.
UNKNOWN = "unknown"
#: An obligation no code can discharge. Never reports ``ready``.
NEEDS_A_HUMAN = "needs_a_human"


@dataclass(frozen=True)
class Check:
    """One obligation, and whether this deployment is meeting it."""

    key: str
    title: str
    status: str
    #: What is true right now, in a sentence someone can act on.
    detail: str
    #: The legal hook, so a reviewer can follow it up rather than trust us.
    reference: str
    #: What to do about it. Empty when the check is satisfied.
    remedy: str = ""


@dataclass(frozen=True)
class Readiness:
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def action_required(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.status == ACTION_REQUIRED)

    @property
    def blocking(self) -> tuple[Check, ...]:
        """What must be fixed before taking a customer's personal data.

        Excludes ``needs_a_human`` deliberately — those never clear, so folding
        them in would make the count permanently non-zero and therefore ignored.
        They are reported separately for exactly that reason.
        """
        return self.action_required

    @property
    def unresolvable(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.status == NEEDS_A_HUMAN)


def _grievance_officer_checks() -> list[Check]:
    """DPDP s13 wants a person, reachable, published.

    A role mailbox is not a person. The Act requires the *name* of the
    individual who will answer, which is why an address and an email alone do
    not clear this check.
    """
    checks = []

    if GRIEVANCE_OFFICER_NAME.strip():
        checks.append(
            Check(
                key="grievance_officer_named",
                title="Grievance officer named",
                status=READY,
                detail=f"{GRIEVANCE_OFFICER_NAME} is published at /api/v1/privacy/subprocessors.",
                reference="DPDP s13",
            )
        )
    else:
        checks.append(
            Check(
                key="grievance_officer_named",
                title="Grievance officer named",
                status=ACTION_REQUIRED,
                detail=(
                    "No name is set. The privacy endpoint is serving null, so a "
                    "Data Principal has nobody to address a complaint to."
                ),
                reference="DPDP s13",
                remedy="Set GRIEVANCE_OFFICER_NAME in .env to a real person's name and restart the api container.",
            )
        )

    if not GRIEVANCE_OFFICER_EMAIL.strip():
        checks.append(
            Check(
                key="grievance_officer_reachable",
                title="Grievance officer reachable",
                status=ACTION_REQUIRED,
                detail="No contact email is set.",
                reference="DPDP s13",
                remedy="Set GRIEVANCE_OFFICER_EMAIL to a monitored mailbox.",
            )
        )
    else:
        checks.append(
            Check(
                key="grievance_officer_reachable",
                title="Grievance officer reachable",
                status=READY,
                detail=f"Complaints go to {GRIEVANCE_OFFICER_EMAIL}.",
                reference="DPDP s13",
            )
        )

    if not GRIEVANCE_OFFICER_ADDRESS.strip():
        checks.append(
            Check(
                key="grievance_officer_address",
                title="Grievance officer postal address",
                status=ACTION_REQUIRED,
                detail="No postal address is published.",
                reference="DPDP s13",
                remedy="Set GRIEVANCE_OFFICER_ADDRESS in .env (quote it — it contains commas).",
            )
        )

    return checks


def _configuration_checks() -> list[Check]:
    checks: list[Check] = []

    checks.append(
        Check(
            key="recording_disclosure",
            title="Callers are told the call is recorded",
            status=READY if RECORDING_DISCLOSURE_ENABLED else ACTION_REQUIRED,
            detail=(
                "Spoken before the greeting on every voice call, so the notice "
                "lands in the recording itself."
                if RECORDING_DISCLOSURE_ENABLED
                else "Disclosure is switched off. Calls are being recorded without telling anyone."
            ),
            reference="DPDP s5 (notice); two-party consent states",
            remedy=""
            if RECORDING_DISCLOSURE_ENABLED
            else "Remove RECORDING_DISCLOSURE_ENABLED=false from .env, or set it to true.",
        )
    )

    # The single worst misconfiguration available here, and it is one
    # environment variable. Anonymous read *and write and delete* on a bucket
    # of recorded human voices.
    checks.append(
        Check(
            key="recordings_not_public",
            title="Recordings are not world-readable",
            status=ACTION_REQUIRED if MINIO_PUBLIC_BUCKET else READY,
            detail=(
                "MINIO_PUBLIC_BUCKET is on: the recordings bucket is anonymously "
                "readable, writable and deletable."
                if MINIO_PUBLIC_BUCKET
                else "Recordings are served by presigned URL; the bucket grants no anonymous access."
            ),
            reference="DPDP s8(5) (reasonable security safeguards); GDPR Art 32",
            remedy="Remove MINIO_PUBLIC_BUCKET from .env entirely and restart."
            if MINIO_PUBLIC_BUCKET
            else "",
        )
    )

    checks.append(
        Check(
            key="retention_windows",
            title="Retention windows are set",
            status=READY,
            detail=(
                f"Recordings {DEFAULT_RECORDING_RETENTION_DAYS} days, transcripts "
                f"{DEFAULT_TRANSCRIPT_RETENTION_DAYS} days by default; customers "
                "may set shorter windows of their own."
            ),
            reference="DPDP s8(7); GDPR Art 5(1)(e)",
        )
    )

    return checks


async def _evidence_checks(
    session: AsyncSession, *, now: datetime | None = None
) -> list[Check]:
    """The checks that would catch a deployment where nothing is running."""
    now = now or datetime.now(UTC)
    checks: list[Check] = []

    # Storage limitation, as an outcome rather than a setting. This is the
    # headline privacy metric precisely because the correct value is zero and
    # any other value means the sweep is not doing its job.
    overdue = await overdue_recordings(session, now=now)
    count = overdue.get("overdue", 0)
    checks.append(
        Check(
            key="no_overdue_recordings",
            title="No recordings past their retention window",
            status=READY if count == 0 else ACTION_REQUIRED,
            detail=(
                "Every recording in storage is inside its window."
                if count == 0
                else f"{count} recordings are past their window and still exist "
                f"(oldest {overdue.get('oldest_overdue_days')} days)."
            ),
            reference="DPDP s8(7); GDPR Art 5(1)(e)",
            remedy=""
            if count == 0
            else (
                "The nightly purge is not completing. Check the arq worker is "
                "running and read its logs for purge_expired_call_data."
            ),
        )
    )

    # Art 33 gives 72 hours to describe a breach's scope. That is only
    # answerable if the log was being written before the breach — afterwards is
    # too late, which is why "has it ever been written" is worth checking.
    logged = (
        await session.execute(select(func.count()).select_from(DataAccessLogModel))
    ).scalar() or 0
    checks.append(
        Check(
            key="access_log_written",
            title="Access to recordings is being logged",
            status=READY if logged else UNKNOWN,
            detail=(
                f"{logged} access events recorded. A breach window can be scoped."
                if logged
                else (
                    "Nothing logged yet. Expected on a new deployment; if "
                    "recordings have been played, the log is not being written."
                )
            ),
            reference="GDPR Art 33 (72-hour breach scoping)",
            remedy=""
            if logged
            else "Play back one recording, then re-check that this reads a non-zero count.",
        )
    )

    # Not a privacy obligation in itself, but the one whose absence destroys
    # every other record here — including the proof that an erasure was carried
    # out. Checked by the age of the newest dump, because the presence of the
    # backup code proves nothing about whether it runs.
    from api.services.backup import last_successful

    backup = await last_successful(now=now)
    age = backup.get("age_hours")
    if not BACKUP_ENABLED:
        backup_status, backup_detail = (
            ACTION_REQUIRED,
            "Backups are switched off. The credit ledger has no other copy.",
        )
    elif not backup.get("available"):
        backup_status, backup_detail = (
            UNKNOWN,
            "No backup found yet. Expected before the first nightly run; an "
            "incident after that.",
        )
    elif age is not None and age > BACKUP_STALE_AFTER_HOURS:
        backup_status, backup_detail = (
            ACTION_REQUIRED,
            f"Newest backup is {age:.0f} hours old — the nightly job is not completing.",
        )
    else:
        backup_status, backup_detail = (
            READY,
            f"{backup['count']} backups held; newest is {age:.0f} hours old.",
        )

    checks.append(
        Check(
            key="database_backed_up",
            title="The database is backed up",
            status=backup_status,
            detail=backup_detail,
            reference="DPDP s8(5) (reasonable security safeguards); GDPR Art 32(1)(c)",
            remedy=""
            if backup_status == READY
            else (
                "Check the arq worker is running and read its logs for "
                "run_database_backup. An untested backup is not a backup — "
                "rehearse a restore once."
            ),
        )
    )

    turnaround = await erasure_turnaround(session, now=now)
    late = turnaround.get("past_deadline", 0)
    checks.append(
        Check(
            key="erasure_within_deadline",
            title="Erasure requests answered in time",
            status=ACTION_REQUIRED if late else READY,
            detail=(
                f"{late} request(s) past the {ERASURE_DEADLINE_DAYS}-day deadline."
                if late
                else f"No request is past the {ERASURE_DEADLINE_DAYS}-day deadline."
            ),
            reference="DPDP s12(3); GDPR Art 12(3), Art 17",
            remedy="Work the queue at /api/v1/privacy/erasure-requests."
            if late
            else "",
        )
    )

    return checks


def _obligations_code_cannot_meet() -> list[Check]:
    """Listed so they are not forgotten, and never markable as done.

    Every one of these is a document or a decision. Shipping software that
    displayed them as satisfied would be worse than not listing them, because
    somebody would rely on it.
    """
    return [
        Check(
            key="notice_and_consent",
            title="Notice and consent for the people being called",
            status=NEEDS_A_HUMAN,
            detail=(
                "On outbound calling the person called is a Data Principal, and "
                "the consent obligation sits with the customer whose lead list "
                "it is — not with Decibyl, which is their Processor. That split "
                "only holds if a signed agreement says so."
            ),
            reference="DPDP s5, s6",
            remedy=(
                "Get a data processing agreement signed by every customer before "
                "they run a campaign, recording that they hold lawful consent for "
                "the numbers they upload."
            ),
        ),
        Check(
            key="published_policies",
            title="Privacy notice and policies published",
            status=NEEDS_A_HUMAN,
            detail=(
                "compliance/ holds fact sheets generated from this code, with "
                "every judgement marked [TO CONFIRM]. They are drafting input, "
                "not published policy."
            ),
            reference="DPDP s5",
            remedy="Have a lawyer turn compliance/PRIVACY-NOTICE-FACTS.md into a published notice at decibyl.ai.",
        ),
        Check(
            key="significant_data_fiduciary",
            title="Significant Data Fiduciary obligations assessed",
            status=NEEDS_A_HUMAN,
            detail=(
                "Above the notified thresholds the Act adds a Data Protection "
                "Officer resident in India, an independent data auditor and "
                "periodic impact assessments. Whether they apply is a question "
                "about volume and sensitivity that only you can answer."
            ),
            reference="DPDP s10",
            remedy="Assess against the notified thresholds; re-assess as volume grows.",
        ),
        Check(
            key="cross_border",
            title="Cross-border transfers reviewed",
            status=NEEDS_A_HUMAN,
            detail=(
                "The derived sub-processor list at /api/v1/privacy/subprocessors "
                "shows which providers a given account's data reaches; most are "
                "outside India. DPDP permits transfer except to restricted "
                "countries, and that list is set by notification."
            ),
            reference="DPDP s16",
            remedy="Check the current restricted-country notification against your active sub-processors.",
        ),
    ]


async def assess(session: AsyncSession, *, now: datetime | None = None) -> Readiness:
    """Every check, in the order someone should work through them."""
    checks: list[Check] = []
    checks.extend(_grievance_officer_checks())
    checks.extend(_configuration_checks())
    checks.extend(await _evidence_checks(session, now=now))
    checks.extend(_obligations_code_cannot_meet())
    return Readiness(checks=tuple(checks))
