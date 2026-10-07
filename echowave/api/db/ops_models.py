"""Tables for stream ``ops`` (handoff 34, 35, 15 H).

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the tables are on ``Base.metadata`` for alembic and
the tests.

Three records, each the authoritative copy of something monitoring tools only
help investigate:

* ``ops_commands`` -- every routine operation a staff member asked for: the
  typed command, role, environment, target, reason, approval, idempotency key
  and result. Accepted is queued, never succeeded.
* ``platform_credential_rotations`` -- one provider key's journey from staged
  to revoked. Ciphertext only; the plaintext never comes back out.
* ``ops_evidence`` -- proof an operation happened: a restore drill, a
  capacity review, a backup, a deployment. Read by the console's "last
  restore test" and "last capacity review" rows.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class OpsCommandModel(Base):
    """One request to run an allowlisted operation (handoff 34).

    ``state`` moves requested -> awaiting_approval -> queued -> running ->
    succeeded | failed, or to rejected / expired / needs_setup. A command
    whose definition needs no second person goes straight to queued.
    """

    __tablename__ = "ops_commands"

    id = Column(Integer, primary_key=True)
    command = Column(String(64), nullable=False, index=True)
    environment = Column(String(32), nullable=False)
    #: The validated target, as the command's schema produced it.
    target = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    reason = Column(String(500), nullable=False)
    #: Unique: the same key asked twice returns the first request.
    idempotency_key = Column(String(128), nullable=False, unique=True)
    state = Column(String(24), nullable=False, index=True)
    requested_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    requested_role = Column(String(32), nullable=True)
    approval_required = Column(Boolean, nullable=False, server_default=text("false"))
    approved_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at = Column(DateTime(timezone=True), nullable=True)
    #: What the impact preview said when the command was requested.
    preview = Column(JSONB, nullable=True)
    #: The handler's result, or the normalized failure.
    result = Column(JSONB, nullable=True)
    reason_code = Column(String(64), nullable=True)
    #: After this an unapproved request expires; a pause lifts itself.
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_ops_commands_created", "created_at"),)


class PlatformCredentialRotationModel(Base):
    """A provider key moving through stage, validate, activate, refresh,
    verify and revoke (handoff 34, "Secret lifecycle").

    The staged key and the previous key are held only as ciphertext, under
    the same secret as ``platform_provider_credentials``. Reverting is
    possible exactly while ``previous_encrypted_key`` is set; revoking clears
    it.
    """

    __tablename__ = "platform_credential_rotations"

    id = Column(Integer, primary_key=True)
    component = Column(String(16), nullable=False)
    provider = Column(String(64), nullable=False)
    environment = Column(String(32), nullable=False)
    state = Column(String(24), nullable=False, index=True)
    staged_encrypted_key = Column(Text, nullable=True)
    staged_last_four = Column(String(8), nullable=False)
    previous_encrypted_key = Column(Text, nullable=True)
    previous_last_four = Column(String(8), nullable=True)
    label = Column(String(128), nullable=True)
    reason = Column(String(500), nullable=False)
    #: Normalized reason code of the last failed step, never vendor prose
    #: containing request data.
    reason_code = Column(String(64), nullable=True)
    staged_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    staged_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    validated_at = Column(DateTime(timezone=True), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    consumers_refreshed_at = Column(DateTime(timezone=True), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    reverted_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_credential_rotations_slot", "component", "provider"),)


class OpsEvidenceModel(Base):
    """Evidence that an operation ran, and how it went (handoff 15 H, 34)."""

    __tablename__ = "ops_evidence"

    id = Column(Integer, primary_key=True)
    #: restore_drill, capacity_review, backup, deployment, laya_evaluation.
    kind = Column(String(32), nullable=False, index=True)
    environment = Column(String(32), nullable=False)
    #: passed, failed or partial.
    outcome = Column(String(16), nullable=False)
    summary = Column(String(500), nullable=False)
    #: Numbers only: durations, counts, percentiles. Never data rows.
    metrics = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    #: Where the full report lives (an artifact, a run URL), if anywhere.
    link = Column(String(500), nullable=True)
    recorded_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_ops_evidence_kind_time", "kind", "occurred_at"),)
