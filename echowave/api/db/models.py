import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    func,
    text,
)
from sqlalchemy.orm import declarative_base, relationship

from api.constants import DEFAULT_CAMPAIGN_RETRY_CONFIG

from ..enums import (
    CallType,
    IntegrationAction,
    OrganizationRole,
    PartnerApplicationStatus,
    PartnerStatementStatus,
    ToolCategory,
    ToolStatus,
    TriggerState,
    WebhookCredentialType,
    WorkflowRunState,
    WorkflowStatus,
)

Base = declarative_base()


# TODO: remove workflow_defintion after migration, remove nullable workflow_defintion_id from Workflow and Workflowrun


class UserModel(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    workflows = relationship("WorkflowModel", back_populates="user")
    selected_organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=True
    )
    selected_organization = relationship("OrganizationModel")
    memberships = relationship(
        "OrganizationMembershipModel",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    # Decibyl-staff privilege tier: None | "support" | "superadmin". See
    # StaffRole in api/enums.py. Nullable rather than a Postgres ENUM so a
    # future tier needs no migration — same convention as account_type below.
    staff_role = Column(String(16), nullable=True)
    email = Column(String, nullable=True)
    password_hash = Column(String, nullable=True)

    # Second factor. The secret is stored Fernet-encrypted, never in the clear:
    # a readable TOTP secret is password-equivalent, since anyone holding it can
    # mint valid codes indefinitely.
    #: When this address was proved. The permanent fact lives on the user; the
    #: transient challenge lives in email_verification_challenges, because a
    #: code with an expiry and an attempt counter is not a property of a person.
    #:
    #: NULL means unproved, including for every account that existed before
    #: this shipped — which is why nothing hard-gates on it yet. Locking out
    #: existing customers to enforce a rule introduced after they signed up is
    #: not a security improvement, it is an outage.
    email_verified_at = Column(DateTime(timezone=True), nullable=True)

    mfa_secret_encrypted = Column(String, nullable=True)
    mfa_enabled = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # The last accepted TOTP counter. Without it a code stays usable for its
    # whole 30-second step and can be replayed by anyone who saw it.
    mfa_last_counter = Column(BigInteger, nullable=True)
    # SHA-256 of each unused recovery code. Hashed rather than encrypted because
    # a reversible store of ten spare passwords is worse than the passwords.
    mfa_recovery_hashes = Column(JSON, nullable=True)

    __table_args__ = (
        Index(
            "ix_users_email_lower",
            func.lower(email),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
    )


class UserConfigurationModel(Base):
    """Per-user keyed JSON store, mirroring organization_configurations.

    Keys are defined in UserConfigurationKey. The legacy v1 AI model
    configuration lives under MODEL_CONFIGURATION; last_validated_at is only
    meaningful for that key.
    """

    __tablename__ = "user_configurations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    key = Column(String, nullable=False)
    configuration = Column(JSON, nullable=False, default=dict)
    last_validated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="_user_configuration_key_uc"),
    )


# New Organization model
class OrganizationModel(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Deprecated: MPS owns quota and credit ledger state.
    quota_type = Column(
        Enum("monthly", "annual", name="quota_type"),
        nullable=False,
        default="monthly",
        server_default=text("'monthly'::quota_type"),
        comment="Deprecated. MPS owns quota and credit ledger state.",
        info={"deprecated": True},
    )
    quota_decibyl_tokens = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="Deprecated. MPS owns quota and credit ledger state.",
        info={"deprecated": True},
    )
    quota_reset_day = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="Deprecated. MPS owns quota and credit ledger state.",
        info={"deprecated": True},
    )
    quota_start_date = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Deprecated. MPS owns quota and credit ledger state.",
        info={"deprecated": True},
    )
    quota_enabled = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Deprecated. MPS owns quota and credit ledger state.",
        info={"deprecated": True},
    )

    price_per_second_usd = Column(Float, nullable=True)

    # ------------------------------------------------------------------
    # Decibyl billing (see api/services/billing and DASHBOARD.md)
    # ------------------------------------------------------------------
    # Explicit per-account platform rate. NULL means "no account override" and
    # the resolver falls through to the volume tier, then the global default.
    # This column is the *current* value for convenience; the effective-dated
    # history in organization_rate_history is the source of truth for
    # recomputing any historical invoice.
    platform_rate_mpaise = Column(Integer, nullable=True)
    billing_currency = Column(
        String(3), nullable=False, default="INR", server_default=text("'INR'")
    )
    # Commercial shape, used for dashboard segmentation. Stored as VARCHAR
    # rather than a Postgres ENUM so new account types need no migration —
    # same convention as workflow_runs.mode. Values: see AccountType.
    account_type = Column(String(32), nullable=True)
    billing_name = Column(String, nullable=True)
    billing_status = Column(
        String(16), nullable=False, default="active", server_default=text("'active'")
    )

    # ------------------------------------------------------------------
    # Referral attribution (see api/services/partners)
    # ------------------------------------------------------------------
    #: This account's own code, as a partner. Minted when an application is
    #: approved and never reissued — it goes in links and email signatures, and
    #: a code that changes silently stops attributing the traffic it was given
    #: to. NULL on every account that is not a partner.
    referral_code = Column(String(16), unique=True, nullable=True, index=True)
    #: The partner this account arrived through. Written **once**, at
    #: provisioning, and never updated.
    #:
    #: Never updated is the whole design. Attribution that can be changed later
    #: is attribution somebody can argue about after the money is calculated,
    #: and the argument always happens at the end of a month. An account that
    #: signed up without a code was not referred, and adding one afterwards
    #: would retroactively create earnings on spend that nobody introduced.
    #:
    #: SET NULL rather than CASCADE: deleting a partner must not delete their
    #: referred customers, who are ordinary accounts with their own balance.
    referred_by_organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    referred_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    memberships = relationship(
        "OrganizationMembershipModel",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    integrations = relationship("IntegrationModel", back_populates="organization")
    usage_cycles = relationship(
        "OrganizationUsageCycleModel", back_populates="organization"
    )
    configurations = relationship(
        "OrganizationConfigurationModel", back_populates="organization"
    )
    api_keys = relationship("APIKeyModel", back_populates="organization")


class OrganizationMembershipModel(Base):
    """A user's standing within one organization.

    Replaces the old roleless ``organization_users`` many-to-many table.
    Every row that existed before this model was introduced was backfilled
    as OWNER — before this, every member had identical access, so that is
    the only backfill that doesn't silently take access away from someone on
    upgrade. New memberships default to MEMBER (see ``add_user_to_organization``
    in api/db/organization_client.py for where that default is applied).
    """

    __tablename__ = "organization_memberships"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # See OrganizationRole in api/enums.py. VARCHAR rather than a Postgres
    # ENUM for the same reason as account_type: a future role needs no
    # migration.
    role = Column(
        String(16),
        nullable=False,
        default=OrganizationRole.MEMBER.value,
        server_default=text(f"'{OrganizationRole.MEMBER.value}'"),
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    user = relationship("UserModel", back_populates="memberships")
    organization = relationship("OrganizationModel", back_populates="memberships")

    __table_args__ = (
        UniqueConstraint(
            "user_id", "organization_id", name="_organization_membership_uc"
        ),
    )


class APIKeyModel(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String, nullable=False)
    key_hash = Column(String, nullable=False, unique=True, index=True)
    key_prefix = Column(String, nullable=False)  # Store first 8 chars for display
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    archived_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    organization = relationship("OrganizationModel", back_populates="api_keys")
    created_by_user = relationship("UserModel")

    # Indexes for performance
    __table_args__ = (
        Index("ix_api_keys_organization_id", "organization_id"),
        # No ix_api_keys_key_hash here: the column above already declares
        # `unique=True, index=True`, which creates an index of exactly that
        # name — unique. Declaring it a second time as non-unique made the
        # model contradict itself, and autogenerate proposed dropping the
        # unique index and recreating it without the constraint. On a table
        # whose whole purpose is looking a key up by hash, losing that
        # uniqueness would let two API keys collide.
        Index("ix_api_keys_active", "is_active"),
    )


class OrganizationConfigurationModel(Base):
    __tablename__ = "organization_configurations"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    key = Column(String, nullable=False)
    value = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    last_validated_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    organization = relationship("OrganizationModel", back_populates="configurations")

    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint("organization_id", "key", name="_organization_key_uc"),
        Index("ix_organization_configurations_organization_id", "organization_id"),
    )


class TelephonyConfigurationModel(Base):
    __tablename__ = "telephony_configurations"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(64), nullable=False)
    provider = Column(String(32), nullable=False)
    credentials = Column(JSON, nullable=False, default=dict)
    is_default_outbound = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # True when the numbers behind this config sit under Decibyl's carrier
    # account rather than credentials the customer supplied. Only these are
    # gated on our KYC flow: a bring-your-own configuration is already verified
    # in the customer's own name with their own carrier, and blocking it on a
    # verification we run would be wrong. See services/kyc/service.py.
    is_platform_managed = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    organization = relationship("OrganizationModel")
    phone_numbers = relationship(
        "TelephonyPhoneNumberModel",
        back_populates="configuration",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "name", name="uq_telephony_configurations_org_name"
        ),
        Index("ix_telephony_configurations_org", "organization_id"),
        Index(
            "uq_telephony_configurations_default",
            "organization_id",
            unique=True,
            postgresql_where=text("is_default_outbound = true"),
        ),
    )


class ContactListModel(Base):
    """A named set of callers an inbound number can be matched against.

    Deliberately its own table rather than a view over campaign CSVs. A
    campaign's contacts are a file in object storage keyed by ``source_id``:
    fine for reading top to bottom while dialling out, useless for the inbound
    question, which is a point lookup on a ringing phone. It is also the wrong
    lifetime — a campaign ends and its file is a historical artifact, while an
    inbound list is a standing description of who this number serves.
    """

    __tablename__ = "contact_lists"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    contacts = relationship(
        "ContactModel", back_populates="contact_list", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_contact_lists_org_name"),
    )


class ContactModel(Base):
    """One caller, and whatever the account knows about them.

    ``attributes`` is open on purpose. What an account wants in front of an
    agent is theirs — a policy number, a due date, the name of the branch —
    and enumerating those columns would mean a migration per customer. It is
    preloaded into the run's ``initial_context``, where prompt templates
    already read from.

    Matching is on ``phone_normalized``: the same canonical form
    ``telephony_phone_numbers`` stores, produced by the same normalizer, so a
    number written ``+91 98765 43210`` in a CSV matches the digits a carrier
    actually sends.
    """

    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    contact_list_id = Column(
        Integer, ForeignKey("contact_lists.id", ondelete="CASCADE"), nullable=False
    )
    #: As the account supplied it, kept so a list reads back the way it was
    #: uploaded rather than in our canonical form.
    phone_raw = Column(String(255), nullable=False)
    phone_normalized = Column(String(255), nullable=False)
    name = Column(String(255), nullable=True)
    attributes = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    contact_list = relationship("ContactListModel", back_populates="contacts")

    __table_args__ = (
        UniqueConstraint(
            "contact_list_id",
            "phone_normalized",
            name="uq_contacts_list_phone",
        ),
        # The inbound lookup: one list, one number, on a ringing phone.
        Index("ix_contacts_lookup", "contact_list_id", "phone_normalized"),
        Index("ix_contacts_org", "organization_id"),
    )


