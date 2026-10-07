"""Tables for launch stream `staff` (LAUNCH-PLAN.md, phase 2; STAFF.md).

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the tables are on ``Base.metadata`` for alembic and
the tests. The one existing table this stream extends -- ``users`` (a staff
suspension) -- keeps its new column in ``models.py``.

Nothing here holds customer content. An evaluation case stores a sanitized
input written for testing; a refund stores amounts and provider references;
an incident stores what staff wrote about the platform.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class StaffRoleGrantModel(Base):
    """A console role held by a staff member, beyond their tier.

    The tier (``users.staff_role``) still decides who is staff at all; these
    rows say which console capabilities a staff member has (operations,
    finance, quality, support). An owner grants and revokes them through an
    approved command; a revoked row stays as history.
    """

    __tablename__ = "staff_role_grants"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role = Column(String(16), nullable=False)
    reason = Column(String(500), nullable=False)
    granted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    command_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index(
            "uq_staff_role_grants_live",
            "user_id",
            "role",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )


class StaffCommandModel(Base):
    """One typed staff command and everything that happened to it.

    The design's operational action contract: typed command, the roles it was
    requested under, environment, target, reason, approval, idempotency key
    and result. The same life as the ops stream's ``ops_commands``
    (requested, awaiting approval, queued, running, succeeded, failed,
    rejected, expired, outcome unknown) for the commands the staff console
    owns: roles, invitations, suspensions, refunds, incidents, evaluations.
    """

    __tablename__ = "staff_commands"

    id = Column(Integer, primary_key=True)
    command = Column(String(48), nullable=False, index=True)
    environment = Column(String(32), nullable=False)
    target = Column(JSON, nullable=False, default=dict)
    reason = Column(String(500), nullable=False)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    state = Column(String(24), nullable=False, index=True)
    requested_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    requested_roles = Column(JSON, nullable=False, default=list)
    approval_required = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    preview = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    reason_code = Column(String(64), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class StaffRefundModel(Base):
    """A refund of one payment, from request to reconciled provider status.

    One row per refund command (unique), so a duplicate submission is the
    same refund. ``provider_refund_id`` is set only by the provider's answer;
    ``refunded`` only after the provider's status was read back.
    """

    __tablename__ = "staff_refunds"

    id = Column(Integer, primary_key=True)
    payment_id = Column(
        Integer,
        ForeignKey("payments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id = Column(Integer, nullable=False, index=True)
    amount_minor = Column(BigInteger, nullable=False)
    currency = Column(String(3), nullable=False)
    #: requested | pending | refunded | failed | outcome_unknown
    state = Column(String(24), nullable=False)
    provider = Column(String(32), nullable=False)
    provider_refund_id = Column(String(64), nullable=True)
    command_id = Column(Integer, nullable=False, unique=True)
    reason_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    reconciled_at = Column(DateTime(timezone=True), nullable=True)


class StaffIncidentModel(Base):
    """An incident: impact, owner, state, and links to what it touches."""

    __tablename__ = "staff_incidents"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    impact = Column(String(500), nullable=False)
    #: sev1 | sev2 | sev3
    severity = Column(String(8), nullable=False)
    #: investigating | mitigating | waiting_approval | running |
    #: verification_failed | resolved
    state = Column(String(24), nullable=False, index=True)
    environment = Column(String(32), nullable=False)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    #: {"release": "...", "providers": [...], "support_cases": [...],
    #:  "ops_commands": [...]}
    links = Column(JSON, nullable=False, default=dict)
    opened_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
    opened_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class StaffIncidentStepModel(Base):
    """One step of an incident's runbook, in order, never edited."""

    __tablename__ = "staff_incident_steps"

    id = Column(Integer, primary_key=True)
    incident_id = Column(
        Integer,
        ForeignKey("staff_incidents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence = Column(Integer, nullable=False)
    #: note | preflight | approval | execution | verification | state
    kind = Column(String(16), nullable=False)
    summary = Column(String(500), nullable=False)
    #: passed | failed | pending | unknown, or NULL for a note
    outcome = Column(String(16), nullable=True)
    ops_command_id = Column(Integer, nullable=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("incident_id", "sequence", name="uq_incident_step_seq"),
    )


class QualityEvalCaseModel(Base):
    """One version of one evaluation case. Editing makes a new version."""

    __tablename__ = "quality_eval_cases"

    id = Column(Integer, primary_key=True)
    dataset = Column(String(64), nullable=False, index=True)
    case_key = Column(String(64), nullable=False)
    version = Column(Integer, nullable=False)
    #: Sanitized: written for testing, or scrubbed before it was stored.
    input = Column(JSON, nullable=False)
    expected = Column(JSON, nullable=False)
    #: {"language": "hi", "kind": "approval", "noise": "quiet", ...}
    subgroup = Column(JSON, nullable=False, default=dict)
    #: draft | approved | retired
    review_state = Column(String(16), nullable=False)
    #: authored | support_case
    source = Column(String(16), nullable=False)
    source_ref = Column(String(64), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "dataset", "case_key", "version", name="uq_quality_eval_case_version"
        ),
    )


class QualityEvalRunModel(Base):
    """A run of a fixed case set against one configuration snapshot."""

    __tablename__ = "quality_eval_runs"

    id = Column(Integer, primary_key=True)
    dataset = Column(String(64), nullable=False, index=True)
    #: A hash of the case ids and versions in the set.
    dataset_version = Column(String(16), nullable=False)
    case_ids = Column(JSON, nullable=False)
    config = Column(JSON, nullable=False)
    config_version = Column(String(16), nullable=False)
    runner = Column(String(32), nullable=False)
    baseline_run_id = Column(Integer, nullable=True)
    #: queued | running | partial | cancelled | passed | regression |
    #: insufficient_sample | failed
    state = Column(String(24), nullable=False, index=True)
    totals = Column(JSON, nullable=True)
    command_id = Column(Integer, nullable=True)
    requested_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class QualityEvalResultModel(Base):
    """One case's result in one run. Never overwritten by a re-run."""

    __tablename__ = "quality_eval_results"

    id = Column(Integer, primary_key=True)
    run_id = Column(
        Integer,
        ForeignKey("quality_eval_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    case_id = Column(Integer, ForeignKey("quality_eval_cases.id"), nullable=False)
    #: passed | failed | unknown
    outcome = Column(String(16), nullable=False)
    #: Deterministic checks: [{"name", "passed", "detail"}].
    checks = Column(JSON, nullable=False, default=list)
    #: Model-judge commentary, kept apart; never decides the outcome.
    judge = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    cost_paise = Column(Integer, nullable=True)
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("run_id", "case_id", name="uq_quality_eval_result"),
    )
