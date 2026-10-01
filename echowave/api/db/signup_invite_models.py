"""Invite codes for invite-only signup (INVITE-1, KAN-273).

Kept out of ``models.py`` so the launch branches do not all append to the
same 6,800-line file; ``models.py`` imports this module at its end so the
tables are on ``Base.metadata`` for alembic and the tests.
"""

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String

from api.db.models import Base


class SignupInviteModel(Base):
    """One invite code. A code admits ``max_uses`` new accounts, one by
    default. ``email`` pins it to one address when set. Minted by staff
    from ``/superuser/invites``; never needed for an existing account."""

    __tablename__ = "signup_invites"

    id = Column(Integer, primary_key=True)
    #: Stored normalised: upper case, no spaces or dashes.
    code = Column(String(32), nullable=False, unique=True, index=True)
    #: Lower-cased; NULL means any address may redeem it.
    email = Column(String(320), nullable=True)
    max_uses = Column(Integer, nullable=False, default=1)
    uses = Column(Integer, nullable=False, default=0)
    note = Column(String(200), nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("max_uses >= 1", name="ck_signup_invites_max_uses"),
        CheckConstraint(
            "uses >= 0 AND uses <= max_uses", name="ck_signup_invites_uses"
        ),
    )


class SignupInviteRedemptionModel(Base):
    """Who came in on which code, and when."""

    __tablename__ = "signup_invite_redemptions"

    id = Column(Integer, primary_key=True)
    invite_id = Column(
        Integer,
        ForeignKey("signup_invites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email = Column(String(320), nullable=False)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    #: ``password`` or ``google``.
    door = Column(String(16), nullable=False)
    redeemed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