class MissedCallEventModel(Base):
    """One ring on a callback-mode number, and what we did about it.

    Exists because the interesting half of this feature is invisible without
    it. A callback that connects becomes an ordinary workflow run and shows up
    wherever calls show up. A callback we *refused* — cooldown, daily cap, loop
    guard, closed calling window — leaves no run at all, so without this row
    the operator sees a quiet dashboard and cannot tell whether the number on
    their hoarding is working, whether nobody is ringing it, or whether we are
    silently declining every caller.

    Written for every ring, before the decision is known, so a crash between
    the ring and the callback leaves evidence rather than nothing.
    """

    __tablename__ = "missed_call_events"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    telephony_phone_number_id = Column(
        Integer,
        ForeignKey("telephony_phone_numbers.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The caller, in the same normalised form the DND list and the loop guard
    #: compare on. Storing the display form instead would mean the row that
    #: records a refusal cannot be matched against the rule that caused it.
    caller = Column(String(32), nullable=False)
    provider = Column(String(32), nullable=True)
    #: pending | called_back | refused | failed. Free-form rather than an enum
    #: because the refusal reasons will grow and an ALTER TYPE migration for
    #: each one buys nothing here.
    outcome = Column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    #: Why we did not call back, in words an operator can act on. NULL when we
    #: did.
    refusal_reason = Column(Text, nullable=True)
    #: The callback, once it exists. SET NULL rather than CASCADE: retention
    #: purges call data long before the operator stops caring how many people
    #: rang their hoarding.
    workflow_run_id = Column(
        Integer, ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True
    )
    received_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # The dashboard query: this org's events, newest first.
        Index("ix_missed_call_events_org_received", "organization_id", "received_at"),
    )


class TelephonyPhoneNumberModel(Base):
    __tablename__ = "telephony_phone_numbers"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    telephony_configuration_id = Column(
        Integer,
        ForeignKey("telephony_configurations.id", ondelete="CASCADE"),
        nullable=False,
    )
    address = Column(String(255), nullable=False)
    address_normalized = Column(String(255), nullable=False)
    address_type = Column(String(16), nullable=False)
    country_code = Column(String(2), nullable=True)
    label = Column(String(64), nullable=True)
    inbound_workflow_id = Column(
        Integer,
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: The agent that rings the caller back, when this number answers by
    #: calling back rather than by answering.
    #:
    #: A missed call is the cheapest thing an Indian customer can send: no
    #: data, no app, no form, no literacy in the form's language. A number in
    #: callback mode is never answered — the inbound leg is rejected before any
    #: media is set up, so neither side pays for it — and this agent places an
    #: outbound call to whoever rang.
    #:
    #: Distinct from `inbound_workflow_id` on purpose, and `inbound` wins when
    #: both are set — a number that can be answered is answered. Callback mode
    #: is a property of the number rather than of the agent because it is the
    #: number printed on the hoarding that decides which behaviour a caller
    #: gets, and the same agent may serve an answered line elsewhere.
    callback_workflow_id = Column(
        Integer,
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Decibyl's own number, lent to every account as an outbound caller ID.
    #:
    #: This is the shape Twilio uses for the same job — one shared number
    #: (+14157234000) places every caller-ID verification call it makes, and it
    #: never becomes a customer's number. A trial account can therefore place a
    #: real call without buying a number first, which is the thing that gates
    #: anyone evaluating the product.
    #:
    #: **Outbound only, and that is enforced rather than intended.** Inbound
    #: dispatch resolves an organization from `(provider, account_id, called
    #: number)` with no organization in the key, so a shared number that could
    #: be dialled *in* would hand one customer's caller to whichever tenant the
    #: database returned first. Both inbound lookups exclude these rows, and so
    #: does the routing-conflict check, because a number nobody can dial in to
    #: cannot conflict with anything.
    #:
    #: Also never rented to a customer: we pay this carrier rent ourselves.
    is_shared_outbound = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_active = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # active | suspended | released — see PhoneNumberStatus. Distinct from
    # is_active, which is the customer's own switch: a number can be switched
    # on by its owner and still suspended by us for non-payment.
    #
    # A released number keeps its row. The row is the only record that we ever
    # held the number, and the number itself may be printed on the customer's
    # signage — deleting it is how an orphaned carrier rental becomes
    # untraceable.
    status = Column(
        String(16), nullable=False, default="active", server_default="active"
    )
    # Set when we bought this number on the customer's behalf. NULL for a
    # number on the customer's own carrier account, which we neither bought nor
    # may release.
    carrier_number_id = Column(String(64), nullable=True)
    provisioned_at = Column(DateTime(timezone=True), nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)
    released_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    release_reason = Column(Text, nullable=True)
    is_default_caller_id = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # ---- Inbound controls -------------------------------------------------
    #
    # These live on the number rather than on the workflow because the number
    # is what a stranger dials. One agent may answer on several numbers — a
    # published support line and a number given only to existing customers —
    # and those two want different rules about who gets through.

    #: Callers to match an incoming call against, and whose stored attributes
    #: are preloaded into the run before the agent speaks. NULL means take
    #: every caller as an unknown one.
    inbound_contact_list_id = Column(
        Integer,
        ForeignKey("contact_lists.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Refuse a caller who is not in that list. Off by default: a number that
    #: silently stops answering strangers is a support ticket, so turning a
    #: published line into a private one has to be a deliberate act.
    inbound_require_known_caller = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: How many calls one caller may place to this number inside the window
    #: below. NULL is unlimited, which is the default — a limit nobody asked
    #: for is a dropped call from a customer who redialled after a bad line.
    inbound_max_calls_per_caller = Column(Integer, nullable=True)
    #: The window that limit is counted over. A lifetime cap locks out a
    #: legitimate repeat caller for good, and the person who would notice is
    #: the caller, who cannot tell us.
    inbound_call_window_hours = Column(
        Integer, nullable=False, default=24, server_default=text("24")
    )
    #: Normalized callers exempt from the limit. The escape hatch for the
    #: office line or a monitoring service that legitimately calls all day.
    inbound_allow_list = Column(
        JSON, nullable=False, default=list, server_default=text("'[]'::json")
    )

    extra_metadata = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    configuration = relationship(
        "TelephonyConfigurationModel", back_populates="phone_numbers"
    )
    # `foreign_keys` is required, not decorative: this table now has two
    # foreign keys to `workflows` (inbound and callback), and SQLAlchemy cannot
    # guess which one a relationship means. Without it, configuring the mapper
    # raises AmbiguousForeignKeysError — and because mapper configuration is
    # lazy and global, the failure surfaces on the first query of any model at
    # all, not on this one. Adding a second FK to a table that already has a
    # relationship to the same target is the whole trap.
    inbound_workflow = relationship("WorkflowModel", foreign_keys=[inbound_workflow_id])
    callback_workflow = relationship(
        "WorkflowModel", foreign_keys=[callback_workflow_id]
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "address_normalized",
            name="uq_phone_numbers_org_address",
        ),
        Index("ix_phone_numbers_config", "telephony_configuration_id"),
        Index(
            "ix_phone_numbers_workflow",
            "inbound_workflow_id",
            postgresql_where=text("inbound_workflow_id IS NOT NULL"),
        ),
        Index(
            "ix_phone_numbers_inbound_lookup",
            "address_normalized",
            "organization_id",
            postgresql_where=text("is_active = true"),
        ),
        Index(
            "uq_phone_numbers_default_caller",
            "telephony_configuration_id",
            unique=True,
            postgresql_where=text("is_default_caller_id = true"),
        ),
    )


class IntegrationModel(Base):
    __tablename__ = "integrations"

    id = Column(Integer, primary_key=True, index=True)
    integration_id = Column(
        String, nullable=False, index=True
    )  # External connection ID
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    provider = Column(String, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"))
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    connection_details = Column(JSON, nullable=False, default=dict)
    action = Column(String, nullable=False, default=IntegrationAction.ALL_CALLS.value)

    # Relationships
    organization = relationship("OrganizationModel", back_populates="integrations")


class WorkflowDefinitionModel(Base):
    __tablename__ = "workflow_definitions"
    id = Column(Integer, primary_key=True, index=True)
    workflow_hash = Column(String, nullable=True)  # Legacy, no longer used
    workflow_json = Column(JSON, nullable=False, default=dict)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=True)
    is_current = Column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Versioning columns
    status = Column(
        String,
        nullable=False,
        default="published",
        server_default=text("'published'"),
    )  # draft | published | archived
    version_number = Column(
        Integer, nullable=True
    )  # Sequential per workflow, display only
    published_at = Column(DateTime(timezone=True), nullable=True)

    # Full behavioral snapshot (moved from WorkflowModel to enable versioning)
    workflow_configurations = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    template_context_variables = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    # Part of the same behavioural snapshot as the two above. It existed in the
    # database but not here, so every `alembic --autogenerate` proposed dropping
    # it — a column holding real data on every published version of every
    # workflow. Declared rather than dropped: the data is the reason.
    call_disposition_codes = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )

    # Table constraints and indexes — unique hash constraint removed (no more dedup)
    __table_args__ = (
        Index("ix_workflow_definitions_workflow_status", "workflow_id", "status"),
    )

    # Relationships
    workflow = relationship(
        "WorkflowModel",
        back_populates="definitions",
        foreign_keys=[workflow_id],
    )
    workflow_runs = relationship("WorkflowRunModel", back_populates="definition")


class FolderModel(Base):
    """A folder for grouping workflows (agents) within an organization.

    Folders are flat (no nesting) and org-scoped. A workflow belongs to at
    most one folder via ``WorkflowModel.folder_id``; a NULL folder_id means
    the workflow is "Uncategorized".
    """

    __tablename__ = "folders"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    organization = relationship("OrganizationModel")
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    workflows = relationship("WorkflowModel", back_populates="folder")

    # Folder names must be unique within an organization.
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_folder_org_name"),
    )


class WorkflowModel(Base):
    __tablename__ = "workflows"
    id = Column(Integer, primary_key=True, index=True)
    workflow_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    user = relationship("UserModel", back_populates="workflows")
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    organization = relationship("OrganizationModel")
    # Optional folder for grouping in the agents list. NULL = "Uncategorized".
    # ON DELETE SET NULL: deleting a folder un-files its agents, never deletes them.
    folder_id = Column(
        Integer,
        ForeignKey("folders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    folder = relationship("FolderModel", back_populates="workflows")
    name = Column(String, index=True, nullable=False)
    status = Column(
        Enum(*[status.value for status in WorkflowStatus], name="workflow_status"),
        nullable=False,
        default=WorkflowStatus.ACTIVE.value,
        server_default=text("'active'::workflow_status"),
    )
    # Whether this agent is answering the phone right now.
    #
    # Separate from ``status``, which is the lifecycle — active or archived,
    # i.e. whether the agent is in the list at all. This is the operational
    # switch: the agent exists, you can see and edit it, and it is or is not
    # taking calls. Folding the two together would hide a paused agent from
    # every screen filtering ``status == 'active'``, including the one you
    # would use to bring it back.
    #
    # Enforced in services/workflow/liveness.py, which every call path routes
    # through — inbound webhook, inbound ARI, the outbound API and the campaign
    # dispatcher. A flag that only some of those honoured would be worse than
    # no flag, because an operator would believe the agent was off.
    is_live = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    workflow_definition = Column(JSON, nullable=False, default=dict)
    template_context_variables = Column(JSON, nullable=False, default=dict)
    call_disposition_codes = Column(JSON, nullable=False, default=dict)
    workflow_configurations = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    runs = relationship("WorkflowRunModel", back_populates="workflow")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Pointer to the currently-live (published) version
    released_definition_id = Column(
        Integer,
        ForeignKey("workflow_definitions.id", use_alter=True),
        nullable=True,
    )
    released_definition = relationship(
        "WorkflowDefinitionModel",
        foreign_keys=[released_definition_id],
        uselist=False,
        viewonly=True,
    )

    # All versions / historical definitions of this workflow
    definitions = relationship(
        "WorkflowDefinitionModel",
        back_populates="workflow",
        foreign_keys="WorkflowDefinitionModel.workflow_id",
    )

    # Relationship to fetch the current (is_current=True) definition
    # Kept for backward compatibility during transition
    current_definition = relationship(
        "WorkflowDefinitionModel",
        primaryjoin=lambda: and_(
            WorkflowDefinitionModel.workflow_id == WorkflowModel.id,
            WorkflowDefinitionModel.is_current.is_(True),
        ),
        uselist=False,
        viewonly=True,
    )

    @property
    def current_definition_id(self):
        """Return ID of the current workflow definition (helper for backwards-compat)."""
        current_def = self.__dict__.get("current_definition")
        if current_def is not None:
            return current_def.id

        # If relationship is not loaded, we cannot safely access definitions without
        # risking an implicit lazy load on a detached instance. Return ``None`` in
        # that scenario so callers can handle the absence explicitly.
        return None


class WorkflowTemplates(Base):
    __tablename__ = "workflow_templates"
    id = Column(Integer, primary_key=True, index=True)
    template_name = Column(String, nullable=False, index=True)
    template_description = Column(String, nullable=False, index=True)
    template_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class WorkflowRunModel(Base):
    __tablename__ = "workflow_runs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False)
    workflow = relationship("WorkflowModel", back_populates="runs")
    definition_id = Column(
        Integer, ForeignKey("workflow_definitions.id"), nullable=True
    )
    definition = relationship("WorkflowDefinitionModel", back_populates="workflow_runs")
    # Stored as VARCHAR (not a Postgres ENUM) so new telephony providers can
    # be added purely in application code without a database migration.
    # See WorkflowRunMode in api/enums.py for the canonical value set.
    mode = Column(String(64), nullable=False)
    call_type = Column(
        Enum(*[call_type.value for call_type in CallType], name="workflow_call_type"),
        nullable=False,
        default=CallType.OUTBOUND.value,
        server_default=text("'outbound'::workflow_call_type"),
    )
    state = Column(
        Enum(*[state.value for state in WorkflowRunState], name="workflow_run_state"),
        nullable=False,
        default=WorkflowRunState.INITIALIZED.value,
        server_default=text("'initialized'::workflow_run_state"),
    )
    is_completed = Column(Boolean, default=False)
    recording_url = Column(String, nullable=True)
    transcript_url = Column(String, nullable=True)
    extra = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )
    # Store storage backend as string enum (s3, minio)
    storage_backend = Column(
        Enum("s3", "minio", name="storage_backend"),
        nullable=False,
        default="s3",
        server_default=text("'s3'::storage_backend"),
    )
    usage_info = Column(JSON, nullable=False, default=dict)
    cost_info = Column(JSON, nullable=False, default=dict)
    initial_context = Column(JSON, nullable=False, default=dict)
    gathered_context = Column(JSON, nullable=False, default=dict)
    logs = Column(JSON, nullable=False, default=dict, server_default=text("'{}'::json"))
    annotations = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=True)
    campaign = relationship("CampaignModel")
    queued_run_id = Column(Integer, ForeignKey("queued_runs.id"), nullable=True)
    queued_run = relationship("QueuedRunModel", foreign_keys=[queued_run_id])
    public_access_token = Column(String(36), nullable=True)
    text_session = relationship(
        "WorkflowRunTextSessionModel",
        back_populates="workflow_run",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # ------------------------------------------------------------------
    # Decibyl billing snapshot (see api/services/billing and DASHBOARD.md)
    # ------------------------------------------------------------------
    # Call lifecycle timestamps. created_at is when the row was made; these
    # describe the call itself and drive answer/completion rates.
    answered_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    # Primary spoken language, for the latency-by-language breakdown.
    language = Column(String(16), nullable=True)
    billable_seconds = Column(Integer, nullable=True)

    # The platform rate resolved at call time, snapshotted so that recomputing
    # this call later reproduces the original number even if the account's rate
    # has since changed.
    platform_rate_mpaise_applied = Column(Integer, nullable=True)
    # The three inputs that produced the rate above. Together they make a
    # receipt self-explaining: the dollar price we quoted, the exchange rate we
    # converted it at, and the granularity we rounded time to. Without them a
    # customer disputing an invoice can only be told the rupee figure, not how
    # it was arrived at.
    platform_rate_micros_usd_applied = Column(Integer, nullable=True)
    usd_inr_paise_applied = Column(Integer, nullable=True)
    pulse_seconds_applied = Column(Integer, nullable=True)
    # billable_seconds rounded up to a whole pulse — the quantity the platform
    # fee is actually computed from.
    billed_seconds = Column(Integer, nullable=True)
    # Usage this call incurred that we hold no rate for, as
    # ["llm:openai/gpt-5", ...]. Empty list means fully costed.
    #
    # Persisted rather than only logged because it is the one thing that makes
    # every margin figure on the dashboard optimistic: unpriced usage is real
    # money we paid and did not record. A number that is quietly wrong is worse
    # than one that is visibly incomplete, so the KPI screen reports it.
    #
    # ``none_as_null`` so SQL NULL means "costed before we tracked this" and is
    # distinguishable from the empty list a freshly costed call writes. Without
    # it SQLAlchemy stores Python None as the JSON value `null`, and the two
    # cases collapse into one that reads as "nothing was missing".
    uncosted_usage = Column(JSON(none_as_null=True), nullable=True)
    # Denormalised from call_cost_items for fast dashboard scans. Provider cost
    # and the platform fee are kept in separate columns and are never summed
    # into a single stored number — that is what makes a hidden markup
    # structurally impossible.
    total_provider_cost_paise = Column(BigInteger, nullable=True)
    total_charged_paise = Column(BigInteger, nullable=True)
    # Set when the cost engine has run for this call; makes costing idempotent.
    costed_at = Column(DateTime(timezone=True), nullable=True)

    cost_items = relationship(
        "CallCostItemModel",
        back_populates="workflow_run",
        cascade="all, delete-orphan",
    )
    turn_metrics = relationship(
        "CallTurnMetricModel",
        back_populates="workflow_run",
        cascade="all, delete-orphan",
    )

    # Indexes
    __table_args__ = (
        Index(
            "idx_workflow_runs_public_access_token",
            "public_access_token",
            unique=True,
            postgresql_where=text("public_access_token IS NOT NULL"),
        ),
        Index(
            "idx_workflow_runs_call_id",
            text("(gathered_context->>'call_id')"),
            postgresql_where=text("gathered_context->>'call_id' IS NOT NULL"),
        ),
        Index("idx_workflow_runs_workflow_id", "workflow_id"),
        Index("idx_workflow_runs_campaign_id", "campaign_id"),
        # Dashboard drill-down is always a time-range scan, either global or
        # narrowed to one account. workflow_runs has no organization_id of its
        # own — it is reached through workflows — so the per-account form is
        # (workflow_id, created_at) and the join to workflows is cheap because
        # that table is small. Headline aggregates never touch these tables;
        # they are served from daily_organization_rollup.
        Index("idx_workflow_runs_created_at", "created_at"),
        Index("idx_workflow_runs_workflow_created_at", "workflow_id", "created_at"),
    )


class WorkflowRunTextSessionModel(Base):
    __tablename__ = "workflow_run_text_sessions"

    workflow_run_id = Column(
        Integer,
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    workflow_run = relationship("WorkflowRunModel", back_populates="text_session")
    revision = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    session_data = Column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'::json"),
    )
    checkpoint = Column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'::json"),
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (Index("ix_workflow_run_text_sessions_updated_at", "updated_at"),)


class OrganizationUsageCycleModel(Base):
    """
    This model is used to track reporting aggregates for an organization for a given
    usage cycle. Quota fields on this model are deprecated; MPS owns quota and
    credit ledger state.
    """

    __tablename__ = "organization_usage_cycles"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)
    quota_decibyl_tokens = Column(
        Integer,
        nullable=False,
        comment="Deprecated. MPS owns quota and credit ledger state.",
        info={"deprecated": True},
    )
    used_decibyl_tokens = Column(Float, nullable=False, default=0)
    total_duration_seconds = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # New USD tracking fields
    used_amount_usd = Column(Float, nullable=True, default=0)
    quota_amount_usd = Column(
        Float,
        nullable=True,
        comment="Deprecated. MPS owns quota and credit ledger state.",
        info={"deprecated": True},
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    organization = relationship("OrganizationModel", back_populates="usage_cycles")

    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "period_start", "period_end", name="unique_org_period"
        ),
        Index("idx_usage_cycles_org_period", "organization_id", "period_end"),
    )


class CampaignModel(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    # Nullable during the legacy → multi-config migration window. Backfilled to the
    # org's default config by the migration; will become NOT NULL in a follow-up.
    telephony_configuration_id = Column(
        Integer, ForeignKey("telephony_configurations.id"), nullable=True
    )

    # Source configuration
    source_type = Column(String, nullable=False, default="csv")
    source_id = Column(String, nullable=False)  # CSV file key

    # State management
    state = Column(
        Enum(
            "created",
            "syncing",
            "running",
            "paused",
            "completed",
            "failed",
            name="campaign_state",
        ),
        nullable=False,
        default="created",
    )

    # Progress tracking
    total_rows = Column(Integer, nullable=True)
    processed_rows = Column(Integer, nullable=False, default=0)
    failed_rows = Column(Integer, nullable=False, default=0)

    # Rate limiting and sync configuration
    rate_limit_per_second = Column(Integer, nullable=False, default=1)
    max_retries = Column(Integer, nullable=False, default=0)
    source_sync_status = Column(String, nullable=False, default="pending")
    source_last_synced_at = Column(DateTime(timezone=True), nullable=True)
    source_sync_error = Column(String, nullable=True)

    # Retry configuration for call failures
    retry_config = Column(
        JSON,
        nullable=False,
        default=DEFAULT_CAMPAIGN_RETRY_CONFIG,
        server_default=text(
            '\'{"enabled": true, "max_retries": 2, "retry_on_busy": true, "retry_on_no_answer": true, "retry_on_voicemail": true, "retry_delay_seconds": 120}\'::jsonb'
        ),
    )

    # Orchestrator tracking fields
    last_batch_scheduled_at = Column(DateTime(timezone=True), nullable=True)
    last_activity_at = Column(DateTime(timezone=True), nullable=True)
    orchestrator_metadata = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )

    # Append-only timestamped log entries for state transitions, failures,
    # and circuit-breaker events. Surfaced in the UI so operators can see
    # why a campaign moved to paused/failed without digging through logs.
    logs = Column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'::json"),
    )

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    organization = relationship("OrganizationModel")
    workflow = relationship("WorkflowModel")
    created_by_user = relationship("UserModel")

    # Indexes
    __table_args__ = (
        Index("ix_campaigns_org_id", "organization_id"),
        Index("ix_campaigns_state", "state"),
        Index("ix_campaigns_workflow_id", "workflow_id"),
        Index(
            "ix_campaigns_telephony_config",
            "telephony_configuration_id",
            postgresql_where=text("telephony_configuration_id IS NOT NULL"),
        ),
        # Index for efficient querying of active campaigns
        Index(
            "idx_campaigns_active_status",
            "state",
            postgresql_where=text("state IN ('syncing', 'running', 'paused')"),
        ),
    )


