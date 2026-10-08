"""What a caller hears when a call cannot be taken.

Found on staging: calling a number whose agent was paused read out "Workflow
not found. Please verify the workflow ID in your webhook URL..." to the
caller. The caller is a member of the public; the message is for the operator.
"""

from __future__ import annotations

import pytest

from api.errors.telephony_errors import (
    OPERATOR_ERROR_DETAILS,
    TELEPHONY_ERROR_MESSAGES,
    TelephonyError,
)

#: Words that describe how a business is set up or billed. None of them belongs
#: in what a caller hears.
SETUP_WORDS = (
    "workflow",
    "webhook",
    "dashboard",
    "configur",
    "credential",
    "account",
    "signature",
    "auth",
    "provider",
    "plan",
    "quota",
    "upgrade",
    "sid",
    "url",
)


@pytest.mark.parametrize(
    "error", [e for e in TelephonyError if e is not TelephonyError.VALID]
)
def test_every_error_has_a_caller_message_free_of_setup_words(error):
    spoken = TELEPHONY_ERROR_MESSAGES[error].lower()
    assert spoken.startswith("sorry")
    assert not [w for w in SETUP_WORDS if w in spoken], spoken


def test_the_operator_detail_is_kept_for_the_logs():
    assert (
        "workflow" in OPERATOR_ERROR_DETAILS[TelephonyError.WORKFLOW_NOT_FOUND].lower()
    )


@pytest.mark.parametrize(
    "provider", ["plivo", "twilio", "telnyx", "vobiz", "cloudonix"]
)
def test_each_provider_speaks_the_caller_message(provider):
    import importlib

    module = importlib.import_module(
        f"api.services.telephony.providers.{provider}.provider"
    )
    cls = next(
        getattr(module, n)
        for n in dir(module)
        if n.endswith("Provider")
        and hasattr(getattr(module, n), "generate_validation_error_response")
    )
    response = cls.generate_validation_error_response(TelephonyError.WORKFLOW_NOT_FOUND)
    body = (response[0] if isinstance(response, tuple) else response).body.decode()
    if body.lstrip().startswith("{"):
        # Telnyx answers its API with JSON: the error code is for machines,
        # the message is what is said.
        import json

        body = json.loads(body)["message"]
    assert "can't take your call" in body
    assert "workflow" not in body.lower()
