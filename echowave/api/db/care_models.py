"""Tables for launch stream `care` (LAUNCH-PLAN.md, phase 2).

Care for older people and their families: medicine reminder calls with a
family alert when a dose is missed, scam checks, step-by-step tech help, and
a family circle the older person consents to. Kept out of ``models.py``
(launch convention, KAN-276); ``models.py`` imports this module at its end so
the tables are on ``Base.metadata`` for alembic and the tests. Simple mode is
a person's preference and lives on ``member_preferences`` (controls).

Every row carries ``organization_id`` -- the workspace of the older person,
whose things these are -- and every read is scoped by it. A family member
reads across that boundary only through an active ``care_circle_members``
row naming them, and only the kinds of thing that row shares.

See ``CARE.md`` at the repository's ``echowave/`` root for the design.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
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


class CareCircleModel(Base):
    """One older person's family circle, in their own workspace.

    Made the first time the person opens their circle. ``display_name`` is
    what the family call them ("Amma"); it is what alerts say, so a family
    member reads "Amma has not taken her 8:00 medicine" rather than an email.
    """

    __tablename__ = "care_circles"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    person_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    display_name = Column(String(60), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "person_user_id", name="uq_care_circle_person"
        ),
    )


class CareCircleMemberModel(Base):
    """A family member, and exactly what the older person shares with them.

    ``status``: ``proposed`` (a consent card is waiting) -> ``invited``
    (the person confirmed; a code exists) -> ``active`` (the family member
    accepted with that code, signed in as the invited email) ->
    ``revoked``. A declined card leaves ``declined``. Only ``active`` rows
    let anybody read anything, and only the kinds in ``shares``.

    ``invite_code_hash`` is a hash: the code itself is shown once, to the
    person who made it, and never stored.
    """

    __tablename__ = "care_circle_members"

    id = Column(Integer, primary_key=True)
    circle_id = Column(
        Integer, ForeignKey("care_circles.id", ondelete="CASCADE"), nullable=False
    )
    #: The circle's workspace, repeated so every query filters on it.
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: Set when the family member accepts; NULL until then.
    member_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    name = Column(String(60), nullable=False)
    #: The email the invitation is for. Accepting needs this address.
    email = Column(String(254), nullable=False)
    #: Kinds from services/care/circle.SHARES.
    shares = Column(JSON, nullable=False, default=list)
    #: Shares a waiting consent card would add (a widening is consented
    #: like an invitation); empty when nothing waits.
    pending_shares = Column(JSON, nullable=False, default=list)
    status = Column(String(16), nullable=False, default="proposed")
    invite_code_hash = Column(String(64), nullable=True)
    invite_expires_at = Column(DateTime(timezone=True), nullable=True)
    #: The action card that carried the person's consent.
    consent_event_id = Column(Integer, nullable=True)
    consented_at = Column(DateTime(timezone=True), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_care_circle_members_circle", "circle_id"),
        Index("ix_care_circle_members_member_user", "member_user_id"),
        Index(
            "uq_care_circle_members_code",
            "invite_code_hash",
            unique=True,
            postgresql_where=text("invite_code_hash IS NOT NULL"),
        ),
    )


class CareMedicineModel(Base):
    """A medicine to be reminded of by phone call, at the times given.

    Reminders only: ``label`` is the person's or family's own words for the
    medicine ("BP tablet after breakfast"); Decibyl reads it back and never
    adds, changes or suggests a dose.

    ``state``: ``awaiting_approval`` until the person confirms the card that
    names the number, times, language and who is told -> ``active`` ->
    ``paused``. A paused medicine places no calls; resuming is a new card.
    """

    __tablename__ = "care_medicines"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    person_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    label = Column(String(80), nullable=False)
    #: ``["08:00", "20:00"]``, local to ``timezone``.
    times = Column(JSON, nullable=False, default=list)
    timezone = Column(String(64), nullable=False)
    #: BCP 47, from member_preferences.LANGUAGES: the language of the call.
    language = Column(String(16), nullable=False)
    #: E.164, the phone the reminder rings.
    phone = Column(String(20), nullable=False)
    #: care_circle_members ids told when a dose is missed or not answered.
    alert_member_ids = Column(JSON, nullable=False, default=list)
    state = Column(String(24), nullable=False, default="awaiting_approval")
    card_event_id = Column(Integer, nullable=True)
    #: The card version the person confirmed; what is active is exactly that.
    approved_version = Column(String(32), nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_care_medicines_org_state", "organization_id", "state"),)


class CareDoseCallModel(Base):
    """One reminder call for one dose: due, placed, and what came of it.

    Unique per (medicine, due time), so two ticks of the scheduler -- or two
    workers -- make one row and one call. ``state``: ``calling`` ->
    ``taken`` | ``not_taken`` | ``not_answered`` | ``unclear``, or
    ``failed`` (with ``reason``) when the call could not be placed.
    ``alerted_at`` marks the family alert, sent at most once.
    """

    __tablename__ = "care_dose_calls"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    medicine_id = Column(
        Integer, ForeignKey("care_medicines.id", ondelete="CASCADE"), nullable=False
    )
    due_at = Column(DateTime(timezone=True), nullable=False)
    state = Column(String(16), nullable=False, default="calling")
    reason = Column(String(64), nullable=True)
    workflow_run_id = Column(Integer, nullable=True)
    #: Marked by the person in the app ("I took it"), not by a call.
    marked_by_user_id = Column(Integer, nullable=True)
    outcome_at = Column(DateTime(timezone=True), nullable=True)
    alerted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("medicine_id", "due_at", name="uq_care_dose_due"),
        Index("ix_care_dose_calls_state", "state", "created_at"),
    )


class CareAlertModel(Base):
    """Something a family member is told, because the person shared it.

    One row per recipient (``member_id``), written only for an active
    member whose shares include the alert's kind at the time it is written.
    The family view also re-checks the member is still active and still
    shares that kind when it reads, so revoking hides past alerts too.
    """

    __tablename__ = "care_alerts"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    circle_id = Column(
        Integer, ForeignKey("care_circles.id", ondelete="CASCADE"), nullable=False
    )
    member_id = Column(
        Integer,
        ForeignKey("care_circle_members.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: ``dose_missed`` | ``call_not_answered`` | ``call_failed`` |
    #: ``help_needed`` | ``scam_checked``.
    kind = Column(String(32), nullable=False)
    #: The share that allowed it (services/care/circle.SHARES).
    share = Column(String(32), nullable=False)
    #: One plain sentence, written at the time.
    title = Column(String(300), nullable=False)
    subject_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("member_id", "kind", "subject_id", name="uq_care_alert_once"),
        Index("ix_care_alerts_member", "member_id", "created_at"),
    )


class CareScamCheckModel(Base):
    """A scam check's answer, without the message.

    The words a person pasted are read once and not kept: only the verdict
    and the warning signs found (codes) are stored, so the history can say
    "a message that asked for an OTP" without holding the OTP.
    """

    __tablename__ = "care_scam_checks"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: ``message`` | ``call``.
    kind = Column(String(16), nullable=False)
    #: ``likely_scam`` | ``be_careful`` | ``no_signs_found``.
    verdict = Column(String(24), nullable=False)
    signals = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_care_scam_checks_user", "organization_id", "user_id", "created_at"),
    )


class CareHelpSessionModel(Base):
    """One run through a tech-help guide, one step at a time.

    ``step`` is the step on screen; ``state``: ``active`` -> ``done`` (it
    worked) | ``stuck`` (a step did not work and the alternatives ran out).
    ``version`` guards against a double tap answering the same step twice.
    """

    __tablename__ = "care_help_sessions"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    guide = Column(String(64), nullable=False)
    step = Column(Integer, nullable=False, default=0)
    #: How many "no" answers on the current step (the alternative shown).
    tries = Column(Integer, nullable=False, default=0)
    state = Column(String(16), nullable=False, default="active")
    version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_care_help_sessions_user", "organization_id", "user_id"),
    )