class QueuedRunModel(Base):
    __tablename__ = "queued_runs"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(
        Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    source_uuid = Column(String, nullable=False)
    context_variables = Column(JSON, nullable=False, default=dict)
    state = Column(
        Enum("queued", "processed", "processing", "failed", name="queued_run_state"),
        nullable=False,
        default="queued",
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    processed_at = Column(DateTime(timezone=True), nullable=True)

    # New retry-related fields
    retry_count = Column(Integer, default=0, nullable=False, server_default=text("0"))
    parent_queued_run_id = Column(Integer, ForeignKey("queued_runs.id"), nullable=True)
    scheduled_for = Column(DateTime(timezone=True), nullable=True)
    retry_reason = Column(String, nullable=True)  # 'busy', 'no_answer', 'voicemail'

    # Why this row was refused before dialling — 'dnd_listed',
    # 'outside_calling_hours'. Distinct from retry_reason, which records why a
    # call that *was* placed did not connect. A refusal is terminal: the row
    # must never be retried, because retrying it is the regulatory breach the
    # refusal exists to prevent.
    refusal_reason = Column(String(64), nullable=True)

    # Relationships
    campaign = relationship("CampaignModel")
    parent_queued_run = relationship("QueuedRunModel", remote_side=[id])

    # Indexes
    __table_args__ = (
        Index("idx_queued_runs_campaign_state", "campaign_id", "state"),
        Index("idx_queued_runs_created", "created_at"),
        Index("idx_queued_runs_source_uuid", "source_uuid"),
        Index(
            "idx_queued_runs_scheduled", "scheduled_for"
        ),  # New index for scheduled retries
        # Optimized index for checking queued runs efficiently
        Index(
            "idx_queued_runs_campaign_state_optimized",
            "campaign_id",
            "state",
            postgresql_where=text("state = 'queued'"),
        ),
        # Optimized index for scheduled retries
        Index(
            "idx_queued_runs_scheduled_optimized",
            "campaign_id",
            "scheduled_for",
            postgresql_where=text("scheduled_for IS NOT NULL"),
        ),
        UniqueConstraint(
            "campaign_id",
            "source_uuid",
            "retry_count",
            name="unique_campaign_source_retry",
        ),
    )


class EmbedTokenModel(Base):
    """Model for storing workflow embed tokens"""

    __tablename__ = "embed_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(255), unique=True, nullable=False, index=True)
    workflow_id = Column(
        Integer,
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    allowed_domains = Column(JSON, nullable=True)  # Array of whitelisted domains
    settings = Column(JSON, nullable=True)  # Widget customization settings
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    usage_limit = Column(Integer, nullable=True)  # Optional usage limit
    usage_count = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    created_by = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    updated_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    workflow = relationship("WorkflowModel")
    organization = relationship("OrganizationModel")
    creator = relationship("UserModel")
    sessions = relationship(
        "EmbedSessionModel", back_populates="embed_token", cascade="all, delete-orphan"
    )


class EmbedSessionModel(Base):
    """Model for storing temporary embed sessions"""

    __tablename__ = "embed_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_token = Column(String(255), unique=True, nullable=False, index=True)
    embed_token_id = Column(
        Integer, ForeignKey("embed_tokens.id", ondelete="CASCADE"), nullable=False
    )
    workflow_run_id = Column(
        Integer, ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=True
    )
    client_ip = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    origin = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)

    # Relationships
    embed_token = relationship("EmbedTokenModel", back_populates="sessions")
    workflow_run = relationship("WorkflowRunModel")


class AgentTriggerModel(Base):
    """Model for storing agent trigger mappings (UUID -> workflow_id).

    This is a minimal lookup table that maps trigger UUIDs to workflows.
    The trigger node in the workflow definition is the source of truth.
    """

    __tablename__ = "agent_triggers"

    id = Column(Integer, primary_key=True, index=True)

    # Globally unique trigger path (UUID format)
    trigger_path = Column(String(36), unique=True, nullable=False, index=True)

    # Link to workflow
    workflow_id = Column(
        Integer, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # State management (active/archived)
    state = Column(
        Enum(*[state.value for state in TriggerState], name="trigger_state"),
        nullable=False,
        default=TriggerState.ACTIVE.value,
        server_default=text("'active'::trigger_state"),
    )

    # Audit
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Relationships
    workflow = relationship("WorkflowModel")
    organization = relationship("OrganizationModel")

    # Indexes for performance
    __table_args__ = (
        Index("ix_agent_triggers_workflow_id", "workflow_id"),
        Index("ix_agent_triggers_state", "state"),
    )


class ExternalCredentialModel(Base):
    """Model for storing external authentication credentials.

    Credentials are stored separately from webhook configurations to allow
    reuse across multiple workflows and secure storage of sensitive data.
    """

    __tablename__ = "external_credentials"

    id = Column(Integer, primary_key=True, index=True)

    # Public UUID reference (used in APIs and workflow definitions)
    # This prevents enumeration attacks and hides internal IDs
    credential_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Organization scoping
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Credential metadata
    name = Column(String, nullable=False)  # Display name, e.g., "Salesforce API"
    description = Column(String, nullable=True)  # Optional description

    # Credential type - uses enum from api/enums.py
    credential_type = Column(
        Enum(
            *[t.value for t in WebhookCredentialType],
            name="webhook_credential_type",
        ),
        nullable=False,
        default=WebhookCredentialType.NONE.value,
    )

    # Encrypted credential data (JSON)
    # Structure depends on credential_type:
    # - api_key: {"header_name": "X-API-Key", "api_key": "value"}
    # - bearer_token: {"token": "value"}
    # - basic_auth: {"username": "user", "password": "value"}
    # - custom_header: {"header_name": "X-Custom", "header_value": "value"}
    credential_data = Column(JSON, nullable=False, default=dict)

    # Audit fields
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Soft delete for safety
    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    organization = relationship("OrganizationModel")
    created_by_user = relationship("UserModel")

    # Indexes and constraints
    __table_args__ = (
        Index("ix_webhook_credentials_organization_id", "organization_id"),
        Index("ix_webhook_credentials_uuid", "credential_uuid"),
        UniqueConstraint("organization_id", "name", name="unique_org_credential_name"),
    )


class WebhookDeliveryModel(Base):
    """Durable record of an outbound webhook delivery attempt.

    Final webhooks (e.g. a workflow's "Final Webhook" node) must not be lost to a
    single transient network error. Instead of firing the HTTP request inline and
    forgetting it, we persist one row per webhook node per workflow run and let an
    ARQ task drive delivery with bounded, backed-off retries. The row is the source
    of truth: it survives worker restarts and a periodic sweeper re-enqueues any
    ``pending`` delivery whose ``scheduled_for`` is overdue. After ``max_attempts``
    transient failures (or on a permanent 4xx) the row is parked as ``dead_letter``
    for inspection rather than retried forever.

    Mirrors the campaign retry pattern (``QueuedRunModel``): persisted state,
    ``scheduled_for`` gating, a hard attempt ceiling, and a terminal failure state.
    """

    __tablename__ = "webhook_deliveries"

    id = Column(Integer, primary_key=True, index=True)

    # Stable idempotency key sent to the receiver so it can dedupe retries.
    delivery_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    workflow_run_id = Column(
        Integer,
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Frozen request definition. The payload is rendered once at enqueue time so
    # retries are deterministic. Secrets are NOT stored here: the auth header is
    # re-resolved from ``credential_uuid`` at send time (honours rotation/revocation).
    webhook_name = Column(String, nullable=True)
    endpoint_url = Column(String, nullable=False)
    http_method = Column(String, nullable=False, default="POST")
    payload = Column(JSON, nullable=False, default=dict)
    custom_headers = Column(JSON, nullable=True)
    credential_uuid = Column(String(36), nullable=True)

    # Workflow node that produced this delivery. Combined with workflow_run_id it
    # is the per-run/per-node idempotency key, so a retried run_integrations does
    # not create (and send) a duplicate delivery for the same node. Non-nullable:
    # a NULL would be distinct under the unique constraint and defeat the dedupe.
    webhook_node_id = Column(String, nullable=False)

    status = Column(
        Enum(
            "pending",
            "succeeded",
            "dead_letter",
            name="webhook_delivery_status",
        ),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    max_attempts = Column(Integer, nullable=False, default=5, server_default=text("5"))
    # When the next attempt becomes due. NULL once terminal (succeeded/dead_letter).
    scheduled_for = Column(DateTime(timezone=True), nullable=True)

    last_status_code = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        # Sweeper lookup: due pending deliveries.
        Index(
            "idx_webhook_deliveries_pending_scheduled",
            "scheduled_for",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("idx_webhook_deliveries_run", "workflow_run_id"),
        # Per-run/per-node idempotency: one delivery per webhook node per run.
        UniqueConstraint(
            "workflow_run_id",
            "webhook_node_id",
            name="uq_webhook_deliveries_run_node",
        ),
    )


class ToolModel(Base):
    """Model for storing reusable tools that can be invoked during workflows.

    Tools provide a standardized way to integrate external functionality - from
    HTTP API calls to native integrations.
    """

    __tablename__ = "tools"

    id = Column(Integer, primary_key=True, index=True)

    # Public identifier (used in APIs and workflow references)
    tool_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Organization scoping
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Tool metadata
    name = Column(String(255), nullable=False)
    description = Column(String, nullable=True)

    # Tool category - uses enum from api/enums.py
    category = Column(
        Enum(
            *[c.value for c in ToolCategory],
            name="tool_category",
        ),
        nullable=False,
        default=ToolCategory.HTTP_API.value,
    )

    # Icon configuration (for UI display)
    icon = Column(String(50), nullable=True)  # Icon identifier
    icon_color = Column(String(7), nullable=True)  # Hex color code

    # Status management
    status = Column(
        Enum(
            *[s.value for s in ToolStatus],
            name="tool_status",
        ),
        nullable=False,
        default=ToolStatus.ACTIVE.value,
        server_default=text("'active'::tool_status"),
    )

    # The tool definition (JSONB) - contains schema_version for compatibility
    # Structure depends on category:
    # - http_api: {"schema_version": 1, "type": "http_api", "config": {...}}
    definition = Column(JSON, nullable=False, default=dict)

    # Audit fields
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    organization = relationship("OrganizationModel")
    created_by_user = relationship("UserModel")

    # Indexes and constraints
    __table_args__ = (
        Index("ix_tools_organization_id", "organization_id"),
        Index("ix_tools_uuid", "tool_uuid"),
        Index("ix_tools_status", "status"),
        Index("ix_tools_category", "category"),
    )


class KnowledgeBaseDocumentModel(Base):
    """Model for storing document-level metadata in the knowledge base.

    Each document represents a source file (PDF, DOCX, etc.) that has been
    processed and chunked for retrieval.
    """

    __tablename__ = "knowledge_base_documents"

    id = Column(Integer, primary_key=True, index=True)

    # Public identifier for API references
    document_uuid = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Organization scoping
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Document metadata
    filename = Column(String(500), nullable=False)
    file_size_bytes = Column(Integer, nullable=True)
    file_hash = Column(String(64), nullable=True)  # SHA-256 hash for deduplication
    mime_type = Column(String(100), nullable=True)

    # Retrieval mode: "chunked" (vector search) or "full_document" (return full text)
    retrieval_mode = Column(
        String(20), nullable=False, default="chunked", server_default="chunked"
    )
    full_text = Column(
        Text, nullable=True
    )  # Stored when retrieval_mode is "full_document"

    # Processing metadata
    source_url = Column(String, nullable=True)  # If document was fetched from URL
    total_chunks = Column(Integer, nullable=False, default=0)
    processing_status = Column(
        Enum(
            "pending",
            "processing",
            "completed",
            "failed",
            name="document_processing_status",
        ),
        nullable=False,
        default="pending",
        server_default=text("'pending'::document_processing_status"),
    )
    processing_error = Column(Text, nullable=True)

    # Docling conversion metadata
    docling_metadata = Column(
        JSON, nullable=False, default=dict
    )  # Store docling document metadata

    # Custom metadata (user-defined tags, categories, etc.)
    custom_metadata = Column(JSON, nullable=False, default=dict)

    # Audit fields
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Soft delete
    is_active = Column(Boolean, default=True, nullable=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    organization = relationship("OrganizationModel")
    created_by_user = relationship("UserModel")
    chunks = relationship(
        "KnowledgeBaseChunkModel",
        back_populates="document",
        cascade="all, delete-orphan",
    )

    # Indexes and constraints
    __table_args__ = (
        Index("ix_kb_documents_organization_id", "organization_id"),
        Index("ix_kb_documents_uuid", "document_uuid"),
        Index("ix_kb_documents_status", "processing_status"),
        Index("ix_kb_documents_created_at", "created_at"),
    )


class WorkflowRecordingModel(Base):
    """Model for storing audio recordings scoped to an organization.

    Recordings are used in hybrid prompts where parts of the output are pre-recorded
    audio rather than dynamically generated TTS.
    """

    __tablename__ = "workflow_recordings"

    id = Column(Integer, primary_key=True, index=True)

    # Descriptive ID used in prompts (unique per organization)
    recording_id = Column(String(64), nullable=False, index=True)

    # Scoping
    workflow_id = Column(
        Integer, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=True
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # TTS configuration metadata (optional, legacy)
    tts_provider = Column(String, nullable=True)
    tts_model = Column(String, nullable=True)
    tts_voice_id = Column(String, nullable=True)

    # Content
    transcript = Column(Text, nullable=False)

    # Storage
    storage_key = Column(String, nullable=False)
    storage_backend = Column(
        Enum("s3", "minio", name="recording_storage_backend"),
        nullable=False,
        default="s3",
        server_default=text("'s3'::recording_storage_backend"),
    )

    # Extra metadata (file_size_bytes, duration_seconds, original_filename, mime_type, etc.)
    recording_metadata = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::json")
    )

    # Audit
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Soft delete
    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    workflow = relationship("WorkflowModel")
    organization = relationship("OrganizationModel")
    created_by_user = relationship("UserModel")

    # Indexes
    __table_args__ = (
        UniqueConstraint(
            "recording_id",
            "organization_id",
            name="uq_workflow_recordings_recording_id_org",
        ),
        Index("ix_workflow_recordings_workflow_id", "workflow_id"),
        Index("ix_workflow_recordings_org_id", "organization_id"),
        Index("ix_workflow_recordings_recording_id", "recording_id"),
    )


class KnowledgeBaseChunkModel(Base):
    """Model for storing document chunks with vector embeddings.

    Each chunk represents a portion of a document that has been:
    1. Extracted and chunked by docling's HybridChunker
    2. Optionally contextualized with surrounding information
    3. Embedded into a vector representation for semantic search
    """

    __tablename__ = "knowledge_base_chunks"

    id = Column(Integer, primary_key=True, index=True)

    # Link to parent document
    document_id = Column(
        Integer,
        ForeignKey("knowledge_base_documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Organization scoping (denormalized for efficient querying)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Chunk content
    chunk_text = Column(Text, nullable=False)  # The actual chunk text
    contextualized_text = Column(
        Text, nullable=True
    )  # Enriched text from chunker.contextualize()

    # Chunk positioning and metadata
    chunk_index = Column(Integer, nullable=False)  # Position in document (0-based)

    # Docling chunk metadata
    chunk_metadata = Column(
        JSON, nullable=False, default=dict
    )  # Store chunk.meta if available

    # Embedding configuration
    embedding_model = Column(
        String(200), nullable=False
    )  # e.g., "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension = Column(
        Integer, nullable=False
    )  # e.g., 384 for all-MiniLM-L6-v2

    # Vector embedding (pgvector column)
    # The dimension should match the embedding_dimension field
    # Default: 1536 dimensions for OpenAI text-embedding-3-small
    # SentenceTransformer (384-dim) also supported but stored as 384-dim vectors
    embedding = Column(Vector(1536), nullable=True)

    # Token count (useful for chunking strategy analysis)
    token_count = Column(Integer, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    document = relationship("KnowledgeBaseDocumentModel", back_populates="chunks")
    organization = relationship("OrganizationModel")

    # Indexes and constraints
    __table_args__ = (
        Index("ix_kb_chunks_document_id", "document_id"),
        Index("ix_kb_chunks_organization_id", "organization_id"),
        Index("ix_kb_chunks_chunk_index", "chunk_index"),
        Index(
            "ix_kb_chunks_embedding_model", "embedding_model"
        ),  # For filtering by model
        # Vector similarity search index (using IVFFlat or HNSW)
        # IVFFlat is good for datasets with 10k-1M vectors
        # HNSW is better for larger datasets but uses more memory
        Index(
            "ix_kb_chunks_embedding_ivfflat",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},  # Adjust based on dataset size
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# ---------------------------------------------------------------------------
# Billing: rate configuration, per-call costing, credits and rollups.
# See api/services/billing/ for the engine and DASHBOARD.md for definitions.
# ---------------------------------------------------------------------------


class OrganizationRateHistoryModel(Base):
    """Effective-dated history of an account's platform rate.

    Rates are never updated in place. Changing a rate closes the current row
    (sets ``effective_to``) and inserts a new one, so recomputing an old
    invoice reproduces the original number. ``set_by`` and ``note`` make every
    change attributable.
    """

    __tablename__ = "organization_rate_history"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # Exactly one of these is set. The list price is in dollars, so most rows
    # are; an override exists in rupees only when the contract was written that
    # way and the customer must not be exposed to FX. Storing both would leave
    # two answers to "what does this account pay".
    platform_rate_micros_usd = Column(Integer, nullable=True)
    platform_rate_mpaise = Column(Integer, nullable=True)
    # NULL falls back to the global default. An account that negotiated
    # whole-minute billing — or finer than 15s — carries it here.
    pulse_seconds = Column(Integer, nullable=True)
    effective_from = Column(DateTime(timezone=True), nullable=False)
    # NULL means "still in effect". At most one open row per organization.
    effective_to = Column(DateTime(timezone=True), nullable=True)
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    organization = relationship("OrganizationModel")
    set_by_user = relationship("UserModel")

    __table_args__ = (
        Index(
            "ix_org_rate_history_org_effective",
            "organization_id",
            "effective_from",
        ),
        # Guards the invariant that an account has at most one open rate row.
        Index(
            "uq_org_rate_history_open",
            "organization_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
        CheckConstraint(
            "(platform_rate_micros_usd IS NOT NULL)::int "
            "+ (platform_rate_mpaise IS NOT NULL)::int = 1",
            name="ck_org_rate_history_one_currency",
        ),
    )


class OrganizationInvitationModel(Base):
    """An offer of a seat in an organization.

    Until this existed there was no way for a second person to join an account
    on a self-hosted deployment at all. Membership was mirrored from Stack Auth
    in SaaS mode and created once at signup otherwise, so every local
    organization had exactly one member forever — which made the whole
    member/admin/owner system unreachable in practice: there was never anybody
    to be a member *of*.

    The token is stored hashed and never again in the clear, the same way an
    API key is. A pending invitation is a bearer credential for a seat in
    somebody's account; a database dump that leaks them is a database dump that
    hands out seats.
    """

    __tablename__ = "organization_invitations"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: Lower-cased on write. The invitation is *to an address*, and accepting
    #: requires signing in as it — otherwise a forwarded link is a seat for
    #: whoever opens it first.
    email = Column(String, nullable=False)
    #: The role the seat carries. Decided when the invitation is written so
    #: that accepting grants exactly what was offered, rather than a default
    #: that has drifted since.
    role = Column(
        String(16),
        nullable=False,
        default=OrganizationRole.MEMBER.value,
        server_default=text(f"'{OrganizationRole.MEMBER.value}'"),
    )
    #: SHA-256 of the token in the link. Unique so a lookup is a single
    #: indexed read rather than a scan over live invitations.
    token_hash = Column(String, nullable=False, unique=True, index=True)
    invited_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    #: Invitations expire. One that does not is a credential with no end,
    #: sitting in a mailbox somebody eventually loses control of.
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    accepted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    #: Withdrawn before it was used. Kept rather than deleted so "who offered
    #: this person a seat, and who took it back" survives.
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    organization = relationship("OrganizationModel")

    __table_args__ = (
        Index("ix_organization_invitations_org", "organization_id"),
        # One live invitation per address per organization. Without it, three
        # clicks of Invite put three valid tokens in somebody's inbox and
        # revoking the one you can see leaves two behind.
        Index(
            "uq_organization_invitations_open",
            "organization_id",
            "email",
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )


class PartnerApplicationModel(Base):
    """A customer asking to be treated as a partner, and what we decided.

    The account already exists and already works — a partner account is an
    ordinary account with a commercial arrangement attached, not a different
    product — so this asks four questions rather than running a second signup.

    Kept as a record of the decision rather than a workflow that deletes
    itself: "why is this account on 12%?" is asked months later, and the answer
    is the application it was granted against. A rejected row stays for the
    same reason, so a second application can be read next to the first.
    """

    __tablename__ = "partner_applications"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # Self-declared. See PartnerKind — it shapes the review rather than
    # granting anything.
    kind = Column(String(32), nullable=False)
    #: Their own forecast, in minutes per month. The single most useful number
    #: on the form: it is what a commission is quoted against, and comparing it
    #: against what the account goes on to actually do is how a rate gets
    #: revisited.
    expected_minutes_per_month = Column(Integer, nullable=True)
    #: Free text. Who their clients are, what they are replacing, anything the
    #: four fixed answers cannot carry.
    note = Column(Text, nullable=True)
    company_website = Column(String, nullable=True)

    status = Column(
        String(16),
        nullable=False,
        default=PartnerApplicationStatus.PENDING.value,
        server_default=text(f"'{PartnerApplicationStatus.PENDING.value}'"),
    )
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    submitted_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    #: Why. Shown to the applicant on a rejection, so it is written for them.
    decision_note = Column(Text, nullable=True)

    organization = relationship("OrganizationModel")

    __table_args__ = (
        Index("ix_partner_applications_status", "status"),
        # One open application per account. Without it a customer who clicks
        # submit twice puts two rows in the queue and a reviewer approves the
        # one that is already stale.
        Index(
            "uq_partner_applications_pending",
            "organization_id",
            unique=True,
            postgresql_where=text(
                f"status = '{PartnerApplicationStatus.PENDING.value}'"
            ),
        ),
    )


class PartnerCommissionModel(Base):
    """Effective-dated commission for a partner account.

    Same shape and the same reason as ``organization_rate_history``: a
    commission is never updated in place. Changing one closes the current row
    and inserts a new one, so a statement for March still reproduces March's
    number after an April renegotiation. Paying a partner a percentage that
    silently moved under an already-issued statement is the failure this
    prevents.

    ``basis`` travels with the rate rather than sitting on the organization,
    because moving a partner from a share of margin to a share of spend is a
    bigger change than moving the percentage, and it has to be reproducible
    the same way.
    """

    __tablename__ = "partner_commissions"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: Basis points, so 1250 is 12.5%. Integer for the same reason every other
    #: money field here is: a float rate multiplied across a month of calls
    #: does not reconcile against a statement.
    commission_bps = Column(Integer, nullable=False)
    #: See CommissionBasis. What the percentage is a percentage of.
    basis = Column(String(32), nullable=False)
    #: The application this was granted against, so the rate and the answers it
    #: was quoted from stay joined up.
    application_id = Column(
        Integer,
        ForeignKey("partner_applications.id", ondelete="SET NULL"),
        nullable=True,
    )
    effective_from = Column(DateTime(timezone=True), nullable=False)
    #: NULL means "still in effect". At most one open row per organization.
    effective_to = Column(DateTime(timezone=True), nullable=True)
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    organization = relationship("OrganizationModel")

    __table_args__ = (
        Index(
            "ix_partner_commissions_org_effective",
            "organization_id",
            "effective_from",
        ),
        Index(
            "uq_partner_commissions_open",
            "organization_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
        # A negative commission is a charge, and 100% of total spend is the
        # whole invoice. Neither is a thing anybody means to type, and both are
        # cheap to refuse here rather than discover on a payout run.
        CheckConstraint(
            "commission_bps >= 0 AND commission_bps <= 10000",
            name="ck_partner_commissions_bps_range",
        ),
    )


class PartnerStatementModel(Base):
    """What a partner earned in one period, and whether we have paid it.

    A statement is generated, then issued, then paid — and only the first of
    those is reversible. Regenerating a draft is how a period that closed early
    or a rollup that arrived late gets corrected; regenerating an issued one is
    how a partner is told a different number than the one they were sent, so
    the service refuses.

    ``amount_paise`` is the sum of this statement's lines and nothing else, the
    same definition ``total_charged_paise`` uses on a call receipt, so a
    statement always reconciles against its own breakdown.
    """

    __tablename__ = "partner_statements"

    id = Column(Integer, primary_key=True, index=True)
    partner_organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: Inclusive IST calendar days, matching daily_organization_rollup.day.
    #: A partner month has to line up with the days the dashboard shows, or the
    #: partner and the account they referred read different months.
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)

    #: Snapshotted from the commission in force, for the reader. Where a rate
    #: changed mid-period there is no single basis on the lines, so this
    #: records what it was at period end and the lines carry the arithmetic.
    basis = Column(String(32), nullable=False)
    #: Sum of the lines. Never computed at read time.
    amount_paise = Column(BigInteger, nullable=False, default=0)
    #: What the commission applied to — margin or spend, per ``basis``. Kept so
    #: the effective rate is derivable even when the rate moved mid-period.
    basis_amount_paise = Column(BigInteger, nullable=False, default=0)

    #: draft | issued | paid — see PartnerStatementStatus.
    status = Column(
        String(16),
        nullable=False,
        default=PartnerStatementStatus.DRAFT.value,
        server_default=text(f"'{PartnerStatementStatus.DRAFT.value}'"),
    )
    generated_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    issued_at = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    #: Whatever identifies the transfer on our side — a UTR, a bank reference.
    #: Free text because the payment happens outside this system entirely.
    payment_reference = Column(String(128), nullable=True)
    note = Column(Text, nullable=True)

    organization = relationship("OrganizationModel")
    lines = relationship(
        "PartnerStatementLineModel",
        back_populates="statement",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # One statement per partner per period. Without it a second generate
        # run doubles what we owe, which is the one arithmetic error nobody
        # notices until it has been paid.
        UniqueConstraint(
            "partner_organization_id",
            "period_start",
            name="uq_partner_statements_period",
        ),
        Index("ix_partner_statements_status", "status"),
    )


class PartnerStatementLineModel(Base):
    """One referred account's contribution to one statement.

    The answer to "why is this number what it is". A partner asking gets the
    accounts, what each of them generated, and what that earned — rather than a
    total they have to take on trust.

    No effective rate column. Where a commission changed mid-period the
    arithmetic is per-day and no single percentage produced this line, so
    storing one would be a number that does not reproduce. ``amount_paise``
    over ``basis_amount_paise`` is the effective rate, and it is honest about
    having been blended.
    """

    __tablename__ = "partner_statement_lines"

    id = Column(Integer, primary_key=True, index=True)
    statement_id = Column(
        Integer, ForeignKey("partner_statements.id", ondelete="CASCADE"), nullable=False
    )
    #: SET NULL, not CASCADE: a referred account closing must not erase the
    #: line that says we owe somebody for the months it was open.
    referred_organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    #: Kept alongside the id so a closed account still reads as a name.
    referred_name = Column(String, nullable=True)
    #: Margin or total spend for this account over the period, per the
    #: statement's basis.
    basis_amount_paise = Column(BigInteger, nullable=False, default=0)
    amount_paise = Column(BigInteger, nullable=False, default=0)

    statement = relationship("PartnerStatementModel", back_populates="lines")

    __table_args__ = (Index("ix_partner_statement_lines_statement", "statement_id"),)


class PlatformVolumeTierModel(Base):
    """Optional volume tiers, applied when an account has no explicit override.

    A tier matches when the account's billable minutes in the current billing
    period reach ``min_period_minutes``. The highest matching threshold wins.
    """

    __tablename__ = "platform_volume_tiers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), nullable=False)
    min_period_minutes = Column(Integer, nullable=False)
    # Same either-or rule as an account override: quoted in dollars by default,
    # in rupees only for a contract written that way.
    platform_rate_micros_usd = Column(Integer, nullable=True)
    platform_rate_mpaise = Column(Integer, nullable=True)
    pulse_seconds = Column(Integer, nullable=True)
    effective_from = Column(DateTime(timezone=True), nullable=False)
    effective_to = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_platform_volume_tiers_effective", "effective_from", "effective_to"),
        CheckConstraint(
            "(platform_rate_micros_usd IS NOT NULL)::int "
            "+ (platform_rate_mpaise IS NOT NULL)::int = 1",
            name="ck_platform_volume_tiers_one_currency",
        ),
    )


class UsdInrRateHistoryModel(Base):
    """Effective-dated USD→INR exchange rate.

    The platform fee is quoted in dollars and settled in rupees, so the rate in
    force when a call happened is part of that call's price. Storing it
    effective-dated — the same rule every other rate here follows — is what
    lets an old invoice be recomputed to the number that was actually charged,
    rather than to whatever the rupee is worth today.

    ``paise_per_usd`` is an integer: ₹96.00 is 9600. Whole paise is finer than
    any published rate needs, and keeping it integral keeps the conversion
    exact until the single rounding in ``usd_to_mpaise``.
    """

    __tablename__ = "usd_inr_rate_history"

    id = Column(Integer, primary_key=True, index=True)
    paise_per_usd = Column(Integer, nullable=False)
    effective_from = Column(DateTime(timezone=True), nullable=False)
    # NULL means "still in effect". At most one open row.
    effective_to = Column(DateTime(timezone=True), nullable=True)
    # Where the number came from — a bank, an API, or a person typing it in.
    # Without this, a rate nobody can source is indistinguishable from a typo.
    source = Column(String(64), nullable=True)
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    set_by_user = relationship("UserModel")

    __table_args__ = (
        Index("ix_usd_inr_rate_history_effective", "effective_from", "effective_to"),
        Index(
            "uq_usd_inr_rate_history_open",
            "effective_to",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
        CheckConstraint("paise_per_usd > 0", name="ck_usd_inr_rate_positive"),
    )


class ProviderRateModel(Base):
    """Effective-dated provider unit rates, passed through at cost.

    Same no-destructive-update rule as the platform rate: superseding a rate
    closes the old row and inserts a new one, so historical calls re-cost to
    the number that was actually charged.
    """

    __tablename__ = "provider_rates"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(64), nullable=False)
    # The specific model this rate is for, e.g. "gpt-4o-mini". Empty string
    # means a provider-wide fallback that applies to any model without its own
    # row. Not nullable, because a partial unique index over a nullable column
    # would let duplicate open fallbacks through.
    model = Column(String(128), nullable=False, server_default="", default="")
    # stt | llm | tts | telephony — see CostComponent.
    component = Column(String(16), nullable=False)
    # minute | 1k_chars | 1k_tokens — see RateUnit.
    unit = Column(String(16), nullable=False)
    # Exactly one of these carries the price, enforced by the check constraint
    # below. Which one is not a storage detail: a dollar-quoted row is
    # converted at read time against the FX then in force, so its cost tracks
    # the rupee the way the invoice does, while a rupee-quoted row has no FX
    # applied at all. Same split, and the same reasoning, as the platform rate
    # on organization_rate_history.
    rate_mpaise = Column(Integer, nullable=True)
    rate_micros_usd = Column(Integer, nullable=True)
    effective_from = Column(DateTime(timezone=True), nullable=False)
    effective_to = Column(DateTime(timezone=True), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint(
            "(rate_mpaise IS NULL) <> (rate_micros_usd IS NULL)",
            name="ck_provider_rates_one_currency",
        ),
        Index(
            "ix_provider_rates_lookup",
            "provider",
            "component",
            "model",
            "effective_from",
        ),
        # One open rate per (provider, model, component). Model is part of the
        # key so a provider-wide fallback ("") and a model-specific override can
        # both be open at once — which is the whole point of the fallback.
        Index(
            "uq_provider_rates_open",
            "provider",
            "component",
            "model",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
    )


class CallCostItemModel(Base):
    """One itemised line on a call receipt.

    Every component is stored separately — provider costs and the platform fee
    are never collapsed into one number. ``units`` is the raw measured quantity
    (seconds, characters, tokens) and ``unit_rate_mpaise`` the rate applied, so
    a receipt can always be re-derived and audited.
    """

    __tablename__ = "call_cost_items"

    id = Column(Integer, primary_key=True, index=True)
    workflow_run_id = Column(
        Integer, ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    # stt | llm | tts | telephony | platform — see CostComponent.
    component = Column(String(16), nullable=False)
    # NULL for the platform fee, which has no third-party provider.
    provider = Column(String(64), nullable=True)
    # The model this line was priced against, so the receipt names what was
    # actually billed. NULL for telephony and the platform fee.
    model = Column(String(128), nullable=True)
    units = Column(BigInteger, nullable=False, default=0)
    unit_rate_mpaise = Column(Integer, nullable=False, default=0)
    # What the customer was charged for this line.
    cost_paise = Column(BigInteger, nullable=False, default=0)
    # What the vendor charged *us* for it, before the managed markup. Equal to
    # cost_paise on a platform line and on every row written before the markup
    # existed, which is what the backfill sets.
    #
    # Stored rather than derived from today's multiplier: recomputing an old
    # receipt against a rate that has since changed would rewrite what a
    # customer was actually charged.
    provider_cost_paise = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    workflow_run = relationship("WorkflowRunModel", back_populates="cost_items")

    __table_args__ = (
        Index("ix_call_cost_items_run", "workflow_run_id"),
        Index("ix_call_cost_items_component", "component"),
    )


class EmbeddingIngestionCostModel(Base):
    """One document's ingestion-embedding cost, alongside what it was charged.

    ``call_cost_items`` pairs ``provider_cost_paise`` with ``cost_paise`` for
    every call, which is what lets the unit-economics screen show a margin
    rather than only a bill. Ingestion has no ``workflow_run_id`` to write a
    ``call_cost_items`` row against — this table is its equivalent, written by
    ``services/billing/embedding_ingestion.py`` in the same transaction as the
    ``credit_ledger`` debit.

    The ledger row alone would only ever answer "what did we charge for
    this" — it has no column for what the vendor charged us. Without this
    table that half of "meter everything" silently didn't happen: the money
    moved, but the one number margin analysis exists to compare — cost against
    charge — had nowhere to live.
    """

    __tablename__ = "embedding_ingestion_costs"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    document_id = Column(
        Integer,
        ForeignKey("knowledge_base_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider = Column(String(64), nullable=False)
    model = Column(String(128), nullable=False, default="", server_default="")
    # server_default alongside the Python default on each of these, matching
    # what migration b6d3f0a4c9e5 actually creates. Declaring one side only is
    # schema drift `alembic check` fails on, and it is the same trap
    # KNOWN_ISSUES.md #10 records: a default set by a migration but absent
    # from the model makes every later --autogenerate propose a spurious
    # change that somebody has to know to delete by hand.
    tokens = Column(BigInteger, nullable=False, default=0, server_default="0")
    # What the vendor charged us, before the managed markup.
    vendor_cost_paise = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    # What this debited the account for — the same figure as the paired
    # credit_ledger row's -delta_paise, stored here too so a margin query
    # never has to join back to the ledger to get it.
    charged_paise = Column(BigInteger, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index(
            "ix_embedding_ingestion_costs_org_created",
            "organization_id",
            "created_at",
        ),
        # One row per document, mirroring the ledger's own at-most-once rule
        # (uq_credit_ledger_embedding_ingest_ref) rather than a second place
        # the two guarantees could drift apart.
        Index(
            "uq_embedding_ingestion_costs_document",
            "document_id",
            unique=True,
        ),
    )


class CallTurnMetricModel(Base):
    """Per-turn latency instrumentation for one call.

    Stage timestamps are milliseconds relative to the start of the call rather
    than absolute times: they are only ever used as differences, and integer
    offsets keep the percentile queries free of timezone and clock-skew
    concerns. ``latency_ms`` is the perceived latency
    (``t_audio_out - t_user_stopped``) stored so percentiles can be computed in
    SQL over raw rows — averaging pre-bucketed percentiles gives wrong answers.
    """

    __tablename__ = "call_turn_metrics"

    id = Column(Integer, primary_key=True, index=True)
    workflow_run_id = Column(
        Integer, ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    turn_index = Column(Integer, nullable=False)

    t_user_stopped_ms = Column(Integer, nullable=True)
    t_endpoint_fired_ms = Column(Integer, nullable=True)
    t_stt_final_ms = Column(Integer, nullable=True)
    t_llm_first_token_ms = Column(Integer, nullable=True)
    t_tts_first_byte_ms = Column(Integer, nullable=True)
    t_audio_out_ms = Column(Integer, nullable=True)

    # Denormalised t_audio_out_ms - t_user_stopped_ms, so percentile queries
    # never have to recompute it across millions of rows.
    latency_ms = Column(Integer, nullable=True)

    tool_called = Column(String(128), nullable=True)
    tool_ms = Column(Integer, nullable=True)

    # Token usage for this turn specifically, not the call.
    #
    # A voice agent resends the whole conversation every turn, so prompt_tokens
    # at turn N *is* the context size, and plotting it against turn_index shows
    # the growth that makes a long call cost more than proportionally. A
    # call-wide total cannot show that shape: ten short exchanges and three long
    # ones sum to the same number.
    #
    # Nullable because the provider does not always report usage, and a turn
    # that reported nothing must stay silent rather than claim zero — a zero
    # would drag the median context size down and read as an improvement.
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    # The share of the prompt served from the provider's cache. The lever on
    # everything above: caching cuts the cost of resent context by most of its
    # value, and the hit rate is not derivable from any other stored figure.
    cached_tokens = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    workflow_run = relationship("WorkflowRunModel", back_populates="turn_metrics")

    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id", "turn_index", name="uq_call_turn_metrics_run_turn"
        ),
        Index("ix_call_turn_metrics_run", "workflow_run_id"),
        # Percentiles are computed over raw rows filtered by time and language.
        Index("ix_call_turn_metrics_latency", "created_at", "latency_ms"),
    )


class OrganizationKycModel(Base):
    """Telephony KYC for one account.

    Kept off ``organizations`` deliberately: this is a dozen fields that only
    matter to accounts wanting phone numbers, and the table already carries the
    billing columns.

    The two ``status`` stages are not interchangeable. Ours is a pre-screen;
    the carrier is the licensee and its verdict is the one that may unblock
    calling. See :class:`~api.enums.KycStatus`.
    """

    __tablename__ = "organization_kyc"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # See KycStatus.
    status = Column(
        String(24), nullable=False, default="not_started", server_default="not_started"
    )
    # individual | company — see KycBusinessType. Decides required documents.
    business_type = Column(String(16), nullable=True)
    # Name as registered, which need not match the account's display name.
    legal_name = Column(String(255), nullable=True)
    gstin = Column(String(20), nullable=True)

    # The registered address, required on a Plivo compliance application's
    # end_user. Held here rather than on organizations because it is the
    # address on the incorporation certificate, which is not necessarily where
    # the account's users sit.
    address_line1 = Column(String(255), nullable=True)
    address_line2 = Column(String(255), nullable=True)
    city = Column(String(128), nullable=True)
    region = Column(String(128), nullable=True)
    postal_code = Column(String(16), nullable=True)
    country_iso = Column(String(2), nullable=False, default="IN", server_default="IN")

    submitted_at = Column(DateTime(timezone=True), nullable=True)

    # Our review. Who signed off matters for compliance, so it is recorded
    # rather than inferred from a log line.
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejection_reason = Column(Text, nullable=True)

    # The carrier leg.
    forwarded_at = Column(DateTime(timezone=True), nullable=True)
    carrier = Column(String(32), nullable=True)
    # The carrier's own application id, so a status poll has something to ask
    # about and a dispute has something to quote.
    carrier_reference = Column(String(128), nullable=True)
    carrier_status = Column(String(64), nullable=True)
    carrier_checked_at = Column(DateTime(timezone=True), nullable=True)
    carrier_rejection_reason = Column(Text, nullable=True)
    carrier_approved_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    organization = relationship("OrganizationModel")
    documents = relationship(
        "KycDocumentModel",
        back_populates="kyc",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # The review queue is "everything waiting on us, oldest first".
        Index("ix_organization_kyc_status_submitted", "status", "submitted_at"),
    )


class KycDocumentModel(Base):
    """One uploaded KYC document.

    Only the storage key lives here. The files are incorporation and tax
    certificates and identity documents — sensitive under the DPDP Act — so
    they go to their own bucket with staff-only access rather than sharing the
    call-recording bucket's policy.
    """

    __tablename__ = "kyc_documents"

    id = Column(Integer, primary_key=True, index=True)
    organization_kyc_id = Column(
        Integer,
        ForeignKey("organization_kyc.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # See KycDocumentKind.
    kind = Column(String(32), nullable=False)
    storage_key = Column(String(512), nullable=False)
    filename = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    # SHA-256 of the bytes. Stored so pre-submit validation can catch the same
    # file uploaded into two slots without reading both objects back out of the
    # bucket — one of the two most common causes of a carrier rejection, and
    # one the customer cannot diagnose from Plivo's reply.
    content_sha256 = Column(String(64), nullable=True)

    uploaded_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    kyc = relationship("OrganizationKycModel", back_populates="documents")

    __table_args__ = (Index("ix_kyc_documents_kyc", "organization_kyc_id"),)


class RecurringChargeModel(Base):
    """A charge that accrues for holding a resource, not for using one.

    Deliberately not a ``CallCostItem``. A call cost item is a line on a
    receipt for a workflow run: it has a run behind it, it is computed from
    measured usage, and it exists only because somebody dialled. A number
    rental has none of those properties — it accrues while the account sleeps,
    and folding it into the per-call path would have meant inventing a fake run
    to hang it off, and would have shown customers a charge for calls they
    never made.

    ``cost_paise`` and ``price_paise`` are both stored because storing only the
    price is how a margin figure starts lying. The dashboard's per-call margin
    already ignored number rental entirely; recording our own cost next to the
    customer's price is what lets that be fixed rather than re-estimated.

    Idempotency is the partial unique index on
    ``(recurring_charge_id, period_start)``: a monthly cron that runs twice, or
    a worker that dies after debiting and before committing its bookkeeping,
    must not bill a customer for the same month twice. This is the same shape
    as the ledger's ``uq_credit_ledger_usage_ref``.
    """

    __tablename__ = "recurring_charges"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # number_rental — see RecurringChargeType.
    charge_type = Column(String(32), nullable=False)
    # What is being rented. For number_rental this is a
    # telephony_phone_numbers.id, held as a plain integer rather than an FK so
    # a future charge type can point at something else entirely.
    resource_id = Column(Integer, nullable=False)

    # active | past_due | suspended | pending_release | released | cancelled.
    status = Column(
        String(24), nullable=False, default="active", server_default="active"
    )

    # What we pay the carrier, and what the customer pays us. Both per period.
    cost_paise = Column(BigInteger, nullable=False, default=0, server_default="0")
    price_paise = Column(BigInteger, nullable=False, default=0, server_default="0")

    started_at = Column(DateTime(timezone=True), nullable=False)
    # The period this charge has been billed through. NULL until the first
    # (prorated) charge lands.
    current_period_start = Column(DateTime(timezone=True), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    next_charge_at = Column(DateTime(timezone=True), nullable=True)

    # Dunning. first_failed_at anchors every deadline in the policy, so it is
    # cleared the moment a charge succeeds — a customer who pays late starts
    # from zero rather than carrying a clock from three months ago.
    first_failed_at = Column(DateTime(timezone=True), nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    failure_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_failure_reason = Column(Text, nullable=True)

    ended_at = Column(DateTime(timezone=True), nullable=True)

    # The autopay mandate collecting for this charge, if there is one. Nullable
    # because a charge can outlive its mandate: a revoked mandate leaves the
    # link in place and the charge falls back to the prepaid balance and the
    # dunning schedule, which is exactly the situation dunning was written for.
    mandate_id = Column(
        Integer, ForeignKey("payment_mandates.id", ondelete="SET NULL"), nullable=True
    )

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    organization = relationship("OrganizationModel")
    mandate = relationship("PaymentMandateModel")
    periods = relationship(
        "RecurringChargePeriodModel",
        back_populates="charge",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_recurring_charges_org", "organization_id"),
        Index("ix_recurring_charges_due", "status", "next_charge_at"),
        # One live charge per resource. Without this a retried provisioning
        # would bill the same number twice a month, for ever, and the second
        # row would look as legitimate as the first.
        Index(
            "uq_recurring_charges_live_resource",
            "charge_type",
            "resource_id",
            unique=True,
            postgresql_where=text("status NOT IN ('released', 'cancelled')"),
        ),
    )


class RecurringChargePeriodModel(Base):
    """One billed month of a recurring charge.

    Exists so "have we billed this month yet?" is a unique-constraint question
    rather than a date comparison. Date arithmetic run twice at a month
    boundary can disagree with itself; a unique index cannot.
    """

    __tablename__ = "recurring_charge_periods"

    id = Column(Integer, primary_key=True, index=True)
    recurring_charge_id = Column(
        Integer,
        ForeignKey("recurring_charges.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)
    # What was actually charged, after proration. Kept alongside the charge's
    # price_paise because a prorated first month differs from it and a
    # statement has to show what was billed, not what the list price was.
    charged_paise = Column(BigInteger, nullable=False)
    cost_paise = Column(BigInteger, nullable=False, default=0, server_default="0")
    prorated = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Where the money came from: the prepaid balance, or the customer's bank
    # under an autopay mandate. Recorded because the two are collected by
    # completely different code paths and a statement that cannot tell them
    # apart makes a double-collection impossible to spot.
    collected_via = Column(
        String(16), nullable=False, default="balance", server_default="balance"
    )
    # The provider's payment id, for a period the customer's bank paid.
    #
    # This is the idempotency key for a mandate collection, and it has to be:
    # the period-start key below cannot serve, because recording a collection
    # advances the charge to the next period, so a redelivered webhook computes
    # a *different* period_start and bills a month forward instead of colliding.
    # The provider's payment id is the same on every redelivery of the same
    # collection, which is the property that makes at-least-once delivery safe.
    provider_payment_id = Column(String(64), nullable=True)
    charged_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    charge = relationship("RecurringChargeModel", back_populates="periods")

    __table_args__ = (
        # The idempotency guarantee. A double-run of the monthly cron collides
        # here and the second attempt is rolled back.
        UniqueConstraint(
            "recurring_charge_id",
            "period_start",
            name="uq_recurring_charge_period",
        ),
        # One period per provider collection. Partial, because balance-collected
        # periods have no payment id and must not all collide on NULL.
        Index(
            "uq_recurring_charge_period_payment",
            "provider_payment_id",
            unique=True,
            postgresql_where=text("provider_payment_id IS NOT NULL"),
        ),
        Index("ix_recurring_charge_periods_org", "organization_id", "charged_at"),
    )


class NotificationModel(Base):
    """One operational notice, sent or attempted.

    Exists for one reason: **so a notice is not sent twice.** The jobs that send
    these are safe to re-run by design — that is what makes a missed cron
    harmless — and without a record, "safe to re-run" would mean a customer with
    a low balance gets the same warning every time the worker restarts.

    ``dedupe_key`` is the caller's statement of what makes two notices the same
    thing. For the low-balance warning it is the severity and the day, so an
    account gets one warning a day and a second one only if it gets worse.

    Failures are recorded rather than retried. A notice that did not send is
    worth seeing in a table; a retry loop around an SMTP server is a way to send
    the same warning four times when the server was merely slow.
    """

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # low_balance | ... — a plain string so a new notice needs no migration.
    kind = Column(String(48), nullable=False)
    dedupe_key = Column(String(128), nullable=False)
    channel = Column(
        String(16), nullable=False, default="email", server_default="email"
    )
    # Who it went to, joined, for the record. Not used to send anything.
    recipients = Column(Text, nullable=True)
    subject = Column(Text, nullable=True)
    sent = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    organization = relationship("OrganizationModel")

    __table_args__ = (
        # The whole point of the table. Claimed *before* the send, so two
        # workers racing on the same account produce one email rather than two.
        UniqueConstraint(
            "organization_id",
            "kind",
            "dedupe_key",
            name="uq_notification_dedupe",
        ),
        Index("ix_notifications_org", "organization_id", "created_at"),
    )


class ManagedBundleModel(Base):
    """A named combination of tiers, as the Simple picker offers it.

    "Everyday", "Natural", "Premium" — one card, one price, one choice. The
    buyer this is for does not know Sarvam from OpenAI and should not have to
    learn in order to answer a phone.

    A bundle references **tiers, not vendors**, and that indirection is the
    whole point. A customer's stored configuration names a tier, so moving a
    vendor is a change in one place that reaches every agent already built. If
    a bundle named a provider and model directly, changing one would leave
    every existing agent on the old vendor and every new one on the new — the
    drift the tier system exists to prevent. Changing what a bundle *runs on*
    is therefore a tier edit; changing what it is *called*, what it costs to
    show, whether it appears at all and in what order, is a bundle edit.

    Seeded on first read rather than by migration, so a deployment that has
    never opened the screen still has something to offer, and so the compiled
    defaults stay the single description of what ships.
    """

    __tablename__ = "managed_bundles"

    id = Column(Integer, primary_key=True, index=True)
    #: Stable identity. What an agent's configuration records, so renaming a
    #: bundle for the storefront never rewrites what anybody chose.
    slug = Column(String(32), nullable=False, unique=True)
    label = Column(String(64), nullable=False)
    blurb = Column(String(240), nullable=False, default="", server_default=text("''"))
    #: pipeline | realtime. Decides which tier columns below are meaningful and
    #: what the agent configuration is compiled into.
    architecture = Column(String(16), nullable=False)
    #: Pipeline bundles. ``llm_tier`` null means the customer picks the brain —
    #: which is what makes Everyday three variants rather than three bundles.
    stt_tier = Column(String(32), nullable=True)
    tts_tier = Column(String(32), nullable=True)
    llm_tier = Column(String(32), nullable=True)
    #: Speech-to-speech bundles.
    realtime_tier = Column(String(32), nullable=True)
    display_order = Column(Integer, nullable=False, default=0, server_default=text("0"))
    #: Off rather than deleted. A bundle an agent already runs on must keep
    #: resolving after it stops being offered to new customers.
    is_enabled = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ManagedTierMappingModel(Base):
    """Which real vendor and model serve a managed tier, chosen by an operator.

    ``managed_tiers.py`` holds the defaults and an environment-variable
    override, and both are fine for bootstrapping a deployment. Neither is a
    product control: moving the Normal brain to a cheaper model, or pointing
    speech-to-speech at a vendor who has just launched an Indic model, is a
    decision somebody makes on a Tuesday afternoon and should not require an
    engineer, a release, or a restart of every worker.

    So the mapping is a row. Resolution order is **this table, then the
    environment variable, then the compiled default** — the table wins because
    it is the deliberate choice made through an audited screen, and a setting
    that silently loses to an environment variable somebody set months ago is
    exactly the "I changed it and nothing happened" failure the rate card just
    had. The screen reports when an environment variable is also present, so
    the precedence is visible rather than surprising.

    Overwritten in place rather than effective-dated, unlike a rate. A rate is
    a fact about money that an old invoice has to be able to reproduce; this is
    a fact about which vendor answers the phone today, and last month's calls
    already recorded the model they actually ran on.
    """

    __tablename__ = "managed_tier_mappings"

    id = Column(Integer, primary_key=True, index=True)
    # "llm" | "stt" | "tts" | "realtime" | "embeddings" — the same vocabulary
    # managed_tiers uses, deliberately not a Postgres enum so a new component
    # is application code rather than a migration.
    component = Column(String(32), nullable=False)
    tier = Column(String(32), nullable=False)
    # The vendor as the service factory names it, and the model string passed
    # straight through to that vendor. Stored together because moving one
    # without the other is almost always a mistake — a model name is only
    # meaningful to the vendor that serves it.
    provider = Column(String(64), nullable=False)
    model = Column(String(128), nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint("component", "tier", name="uq_managed_tier_mapping"),
    )


class PlatformModelModel(Base):
    """A model Decibyl offers on its own keys — the managed catalogue.

    The offering used to be implied rather than stated: whatever the registry
    could build, crossed with whatever a tier happened to point at, crossed with
    whatever had a rate row. Three sources, no single answer to "what do we
    sell", and a customer's model picker showed every vendor the codebase had
    ever integrated whether or not we held a key for it or had priced it.

    This is the answer. An operator installs a provider key, reads the models
    that vendor actually serves, and ticks the ones we offer. Those rows are the
    catalogue: what the rate card asks a price for, what a bundle can name, and
    what a customer sees.

    **A row here is a commitment, not a capability.** It says we hold the key,
    we have priced it, and a customer may choose it. Deleting one withdraws it
    from sale without touching any agent already built — a stored configuration
    names a tier or a model string, and resolution is unchanged.

    The rate lives in ``provider_rates``, keyed on the same
    ``(component, provider, model)``, rather than being duplicated here. One
    price per model, in the table that already effective-dates it.
    """

    __tablename__ = "platform_models"

    id = Column(Integer, primary_key=True, index=True)
    # stt | llm | tts | realtime | embeddings — the slot this model fills.
    component = Column(String(24), nullable=False)
    provider = Column(String(64), nullable=False)
    #: The vendor's own model id, exactly as the service factory will send it.
    #: Empty is not allowed: a provider-wide entry would be a promise we cannot
    #: price, since a rate row keyed on "" is the fallback and not a model.
    model = Column(String(128), nullable=False)

    #: What the customer reads. Defaults to the model id; worth setting to
    #: something a clinic owner recognises.
    label = Column(String(120), nullable=False, default="", server_default="")

    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        # One row per model per slot. A vendor serving two components — Sarvam
        # does speech at both ends — gets one row for each, because they are
        # priced separately and chosen separately.
        Index(
            "uq_platform_models_slot",
            "component",
            "provider",
            "model",
            unique=True,
        ),
    )


class SubscriptionPlanModel(Base):
    """A plan: one monthly price that buys a call balance and some numbers.

    The ₹2,999 starter plan existed as three constants and a hardcoded route.
    That works for exactly one plan and stops working the moment somebody wants
    a second, which is what this table is for — a plan is now a row an operator
    writes, priced and named without a release, exactly as the rate card and the
    model bundles already are.

    **Every figure is net of GST**, like everything else the ledger touches. The
    customer is charged the grossed-up amount at the bank, computed per account
    against their own billing profile — see ``mandates.create_plan_mandate``.

    ``price_paise`` is stored rather than derived from its parts. A plan is a
    commercial object: ₹2,999 for ₹2,500 of balance and a ₹499 number is a
    deliberate ₹0 margin on the parts and a bet on the balance being spent at a
    markup, and a later plan may discount differently. What is *not* allowed is
    a price below the balance it grants — that is selling a rupee for less than
    a rupee, and ``subscription_plans.save`` refuses it rather than trusting
    whoever typed it.

    ``included_numbers`` is the entitlement, and it is load-bearing. Before it
    existed, a plan mandate authorised *every* number an account provisioned —
    ``_mandate_collects`` returned true for all of them, so the monthly job
    skipped them all, while ``record_mandate_collection`` settled only the
    lowest-numbered charge. Numbers two and beyond were free for ever, and
    nothing anywhere read as an error.
    """

    __tablename__ = "subscription_plans"

    id = Column(Integer, primary_key=True, index=True)
    #: Stable identifier used by the mandate, the ledger note and the API.
    #: Never renamed once a customer is on it: it is what a collection is
    #: reconciled against months later.
    code = Column(String(32), nullable=False, unique=True)
    label = Column(String(80), nullable=False)
    blurb = Column(Text, nullable=False, default="", server_default="")

    #: Net of GST, per period.
    price_paise = Column(BigInteger, nullable=False)
    #: Call balance granted when a cycle is collected.
    balance_paise = Column(BigInteger, nullable=False, default=0, server_default="0")
    #: How many numbers the price covers. The next one bills separately.
    included_numbers = Column(Integer, nullable=False, default=0, server_default="0")
    #: What a number beyond the entitlement costs per month. Null falls back to
    #: NUMBER_RENTAL_PRICE_PAISE, so a plan that does not want its own figure
    #: follows the platform rental price rather than pinning a stale copy.
    extra_number_price_paise = Column(BigInteger, nullable=True)
    #: How much the plan lets an account keep in its knowledge base, in bytes.
    #:
    #: An entitlement rather than a meter, which is what the category does:
    #: ElevenLabs sells RAG as a per-tier allowance and Vapi folds it into the
    #: plan. Nobody charges per embedded token, and being the only platform
    #: that does would cost more to build than it could recover.
    #:
    #: Zero is the meaningful default, and it is why this is a plan column
    #: rather than a global ceiling: every document is embedded at ingestion on
    #: *our* model key, and embeddings have no cost component, no rate and no
    #: ledger debit anywhere. An account with no plan can therefore spend our
    #: money without ever being billed for it, and did — the only thing
    #: standing in the way was one environment variable set the same for
    #: everyone, subscriber or not.
    knowledge_base_bytes = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    #: The largest single document the plan accepts, in bytes.
    #:
    #: A separate number from the total because they fail differently. The
    #: total is about the standing cost of holding and re-embedding a corpus;
    #: this one is about one upload's worth of worker time, memory and disk —
    #: a 200MB scan is a stalled queue whatever the account's total allowance
    #: is. Tiering both lets a larger plan take bigger manuals as well as more
    #: of them, which is the actual difference between a shop's FAQ and an
    #: insurer's policy library.
    #:
    #: Zero falls back to KNOWLEDGE_BASE_MAX_FILE_SIZE_BYTES, the deployment
    #: ceiling, so a plan with no opinion follows the platform rather than
    #: pinning a copy that goes stale the day it moves.
    knowledge_base_max_file_bytes = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    #: The per-minute platform fee this plan entitles an account to, in
    #: millipaise. Applied to ``organization_rate_history`` the moment the
    #: mandate is authorised — see ``mandates.apply_subscription_event``.
    #:
    #: Null means the plan says nothing about the fee, and the account keeps
    #: whatever rate it already had: a negotiated one, or the list price. That
    #: is deliberately different from zero, which would be a plan that gives
    #: away the fee entirely. Only a plan with an explicit figure moves anyone,
    #: so pricing a tier later cannot silently re-rate the accounts on it.
    #:
    #: The fee is what a larger plan actually discounts. Balance and numbers are
    #: cheap to be generous with; this is the number a customer compares against
    #: a competitor, and the one they move tier to change.
    platform_rate_mpaise = Column(Integer, nullable=True)

    #: The provider plan this subscribes to. Pinned per plan for the same
    #: reason the rental plan is pinned: a plan created lazily per environment
    #: fragments the provider's own reporting into pieces nobody can rejoin.
    #:
    #: Created at the **gross**: a pinned plan's amount is what the bank
    #: collects and nothing in this codebase can change it, so a plan created
    #: at the net figure collects no GST at all, monthly, by standing
    #: instruction.
    razorpay_plan_id = Column(String(64), nullable=True)
    #: The same plan for an account whose supply is zero-rated — outside India
    #: with an LUT on file. Created at the **net** figure, because that is the
    #: whole of what such an account owes.
    #:
    #: A second row rather than a computation, because a pinned plan is an
    #: object at the provider and there is no arithmetic that can turn one
    #: amount into another once the bank holds the instruction. Null until
    #: somebody creates it, and an export account cannot subscribe until they
    #: do — a visible refusal rather than an invisible 18% overcharge.
    razorpay_plan_id_export = Column(String(64), nullable=True)

    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class PaymentMandateModel(Base):
    """A customer's standing authorisation for us to collect, on file.

    Distinct from ``PaymentModel``, which is one collected top-up. A mandate is
    the *permission* — a UPI Autopay / eNACH / card authorisation held at the
    provider — and it has a life of its own: it is created before any money
    moves, it can be revoked by the customer's bank without telling us first,
    and it survives every individual collection made under it.

    It exists because a monthly number rental collected out of a prepaid
    balance stops the moment the balance does, and the failure mode is a number
    we keep paying a carrier for. With a mandate the collection is pushed to the
    customer's bank on a schedule instead of pulled from a balance they may have
    forgotten to top up.

    ``status`` mirrors the provider's own subscription states rather than a
    private vocabulary, so a row can be reconciled against their dashboard
    without a translation table. Only the states in
    :meth:`MandateStatus.authorised` mean we may hand over a number.
    """

    __tablename__ = "payment_mandates"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    provider = Column(
        String(32), nullable=False, default="razorpay", server_default="razorpay"
    )
    # What the mandate is for. Today only number_rental, held as a string so a
    # second recurring product does not need a migration to coexist with it.
    purpose = Column(
        String(32),
        nullable=False,
        default="number_rental",
        server_default="number_rental",
    )

    # The provider's identifiers. subscription_id is what every webhook keys
    # off, so it is the one that must be unique.
    subscription_id = Column(String(64), nullable=True)
    plan_id = Column(String(64), nullable=True)
    customer_id = Column(String(64), nullable=True)

    status = Column(
        String(24), nullable=False, default="created", server_default="created"
    )
    # Where the customer authorises. Provider-hosted, short-lived, and the only
    # thing the purchase flow needs to hand back to the browser.
    short_url = Column(Text, nullable=True)

    price_paise = Column(BigInteger, nullable=False, default=0, server_default="0")

    #: Which plan this mandate bought, for a ``starter_plan`` mandate. Null for
    #: a bare number rental, and null for the plan mandates that predate the
    #: plans table — those are the single hardcoded starter plan and resolve to
    #: it by default, which is why this is nullable rather than backfilled to a
    #: code that might not be the one they signed up on.
    plan_code = Column(String(32), nullable=True)

    authorised_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    last_charged_at = Column(DateTime(timezone=True), nullable=True)
    last_failure_reason = Column(Text, nullable=True)

    provider_payload = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    organization = relationship("OrganizationModel")

    __table_args__ = (
        Index("ix_payment_mandates_org", "organization_id", "status"),
        # One provider subscription maps to exactly one row. Webhooks arrive at
        # least once and out of order; without this a redelivery during a race
        # writes a second row and half the events then update the wrong one.
        Index(
            "uq_payment_mandates_subscription",
            "provider",
            "subscription_id",
            unique=True,
            postgresql_where=text("subscription_id IS NOT NULL"),
        ),
        # One live mandate per account per purpose. A customer who abandons an
        # authorisation and starts again must not end up with two banks
        # collecting for the same number.
        Index(
            "uq_payment_mandates_live",
            "organization_id",
            "purpose",
            unique=True,
            postgresql_where=text(
                "status NOT IN ('cancelled', 'completed', 'expired')"
            ),
        ),
    )


class CreditLedgerModel(Base):
    """Append-only credit ledger. Balance is derived, never edited in place.

    ``balance_after_paise`` is the running balance at the time the row was
    written, so a statement can be rendered without replaying the whole table.
    """

    __tablename__ = "credit_ledger"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # Positive credits the account, negative debits it.
    delta_paise = Column(BigInteger, nullable=False)
    # topup | usage | adjustment | trial — see CreditLedgerKind.
    kind = Column(String(16), nullable=False)
    # What caused this entry, e.g. ("workflow_run", 1234).
    ref_type = Column(String(32), nullable=True)
    ref_id = Column(String(64), nullable=True)
    balance_after_paise = Column(BigInteger, nullable=False)
    note = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    organization = relationship("OrganizationModel")
    created_by_user = relationship("UserModel")

    __table_args__ = (
        Index("ix_credit_ledger_org_created", "organization_id", "created_at"),
        # A completed run must debit the ledger at most once, even if the
        # completion task is retried.
        Index(
            "uq_credit_ledger_usage_ref",
            "organization_id",
            "ref_type",
            "ref_id",
            unique=True,
            postgresql_where=text("kind = 'usage' AND ref_id IS NOT NULL"),
        ),
        # And a run may hold funds at most once. A retried start that stacked a
        # second hold would take an account's spending power away twice for one
        # call, and only the first would ever be released.
        Index(
            "uq_credit_ledger_reservation_ref",
            "organization_id",
            "ref_type",
            "ref_id",
            unique=True,
            postgresql_where=text("kind = 'reservation' AND ref_id IS NOT NULL"),
        ),
        # And a plan cycle grants its balance at most once. Razorpay delivers
        # webhooks at least once, so without this a redelivered
        # `subscription.charged` would hand out a second Rs2,500 — real money,
        # to an account that paid for it once. The ref is the provider's
        # payment id, which is identical on every redelivery of the same
        # collection; keying off the period instead would not work, because
        # recording a collection advances the period.
        Index(
            "uq_credit_ledger_plan_ref",
            "organization_id",
            "ref_type",
            "ref_id",
            unique=True,
            postgresql_where=text("kind = 'plan' AND ref_id IS NOT NULL"),
        ),
        # A cycle's balance expires once. ``ref_id`` is the grant being
        # retired, so the daily sweep and the next collection can both reach
        # the same grant and only one debit lands — which matters because the
        # two run on different schedules and will race on a renewal that
        # arrives late.
        Index(
            "uq_credit_ledger_plan_expiry_ref",
            "organization_id",
            "ref_type",
            "ref_id",
            unique=True,
            postgresql_where=text("kind = 'plan_expiry' AND ref_id IS NOT NULL"),
        ),
        # One signup bonus per organization, ever. Two requests racing during
        # signup would both find no bonus and both grant one, so this is
        # enforced here rather than by the check in application code that the
        # race defeats.
        Index(
            "uq_credit_ledger_signup_bonus",
            "organization_id",
            unique=True,
            postgresql_where=text("kind = 'trial' AND ref_type = 'signup_bonus'"),
        ),
        # The sweeper scans open reservations by age; without this it reads
        # every ledger row on the platform every few minutes.
        Index(
            "ix_credit_ledger_reservation_open",
            "created_at",
            postgresql_where=text("kind = 'reservation'"),
        ),
        # A captured payment credits at most once. `handle_webhook` takes a row
        # lock on the payment before crediting, which is what makes the ordinary
        # retry a no-op; this is the backstop for the case the lock cannot cover
        # — two deliveries handled by different API processes against different
        # sessions, where the application-level check is the only thing between
        # them and a second credit. Razorpay delivers at least once, so this is
        # an expected event rather than a defensive one.
        Index(
            "uq_credit_ledger_topup_ref",
            "organization_id",
            "ref_type",
            "ref_id",
            unique=True,
            postgresql_where=text("kind = 'topup' AND ref_id IS NOT NULL"),
        ),
        # A rental period debits at most once. recurring_charge_periods already
        # refuses a duplicate period row, but the debit and that row are two
        # writes: this is what makes the ledger side safe on its own, so a
        # retry that got as far as the debit cannot charge twice.
        Index(
            "uq_credit_ledger_rental_ref",
            "organization_id",
            "ref_type",
            "ref_id",
            unique=True,
            postgresql_where=text("kind = 'rental' AND ref_id IS NOT NULL"),
        ),
        # A document's ingestion embeddings debit at most once, even if the
        # ARQ job is retried after crashing between the vendor call and this
        # write.
        Index(
            "uq_credit_ledger_embedding_ingest_ref",
            "organization_id",
            "ref_type",
            "ref_id",
            unique=True,
            postgresql_where=text("kind = 'embedding_ingest' AND ref_id IS NOT NULL"),
        ),
    )


class DailyOrganizationRollupModel(Base):
    """Pre-aggregated per-account, per-day figures backing the dashboard.

    Dashboard pages read from here rather than scanning workflow_runs, which is
    what keeps them inside the performance budget at a million calls.

    ``day`` is an **IST calendar day**, not a UTC one. Timestamps are stored in
    UTC everywhere else, but bucketing by UTC day would split an Indian working
    day across two rows and make every number look wrong to us.
    """

    __tablename__ = "daily_organization_rollup"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    day = Column(Date, nullable=False)

    calls = Column(Integer, nullable=False, default=0)
    answered_calls = Column(Integer, nullable=False, default=0)
    completed_calls = Column(Integer, nullable=False, default=0)
    billable_seconds = Column(BigInteger, nullable=False, default=0)
    billable_minutes = Column(BigInteger, nullable=False, default=0)

    provider_cost_paise = Column(BigInteger, nullable=False, default=0)
    charged_paise = Column(BigInteger, nullable=False, default=0)
    # charged_paise - provider_cost_paise, stored so margin sorting is indexable.
    margin_paise = Column(BigInteger, nullable=False, default=0)

    refreshed_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    organization = relationship("OrganizationModel")

    __table_args__ = (
        UniqueConstraint("organization_id", "day", name="uq_daily_rollup_org_day"),
        Index("ix_daily_rollup_day", "day"),
        Index("ix_daily_rollup_org_day", "organization_id", "day"),
    )


class PlatformProviderCredentialModel(Base):
    """Decibyl's own API key for a model provider.

    These are the keys that make an account "managed": a customer who does not
    bring their own OpenAI or Deepgram key runs on ours, and the usage is
    metered and passed through at cost on their receipt.

    The key is stored **encrypted** (Fernet, keyed by PLATFORM_CREDENTIAL_SECRET)
    rather than as plaintext JSON like the per-organization configuration store.
    A leak of this table is a leak of every customer's inference capacity billed
    to us, which is a materially different blast radius from one tenant's own
    key, so it does not share that storage.

    Scoped by (provider, component) rather than provider alone: the same vendor
    can serve two components on separate keys and separate billing accounts —
    Sarvam does STT and TTS, and an operator may well want those split.
    """

    __tablename__ = "platform_provider_credentials"

    id = Column(Integer, primary_key=True, index=True)
    # stt | llm | tts — see CostComponent. Telephony credentials are not here;
    # they live on telephony_configurations, which already models per-account
    # carrier accounts.
    component = Column(String(16), nullable=False)
    provider = Column(String(64), nullable=False)
    # Ciphertext. Never returned by any endpoint — see `masked_key`.
    encrypted_key = Column(Text, nullable=False)
    # Last four characters of the plaintext, kept so an operator can tell which
    # key is installed without the key being readable.
    key_last_four = Column(String(8), nullable=False)
    label = Column(String(128), nullable=True)
    # Off by default is wrong here — a key that has been entered is meant to be
    # used. Deactivating is how you take a provider out of service without
    # deleting the audit trail.
    is_active = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    set_by_user = relationship("UserModel")

    __table_args__ = (
        UniqueConstraint(
            "component", "provider", name="uq_platform_provider_credential"
        ),
        Index("ix_platform_provider_credentials_lookup", "component", "provider"),
    )


class OrganizationProviderCredentialModel(Base):
    """A customer's own API key for a model provider — the BYOK vault.

    The mirror of ``platform_provider_credentials``: same shape, same
    encryption, scoped to one organization. Together they are the two key
    sources a stack can draw on, and keeping them structurally identical is
    what lets a single slot be flipped between managed and BYOK without the
    pipeline knowing which it got.

    **Why keys moved out of the configuration JSON.** They used to live inline
    in ``organization_configurations`` alongside model choices, as plaintext
    within a JSON blob. That had three consequences worth naming, because each
    one shaped a piece of the old UI:

    1. A key could only be entered where a model was being chosen, so every
       model screen grew API-key fields and the two concerns became
       inseparable — you could not store a key you were not immediately using.
    2. Switching a slot from one provider to another discarded the key you had
       pasted for the first, because it lived in the branch of the JSON you
       navigated away from.
    3. Tenant keys sat in plaintext while ours sat encrypted, which is backwards
       — a customer's key is *their* liability and deserves at least the care we
       give our own.

    Scoped by (organization, component, provider) for the same reason the
    platform table is: one vendor can serve two components on separate keys and
    separate billing accounts, and Sarvam doing both STT and TTS is the case in
    front of us.
    """

    __tablename__ = "organization_provider_credentials"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # stt | llm | tts — see CostComponent. Telephony is deliberately absent:
    # carrier credentials live on telephony_configurations, which already models
    # per-account carrier accounts and the KYC that goes with them.
    component = Column(String(16), nullable=False)
    provider = Column(String(64), nullable=False)
    # Ciphertext, Fernet, keyed by PLATFORM_CREDENTIAL_SECRET — the same secret
    # that protects our own keys. Never returned by any endpoint.
    encrypted_key = Column(Text, nullable=False)
    # Last four characters of the plaintext, so a customer can tell which key is
    # installed without it being readable back.
    key_last_four = Column(String(8), nullable=False)
    label = Column(String(128), nullable=True)
    # A key that has been entered is meant to be used. Deactivating is how you
    # take a provider out of service without deleting it.
    is_active = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    organization = relationship("OrganizationModel")
    set_by_user = relationship("UserModel")

    #: One row per account, component and provider. The unique constraint is
    #: also the lookup index — Postgres backs it with a btree on exactly these
    #: three columns in this order, which is the order every read here uses. A
    #: second index on the same tuple was carried alongside it for a while and
    #: served no query the constraint's own index did not: it only cost a write
    #: on every key rotation.
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "component",
            "provider",
            name="uq_organization_provider_credential",
        ),
    )


class GoogleCalendarConnectionModel(Base):
    """An organization's OAuth grant to create events on one Google Calendar.

    One row per organization — connecting a second account replaces this one
    rather than adding a row, because a workflow's ``google_calendar`` tool
    has no way to choose between two connected calendars and offering the
    choice with no way to act on it would be worse than not offering it.

    Both tokens are Fernet-encrypted under ``PLATFORM_CREDENTIAL_SECRET``, the
    same secret and the same at-rest posture as ``organization_provider_credentials``
    — a Calendar refresh token is a standing grant to read and write someone's
    calendar indefinitely, which is at least as sensitive as a model provider
    key. Never returned by any endpoint; ``connected_email`` exists so the
    owning organization can see *which* Google account is connected without
    the tokens themselves ever leaving the database.

    ``access_token``/``access_token_expires_at`` are a cache: Google's access
    tokens last about an hour, and refreshing on every call would mean every
    tool invocation pays a synchronous round trip to Google's token endpoint
    before it can do the one that actually creates the event. The refresh
    token is the durable credential; the access token is reconstructible from
    it at any time and is only stored to avoid that extra hop.
    """

    __tablename__ = "google_calendar_connections"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Ciphertext, Fernet, keyed by PLATFORM_CREDENTIAL_SECRET. Never returned
    # by any endpoint. This is the durable grant — it does not expire on
    # Google's side until the organization disconnects or Google revokes it.
    encrypted_refresh_token = Column(Text, nullable=False)

    # Short-lived cache of the current access token, also encrypted — it is
    # exactly as sensitive as the refresh token for the hour it is valid.
    encrypted_access_token = Column(Text, nullable=True)
    access_token_expires_at = Column(DateTime(timezone=True), nullable=True)

    # The Google account email and calendar id this grant covers, so the
    # "Connected as ..." line in the UI needs no token decryption to render.
    connected_email = Column(String(320), nullable=True)
    calendar_id = Column(
        String(255), nullable=False, default="primary", server_default="primary"
    )

    is_active = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    connected_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    organization = relationship("OrganizationModel")
    connected_by_user = relationship("UserModel")

    #: One connection per organization. See the class docstring — a second
    #: `Connect` replaces this row rather than adding one.
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_google_calendar_connection_org"),
    )


class PaymentModel(Base):
    """One attempt by an account to buy credit.

    Exists so a webhook can be made idempotent and a payment reconciled against
    what it actually credited. Razorpay retries webhooks — at least once, not
    exactly once — so without a row to check against, a retry would credit the
    account twice.

    The row is created when the order is, before the customer has paid, and
    moves to ``paid`` only when a **signature-verified** webhook says so. A
    client reporting success is never enough: the browser is not a trusted
    party in a payment flow.
    """

    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    provider = Column(
        String(32),
        nullable=False,
        default="razorpay",
        server_default=text("'razorpay'"),
    )
    # The order we asked the provider to create. Unique: one row per order.
    order_id = Column(String(64), nullable=False)
    # Set when the payment succeeds. Unique when present, which is what makes a
    # duplicate webhook a no-op rather than a second credit.
    payment_id = Column(String(64), nullable=True)
    # Credit bought, net of GST. This is what reaches the ledger, and the ledger
    # is GST-exclusive throughout — see services/billing/tax.py.
    amount_paise = Column(BigInteger, nullable=False)
    # What the customer is actually charged: amount_paise plus tax. The webhook
    # checks the payload against *this*, because this is what Razorpay collected.
    # Equal to amount_paise for a zero-rated export.
    gross_paise = Column(BigInteger, nullable=True)
    # Tax within gross_paise, split for the receipt voucher. Nullable because
    # payments taken before GST existed have no split to record, which is not
    # the same as a split of zero.
    cgst_paise = Column(BigInteger, nullable=True)
    sgst_paise = Column(BigInteger, nullable=True)
    igst_paise = Column(BigInteger, nullable=True)
    # created | paid | failed
    status = Column(
        String(16), nullable=False, default="created", server_default=text("'created'")
    )
    # The ledger row this produced, so a payment and its credit can be walked
    # in either direction during a reconciliation.
    credit_ledger_id = Column(
        Integer, ForeignKey("credit_ledger.id", ondelete="SET NULL"), nullable=True
    )
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    paid_at = Column(DateTime(timezone=True), nullable=True)
    # The verified webhook payload, for disputes. Kept because a customer
    # querying a charge six months on is answered by what the provider sent,
    # not by our summary of it.
    provider_payload = Column(JSON, nullable=True)

    organization = relationship("OrganizationModel")
    created_by_user = relationship("UserModel")

    __table_args__ = (
        UniqueConstraint("provider", "order_id", name="uq_payments_provider_order"),
        # Partial unique index: at most one row per settled payment id, which is
        # the database-level guard behind webhook idempotency. NULLs are exempt
        # so unpaid orders do not collide.
        Index(
            "uq_payments_provider_payment",
            "provider",
            "payment_id",
            unique=True,
            postgresql_where=text("payment_id IS NOT NULL"),
        ),
        Index("ix_payments_org_created", "organization_id", "created_at"),
    )


class BillingAuditLogModel(Base):
    """Who changed a rate or moved credit, when, and from what to what."""

    __tablename__ = "billing_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # platform_rate_changed | credit_adjusted | provider_rate_changed
    action = Column(String(48), nullable=False)
    old_value = Column(JSON, nullable=False, default=dict)
    new_value = Column(JSON, nullable=False, default=dict)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    organization = relationship("OrganizationModel")
    actor = relationship("UserModel")

    __table_args__ = (
        Index("ix_billing_audit_org_created", "organization_id", "created_at"),
        Index("ix_billing_audit_action", "action"),
    )


class ManagedMarkupHistoryModel(Base):
    """Effective-dated history of the multiple charged on managed model usage.

    Never updated in place, for the same reason ``OrganizationRateHistoryModel``
    is not: re-costing a call from March has to reproduce what that customer was
    actually charged, and a mutable setting makes that impossible the first time
    anyone edits it.

    One row, globally — this is not per account. An account that negotiated a
    different multiple gets it through its own rate history, not by moving the
    number every other account pays.
    """

    __tablename__ = "managed_markup_history"

    id = Column(Integer, primary_key=True, index=True)
    #: Basis points. 10000 is at cost; 14000 charges 1.4x what the vendor did.
    markup_bps = Column(Integer, nullable=False)
    effective_from = Column(DateTime(timezone=True), nullable=False)
    #: NULL means "still in effect". At most one open row, by index below.
    effective_to = Column(DateTime(timezone=True), nullable=True)
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    set_by_user = relationship("UserModel")

    __table_args__ = (
        Index("ix_managed_markup_effective", "effective_from"),
        # There is exactly one markup in force at a time. Enforced here rather
        # than in application code because two concurrent saves would otherwise
        # both close the old row and both open a new one.
        Index(
            "uq_managed_markup_open",
            "effective_to",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
    )


class ManagedMarkupOverrideModel(Base):
    """Effective-dated markup override for one ``(component, provider, model)``.

    ``ManagedMarkupHistoryModel`` sets one multiple for every managed line on
    every account. This table narrows that for a specific line — a model that
    is unusually cheap or expensive to us relative to what the blanket markup
    would charge for it — without touching what anything else bills. Absence
    of a row here is the normal case; most lines are priced by the global
    multiple.

    Keyed exactly like ``ProviderRateModel``: ``model = ""`` is a provider-wide
    override (every model from that vendor, for that component), and a
    model-specific row wins over it when both exist. Same reasoning — rates
    (and here, the multiple on them) differ by more than the provider name
    alone.

    Never updated in place, for the same re-costing reason every other rate
    table here follows: recomputing an old call has to reproduce the multiple
    that was actually charged, not today's.

    No OTP confirmation, unlike the global markup: a single-line override
    cannot move every account's bill at once the way the global value can, so
    it follows the same admin-only write pattern as a provider rate edit
    rather than the two-factor ceremony reserved for the blanket multiple.
    """

    __tablename__ = "managed_markup_overrides"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(64), nullable=False)
    # Empty string means "every model from this provider, for this component"
    # — the provider-wide fallback. Not nullable, for the same reason
    # ProviderRateModel.model is not: a partial unique index over a nullable
    # column would let duplicate open fallbacks through.
    model = Column(String(128), nullable=False, server_default="", default="")
    # stt | llm | tts | telephony — see CostComponent. LLM is the case this
    # was built for, but the key shape is component-general like the rate
    # card itself, rather than assuming only LLM will ever need one.
    component = Column(String(16), nullable=False)
    #: Basis points, same scale as ManagedMarkupHistoryModel.markup_bps.
    #: 10000 is at cost.
    markup_bps = Column(Integer, nullable=False)
    effective_from = Column(DateTime(timezone=True), nullable=False)
    #: NULL means "still in effect".
    effective_to = Column(DateTime(timezone=True), nullable=True)
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    set_by_user = relationship("UserModel")

    __table_args__ = (
        Index(
            "ix_managed_markup_overrides_lookup",
            "provider",
            "component",
            "model",
            "effective_from",
        ),
        # One open override per (provider, component, model), exactly the
        # ProviderRateModel pattern — a provider-wide fallback and a
        # model-specific override can both be open at once.
        Index(
            "uq_managed_markup_overrides_open",
            "provider",
            "component",
            "model",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
    )


class MarkupChangeChallengeModel(Base):
    """A pending markup change, waiting on a code from the inbox.

    The proposed value lives **here**, server-side, not in the confirming
    request. The request carries only the code — so someone who intercepts or
    replays it cannot swap in a different multiple, and the number that applies
    is always the one that was emailed about.

    One row at a time: requesting a new change replaces any pending one, which
    is also what makes an abandoned change harmless.
    """

    __tablename__ = "markup_change_challenges"

    id = Column(Integer, primary_key=True, index=True)
    #: What would be applied on a correct code.
    markup_bps = Column(Integer, nullable=False)
    #: Recorded so the confirmation email and the audit row can say what moved,
    #: even if something else changed the live value in between.
    previous_markup_bps = Column(Integer, nullable=False)
    note = Column(Text, nullable=True)

    #: Salted SHA-256, never the code itself — same construction as the email
    #: verification challenge, compared with hmac.compare_digest.
    code_hash = Column(String(64), nullable=False)
    code_salt = Column(String(32), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    requested_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    requested_by_user = relationship("UserModel")


class BillingProfileModel(Base):
    """Who an account is, for tax purposes.

    Separate from the organization because it answers a different question. The
    organization is who logs in; this is who the invoice is made out to, and the
    two are routinely different — an agency logs in, its holding company is
    billed.

    ``state_code`` is the consequential field: for a domestic customer it
    decides CGST+SGST versus IGST, so a wrong value means the right total split
    the wrong way, which is a filing correction rather than a display bug.
    """

    __tablename__ = "billing_profiles"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Name on the invoice. Not the organization's display name — that is a
    # workspace label people rename freely, and an invoice cannot be.
    legal_name = Column(String(256), nullable=True)
    # Null for an unregistered customer, who is still billed and still invoiced.
    gstin = Column(String(15), nullable=True)

    address_line1 = Column(String(256), nullable=True)
    address_line2 = Column(String(256), nullable=True)
    city = Column(String(128), nullable=True)
    # Two-digit GST state code, not a name. "29", never "Karnataka" — a name
    # cannot be compared against the supplier's state without a lookup table
    # that would then be a second place to get it wrong.
    state_code = Column(String(2), nullable=True)
    postal_code = Column(String(16), nullable=True)
    # ISO 3166-1 alpha-2. Anything other than IN is an export.
    country_code = Column(
        String(2), nullable=False, default="IN", server_default=text("'IN'")
    )

    # Where documents are sent, when that is not the account owner's address.
    billing_email = Column(String(320), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    organization = relationship("OrganizationModel")


class DocumentSequenceModel(Base):
    """The last number issued in a document series.

    GST requires a consecutive serial, unique within a financial year, with no
    gaps. A gap has to be explained; two documents sharing a number is worse.
    So numbers come from here under a row lock rather than from a count of
    existing documents, which races, or from a database sequence, which is
    explicitly allowed to skip.
    """

    __tablename__ = "document_sequences"

    id = Column(Integer, primary_key=True, index=True)
    # tax_invoice | receipt_voucher
    kind = Column(String(24), nullable=False)
    # Indian financial year, April to March, as "26-27".
    financial_year = Column(String(5), nullable=False)
    last_number = Column(Integer, nullable=False, default=0, server_default=text("0"))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint("kind", "financial_year", name="uq_document_sequence"),
    )


class TaxDocumentModel(Base):
    """A receipt voucher or a tax invoice, as issued.

    Two documents because GST requires two. A prepaid top-up is an advance, and
    for services the time of supply is the earlier of invoice or payment — so
    tax falls due when the money arrives, evidenced by a **receipt voucher**.
    The **tax invoice** follows when the service is actually supplied, monthly,
    against measured usage, and adjusts the advance already taxed.

    Every figure is stored, never derived at render time. An issued document is
    a statement about a moment: the customer's address, our address, the rate
    and the split were all what they were on the day. Recomputing any of it
    from current data would silently rewrite history the first time a customer
    moves office or a rate changes.
    """

    __tablename__ = "tax_documents"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # tax_invoice | receipt_voucher
    kind = Column(String(24), nullable=False)
    # The serial as printed, e.g. "INV/26-27/000001".
    number = Column(String(32), nullable=False)
    financial_year = Column(String(5), nullable=False)
    issued_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Billing period, for an invoice. Null on a receipt voucher, which covers a
    # payment rather than a stretch of time.
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)

    taxable_paise = Column(BigInteger, nullable=False)
    cgst_paise = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    sgst_paise = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    igst_paise = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    total_paise = Column(BigInteger, nullable=False)
    # intra_state | inter_state | export
    supply_type = Column(String(16), nullable=False)
    place_of_supply = Column(String(8), nullable=True)
    rate_basis_points = Column(Integer, nullable=False)

    # Frozen copies of both parties and the line items, as of issue.
    supplier_snapshot = Column(JSON, nullable=False, default=dict)
    customer_snapshot = Column(JSON, nullable=False, default=dict)
    line_items = Column(JSON, nullable=False, default=list)

    # The payment a receipt voucher acknowledges.
    payment_id = Column(
        Integer, ForeignKey("payments.id", ondelete="SET NULL"), nullable=True
    )
    # The provider's payment id, for money collected under an autopay mandate.
    #
    # A mandate collection has no ``payments`` row to point at: that table is
    # one attempt by an account to *buy credit*, and a bank paying a standing
    # instruction is not that. The provider's id is the right key anyway — it is
    # identical on every redelivery of the same collection, which is the
    # property that makes at-least-once webhook delivery safe, and it is what
    # every other idempotency guard on this path already keys off.
    provider_payment_id = Column(String(64), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    organization = relationship("OrganizationModel")
    payment = relationship("PaymentModel")

    __table_args__ = (
        # A number identifies exactly one document. The series is per financial
        # year, so the year is part of the key.
        UniqueConstraint(
            "kind", "financial_year", "number", name="uq_tax_document_number"
        ),
        # One invoice per account per period. Without this a retried monthly run
        # issues a second invoice for a month already invoiced, and a duplicate
        # invoice is a filing correction rather than a deletion.
        Index(
            "uq_tax_invoice_period",
            "organization_id",
            "kind",
            "period_start",
            unique=True,
            postgresql_where=text("kind = 'tax_invoice' AND period_start IS NOT NULL"),
        ),
        # One receipt voucher per payment.
        Index(
            "uq_receipt_voucher_payment",
            "payment_id",
            unique=True,
            postgresql_where=text(
                "kind = 'receipt_voucher' AND payment_id IS NOT NULL"
            ),
        ),
        # And one per mandate collection. Separate from the index above because
        # the two name different things: a top-up we created an order for, and
        # money a bank moved on a standing instruction. A collection redelivered
        # by the provider must not produce a second document — a duplicate
        # voucher is a filing correction, not a deletion.
        Index(
            "uq_receipt_voucher_collection",
            "provider_payment_id",
            unique=True,
            postgresql_where=text(
                "kind = 'receipt_voucher' AND provider_payment_id IS NOT NULL"
            ),
        ),
        Index("ix_tax_documents_org_issued", "organization_id", "issued_at"),
    )


class DataRetentionPolicyModel(Base):
    """How long one organization's call data is kept.

    Storage limitation is the one privacy obligation that cannot be satisfied by
    a document: DPDP s8(7) and GDPR Art 5(1)(e) both require that personal data
    stop existing once its purpose is served, and only a job that deletes things
    can do that.

    Per organization because the right answer differs by customer — a clinic
    under a records-retention rule and a lead-generation campaign have opposite
    needs, and a single platform-wide number would be wrong for both. Absent a
    row, the platform default applies.

    Note what is *not* covered here: billing figures. Those are retained for the
    statutory period regardless, because GST records must survive long after the
    conversation they describe should not. See services/privacy/retention.py.
    """

    __tablename__ = "data_retention_policies"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Days to keep call recordings (audio). Null means the platform default;
    # 0 means never store them at all.
    recording_retention_days = Column(Integer, nullable=True)
    # Days to keep transcripts and the context gathered during a call. Usually
    # longer than audio: text is far less sensitive and far more useful.
    transcript_retention_days = Column(Integer, nullable=True)

    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    organization = relationship("OrganizationModel")


class DataAccessLogModel(Base):
    """Who opened a recording or transcript, and when.

    Two questions this exists to answer, both of which arrive at the worst
    possible moment. A data principal asks who has listened to their call. A
    breach is suspected and somebody has to say what was reached.

    Deliberately records the *act of access*, not the result: a row is written
    when a signed URL is issued, because that is the moment access becomes
    possible. Whether the browser then played the audio is not something the
    server can know, and pretending otherwise would make the log a worse record
    than an honest one.
    """

    __tablename__ = "data_access_log"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    # Null for access through a public share link, which is exactly the case
    # worth being able to see afterwards.
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # recording | transcript | kyc_document | export
    resource_type = Column(String(32), nullable=False)
    resource_id = Column(String(128), nullable=True)
    workflow_run_id = Column(Integer, nullable=True)

    # signed_url | download | export
    action = Column(String(32), nullable=False)
    # How the caller authenticated: session | api_key | public_token | staff
    actor_kind = Column(String(24), nullable=True)
    ip_address = Column(String(64), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    organization = relationship("OrganizationModel")
    user = relationship("UserModel")

    __table_args__ = (
        Index("ix_data_access_org_created", "organization_id", "created_at"),
        Index("ix_data_access_run", "workflow_run_id"),
    )


class ErasureRequestModel(Base):
    """A request to delete somebody's data, and what came of it.

    Both DPDP and GDPR give a deadline for responding, so a request that is
    handled but not recorded is indistinguishable from one that was ignored.
    This table is the evidence — what was asked, when, what was actually erased,
    and by whom.

    Kept after completion, and deliberately so: the record of an erasure is not
    itself the erased data, and destroying it would remove the only proof the
    obligation was met.
    """

    __tablename__ = "erasure_requests"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # phone_number | organization
    subject_type = Column(String(24), nullable=False)
    # The phone number requested, stored hashed rather than in the clear: a
    # register of numbers that asked to be forgotten would be its own personal
    # data, and a rather sensitive one.
    subject_hash = Column(String(64), nullable=True)

    # pending | completed | failed
    status = Column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    requested_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    requested_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Counts only — never the data itself.
    runs_affected = Column(Integer, nullable=False, default=0, server_default=text("0"))
    objects_deleted = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    note = Column(Text, nullable=True)

    organization = relationship("OrganizationModel")

    __table_args__ = (
        Index("ix_erasure_org_requested", "organization_id", "requested_at"),
        Index("ix_erasure_status", "status"),
    )


class AgreementAcceptanceModel(Base):
    """Who accepted which agreement, when, and from where.

    A click-wrap is enforceable in India — IT Act s10A, given reasonable notice
    and an affirmative act — but only if it can be *shown*. Without a record
    there is nothing to produce in a dispute, and the acceptance is worth very
    little however carefully the terms were drafted.

    Append-only by design. Superseding an agreement means writing a new row for
    the new version, never editing the old one: the question in a dispute is
    what the customer agreed to at the time, and an updatable record cannot
    answer it.

    The IP address is kept because it is part of what makes the record
    evidential. It is personal data, and it ages out with the account rather
    than with call data — an acceptance record outlives the calls it authorised,
    for the same reason an invoice does.
    """

    __tablename__ = "agreement_acceptances"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # dpa | terms | privacy_notice
    agreement = Column(String(32), nullable=False)
    # The published version string, e.g. "2026-07". Meaningless as a boolean:
    # "they accepted the DPA" is not a defence when the DPA has changed twice
    # since.
    version = Column(String(32), nullable=False)

    accepted_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(512), nullable=True)

    __table_args__ = (
        Index(
            "ix_agreement_acceptances_org_agreement",
            "organization_id",
            "agreement",
        ),
    )


class DoNotCallEntryModel(Base):
    """A number this organization must not call.

    Per-organization rather than global. A number that asked *this* customer to
    stop has not asked every customer on the platform to stop, and merging the
    lists would leak one customer's contact list into another's — the entries
    are themselves personal data.

    The stored number is the normalised key from
    services/compliance/dnd.normalise_number, never what the user typed. The
    list is only protection if the value a customer uploads and the value the
    dialler looks up reduce to the same string.
    """

    __tablename__ = "do_not_call_entries"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    phone_number = Column(String(20), nullable=False)

    # How the number got here: "manual", "upload", "caller_request", "carrier".
    # Kept because a regulator asking why a number was suppressed is asking for
    # provenance, and "it was in the table" is not an answer.
    source = Column(String(32), nullable=False, default="manual")
    note = Column(String(255), nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        # The uniqueness is what makes a re-upload idempotent instead of
        # growing the table by its own size every time.
        UniqueConstraint("organization_id", "phone_number", name="_dnc_org_number_uc"),
        # The dialler's lookup is (organization_id, phone_number) on every
        # single call, so it gets a covering index rather than relying on the
        # unique constraint's ordering by accident.
        Index(
            "ix_do_not_call_entries_org_number",
            "organization_id",
            "phone_number",
        ),
    )


class VerifiedNumberModel(Base):
    """A phone number an organization has proved it can answer.

    Distinct from ``telephony_phone_numbers``, which is for numbers we rent to a
    customer and bind to an inbound workflow. This is the opposite direction: a
    number the customer already owns, proved by answering an SMS, so we are
    willing to dial it for a test call.

    Why prove it at all: ``organization_preferences.test_phone_number`` is free
    text, and routes/telephony.py dials it directly. Without ownership proof an
    account can have Decibyl ring any number its user types, which is a
    telephone-harassment vector wearing the costume of a convenience feature.
    """

    __tablename__ = "verified_numbers"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: Normalised by services/compliance/dnd.normalise_number — the same key the
    #: DND list uses, so "is this verified" and "is this suppressed" are asked
    #: about the same string.
    phone_number = Column(String(20), nullable=False)
    label = Column(String(64), nullable=True)

    # pending | verified
    status = Column(String(16), nullable=False, default="pending")

    #: The OTP, hashed with a per-row salt. NOT the bare SHA-256 used for
    #: recovery codes: those are full-entropy random values with no dictionary
    #: to precompute, while a six-digit code has a search space of one million
    #: and an unsalted digest of one is a rainbow-table lookup. The salt is what
    #: makes a leaked table useless without per-row work.
    code_hash = Column(String(64), nullable=True)
    code_salt = Column(String(32), nullable=True)
    code_expires_at = Column(DateTime(timezone=True), nullable=True)

    #: Wrong guesses against the current code. A six-digit code falls to
    #: exhaustive guessing in under a second without this.
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    #: Codes sent to this number, ever. Bounds the number of texts an account
    #: can make us send to a stranger — the abuse this feature would otherwise
    #: enable is using our carrier to SMS-bomb somebody.
    send_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    last_sent_at = Column(DateTime(timezone=True), nullable=True)

    verified_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "phone_number", name="_verified_number_org_number_uc"
        ),
        Index("ix_verified_numbers_org_number", "organization_id", "phone_number"),
    )


class EmailVerificationChallengeModel(Base):
    """A live one-time code for an email address.

    One row per user, replaced on each send. Separate from the users table
    because it is transient: it exists for ten minutes, carries an attempt
    counter, and is destroyed on success. Keeping it beside the durable account
    record would mean every read of a user drags along a dead credential.
    """

    __tablename__ = "email_verification_challenges"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: Stored so a code cannot be replayed against a different address after
    #: the user edits theirs mid-flow.
    email = Column(String, nullable=False)

    code_hash = Column(String(64), nullable=False)
    code_salt = Column(String(32), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    send_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    last_sent_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (UniqueConstraint("user_id", name="_email_verification_user_uc"),)
