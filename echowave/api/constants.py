import os
from pathlib import Path

from api.enums import Environment

ENVIRONMENT = os.getenv("ENVIRONMENT", Environment.LOCAL.value)
# Absolute path to the project root directory (i.e. the directory containing
# the top-level api/ package). Having a single canonical location helps
# when constructing file-system paths elsewhere in the codebase.
APP_ROOT_DIR: Path = Path(__file__).resolve().parent

FILLER_SOUND_PROBABILITY = 0.0

VOICEMAIL_RECORDING_DURATION = 5.0

# Langfuse Configuration
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")

# URLs for deployment
#
# PUBLIC_BASE_URL is the single canonical origin a deployment is reached at
# (scheme + host, e.g. https://203-0-113-10.sslip.io). For a standard single-host
# install it is the only endpoint value an operator sets — the per-subsystem URLs
# below derive from it (and from PUBLIC_HOST for the TURN/ICE host). Each derived
# var can still be set explicitly to override it for a split deployment.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL") or None
PUBLIC_HOST = os.getenv("PUBLIC_HOST") or None

# Where the API is reached, as distinct from where the deployment is rooted.
#
# Derived from DECIBYL_API_HOST before PUBLIC_BASE_URL, because on a split-
# hostname install those are not the same place and PUBLIC_BASE_URL is the
# wrong one. The root host serves a 404 pointer and nothing else — no /api/,
# no /voice-audio/ — so falling back to it here published an address that
# answers nothing. That value is not cosmetic: it is what /health reports to
# the browser as "the backend is at", and what telephony webhooks, embed
# snippets and OAuth redirect URIs are built from. Every one of those pointed
# at a dead host.
#
# The derivation mirrors decibyl_render_subdomain_nginx_conf in
# scripts/lib/setup_common.sh, and it has to. That renderer only requires the
# operator to name ONE host: it takes whichever of the three is set, strips a
# label to get the apex, and fills in the conventional api./app./docs. names
# beneath it. An operator who set only DECIBYL_APP_HOST therefore gets an
# nginx that serves api.<domain> correctly while the application, reading the
# raw variable, has never heard of it — deploy side and app side disagreeing
# about the same deployment, which is the exact shape of the bug this constant
# already had once. Keep the two in step.
def _derive_api_base_url() -> str | None:
    explicit = os.getenv("DECIBYL_API_HOST")
    if explicit:
        return f"https://{explicit}"

    root_host = os.getenv("DECIBYL_ROOT_HOST")
    if not root_host:
        named = os.getenv("DECIBYL_APP_HOST") or os.getenv("DECIBYL_DOCS_HOST")
        if not named:
            return None
        # Strip one label. A bare apex (decibyl.ai) has no subdomain to strip,
        # so keep it rather than reducing it to the public suffix.
        root_host = named.split(".", 1)[1] if named.count(".") >= 2 else named

    return f"https://api.{root_host}"


_SUBDOMAIN_API_BASE_URL = _derive_api_base_url()

# Public URL the backend builds webhook/callback/embed links from. Derives from
# DECIBYL_API_HOST, then PUBLIC_BASE_URL (public IP / domain), falling back to
# localhost for local dev.
# When this is a non-public address (localhost or a private/reserved IP) the host
# isn't reachable from the internet, so get_backend_endpoints() resolves a running
# Cloudflare tunnel's URL at runtime instead (see api/utils/common.py).
BACKEND_API_ENDPOINT = (
    os.getenv("BACKEND_API_ENDPOINT")
    or _SUBDOMAIN_API_BASE_URL
    or PUBLIC_BASE_URL
    or "http://localhost:8000"
)
# Where the browser reaches the *app*, as distinct from the API. Used to build
# the embed widget's script URL, which is copied into a customer's own website
# — so a wrong value here ships a broken snippet to their visitors rather than
# failing anywhere we would notice.
#
# Derived from DECIBYL_APP_HOST when the deployment has split hostnames, so
# naming the app host once fixes this too. The localhost default is correct for
# local development and dangerous anywhere else, which is why it is last.
UI_APP_URL = (
    os.getenv("UI_APP_URL")
    or (f"https://{os.getenv('DECIBYL_APP_HOST')}" if os.getenv("DECIBYL_APP_HOST") else None)
    or "http://localhost:3010"
)

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]

DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "oss")
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
AUTH_PROVIDER = os.getenv("AUTH_PROVIDER", "local")
ENABLE_SIGNUP = os.getenv("ENABLE_SIGNUP", "true").lower() == "true"
# Stack Auth public client config. These are safe to expose to the browser (the
# publishable client key is public by design, and the project id is non-sensitive),
# and are served to the UI at runtime via /api/v1/health so the frontend no longer
# needs them baked into the bundle at build time.
STACK_AUTH_PROJECT_ID = os.getenv("STACK_AUTH_PROJECT_ID")
STACK_PUBLISHABLE_CLIENT_KEY = os.getenv("STACK_PUBLISHABLE_CLIENT_KEY")
# Sign in with Google, layered on top of local auth rather than replacing it.
# Unset on a deployment that cannot reach Google — an air-gapped install, or
# one that simply does not want it — and the button never renders and the
# routes refuse; nothing else about authentication changes.
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
DECIBYL_MPS_SECRET_KEY = os.getenv("DECIBYL_MPS_SECRET_KEY", None)
# The Model Proxy Service. Three features still call it — recording
# transcription, service keys, and agent generation (which builds locally when
# the call fails). Knowledge base ingestion used to be a fourth, and was the
# reason an unset variable here could block a launch: it had no local path at
# all.
#
# The default is kept because the managed product genuinely runs at that host,
# but whether it was *chosen* is now recorded. A deployment that never set this
# variable and cannot reach that hostname should be told so by readiness rather
# than by a customer's failed upload.
DEFAULT_MPS_API_URL = "https://services.decibyl.ai"
MPS_API_URL = os.getenv("MPS_API_URL") or DEFAULT_MPS_API_URL
MPS_API_URL_IS_DEFAULT = not os.getenv("MPS_API_URL")

# Which backend converts and chunks an uploaded knowledge base document:
# "local" (in this process, no network), "mps" (delegate, and fail if it is
# down), or "auto" (MPS when configured, local on any failure).
#
# Local is the default deliberately. Ingestion is the one path where a remote
# dependency has no fallback worth the name — a customer uploads a policy
# document and either gets a knowledge base or gets nothing — so the default
# must be the one that works on a deployment where nothing else is stood up.
KB_DOCUMENT_PROCESSOR = (os.getenv("KB_DOCUMENT_PROCESSOR") or "local").strip().lower()
DECIBYL_DEVOPS_SECRET = os.getenv("DECIBYL_DEVOPS_SECRET") or None

# Decibyl's own Plivo account — the parent under which managed numbers are
# bought and compliance applications are filed on customers' behalf. Distinct
# from the per-organization credentials on telephony_configurations, which are
# a customer's own carrier account and are never used for this.
#
# PLATFORM_PLIVO_APPLICATION_ID is the Plivo Application whose answer_url
# points at our inbound dispatcher. Numbers are bought with this app_id set, so
# the number-to-application link needs no console step.
PLATFORM_PLIVO_AUTH_ID = os.getenv("PLATFORM_PLIVO_AUTH_ID") or None
PLATFORM_PLIVO_AUTH_TOKEN = os.getenv("PLATFORM_PLIVO_AUTH_TOKEN") or None
PLATFORM_PLIVO_APPLICATION_ID = os.getenv("PLATFORM_PLIVO_APPLICATION_ID") or None

# What we pay the carrier per number per month, and what we charge for it, both
# in paise. Recorded as two separate numbers because recording only the retail
# price is how a margin figure starts lying — see api/services/billing/rentals.py.
#
# Defaults are the India local-DID figures the launch plan was costed on.
# Confirm against Plivo's live price list before relying on the margin.
NUMBER_RENTAL_COST_PAISE = int(os.getenv("NUMBER_RENTAL_COST_PAISE", "25000"))
NUMBER_RENTAL_PRICE_PAISE = int(os.getenv("NUMBER_RENTAL_PRICE_PAISE", "34900"))

# What we charge for provider usage on *our* keys, as basis points of what the
# vendor charges us. 13000 = 1.30x; 10000 would be at cost.
#
# In basis points rather than a float because every other money path in this
# codebase is integer arithmetic, and a 1.3 that is really 1.2999999999999998
# reconciles differently depending on the order lines are summed.
#
# It applies only to managed components. A customer on their own key is charged
# nothing for provider usage at all — they already paid the vendor — which is
# enforced upstream in services/billing/usage.py, where a BYOK component
# produces no line to mark up.
#
# The vendor's true cost is stored alongside the marked-up figure rather than
# replaced by it. Baking the markup into the rate card instead would have made
# provider_rates hold retail, and every margin figure on the unit-economics
# screen would silently read zero.
MANAGED_PROVIDER_MARKUP_BPS = int(os.getenv("MANAGED_PROVIDER_MARKUP_BPS", "14000"))

# Whether customers can start telephony verification at all.
#
# The flow depends on an ISV/reseller arrangement with the carrier — subaccounts
# created per end customer, each verified in its own name. Until that account
# exists, collecting incorporation documents we cannot forward is worse than
# saying "not yet": it asks for identity records, stores them under DPDP
# obligations, and delivers nothing.
#
# Flip to true once the carrier has approved the reseller account. Everything
# behind it is built and tested; this only controls whether the door is open.
MANAGED_TELEPHONY_ENABLED = (
    os.getenv("MANAGED_TELEPHONY_ENABLED", "false").lower() == "true"
)

