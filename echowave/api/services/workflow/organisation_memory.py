"""What an organization's agents remember between calls.

Today an agent forgets everything the moment a call ends. The next call from
the same person starts from nothing and asks again for the address it was given
last week, which is the single most obvious way a machine announces itself as a
machine.

The facts already exist: every call's ``gathered_context.extracted_variables``
holds what the conversation collected. Nothing has ever read them afterwards.
This module promotes the durable ones into
:class:`~api.db.models.OrganisationFactModel` so the next call can start
knowing, and reads them back.

Three rules, and they are the whole of the care required.

**An agent's guess never overwrites the account's own record.** Contact
attributes are uploaded, typed, trusted. These are inferred by a model from
speech that may have been misheard. They live in a different table and lose
every collision -- see :func:`merge_for_prompt`.

**Not everything collected is worth remembering.** A name and a delivery
address describe the person and are true next month. "Preferred appointment
slot" describes one booking, and presenting it next time as a known fact is
worse than not knowing: the agent confidently tells a caller what they want
before they have said it. The decision is per key and it is deliberately
conservative -- see :data:`EPHEMERAL_SUFFIXES`.

**A remembered fact must be traceable to the call that produced it.** An
operator hearing their agent state something wrong has to be able to find where
it came from, and a fact nobody can falsify is worse in front of an agent than
no fact at all.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from loguru import logger

from api.db import db_client
from api.services.compliance.dnd import normalise_number
from api.services.workflow.known_values import INTERNAL_KEYS

SUBJECT_CONTACT = "contact"

#: Keys whose value describes one call rather than the person.
#:
#: Matched as suffixes because operators name variables in their own words and
#: "preferred_time", "callback_time" and "delivery_time" should all be caught
#: by one entry. The list errs toward forgetting: a fact wrongly forgotten costs
#: one repeated question, and a fact wrongly remembered has the agent tell a
#: caller something about themselves that is not true.
EPHEMERAL_SUFFIXES: tuple[str, ...] = (
    "_time",
    "_date",
    "_slot",
    "_today",
    "_now",
    "_reason",
    "_query",
    "_question",
    "_request",
    "_issue",
    "_complaint",
    "_otp",
    "_code",
    "_amount",
    "_quantity",
    "_qty",
)

#: Never remembered at any length, whatever they are called. An OTP is the
#: obvious one; storing one is a security defect rather than a product choice.
NEVER_REMEMBER: frozenset[str] = frozenset(
    {"otp", "code", "password", "pin", "cvv", "card_number", "captcha"}
)

#: A value longer than this is a sentence, not a fact. Whole answers land in
#: extracted variables sometimes, and putting a paragraph in front of the next
#: call as an established truth is how a prompt fills with noise.
MAX_FACT_CHARS = 200


def is_durable(key: str, value: Any) -> bool:
    """Whether this collected value describes the person rather than the call."""
    if not isinstance(key, str) or not key.strip():
        return False
    name = key.strip().lower()

    if name in INTERNAL_KEYS or name in NEVER_REMEMBER:
        return False
    if any(name.endswith(suffix) for suffix in EPHEMERAL_SUFFIXES):
        return False
    if any(part in NEVER_REMEMBER for part in name.split("_")):
        return False

    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (dict, list)):
        # Structure collected mid-call is a payload, not a fact about somebody.
        return False

    text = str(value).strip()
    return bool(text) and len(text) <= MAX_FACT_CHARS


def durable_facts(extracted: Mapping[str, Any] | None) -> dict[str, str]:
    """The subset of a call's collected values worth carrying forward."""
    if not isinstance(extracted, Mapping):
        return {}
    return {
        key.strip().lower(): str(value).strip()
        for key, value in extracted.items()
        if is_durable(key, value)
    }


#: Where a caller's number lands in a run's initial context, in the order the
#: dispatchers write it. Two spellings because inbound and outbound arrived at
#: different names and neither is wrong -- see campaign_call_dispatcher and
#: ari_manager.
PHONE_KEYS: tuple[str, ...] = ("phone_number", "caller_number", "phone", "to_number")


def subject_key_for_run(initial_context: Mapping[str, Any] | None) -> Optional[str]:
    """Who this call was with, as the key their facts hang off.

    The same normalized form contacts already match on, produced by the same
    normalizer, so a fact learned on a call and an attribute uploaded in a CSV
    describe the same person rather than two people who happen to share a phone.

    None for a browser test or a chat, which have no number and therefore no
    subject -- remembering those against a shared empty key would pool every
    anonymous visitor's facts into one imaginary person.
    """
    context = initial_context if isinstance(initial_context, Mapping) else {}
    for key in PHONE_KEYS:
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            normalised = normalise_number(value)
            if normalised:
                return normalised
    return None


async def promote_from_run(
    *,
    organization_id: Optional[int],
    workflow_run_id: Optional[int],
    gathered_context: Mapping[str, Any] | None,
    subject_key: Optional[str],
) -> int:
    """Remember what this call learned about whoever was on it.

    Returns how many facts were written, for the log. Never raises: this runs
    after the call, the caller has hung up, and a memory write that fails must
    not fail the post-call pipeline that also sends their confirmation.
    """
    if not organization_id or not subject_key:
        return 0

    context = gathered_context if isinstance(gathered_context, Mapping) else {}
    facts = durable_facts(context.get("extracted_variables"))
    if not facts:
        return 0

    try:
        written = await db_client.remember_facts(
            organization_id=organization_id,
            subject_type=SUBJECT_CONTACT,
            subject_key=subject_key,
            facts=facts,
            source_run_id=workflow_run_id,
        )
    except Exception as exc:  # noqa: BLE001 - memory must not break post-call work
        logger.warning("Could not remember facts from run {}: {}", workflow_run_id, exc)
        return 0

    logger.info(
        "Remembered {} fact(s) about {} from run {}",
        written,
        subject_key,
        workflow_run_id,
    )
    return written


async def recall_for_subject(
    *, organization_id: Optional[int], subject_key: Optional[str]
) -> dict[str, str]:
    """What is already known about this caller. Empty on any failure."""
    if not organization_id or not subject_key:
        return {}
    try:
        return await db_client.recall_facts(
            organization_id=organization_id,
            subject_type=SUBJECT_CONTACT,
            subject_key=subject_key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not recall facts for {}: {}", subject_key, exc)
        return {}


def merge_for_prompt(
    *, operator_data: Mapping[str, Any] | None, remembered: Mapping[str, str] | None
) -> dict[str, Any]:
    """Combine what the account told us with what the agent worked out.

    Operator data wins every collision, unconditionally. The account uploaded
    that address; we inferred this one from a phone line with a child shouting
    in the background. When the two disagree the account is right, and if it is
    not, that is theirs to correct in their own record rather than ours to
    quietly override.
    """
    merged: dict[str, Any] = dict(remembered or {})
    merged.update(
        {k: v for k, v in (operator_data or {}).items() if v not in (None, "")}
    )
    return merged
