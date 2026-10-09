"""Generated images and the reference images people attach (services/images/).

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the table is on ``Base.metadata``.

One row per image: a poster option the provider made, or a logo or product
photo a person attached for the provider to work from. The bytes live in the
workspace's object storage under ``images/<organization_id>/``; this row is
how they are found, and every read goes through ``services/images/store``
with ``organization_id`` in the query.

It is also the meter. Each generated image carries what its vendor charges
for it (``vendor_cost_paise``, from the rate book) beside what the customer
was charged (``charged_paise``, zero while image generation is free to try),
and the usage the vendor reported, so a price can be set from real numbers.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class GeneratedImageModel(Base):
    """One image in a workspace: made by a provider, or attached by a person."""

    __tablename__ = "generated_images"

    id = Column(Integer, primary_key=True, index=True)
    #: The id the thread, the card and the model use: ``img_`` and 32 hex.
    image_uuid = Column(String(40), nullable=False, unique=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: The agent that made it, when an agent did; empty for Decibyl's thread.
    workflow_id = Column(
        Integer, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True
    )
    #: ``generated`` or ``reference``.
    kind = Column(String(16), nullable=False, default="generated")
    provider = Column(String(32), nullable=False, default="", server_default="")
    model = Column(String(128), nullable=False, default="", server_default="")
    #: ``byok`` (the workspace's key) or ``platform`` (Decibyl's key).
    key_source = Column(String(16), nullable=False, default="", server_default="")
    #: The format asked for: ``instagram_square``, ``a4_poster`` ...
    format = Column(String(32), nullable=False, default="", server_default="")
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    mime_type = Column(String(32), nullable=False, default="image/png")
    size_bytes = Column(Integer, nullable=False, default=0, server_default="0")
    filename = Column(String(255), nullable=True)
    storage_key = Column(String(512), nullable=False)
    storage_backend = Column(String(16), nullable=False)
    #: One request's options share this, so the grid is one card.
    request_id = Column(String(40), nullable=True)
    option_index = Column(Integer, nullable=False, default=0, server_default="0")
    #: The image this one edits, when it is an edit.
    parent_uuid = Column(String(40), nullable=True)
    #: The structured brief the image was made from (business, headline,
    #: lines, language, format, look), so an edit is grounded in what the
    #: person already approved and nothing else.
    spec = Column(JSON, nullable=True)
    #: The prompt the provider was sent, composed by us from ``spec``.
    prompt = Column(Text, nullable=True)
    usage = Column(JSON, nullable=True)
    vendor_cost_paise = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    charged_paise = Column(BigInteger, nullable=False, default=0, server_default="0")
    #: ``rate_card`` (a row on file), ``default_book`` (the file's figure) or
    #: ``none`` -- said, never left to look like a cost of zero.
    cost_source = Column(String(16), nullable=False, default="", server_default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_generated_images_org_created", "organization_id", "created_at"),
        Index("ix_generated_images_org_request", "organization_id", "request_id"),
    )