# Razorpay. Decibyl sells prepaid credit, so these take money in; nothing here
# ever moves money out.
#
# The webhook secret is separate from the API secret on purpose: it is the only
# thing authenticating an inbound webhook, since Razorpay cannot present a
# bearer token. Without it the webhook route refuses every request rather than
# accepting unverified ones — an unauthenticated credit endpoint would let
# anyone top up any account for free.
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID") or None
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET") or None
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET") or None
RAZORPAY_API_BASE = os.getenv("RAZORPAY_API_BASE", "https://api.razorpay.com/v1")

# Autopay for the monthly number rental. Razorpay Subscriptions is a separate
# product from the one-off order flow above and needs its own activation on the
# account — their approval, not our configuration.
#
# The plan is created once and reused. Pin its id here after creating it, or
# leave unset and let the first mandate create one; pinning is preferable
# because a plan created per environment quietly fragments the reporting.
RAZORPAY_RENTAL_PLAN_ID = os.getenv("RAZORPAY_RENTAL_PLAN_ID") or None

# Whether a number may only be issued against an authorised mandate.
#
# On by default, because the alternative is the failure this whole path exists
# to prevent: a number we pay a carrier for every month, attached to an account
# whose prepaid balance ran out, with nothing standing behind the rent. Set it
# false only while Subscriptions activation is still pending — with it false the
# rental falls back to the prepaid balance and the dunning schedule.
REQUIRE_MANDATE_FOR_NUMBERS = (
    os.getenv("REQUIRE_MANDATE_FOR_NUMBERS", "true").lower() != "false"
)

# Google OAuth, for the google_calendar tool's "Connect Google Calendar" flow.
# This is our OAuth app's identity, not a per-organization secret — every
# organization that connects a calendar authorizes against the same client,
# and what is per-organization is the refresh token that flow produces (see
# GoogleCalendarConnectionModel). Registered in Google Cloud Console with
# redirect URI {BACKEND_API_ENDPOINT}/api/v1/integrations/google-calendar/callback.
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID") or None
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or None
# IANA zone name events are created in. Google's API takes a zone name rather
# than a UTC offset, so "Asia/Kolkata" is correct across DST-free India
# year-round; a deployment outside India overrides this.
GOOGLE_CALENDAR_DEFAULT_TIMEZONE = os.getenv(
    "GOOGLE_CALENDAR_DEFAULT_TIMEZONE", "Asia/Kolkata"
)

# Bounds on a single top-up. The floor keeps card fees from exceeding the
# credit bought; the ceiling is a guard against a mistyped amount, not a
# business limit — raise it deliberately for an enterprise invoice.
MIN_TOPUP_PAISE = int(os.getenv("MIN_TOPUP_PAISE", "10000"))  # Rs 100
MAX_TOPUP_PAISE = int(os.getenv("MAX_TOPUP_PAISE", "50000000"))  # Rs 5,00,000

# Refuse a run when the account has no credit, and hold an estimate while each
# call is live. On by default: prepaid with the gate switched off is postpaid
# with extra steps, and the failure it prevents — an account running up a bill
# it never intended to pay — is only ever discovered after the money is gone.
# Set false for a deployment that settles some other way.
BALANCE_ENFORCEMENT_ENABLED = (
    os.getenv("BALANCE_ENFORCEMENT_ENABLED", "true").lower() == "true"
)

# How many minutes of a typical call to hold while it runs. Not a call-length
# limit and not a worst case — a hold sized for the longest call anyone might
# make would stop a funded account running the calls it is paying for, and the
# ledger ends up exact either way because the hold is released and the true
# cost debited.
RESERVATION_MINUTES = int(os.getenv("RESERVATION_MINUTES", "5"))

# When to assume a hold belongs to a call that died rather than one still
# running. Must exceed the longest call anyone plausibly makes: sweeping a live
# call's hold puts the account back over its balance while it is still spending.
RESERVATION_MAX_AGE_MINUTES = int(os.getenv("RESERVATION_MAX_AGE_MINUTES", "180"))

# Free credit for a new account, in micro-dollars. $5 by default.
#
# Denominated in dollars like the list price, and converted at the FX rate in
# force when the account signs up — a rupee figure would quietly get cheaper in
# dollar terms every time the rupee weakened.
#
# It is a gift, not a sale: no GST, no receipt voucher, and it lands as a
# `trial` ledger row so revenue reporting can always separate given credit from
# bought credit. Set to 0 to switch it off.
SIGNUP_BONUS_MICROS_USD = int(os.getenv("SIGNUP_BONUS_MICROS_USD", "5000000"))

