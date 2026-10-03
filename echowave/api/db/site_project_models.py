"""Studio sites: the web apps an organisation builds from the Studio chat.

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the table is on ``Base.metadata`` for alembic and
the tests.

The source tree and the last successful build live on the row, as JSON maps
of path to content. A site is a few hundred kilobytes of source and a few
hundred more of built output, which is well within what a row holds, and one
row means a site is created, edited, built and deleted transactionally with
nothing to reap in object storage. :mod:`api.services.studio.sites` enforces
the size ceilings that keep that true.
"""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from api.db.models import Base


class SiteProjectModel(Base):
    """One site. ``files`` is the source; ``dist`` is the last good build."""

    __tablename__ = "site_projects"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name = Column(String(120), nullable=False)
    #: ``vite-react`` -- see ``services/studio/scaffold.py``.
    framework = Column(String(32), nullable=False)
    #: Relative path -> UTF-8 text.
    files = Column(JSONB, nullable=False, default=dict)
    #: The agents this site carries, as workflow ids, in the order shown.
    agent_workflow_ids = Column(JSONB, nullable=False, default=list)
    #: Unguessable; the preview URL is the only thing it unlocks.
    preview_token = Column(String(64), nullable=False, unique=True, index=True)
    #: ``none`` | ``building`` | ``succeeded`` | ``failed``.
    build_status = Column(String(16), nullable=False, default="none")
    build_log = Column(Text, nullable=True)
    build_started_at = Column(DateTime(timezone=True), nullable=True)
    built_at = Column(DateTime(timezone=True), nullable=True)
    build_seconds = Column(Float, nullable=True)
    #: Relative path -> base64 of the built file. Kept from the last build
    #: that succeeded, so a failed rebuild never takes the preview down.
    dist = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
