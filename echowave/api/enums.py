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


class RunFailureReason(str, Enum):
    """Why a call did not happen, or did not finish.

    A small, stable vocabulary rather than free text, because the question this
    answers is "what is failing across the fleet" and that cannot be asked of a
    column holding a thousand distinct sentences. The detail that *is* unique
    to one call goes in ``failure_detail`` beside it.

    Grouped by who has to act, which is the only grouping that matters when
    somebody is looking at the breakdown at 9am:

    * The **customer** can fix insufficient_credit, no_verified_number.
    * **We** have to fix provider_error, platform_error, no_platform_key.
    * **Nobody** can fix caller_hung_up or the compliance refusals — those are
      the system working, and they must not sit in the same bucket as faults
      or every real problem is buried under them.
    """

    # The account's own situation.
    INSUFFICIENT_CREDIT = "insufficient_credit"
    NO_VERIFIED_NUMBER = "no_verified_number"
    CONCURRENCY_LIMIT = "concurrency_limit"

    # Deliberate refusals. Not faults — the system doing its job.
    DND_LISTED = "dnd_listed"
    OUTSIDE_CALLING_HOURS = "outside_calling_hours"
    CALLER_HUNG_UP = "caller_hung_up"

    # Ours.
    PROVIDER_ERROR = "provider_error"
    NO_PLATFORM_KEY = "no_platform_key"
    TELEPHONY_ERROR = "telephony_error"
    PLATFORM_ERROR = "platform_error"

    # The honest bucket. A reason we have not classified yet is worth a name of
    # its own: folding it into platform_error would claim we know it is ours.
    UNKNOWN = "unknown"


#: Which reasons are the system working rather than something broken. Kept
#: beside the enum so a screen cannot drift from it, and so "how many calls
#: failed" can exclude the ones that did exactly what they were told to.
DELIBERATE_FAILURES = frozenset(
    {
        RunFailureReason.DND_LISTED,
        RunFailureReason.OUTSIDE_CALLING_HOURS,
        RunFailureReason.CALLER_HUNG_UP,
    }
)

#: Whose problem each reason is. Drives the breakdown grouping.
FAILURE_OWNER = {
    RunFailureReason.INSUFFICIENT_CREDIT: "customer",
    RunFailureReason.NO_VERIFIED_NUMBER: "customer",
    RunFailureReason.CONCURRENCY_LIMIT: "customer",
    RunFailureReason.DND_LISTED: "nobody",
    RunFailureReason.OUTSIDE_CALLING_HOURS: "nobody",
    RunFailureReason.CALLER_HUNG_UP: "nobody",
    RunFailureReason.PROVIDER_ERROR: "us",
    RunFailureReason.NO_PLATFORM_KEY: "us",
    RunFailureReason.TELEPHONY_ERROR: "us",
    RunFailureReason.PLATFORM_ERROR: "us",
    RunFailureReason.UNKNOWN: "us",
}


class WorkflowRunStatus(Enum):
    # historical modes
    VOICE = "VOICE"
    CHAT = "CHAT"


class OrganizationConfigurationKey(Enum):
    CONCURRENT_CALL_LIMIT = "CONCURRENT_CALL_LIMIT"
    # Split by direction. Kept alongside the single limit above rather than
    # replacing it: an account that has one set keeps whatever it was given,
    # because silently re-interpreting a number somebody chose is how a
    # customer's capacity changes without anybody telling them.
    CONCURRENT_CALL_LIMIT_INBOUND = "CONCURRENT_CALL_LIMIT_INBOUND"
    CONCURRENT_CALL_LIMIT_OUTBOUND = "CONCURRENT_CALL_LIMIT_OUTBOUND"
    # Most the account may spend in one IST day, in paise. 0 disables it.
    DAILY_SPEND_CEILING_PAISE = "DAILY_SPEND_CEILING_PAISE"
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

    @classmethod
    def provider_components(cls) -> tuple["CostComponent", ...]:
        """Components that represent money paid to a third party."""
        return (cls.STT, cls.LLM, cls.TTS, cls.TELEPHONY)


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
    # A referral reward, credited once the referred account's first payment has
    # settled and survived the clawback window. Kept distinct from ADJUSTMENT
    # because it is earned rather than granted: it has a payment behind it, it
    # is reportable as a marketing cost, and an adjustment that folded it in
    # would make "what did referrals cost us" unanswerable.
    REFERRAL = "referral"


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


class OrganizationRole(str, Enum):
    """A member's standing within one organization.

    Every existing member had identical access before this existed, so the
    migration that introduced this column backfilled everyone as OWNER —
    nothing regresses on upgrade. New members joining afterwards default to
    MEMBER; an existing Owner promotes from there.

    Ordered least to most privileged; ``ORGANIZATION_ROLE_RANK`` below is the
    ordering a permission check compares against.
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