# --- Data retention -----------------------------------------------------------
#
# How long call data is kept before it is deleted. Storage limitation is
# required by DPDP s8(7) and GDPR Art 5(1)(e), and it is the one privacy
# obligation no document can satisfy — only a job that deletes things does.
#
# Audio and text age separately on purpose. A recording is a person's voice,
# among the most identifying data there is and rarely useful a month later. A
# transcript is text: far less sensitive, and what reporting and quality review
# actually read. A single window would be too short to run a business on or too
# long to defend.
#
# Per-organization overrides live in `data_retention_policies`; these are the
# defaults for an account that has set nothing.
DEFAULT_RECORDING_RETENTION_DAYS = int(
    os.getenv("DEFAULT_RECORDING_RETENTION_DAYS", "90")
)
DEFAULT_TRANSCRIPT_RETENTION_DAYS = int(
    os.getenv("DEFAULT_TRANSCRIPT_RETENTION_DAYS", "365")
)

# Spoken before the greeting on every call, unless a workflow opts out.
#
# Twelve US states require two-party consent to record a call, Indian telecom
# rules expect disclosure, and DPDP treats the recording as personal data
# collected from the person who answered. The industry answer is the same
# everywhere: say so in the agent's first turn.
#
# On by default. A customer who wants it off has to turn it off deliberately,
# rather than never discovering the setting existed — the failure of omission
# is the one that ends up in front of a regulator.
#
# The disclosure needs no separate consent record: it is spoken into the call,
# so it appears in the recording and the transcript. The evidence that it
# happened is the artefact itself, which is stronger than any flag we could set
# beside it.
RECORDING_DISCLOSURE_ENABLED = (
    os.getenv("RECORDING_DISCLOSURE_ENABLED", "true").lower() == "true"
)
RECORDING_DISCLOSURE_TEXT = os.getenv(
    "RECORDING_DISCLOSURE_TEXT",
    "Just so you know, this call is recorded for quality and training purposes.",
)

# Follow the caller's language mid-call.
#
# An agent is configured with one language; a caller in India frequently is not.
# They open in English, switch to Hindi when the conversation gets substantive,
# and mix both in a sentence. With this on, the agent's voice follows — after
# two consecutive turns in the new language, so a single mis-detection cannot
# flip it.
#
# Requires an STT in multilingual mode to be useful: Deepgram already defaults
# to "multi", so detection has been running all along with nothing reading it.
# Realtime speech-to-speech models ignore this entirely — they hear the caller
# directly and already answer in the language they hear.
FOLLOW_CALLER_LANGUAGE = os.getenv("FOLLOW_CALLER_LANGUAGE", "false").lower() == "true"

# ─── Database connection pool ────────────────────────────────────────────────
#
# SQLAlchemy defaults to 5 connections with 10 overflow. That ceiling is
# invisible until concurrency crosses it, and then it does not raise — callers
# *queue* on a connection while a live caller hears silence. It presents as
# latency, so it gets blamed on the model rather than on the pool.
#
# Size against peak *concurrent calls*, not request rate: every in-flight call
# touches the database several times over its life. A rough rule is
#   pool_size >= peak_concurrent_calls / FASTAPI_WORKERS
# with overflow for the bursts, and Postgres max_connections above the total
# across every worker, the arq worker and the campaign dispatcher.
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "20"))
DB_POOL_MAX_OVERFLOW = int(os.getenv("DB_POOL_MAX_OVERFLOW", "20"))

# Seconds a caller waits for a connection before giving up. Deliberately short:
# a call that cannot get a connection in ten seconds has already failed from the
# listener's point of view, and failing fast frees the slot for the next one.
DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "10"))

# Concurrent post-call jobs. Raise with call volume: a backlog here does not
# drop calls, it delays costing and makes the billing dashboard report stale
# numbers, which looks like a reporting bug rather than a queue depth problem.
ARQ_MAX_JOBS = int(os.getenv("ARQ_MAX_JOBS", "10"))

# ─── Database backups ────────────────────────────────────────────────────────
#
# The credit ledger is the only record of what every customer has paid, and it
# cannot be rebuilt from anywhere else — Razorpay knows what was charged but not
# what was consumed, reserved or adjusted, and every issued tax invoice is
# numbered against rows that live only here.
#
# On by default. A deployment that has to opt in to backups is a deployment
# without backups.
BACKUP_ENABLED = os.getenv("BACKUP_ENABLED", "true").lower() != "false"

# How long dumps are kept. A dump is a copy of every phone number and recording
# reference in the system, so it ages under the same obligation as the data
# inside it — backups that quietly exempt themselves from retention are not a
# retention policy.
BACKUP_RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))

# Older than this and the nightly job has not completed. Set above 24 hours so
# one missed run is a warning rather than an alarm, but well under two days so a
# silently dead worker is caught on the second morning rather than the tenth.
BACKUP_STALE_AFTER_HOURS = int(os.getenv("BACKUP_STALE_AFTER_HOURS", "36"))

