from enum import Enum


class IntegrationAction(Enum):
    ALL_CALLS = "All Calls"
    QUALIFIED_CALLS = "Qualified Calls"


class Environment(Enum):
    LOCAL = "local"
    PRODUCTION = "production"
    TEST = "test"


class CallType(Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class TelephonyCallStatus(str, Enum):
    INITIATED = "initiated"
    RINGING = "ringing"
    IN_PROGRESS = "in-progress"
    ANSWERED = "answered"
    COMPLETED = "completed"
    FAILED = "failed"
    BUSY = "busy"
    NO_ANSWER = "no-answer"
    CANCELED = "canceled"
    ERROR = "error"

    @classmethod
    def from_raw(cls, value: object) -> "TelephonyCallStatus | None":
        if isinstance(value, cls):
            return value

        if value in (None, ""):
            return None

        try:
            return cls(str(value).lower())
        except ValueError:
            return None


class WorkflowRunMode(Enum):
    ARI = "ari"
    PLIVO = "plivo"
    TWILIO = "twilio"
    VONAGE = "vonage"
    VOBIZ = "vobiz"
    CLOUDONIX = "cloudonix"
    TELNYX = "telnyx"
    WEBRTC = "webrtc"
    SMALLWEBRTC = "smallwebrtc"
    TEXTCHAT = "textchat"

    # Historical, not used anymore. Don't
    # use and don't remove
    STASIS = "stasis"
    VOICE = "VOICE"
    CHAT = "CHAT"


class StorageBackend(Enum):
    """Storage backend enumeration.

    Currently supported backends:
    - S3: Amazon S3
    - MINIO: MinIO

    Future extensibility: Additional backends like GCS, Azure can be added by:
    1. Adding new enum values as strings
    2. Implementing storage logic in services/storage.py
    3. Database will automatically support new values via SQLAlchemy Enum type
    """

    # Currently implemented backends
    S3 = "s3"  # AWS S3 for cloud deployments
    MINIO = "minio"  # MinIO for local/OSS deployments

    @classmethod
    def get_current_backend(cls):
        """Get current backend based on ENABLE_AWS_S3 flag."""
        from api.constants import ENABLE_AWS_S3

        if ENABLE_AWS_S3:
            return cls.S3
        else:
            return cls.MINIO


class WorkflowRunState(Enum):
    INITIALIZED = "initialized"  # Workflow run created, ready for connection
    RUNNING = "running"  # Websocket connected and pipeline active
    COMPLETED = "completed"  # Workflow run finished


class WorkflowRunStatus(Enum):
    # historical modes
    VOICE = "VOICE"
    CHAT = "CHAT"


class OrganizationConfigurationKey(Enum):
    CONCURRENT_CALL_LIMIT = "CONCURRENT_CALL_LIMIT"
    TELEPHONY_CONFIGURATION = (
        "TELEPHONY_CONFIGURATION"  # Stores all providers + active one
    )
    TWILIO_CONFIGURATION = (
        "TWILIO_CONFIGURATION"  # Deprecated - for backward compatibility
    )
    LANGFUSE_CREDENTIALS = (
        "LANGFUSE_CREDENTIALS"  # Org-level Langfuse tracing credentials
    )
    MODEL_CONFIGURATION_V2 = (
        "MODEL_CONFIGURATION_V2"  # Org-level v2 AI model configuration
    )
    ORGANIZATION_PREFERENCES = "ORGANIZATION_PREFERENCES"  # Org-level defaults such as timezone/test call number
    MODEL_CONFIGURATION_PREFERENCES = "MODEL_CONFIGURATION_PREFERENCES"  # Deprecated; read fallback for old org preferences


class UserConfigurationKey(Enum):
    """Keys for the per-user keyed JSON store (user_configurations)."""

    MODEL_CONFIGURATION = (
        "MODEL_CONFIGURATION"  # Legacy per-user v1 AI model configuration
    )
    ONBOARDING = "ONBOARDING"  # Post-signup onboarding state (gate, tooltips, actions)


class WorkflowStatus(Enum):
    """Workflow status values"""

    ACTIVE = "active"
    ARCHIVED = "archived"
    # Future statuses can be added here like:
    # DRAFT = "draft"
    # PAUSED = "paused"


class RedisChannel(Enum):
    """Redis pub/sub channel names"""

    CAMPAIGN_EVENTS = "campaign_events"
    WORKER_SYNC = "worker_sync"


class TriggerState(Enum):
    """Agent trigger state values"""

    ACTIVE = "active"
    ARCHIVED = "archived"


class WebhookCredentialType(Enum):
    """Webhook credential authentication types"""

    NONE = "none"  # No authentication
    API_KEY = "api_key"  # API key in header
    BEARER_TOKEN = "bearer_token"  # Bearer token auth
    BASIC_AUTH = "basic_auth"  # Username/password
    CUSTOM_HEADER = "custom_header"  # Custom header key-value


class ToolCategory(Enum):
    """Tool category types"""

    HTTP_API = "http_api"  # Custom HTTP API calls (implemented)
    END_CALL = "end_call"  # End call tool
    TRANSFER_CALL = "transfer_call"  # Transfer call to phone number (Twilio only)
    CALCULATOR = "calculator"  # Built-in calculator tool
    NATIVE = "native"  # Built-in integrations (future: dtmf_input)
    INTEGRATION = "integration"  # Reserved: third-party integrations with no dedicated category yet
    MCP = "mcp"  # Customer-provided MCP server exposing a tool catalog
    GOOGLE_CALENDAR = (
        "google_calendar"  # Create events on a connected Google Calendar (implemented)
    )


class ToolStatus(Enum):
    """Tool status values"""

    ACTIVE = "active"  # Tool is available for use
    ARCHIVED = "archived"  # Tool is soft-deleted
    DRAFT = "draft"  # Tool is being configured (not ready for use)


class PostHogEvent(str, Enum):
    """PostHog event names — backend events only."""

    WORKFLOW_CREATED = "workflow_created"
    WORKFLOW_PUBLISHED = "workflow_published"
    WORKFLOW_DUPLICATED = "workflow_duplicated"
    CALL_STARTED = "call_started"
    CALL_COMPLETED = "call_completed"
    CALL_FAILED = "call_failed"
    TELEPHONY_CONFIGURED = "telephony_configured"
    KNOWLEDGE_BASE_CREATED = "knowledge_base_created"
    TOOL_CREATED = "tool_created"
    AGENT_EMBEDDED = "agent_embedded"
    SIGNED_UP = "signed_up"
    SIGNED_IN = "signed_in"
    ORGANIZATION_CREATED = "organization_created"
    ORGANIZATION_USER_ASSOCIATED = "organization_user_associated"
    # usage_* events track orgs hitting capacity/limit boundaries
    USAGE_CONCURRENT_CALL_LIMIT_REACHED = "usage_concurrent_call_limit_reached"


class CostComponent(str, Enum):
    """Line-item components on a call receipt.

    ``PLATFORM`` is Decibyl revenue; every other component is a provider cost
    passed through at cost. They are never summed into a single stored number —
    see ``api/services/billing`` and DASHBOARD.md.
    """

    STT = "stt"
    LLM = "llm"
    TTS = "tts"
    TELEPHONY = "telephony"
    PLATFORM = "platform"
    #: Retired. A BYOK call now carries an uplifted *platform* rate rather
    #: than a second fee line — see ``billing/usage.py:byok_platform_tier``.
    #: The member stays so a receipt written while the separate line existed
    #: still resolves rather than raising on read.
    ORCHESTRATION = "orchestration"
    #: A priced feature used on the call — knowledge-base retrieval, post-call
    #: QA. Also Decibyl revenue. One component rather than one per feature, so
    #: adding a feature to the catalogue never needs a migration; which feature
    #: a line is for is carried in ``provider``. See ``billing/addons.py``.
    ADDON = "addon"

    @classmethod
    def provider_components(cls) -> tuple["CostComponent", ...]:
        """Components that represent money paid to a third party."""
        return (cls.STT, cls.LLM, cls.TTS, cls.TELEPHONY)

    @classmethod
    def revenue_components(cls) -> tuple["CostComponent", ...]:
        """Components that are Decibyl revenue rather than a pass-through.

        These never carry a provider cost and are never marked up — a markup
        on our own fee would be a margin on a margin.
        """
        return (cls.PLATFORM, cls.ORCHESTRATION, cls.ADDON)


class RateUnit(str, Enum):
    """Unit a provider rate is quoted in."""

    MINUTE = "minute"
    THOUSAND_CHARS = "1k_chars"
    THOUSAND_TOKENS = "1k_tokens"
    #: A recurring charge for holding a resource, quoted per calendar month.
    #: Unlike every other unit here this is not measured from a call — nothing
    #: on a receipt multiplies it, and ``compute_call_cost`` never sees it. It
    #: exists so a number rental can be expressed as a rate at all; see
    #: ``api/services/billing/rentals.py``.
    MONTH = "month"


class CreditLedgerKind(str, Enum):
    """Why a credit ledger row exists."""

    TOPUP = "topup"
    USAGE = "usage"
    ADJUSTMENT = "adjustment"
    TRIAL = "trial"
    # Funds held while a call is in flight, released when it is costed. Counts
    # against the balance immediately so concurrent calls cannot each spend the
    # same rupee.
    RESERVATION = "reservation"
    # A recurring charge for a resource held between calls — today, a rented
    # phone number. Kept distinct from USAGE because it is not a call receipt:
    # it has no workflow run behind it, it accrues whether or not the account
    # dials, and a statement that folded it into usage would tell a customer
    # they were charged for calls they never made.
    RENTAL = "rental"
    # The call balance a starter-plan cycle grants. Distinct from TOPUP because
    # it is not credit the customer chose to buy: it arrives with the plan, on
    # the plan's schedule, and a statement that called it a top-up would invite
    # the question of when they bought it.
    PLAN = "plan"
    # What was left of a plan cycle's balance when that cycle ended. Plan
    # balance is for the month it was granted for; a top-up, which the customer
    # bought outright, never expires. Kept as its own kind rather than as a
    # negative adjustment so a statement can say "this expired" — an
    # unexplained debit the size of last month's grant is the single most
    # alarming line a billing screen can show.
    PLAN_EXPIRY = "plan_expiry"


class BillingAuditAction(str, Enum):
    """Auditable billing changes. Every one records who, when, old and new."""

    PLATFORM_RATE_CHANGED = "platform_rate_changed"
    CREDIT_ADJUSTED = "credit_adjusted"
    PROVIDER_RATE_CHANGED = "provider_rate_changed"
    # The USD→INR rate. Audited like any other rate because the list price is
    # quoted in dollars, so changing this changes what every account pays.
    EXCHANGE_RATE_CHANGED = "exchange_rate_changed"
    # The multiple charged on managed model usage. Moves the price of every
    # managed call at once, which is why it is the one change that requires a
    # code from the inbox before it applies.
    MANAGED_MARKUP_CHANGED = "managed_markup_changed"
    # A markup override for one (component, provider, model). Narrower than
    # MANAGED_MARKUP_CHANGED — moves one line's price, not every account's —
    # which is why it does not require the OTP code that one does.
    MANAGED_MARKUP_OVERRIDE_CHANGED = "managed_markup_override_changed"


class KycStatus(str, Enum):
    """Where an account sits in telephony KYC.

    Two stages, deliberately. Our review is a pre-screen — we check the
    documents are complete and legible before spending a carrier round-trip on
    them. The carrier is the telecom licensee, and under the DoT directive of
    June 2025 it is the licensee that must verify the end user, so only
    ``CARRIER_APPROVED`` may unblock calling. Gating on our own review instead
    would let an account dial on a sub-account the carrier still has blocked.
    """

    NOT_STARTED = "not_started"
    #: Customer has uploaded documents; waiting on us.
    SUBMITTED = "submitted"
    #: A staff member has picked it up.
    UNDER_REVIEW = "under_review"
    #: We rejected it — the customer resubmits.
    REJECTED = "rejected"
    #: Sent to the carrier; waiting on them.
    FORWARDED = "forwarded"
    #: The carrier rejected it. Terminal until the customer resubmits.
    CARRIER_REJECTED = "carrier_rejected"
    #: The licensee has verified the account. The only state that permits calls.
    CARRIER_APPROVED = "carrier_approved"
    #: The licensee has suspended a previously approved application. Plivo
    #: suspends for cause — a document that no longer holds, an unanswered
    #: query — and can reinstate, so this is not the end of the road. Calling
    #: stops immediately; the numbers stay held.
    SUSPENDED = "suspended"
    #: The application lapsed. Plivo expires applications whose documents have
    #: aged out, and an expired one cannot be amended — the account has to
    #: build a new application from scratch.
    EXPIRED = "expired"


class KycBusinessType(str, Enum):
    """Drives which documents are required."""

    INDIVIDUAL = "individual"
    COMPANY = "company"


class RecurringChargeType(str, Enum):
    """What a recurring charge is for."""

    #: Monthly rental on a phone number held at the carrier.
    NUMBER_RENTAL = "number_rental"


class RecurringChargeStatus(str, Enum):
    """Where a recurring charge sits in the dunning cycle.

    The order here is the order a charge moves through, and the day thresholds
    live in ``api/services/billing/dunning.py`` rather than being scattered
    across the states — a policy that decides when someone loses a phone number
    should be readable in one place.
    """

    #: Billing normally. The only state in which the resource works fully.
    ACTIVE = "active"
    #: A charge failed for want of balance. Retried daily; calls still work.
    PAST_DUE = "past_due"
    #: Calls are blocked, but the number is still held at the carrier and can
    #: be recovered in full by topping up.
    SUSPENDED = "suspended"
    #: Long enough past due that release is permitted. Nothing releases
    #: automatically from here — see ``dunning.py``.
    PENDING_RELEASE = "pending_release"
    #: The resource is gone. Terminal, and irreversible at the carrier.
    RELEASED = "released"
    #: Ended cleanly at the customer's request, with nothing outstanding.
    CANCELLED = "cancelled"


class MandateStatus(str, Enum):
    """Where an autopay mandate sits with the payment provider.

    Mirrors Razorpay's subscription states rather than inventing our own, so a
    row can be reconciled against their dashboard without a translation table.
    Only ``ACTIVE`` and ``AUTHENTICATED`` mean the customer has actually
    authorised us to collect.
    """

    #: Created by us; the customer has not opened the authorisation link yet.
    CREATED = "created"
    #: The customer authorised the mandate. Collection starts at the first
    #: cycle.
    AUTHENTICATED = "authenticated"
    #: Authorised and collecting.
    ACTIVE = "active"
    #: A collection failed and the provider stopped trying. The mandate still
    #: exists and can be resumed, but nothing is being collected.
    HALTED = "halted"
    #: Awaiting a retry after a failed charge. Still authorised.
    PENDING = "pending"
    #: The customer or their bank revoked it. Terminal.
    CANCELLED = "cancelled"
    #: Ran to the end of its cycles. Terminal.
    COMPLETED = "completed"
    #: The authorisation link expired before it was used. Terminal.
    EXPIRED = "expired"

    @classmethod
    def authorised(cls) -> frozenset[str]:
        """The states in which we may hand over a number.

        Written as a set rather than a comparison because "is this mandate good
        enough to provision against" is asked from three places, and three
        copies of the same tuple is how they drift apart.
        """
        return frozenset({cls.AUTHENTICATED.value, cls.ACTIVE.value})


class PhoneNumberStatus(str, Enum):
    """Lifecycle of a number we hold at a carrier on a customer's behalf.

    Distinct from ``is_active``, which is the customer's own on/off switch. A
    number can be ``is_active=True`` and ``SUSPENDED`` here — the customer
    wants it working, and it is not, because they have not paid.
    """

    #: Bought and linked; routing works.
    ACTIVE = "active"
    #: Held at the carrier, calls blocked. Recoverable.
    SUSPENDED = "suspended"
    #: Released at the carrier. The row survives as the record that we once
    #: held it; nothing may route to it again.
    RELEASED = "released"


class KycDocumentKind(str, Enum):
    """Document types the carriers ask for on Indian numbers."""

    CERTIFICATE_OF_INCORPORATION = "certificate_of_incorporation"
    GST_CERTIFICATE = "gst_certificate"
    ADDRESS_PROOF = "address_proof"
    AUTHORISED_SIGNATORY_ID = "authorised_signatory_id"
    OTHER = "other"


class AccountType(str, Enum):
    """Commercial shape of an organization, used for dashboard segmentation."""

    CLINIC = "clinic"
    AGENCY = "agency"
    ENTERPRISE = "enterprise"


class PartnerKind(str, Enum):
    """What a partner applicant says they are.

    Self-declared on the application and never enforced — it decides how the
    application reads to whoever reviews it, and it is the main input to the
    commission staff then set. Somebody who builds one agent for their own
    clinic and somebody reselling to forty of them are the same product and
    very different commercial conversations.

    DEVELOPER
        Builds on the API for their own product or a client's. Usually low
        volume, usually wants the SDK rather than the dashboard.
    AGENCY
        Runs accounts on behalf of clients, billed through their own account.
    RESELLER
        Sells Decibyl under a commercial arrangement and is paid a commission
        on what their accounts spend.
    """

    DEVELOPER = "developer"
    AGENCY = "agency"
    RESELLER = "reseller"


class PartnerApplicationStatus(str, Enum):
    """Where an application has got to.

    Deliberately three states and no "claimed": the KYC queue has claiming
    because a document review takes minutes of somebody's attention, and two
    reviewers doing it at once is wasted work. Reading four answers is not
    that, and a claim that is never released is a queue that silently stops.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CommissionBasis(str, Enum):
    """What a partner's percentage is a percentage *of*.

    The two answers are not interchangeable and the gap between them is most
    of the margin, so this is stored per partner rather than assumed.

    PLATFORM_FEE
        A share of what we keep — the charge minus what the providers cost us.
        Safe by construction: the commission cannot exceed the margin, so a
        partner account can never be sold at a loss however the rate card
        moves underneath it.
    TOTAL_SPEND
        A share of everything the account is charged, provider cost included.
        The number a reseller usually asks for because it is simple to
        forecast, and the one that can go underwater — 20% of total spend on
        an account running at a 15% margin loses money on every minute. Staff
        setting this one are quoting against the rate card, not against a
        percentage they can reason about in isolation.
    """

    PLATFORM_FEE = "platform_fee"
    TOTAL_SPEND = "total_spend"


class PartnerStatementStatus(str, Enum):
    """How far along a period's earnings are.

    The three states exist because only one of them is reversible, and knowing
    which is the difference between correcting a number and changing one a
    partner has already been told.

    DRAFT
        Generated from the rollups, not shown to the partner as final.
        Regenerating replaces it — which is how a late-arriving rollup or a
        recosted call gets picked up.
    ISSUED
        Sent. The number is now something somebody has read, so regeneration
        is refused: a correction after this point is a new statement with a
        note, not a quiet edit of the old one.
    PAID
        Money has left. Carries the reference for the transfer, which happens
        outside this system — there is no payout rail here, deliberately.
    """

    DRAFT = "draft"
    ISSUED = "issued"
    PAID = "paid"


class OrganizationRole(str, Enum):
    """A member's standing within one organization.

    Every existing member had identical access before this existed, so the
    migration that introduced this column backfilled everyone as OWNER —
    nothing regresses on upgrade. New members joining afterwards default to
    MEMBER; an existing Owner promotes from there.

    Ordered least to most privileged; ``ORGANIZATION_ROLE_RANK`` below is the
    ordering a permission check compares against.

    What each tier can do, so that assigning one is a decision rather than a
    guess. ADMIN gated nothing at all for a while, which is the worse failure:
    a role that appears in a picker and restricts nobody is a permission an
    operator believes they have granted.

    MEMBER
        Builds and runs agents. Everything the product is for — workflows,
        campaigns, calls, recordings, the knowledge base — and can spend the
        organization's balance by placing calls.
    ADMIN
        Everything a member can do, plus the surfaces where one person's action
        binds the whole account: the BYOK key vault and integration
        credentials (secrets, and spend under someone else's contract), the
        billing profile (which decides what tax the customer is charged), the
        autopay mandate (a standing authority to debit a bank account), and
        removing a number from the do-not-disturb list (a regulatory act, and
        the one entry nobody notices going missing).
    OWNER
        Everything an admin can do, plus membership: who is in the
        organization and at what tier. The last Owner cannot be demoted or
        removed.

    Buying credit is deliberately *not* admin-gated. A member who cannot top up
    when the balance runs out is a member who cannot work, and paying us more
    money is not a privilege that needs protecting.
    """

    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"


#: Rank for "at least this role" checks. Higher is more privileged.
ORGANIZATION_ROLE_RANK: dict[str, int] = {
    OrganizationRole.MEMBER.value: 0,
    OrganizationRole.ADMIN.value: 1,
    OrganizationRole.OWNER.value: 2,
}


class StaffRole(str, Enum):
    """Decibyl-staff privilege tier, independent of any organization.

    Two tiers because the staff-only surfaces are not one thing: KYC review
    is cross-account document review with no further reach, while billing
    rate changes, platform provider keys, and account impersonation can move
    money or expose secrets. SUPPORT covers the former; SUPERADMIN is
    required for the latter and implies everything SUPPORT can do.

    Stored as a nullable VARCHAR on ``UserModel`` (``None`` = not staff)
    rather than a Postgres ENUM, so a future tier needs no migration — same
    convention as ``OrganizationModel.account_type``.
    """

    SUPPORT = "support"
    SUPERADMIN = "superadmin"


#: Rank for "at least this tier" checks. Higher is more privileged.
STAFF_ROLE_RANK: dict[str, int] = {
    StaffRole.SUPPORT.value: 0,
    StaffRole.SUPERADMIN.value: 1,
}
