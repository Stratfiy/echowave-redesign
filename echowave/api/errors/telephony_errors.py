"""
Telephony error constants and messages for inbound call validation.
Centralizes error handling across all telephony providers.
"""

from enum import Enum


class TelephonyError(Enum):
    """Telephony validation error types"""

    PROVIDER_MISMATCH = "PROVIDER_MISMATCH"
    WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"
    ACCOUNT_VALIDATION_FAILED = "ACCOUNT_VALIDATION_FAILED"
    PHONE_NUMBER_NOT_CONFIGURED = "PHONE_NUMBER_NOT_CONFIGURED"
    SIGNATURE_VALIDATION_FAILED = "SIGNATURE_VALIDATION_FAILED"
    CONCURRENT_CALL_LIMIT = "CONCURRENT_CALL_LIMIT"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    GENERAL_AUTH_FAILED = "GENERAL_AUTH_FAILED"
    VALID = "VALID"


# What went wrong, for the operator: logs and support, never the caller.
OPERATOR_ERROR_DETAILS = {
    TelephonyError.PROVIDER_MISMATCH: "Configuration error: This phone number is configured for a different telephony provider. Please check your dashboard settings and update your webhook URL configuration.",
    TelephonyError.WORKFLOW_NOT_FOUND: "Workflow not found. Please verify the workflow ID in your webhook URL is correct and the workflow exists in your dashboard.",
    TelephonyError.ACCOUNT_VALIDATION_FAILED: "Authentication error: Account credentials do not match. Please verify your account SID configuration in the dashboard matches your telephony provider settings.",
    TelephonyError.PHONE_NUMBER_NOT_CONFIGURED: "Phone number not configured: This number is not set up for inbound calls in your account. Please add this number to your telephony configuration.",
    TelephonyError.SIGNATURE_VALIDATION_FAILED: "Security error: Webhook signature validation failed. Please verify your auth token configuration and ensure requests are coming from your telephony provider.",
    TelephonyError.CONCURRENT_CALL_LIMIT: "Service temporarily unavailable: Your account has reached its concurrent call limit. Please try again later.",
    TelephonyError.QUOTA_EXCEEDED: "Service temporarily unavailable: Your account has exceeded usage limits. Please contact your administrator or upgrade your plan to continue receiving calls.",
    TelephonyError.GENERAL_AUTH_FAILED: "Authentication failed: Please check your webhook URL configuration and ensure your telephony provider settings match your dashboard configuration.",
}


# What the caller hears, spoken by every provider before it hangs up.
#
# These used to be the operator details above, read aloud. A member of the
# public who rang a business heard "Workflow not found. Please verify the
# workflow ID in your webhook URL" -- found on staging, when a paused agent's
# number was called. A caller can fix none of these and should not learn how
# the business is set up or billed, so each says only that the call cannot be
# taken and what to do. The detail stays in the logs (the dispatch logs the
# reason before it answers with one of these).
_CANNOT_TAKE = (
    "Sorry, this number can't take your call right now. Please try again later."
)

TELEPHONY_ERROR_MESSAGES = {
    TelephonyError.PROVIDER_MISMATCH: _CANNOT_TAKE,
    TelephonyError.WORKFLOW_NOT_FOUND: _CANNOT_TAKE,
    TelephonyError.ACCOUNT_VALIDATION_FAILED: _CANNOT_TAKE,
    TelephonyError.PHONE_NUMBER_NOT_CONFIGURED: _CANNOT_TAKE,
    TelephonyError.SIGNATURE_VALIDATION_FAILED: _CANNOT_TAKE,
    TelephonyError.CONCURRENT_CALL_LIMIT: "Sorry, all our lines are busy right now. Please try again in a few minutes.",
    TelephonyError.QUOTA_EXCEEDED: _CANNOT_TAKE,
    TelephonyError.GENERAL_AUTH_FAILED: _CANNOT_TAKE,
}