# How often the background worker records that it is alive, and how long
# without one before it is presumed dead.
#
# One container runs uvicorn and the ARQ worker together, so the API keeps
# answering 200 when the worker dies — and calls silently stop being costed
# while invoices stop being issued. Nothing else in the system notices, which
# is what makes this worth its own signal rather than inferring it from the
# absence of costed calls (a quiet night looks identical).
#
# Five minutes tolerates a few missed beats and a restart without alarming,
# and is short enough that a dead worker is caught inside one business hour
# rather than at the next invoice run.
WORKER_HEARTBEAT_INTERVAL_SECONDS = int(
    os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "60")
)
WORKER_HEARTBEAT_STALE_AFTER_SECONDS = int(
    os.getenv("WORKER_HEARTBEAT_STALE_AFTER_SECONDS", "300")
)

# DPDP s13 requires a named, contactable person for data protection grievances,
# published where a Data Principal can find it. Surfaced through the API so the
# app and the marketing site cannot drift apart on who it is.
GRIEVANCE_OFFICER_NAME = os.getenv("GRIEVANCE_OFFICER_NAME", "")
GRIEVANCE_OFFICER_EMAIL = os.getenv("GRIEVANCE_OFFICER_EMAIL", "privacy@decibyl.ai")
GRIEVANCE_OFFICER_ADDRESS = os.getenv("GRIEVANCE_OFFICER_ADDRESS", "")

# --- GST ---------------------------------------------------------------------
#
# Who is supplying, for the top half of every invoice. These are facts about one
# legal entity rather than settings, but they are configuration because a
# self-hosted or second-entity deployment supplies as somebody else, and a
# GSTIN compiled into source is one nobody can correct without a release.
#
# SUPPLIER_STATE_CODE decides intra-state (CGST+SGST) versus inter-state (IGST)
# for every domestic customer, so it is the single most consequential value
# here. It is the first two digits of the GSTIN.
SUPPLIER_LEGAL_NAME = os.getenv("SUPPLIER_LEGAL_NAME", "")
SUPPLIER_GSTIN = os.getenv("SUPPLIER_GSTIN", "")
SUPPLIER_STATE_CODE = os.getenv("SUPPLIER_STATE_CODE", "") or (
    SUPPLIER_GSTIN[:2] if len(SUPPLIER_GSTIN) >= 2 else ""
)
SUPPLIER_ADDRESS = os.getenv("SUPPLIER_ADDRESS", "")
SUPPLIER_PAN = os.getenv("SUPPLIER_PAN", "")

# 18% on services, held in basis points so the rate is an exact integer and a
# future 18.5% needs no code change. Two-slab structure since the 2025 reform;
# software, SaaS and telecom all sit at 18%.
GST_RATE_BASIS_POINTS = int(os.getenv("GST_RATE_BASIS_POINTS", "1800"))

# SAC printed on the invoice. Defaulted rather than fixed because the right code
# for a voice-AI platform is a judgement between IT services and
# telecommunications — confirm it with your accountant before the first filing.
SUPPLIER_SAC_CODE = os.getenv("SUPPLIER_SAC_CODE", "998314")

# Whether a Letter of Undertaking is on file. Without one an export is not
# zero-rated — IGST is payable and reclaimed by refund — so this defaults to
# false and exports are refused rather than silently invoiced at zero.
SUPPLIER_HAS_LUT = os.getenv("SUPPLIER_HAS_LUT", "false").lower() == "true"
SUPPLIER_LUT_NUMBER = os.getenv("SUPPLIER_LUT_NUMBER", "")

# Document number prefixes. GST requires a consecutive serial unique within a
# financial year, at most 16 characters, so these are short by necessity:
# "INV/26-27/000001" is exactly 16.
INVOICE_NUMBER_PREFIX = os.getenv("INVOICE_NUMBER_PREFIX", "INV")
RECEIPT_NUMBER_PREFIX = os.getenv("RECEIPT_NUMBER_PREFIX", "RV")

# --- Outbound email ------------------------------------------------------------
#
# SMTP rather than a named provider's API: every provider that matters (SES,
# Sendgrid, Mailgun, Postmark, or a plain Gmail/Workspace account) speaks SMTP,
# so one client works everywhere and switching providers is a credential change,
# not a code change. There is no default host — mail is off until an operator
# points it somewhere, the same posture as Razorpay and Google OAuth.
SMTP_HOST = os.getenv("SMTP_HOST") or None
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME") or None
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") or None
# STARTTLS on an explicit connection (587) vs. implicit TLS from connect (465).
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS") or None
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Decibyl")

