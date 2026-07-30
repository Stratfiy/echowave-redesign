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
    Table,
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
    ToolCategory,
    ToolStatus,
    TriggerState,
    WebhookCredentialType,
    WorkflowRunState,
    WorkflowStatus,
)

Base = declarative_base()


# TODO: remove workflow_defintion after migration, remove nullable workflow_defintion_id from Workflow and Workflowrun


# Association table for many-to-many relationship between users and organizations
organization_users_association = Table(
    "organization_users",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column(
        "organization_id", Integer, ForeignKey("organizations.id"), primary_key=True
    ),
)


class UserModel(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    workflows = relationship("WorkflowModel", back_populates="user")
    selected_organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=True
    )
    selected_organization = relationship("OrganizationModel", back_populates="users")
    organizations = relationship(
        "OrganizationModel",
        secondary=organization_users_association,
        back_populates="users",
    )
    is_superuser = Column(Boolean, default=False)
    email = Column(String, nullable=True)
    password_hash = Column(String, nullable=True)

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

    # Relationships
    users = relationship(
        "UserModel",
        secondary=organization_users_association,
        back_populates="organizations",
    )
    integrations = relationship("IntegrationModel", back_populates="organization")
    usage_cycles = relationship(
        "OrganizationUsageCycleModel", back_populates="organization"
    )
    configurations = relationship(
        "OrganizationConfigurationModel", back_populates="organization"
    )
    api_keys = relationship("APIKeyModel", back_populates="organization")


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
        Index("ix_api_keys_key_hash", "key_hash"),
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
    is_active = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    is_default_caller_id = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
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
    inbound_workflow = relationship("WorkflowModel")

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
    rate_mpaise = Column(Integer, nullable=False)
    effective_from = Column(DateTime(timezone=True), nullable=False)
    effective_to = Column(DateTime(timezone=True), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
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
    cost_paise = Column(BigInteger, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    workflow_run = relationship("WorkflowRunModel", back_populates="cost_items")

    __table_args__ = (
        Index("ix_call_cost_items_run", "workflow_run_id"),
        Index("ix_call_cost_items_component", "component"),
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

    uploaded_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    kyc = relationship("OrganizationKycModel", back_populates="documents")

    __table_args__ = (Index("ix_kyc_documents_kyc", "organization_kyc_id"),)


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
