"""Feature switches set from the staff console (ADMIN-1).

One row turns one feature on or off, either for one organisation or, with
``organization_id`` NULL, for everyone. It replaces editing
``FEATURE_ORG_OVERRIDES`` in ``.env`` and restarting: the env var is still
read, below the table, as a fallback (see ``services/features.py``).

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the table is on ``Base.metadata`` for alembic and
the tests.
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
    text,
)

from api.db.models import Base


class FeatureOverrideModel(Base):
    """One feature forced on or off, for one organisation or for everyone.

    ``enabled`` is a real boolean rather than "a row means on": a feature
    that is on globally can be held off for one organisation that is not
    ready for it.

    Uniqueness is two partial indexes rather than one constraint, because a
    plain ``UNIQUE (feature, organization_id)`` treats every NULL as distinct
    and would let two global rows for the same feature coexist.
    """

    __tablename__ = "feature_overrides"

    id = Column(Integer, primary_key=True)
    #: A key of ``services.features.FLAGS``; checked by the service on write.
    feature = Column(String(64), nullable=False)
    #: NULL means the global switch.
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
    )
    enabled = Column(Boolean, nullable=False)
    note = Column(String(300), nullable=True)
    #: After this the row is ignored, as if it had been deleted.
    expires_at = Column(DateTime(timezone=True), nullable=True)
    set_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("now()"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=text("now()"),
    )

    __table_args__ = (
        Index(
            "uq_feature_overrides_feature_org",
            "feature",
            "organization_id",
            unique=True,
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
        Index(
            "uq_feature_overrides_feature_global",
            "feature",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
        ),
        Index("ix_feature_overrides_organization_id", "organization_id"),
    )