# Fernet key encrypting the platform's own provider API keys at rest. Generate
# with: python -c "from cryptography.fernet import Fernet;
# print(Fernet.generate_key().decode())"
#
# Deliberately has no default. A fallback would mean a deployment that forgot to
# set it still "worked", with every OpenAI and Deepgram key on the platform
# encrypted under a value published in this file. Storing a key without it
# raises instead — see services/configuration/platform_credentials.py.
PLATFORM_CREDENTIAL_SECRET = os.getenv("PLATFORM_CREDENTIAL_SECRET") or None

# Storage Configuration
ENABLE_AWS_S3 = os.getenv("ENABLE_AWS_S3", "false").lower() == "true"

# MinIO Configuration
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
# Full URL (scheme + host) browsers use to reach object storage. Derives from
# DECIBYL_API_HOST, then PUBLIC_BASE_URL (remote nginx proxies /voice-audio/ to
# MinIO); set explicitly only to point object storage at a separate origin.
#
# The API host, not the app host: on a split-hostname install nginx proxies
# /voice-audio/ from the API host alone, deliberately, so the SDK and the
# dashboard both fetch recordings from exactly one name. Deriving this from
# PUBLIC_BASE_URL pointed every presigned recording URL at the root host, where
# there is no /voice-audio/ location at all.
MINIO_PUBLIC_ENDPOINT = (
    os.getenv("MINIO_PUBLIC_ENDPOINT")
    or _SUBDOMAIN_API_BASE_URL
    or PUBLIC_BASE_URL
    or "http://localhost:9000"
)
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "voice-audio")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# Whether to grant the recordings bucket an anonymous read/write/delete policy.
#
# Off by default, and it must stay off anywhere the MinIO endpoint is reachable
# from outside the machine. This bucket holds recordings and transcripts of real
# conversations; the policy it enables lets anyone who can reach the endpoint
# read any object whose URL they guess, and overwrite or delete it.
#
# Access is by presigned URL instead, which needs no bucket policy at all. This
# exists only for a local stack where signing across two hostnames is a nuisance.
MINIO_PUBLIC_BUCKET = os.getenv("MINIO_PUBLIC_BUCKET", "false").lower() == "true"

# KYC documents live in their own bucket, never alongside call recordings.
# They are incorporation certificates, tax certificates and identity documents
# — sensitive personal and business data under the DPDP Act — and need a
# tighter access policy and retention rule than call audio. Keeping them in a
# separate bucket makes that policy expressible at the bucket level rather
# than hoping every code path remembers.
KYC_BUCKET = os.getenv("KYC_BUCKET", "kyc-documents")

# AWS S3 Configuration
S3_BUCKET = os.environ.get("S3_BUCKET")
# ap-south-1 (Mumbai), not us-east-1. This bucket holds call recordings and
# transcripts — conversations with people in India — and the region is where
# that personal data comes to rest. Defaulting to Virginia put it offshore for
# every deployment that never set the variable, and a data-residency mistake of
# that kind is invisible until someone asks where the recordings live.
S3_REGION = os.environ.get("S3_REGION", "ap-south-1")
# Optional overrides for S3-compatible backends (e.g. MinIO, rustfs, Ceph).
# S3_ENDPOINT_URL: full URL of a custom S3 endpoint (e.g. "https://s3.example.com").
#   Leave unset to use AWS's default endpoint resolution.
# S3_SIGNATURE_VERSION: botocore signature version used to sign requests and
#   presigned URLs. Defaults to None (botocore's default, currently SigV2 for
#   presigned URLs). Set to "s3v4" for S3-compatible servers that require SigV4.
# S3_ADDRESSING_STYLE: "auto" (default), "path", or "virtual". Many S3-compatible
#   servers and TLS setups require "path".
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
S3_SIGNATURE_VERSION = os.environ.get("S3_SIGNATURE_VERSION")
S3_ADDRESSING_STYLE = os.environ.get("S3_ADDRESSING_STYLE")

# Sentry configuration
SENTRY_DSN = os.getenv("SENTRY_DSN")

# PostHog configuration
POSTHOG_API_KEY = os.getenv("POSTHOG_API_KEY")
POSTHOG_HOST = os.getenv("POSTHOG_HOST", "https://us.i.posthog.com")


ENABLE_ARI_STASIS = os.getenv("ENABLE_ARI_STASIS", "false").lower() == "true"
SERIALIZE_LOG_OUTPUT = os.getenv("SERIALIZE_LOG_OUTPUT", "false").lower() == "true"

# Logging configuration
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", None)
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()

# Log rotation configuration
LOG_ROTATION_SIZE = os.getenv("LOG_ROTATION_SIZE", "100 MB")
LOG_RETENTION = os.getenv("LOG_RETENTION", "7 days")
LOG_COMPRESSION = os.getenv("LOG_COMPRESSION", "gz")
ENABLE_TELEMETRY = os.getenv("ENABLE_TELEMETRY", "true").lower() == "true"


