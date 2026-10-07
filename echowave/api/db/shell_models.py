"""Tables for launch stream `shell` (screens 01 and 02).

Kept out of ``models.py`` for the same reason as ``signup_invite_models``:
the launch branches must not all append to one file. ``models.py`` imports
this module at its end so the tables are on ``Base.metadata``.
"""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, func

from api.db.models import Base


class WaitlistRequestModel(Base):
    """One person asking for early access (screen 01).

    One row per address, whatever the number of submissions: the unique
    index on the lower-cased email is what makes "submitting twice creates
    one request" true under a double click or two tabs, not the button.
    """

    __tablename__ = "waitlist_requests"

    id = Column(Integer, primary_key=True)
    #: Stored lower-cased and trimmed.
    email = Column(String(320), nullable=False)
    #: A language code from ``services/shell/languages.py``.
    language = Column(String(16), nullable=False, default="en")
    #: What they would ask first. Optional, and never sent to analytics.
    first_task = Column(Text, nullable=True)
    phone = Column(String(32), nullable=True)
    occupation = Column(String(120), nullable=True)
    #: ``waitlist`` for a new request, ``renewal`` for "Request a new
    #: invitation" from an expired or revoked link.
    source = Column(String(16), nullable=False, default="waitlist")
    #: ``waitlisted`` until staff invite them; ``invited`` after.
    status = Column(String(16), nullable=False, default="waitlisted")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        Index("ux_waitlist_requests_email", func.lower(email), unique=True),
    )


class UserOnboardingModel(Base):
    """One person's answers at the door (screen 02): language, the timezone
    they confirmed, and when they finished. Owned by the person, never the
    workspace: saving it changes no organisation default.

    The `controls` stream owns the full member preference model; this row is
    the narrow slice onboarding needs, and is meant to be read into it.
    """

    __tablename__ = "user_onboarding"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    language = Column(String(16), nullable=True)
    #: An IANA name, only ever written after the person confirmed it.
    timezone = Column(String(64), nullable=True)
    timezone_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    preferred_name = Column(String(80), nullable=True)
    #: Set when they start their first task or skip; the landing reads it.
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
