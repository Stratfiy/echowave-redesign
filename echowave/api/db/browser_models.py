"""Decibyl's private browser: sessions, saved logins and the staff site list.

Kept out of ``models.py`` (launch convention, KAN-276); ``models.py`` imports
this module at its end so the tables are on ``Base.metadata`` for alembic and
the tests.

Three tables, three owners:

- ``browser_sessions`` -- one task in one browser, owned by the **person**
  who asked (``user_id``) inside an organisation. Nobody else reads it, not
  even a colleague on the same thread: what a browser saw on somebody's bank
  page is theirs.
- ``browser_site_logins`` -- the cookies of one site for one person,
  Fernet-encrypted. Only cookies: never a password, never local storage.
  Deleting the row is the whole of "forget this login".
- ``browser_site_rules`` -- the staff's allow and deny list. Platform-wide,
  because a site whose terms forbid automation forbids it for everyone.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from api.db.models import Base


def _now() -> datetime:
    return datetime.now(UTC)


class BrowserSessionModel(Base):
    """One task in one isolated browser."""

    __tablename__ = "browser_sessions"

    id = Column(Integer, primary_key=True)
    #: Unguessable, and the only id the UI and the thread row carry.
    session_uuid = Column(String(36), nullable=False, unique=True, index=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Which of Decibyl's conversations it was asked in.
    thread_id = Column(String(64), nullable=True)
    #: The ``browser_session`` timeline row that shows the panel.
    event_id = Column(BigInteger, nullable=True)
    #: What the person asked for, in Decibyl's words for the browser.
    task = Column(Text, nullable=False)
    #: The person's own line, kept for the gate: what they asked to be done
    #: is read from their words, never from a page.
    request = Column(Text, nullable=False, default="")
    #: Hosts the task is about. When set, the browser stays on them.
    sites = Column(JSONB, nullable=False, default=list)
    #: Consequential verbs the person asked for (submit, pay, send, book,
    #: sign_up). Anything outside this is refused, not carded.
    allowed_verbs = Column(JSONB, nullable=False, default=list)
    #: ``starting`` | ``working`` | ``waiting_for_you`` | ``captcha`` |
    #: ``taken_over`` | ``done`` | ``failed`` | ``stopped`` | ``limit_reached``.
    state = Column(String(24), nullable=False, default="starting", index=True)
    #: One line on the state, for the panel ("Waiting for you to approve...").
    state_note = Column(String(500), nullable=True)
    #: {"steps": n, "minutes": n, "cost_paise": n}
    limits = Column(JSONB, nullable=False, default=dict)
    #: What it has used so far, same keys.
    used = Column(JSONB, nullable=False, default=dict)
    #: The step list the panel shows, oldest first.
    steps = Column(JSONB, nullable=False, default=list)
    #: The gate waiting on a person, if any: {gate_id, event_id, label}.
    pending = Column(JSONB, nullable=True)
    #: Sites the person said to keep signed in to, on hand back.
    keep_login_sites = Column(JSONB, nullable=False, default=list)
    #: The receipt: {summary, actions: [...], links: [...]} once it ends.
    receipt = Column(JSONB, nullable=True)
    #: The driver's handle for the box, while it runs.
    driver_handle = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class BrowserSiteLoginModel(Base):
    """The cookies of one site for one person, encrypted at rest."""

    __tablename__ = "browser_site_logins"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", "site", name="uq_browser_login_person_site"
        ),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The registrable host the cookies are for, e.g. ``bescom.co.in``.
    site = Column(String(255), nullable=False)
    #: Fernet token of the JSON list of cookies. Never plaintext: with no
    #: key configured, nothing is saved at all.
    cookies_encrypted = Column(LargeBinary, nullable=False)
    cookie_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
    last_used_at = Column(DateTime(timezone=True), nullable=True)


class BrowserSiteRuleModel(Base):
    """A staff decision about one site: allow, or deny with a reason."""

    __tablename__ = "browser_site_rules"

    id = Column(Integer, primary_key=True)
    #: A host; the rule covers its subdomains too.
    site = Column(String(255), nullable=False, unique=True)
    #: ``allow`` | ``deny``.
    rule = Column(String(8), nullable=False)
    reason = Column(String(500), nullable=False, default="")
    set_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