def _get_version() -> str:
    """Read version from pyproject.toml."""
    try:
        import tomllib

        pyproject_path = APP_ROOT_DIR / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            pyproject = tomllib.load(f)
        return pyproject.get("project", {}).get("version", "dev")
    except Exception:
        return "dev"


# Application version (read from pyproject.toml)
APP_VERSION = _get_version()

# Country code mapping: ISO country code -> international dialing prefix
COUNTRY_CODES = {
    "US": "1",  # United States
    "CA": "1",  # Canada
    "GB": "44",  # United Kingdom
    "IN": "91",  # India
    "AU": "61",  # Australia
    "DE": "49",  # Germany
    "FR": "33",  # France
    "BR": "55",  # Brazil
    "MX": "52",  # Mexico
    "IT": "39",  # Italy
    "ES": "34",  # Spain
    "NL": "31",  # Netherlands
    "SE": "46",  # Sweden
    "NO": "47",  # Norway
    "DK": "45",  # Denmark
    "FI": "358",  # Finland
    "CH": "41",  # Switzerland
    "AT": "43",  # Austria
    "BE": "32",  # Belgium
    "LU": "352",  # Luxembourg
    "IE": "353",  # Ireland
}

# Floor at 1 so a misconfigured env var (0 or negative) can't silently block
# every call in the deployment.
DEFAULT_ORG_CONCURRENCY_LIMIT = max(
    1, int(os.getenv("DEFAULT_ORG_CONCURRENCY_LIMIT", "10"))
)
DEFAULT_CAMPAIGN_RETRY_CONFIG = {
    "enabled": True,
    "max_retries": 1,
    "retry_delay_seconds": 120,
    "retry_on_busy": True,
    "retry_on_no_answer": True,
    "retry_on_voicemail": False,
}


# Outbound webhook delivery: bounded retry with exponential backoff.
# Delivery is persisted (see WebhookDeliveryModel) and retried by an ARQ task so a
# transient network error can't permanently drop a final webhook. After
# ``max_attempts`` transient failures the delivery is parked as ``dead_letter``.
DEFAULT_WEBHOOK_DELIVERY_CONFIG = {
    "max_attempts": int(os.getenv("WEBHOOK_DELIVERY_MAX_ATTEMPTS", 5)),
    "base_delay_seconds": int(os.getenv("WEBHOOK_DELIVERY_BASE_DELAY_SECONDS", 30)),
    "max_delay_seconds": int(os.getenv("WEBHOOK_DELIVERY_MAX_DELAY_SECONDS", 600)),
    "timeout_seconds": int(os.getenv("WEBHOOK_DELIVERY_TIMEOUT_SECONDS", 30)),
}


# Circuit breaker defaults for campaign call failure detection
DEFAULT_CIRCUIT_BREAKER_CONFIG = {
    "enabled": True,
    "failure_threshold": 0.5,  # 50% failure rate trips the breaker
    "window_seconds": 120,  # 2-minute sliding window
    "min_calls_in_window": 5,  # Don't trip until at least 5 outcomes
}


TURN_SECRET = os.getenv("TURN_SECRET")
# Host browsers dial for TURN/ICE. Derives from PUBLIC_HOST; set explicitly only
# when the TURN server runs on a separate host from the app.
TURN_HOST = os.getenv("TURN_HOST") or PUBLIC_HOST or "localhost"
TURN_PORT = int(os.getenv("TURN_PORT", "3478"))
TURN_TLS_PORT = int(os.getenv("TURN_TLS_PORT", "5349"))
TURN_CREDENTIAL_TTL = int(os.getenv("TURN_CREDENTIAL_TTL", "86400"))
# Diagnostic flag: when true, strip all non-relay ICE candidates from the
# answer SDP so every media path must traverse the TURN server. Use for
# verifying TURN connectivity end-to-end; expect connection failures if
# TURN is misconfigured or unreachable.
FORCE_TURN_RELAY = os.getenv("FORCE_TURN_RELAY", "false").lower() == "true"

# OSS Email/Password Auth
OSS_JWT_SECRET = os.getenv("OSS_JWT_SECRET", "change-me-in-production")
OSS_JWT_EXPIRY_HOURS = int(os.getenv("OSS_JWT_EXPIRY_HOURS", "720"))  # 30 days

TUNER_BASE_URL = os.getenv("TUNER_BASE_URL", "https://api.usetuner.ai")


# ---------------------------------------------------------------------------
# TCCCPR compliance: do-not-disturb scrubbing and the calling window.
#
# Both are on by default and both are checked immediately before a number is
# dialled, not when a campaign is queued. A campaign started at 8pm dials into
# the night, so a check that ran only at start would pass once and then be
# wrong for every row after 9pm.
#
# The kill switch exists for deployments outside India's jurisdiction, where
# these particular hours and this particular registry do not apply. It is not
# there to be turned off during a busy week: a call placed to a registered
# number is a regulatory event, not a delivery failure.
# ---------------------------------------------------------------------------
DND_ENFORCEMENT_ENABLED = (
    os.getenv("DND_ENFORCEMENT_ENABLED", "true").lower() != "false"
)

# TCCCPR permits promotional calling between 09:00 and 21:00 in the recipient's
# local time. We hold the org's configured timezone as the proxy for that —
# see the note in services/compliance/dnd.py about why that is sound for a
# single-jurisdiction deployment and what would have to change otherwise.
CALLING_HOURS_START = os.getenv("CALLING_HOURS_START", "09:00")
CALLING_HOURS_END = os.getenv("CALLING_HOURS_END", "21:00")

# Fallback when an organization has set no timezone. India has no DST, so a
# fixed zone name is correct year-round here.
DEFAULT_ORGANIZATION_TIMEZONE = os.getenv(
    "DEFAULT_ORGANIZATION_TIMEZONE", "Asia/Kolkata"
)


# ---------------------------------------------------------------------------
# Verified test numbers.
#
# An account proves it can answer a number by receiving an SMS, and only then
# will we dial it. Three limits, each closing a different abuse: attempts per
# code (six digits falls to exhaustive guessing in under a second), sends per
# number (otherwise the endpoint is a free SMS cannon aimed at a stranger, on
# our carrier bill), and a cooldown between sends.
# ---------------------------------------------------------------------------
VERIFICATION_CODE_TTL_MINUTES = int(os.getenv("VERIFICATION_CODE_TTL_MINUTES", "10"))
VERIFICATION_MAX_ATTEMPTS = int(os.getenv("VERIFICATION_MAX_ATTEMPTS", "5"))
VERIFICATION_MAX_SENDS = int(os.getenv("VERIFICATION_MAX_SENDS", "5"))
VERIFICATION_RESEND_COOLDOWN_SECONDS = int(
    os.getenv("VERIFICATION_RESEND_COOLDOWN_SECONDS", "60")
)

# Whether a test call may only go to a number the account has verified.
#
# OFF by default, and that default is temporary. It should be true — dialling
# whatever string a user types is the harassment vector this mechanism exists
# to close — but a permission nobody can obtain is not a permission, it is an
# outage. VERIFICATION_CHANNEL is `log` on every real deployment today, which
# refuses to run outside dev, so with this on nobody could verify a number and
# every test call would be refused with no way forward.
#
# Flip it to true in the same change that makes delivery work (DLT registration
# for SMS, or a proven outbound path for voice). There is a test asserting the
# gate itself works, so the only thing this default controls is whether it is
# enforced before anyone can satisfy it.
#
# It does not affect campaigns or the trigger API either way: those dial numbers
# the customer supplies as data, and are governed by the DND list and the
# calling window instead.
REQUIRE_VERIFIED_TEST_NUMBER = (
    os.getenv("REQUIRE_VERIFIED_TEST_NUMBER", "false").lower() == "true"
)

# How a verification code reaches the person holding the phone.
#
#   log        development only; writes the code to the log and refuses to run
#              outside a dev/test ENVIRONMENT
#   voice      call the number and read the code out (not wired — see
#              services/telephony/verification_sender.py)
#   plivo_sms  SMS on Decibyl's own Plivo account
#
# Defaults to log so a deployment that has configured nothing fails visibly and
# safely rather than appearing to send codes it never sent.
#
# SMS to Indian numbers needs DLT registration first — a Principal Entity, a
# registered header, and a registered content template. Unregistered traffic is
# blocked by the operator, and the failure looks like success: Plivo accepts the
# request and the message never arrives.
VERIFICATION_CHANNEL = os.getenv("VERIFICATION_CHANNEL", "log")

# The sender number for platform-originated SMS, on Decibyl's own Plivo account.
# Distinct from a customer's from_numbers: the accounts this serves have no
# telephony configuration of their own, which is the whole reason the feature
# exists. Must be the number registered against the DLT header.
PLATFORM_SMS_FROM_NUMBER = os.getenv("PLATFORM_SMS_FROM_NUMBER") or None

# Decibyl's own Twilio account, the alternative to PLATFORM_PLIVO_* for
# platform-originated SMS. Having both is about not being locked to one carrier
# — it is NOT a way around DLT, which attaches to the sending entity and the
# Indian destination rather than to the carrier.
PLATFORM_TWILIO_ACCOUNT_SID = os.getenv("PLATFORM_TWILIO_ACCOUNT_SID") or None
PLATFORM_TWILIO_AUTH_TOKEN = os.getenv("PLATFORM_TWILIO_AUTH_TOKEN") or None
