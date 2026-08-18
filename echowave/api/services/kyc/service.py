"""Telephony KYC orchestration.

Sits between the routes and the data layer. The state machine in
:mod:`api.services.kyc.state` decides what is legal; this module applies it,
records who did what, and keeps the stored objects and their rows consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger

from api.constants import MANAGED_TELEPHONY_ENABLED
from api.db import db_client
from api.db.models import KycDocumentModel, OrganizationKycModel
from api.enums import KycBusinessType, KycDocumentKind, KycStatus
from api.services.kyc import documents as document_store
from api.services.kyc import validation
from api.services.kyc.carrier import CarrierVerdict, get_carrier
from api.services.kyc.state import (
    KycTransitionError,
    assert_transition,
    may_place_telephony_calls,
    missing_documents,
    required_documents,
)
from api.utils.common import get_backend_endpoints


@dataclass(frozen=True)
class KycView:
    """What a customer is told about their own verification."""

    status: str
    business_type: str | None
    legal_name: str | None
    gstin: str | None
    # The registered address. Returned so the form can show back what was
    # saved -- without it a reload presents empty inputs, and saving again
    # writes those blanks over an address the customer had already given.
    address_line1: str | None
    address_line2: str | None
    city: str | None
    region: str | None
    postal_code: str | None
    submitted_at: datetime | None
    rejection_reason: str | None
    carrier_rejection_reason: str | None
    required_documents: tuple[str, ...]
    missing_documents: tuple[str, ...]
    documents: tuple[dict, ...]
    can_submit: bool
    telephony_enabled: bool


def _document_summary(document: KycDocumentModel) -> dict:
    """A document as the customer and reviewer see it — never the storage key.

    The key is an internal locator; exposing it invites someone to try
    constructing a bucket URL from it.
    """
    return {
        "id": document.id,
        "kind": document.kind,
        "filename": document.filename,
        "content_type": document.content_type,
        "size_bytes": document.size_bytes,
        "uploaded_at": document.uploaded_at.isoformat()
        if document.uploaded_at
        else None,
    }


class KycValidationError(ValueError):
    """Pre-submit checks failed. Carries every problem, not just the first.

    A route turns ``problems`` into a field-keyed response so the form can mark
    each input; ``str(exc)`` stays readable for logs and for callers that only
    want a sentence.
    """

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__(" ".join(p.message for p in self.problems))


def build_end_user(record: OrganizationKycModel) -> dict:
    """The ``end_user`` object on a Plivo compliance application.

    ``name`` is the registered legal name and nothing else. It is compared
    character for character against the registration certificate at the
    carrier, which is why ``validation.check_name_match`` runs before anything
    reaches here — by this point a mismatch is already a rejection in waiting.
    """
    return {
        "name": (record.legal_name or "").strip(),
        "last_name": "",
        # ``type``, not ``user_type``. This dict is sent to Plivo verbatim as
        # the ``end_user`` object, and Plivo names the field ``type`` there —
        # it refuses the whole application with "end_user.type is required.
        # Allowed values: individual, business." The requirements *lookup*
        # endpoint elsewhere in plivo_compliance.py really does take
        # ``user_type``, which is where the wrong name came from; the two
        # endpoints disagree and only one of them is this one.
        "type": "business"
        if record.business_type == KycBusinessType.COMPANY.value
        else "individual",
        "country_iso": record.country_iso or "IN",
        "address": " ".join(
            part
            for part in [
                (record.address_line1 or "").strip(),
                (record.address_line2 or "").strip(),
            ]
            if part
        ),
        "city": (record.city or "").strip(),
        "region": (record.region or "").strip(),
        "postal_code": (record.postal_code or "").strip(),
        # Plivo takes the tax id as part of the end user for Indian business
        # applications. Absent for an individual, which is legitimate.
        "business_id_number": (record.gstin or "").strip(),
    }


async def compliance_callback_url() -> str:
    """Where Plivo posts compliance status changes.

    Resolved at call time rather than held in a constant because the backend
    endpoint is discovered (tunnel URLs in development), and a callback URL
    baked in at import would point at a tunnel that has since moved.
    """
    backend_endpoint, _ = await get_backend_endpoints()
    return f"{backend_endpoint}/api/v1/kyc/carrier-callback"


async def _carrier_documents(record: OrganizationKycModel) -> list[dict]:
    """Document metadata **and bytes**, as a carrier submission needs them.

    The summaries shown to reviewers deliberately omit the storage key; a
    carrier needs the file itself, so this is the one place that reads the
    objects back out of the bucket.
    """
    payload = []
    for document in record.documents or []:
        content = await document_store.read_document(document.storage_key)
        payload.append(
            {
                "kind": document.kind,
                "filename": document.filename,
                "content_type": document.content_type,
                "content": content,
            }
        )
    return payload


def build_view(record: OrganizationKycModel) -> KycView:
    """Shape a record for display, including what is still outstanding."""
    supplied = {d.kind for d in (record.documents or [])}
    missing = missing_documents(record.business_type, supplied)

    return KycView(
        status=record.status,
        business_type=record.business_type,
        legal_name=record.legal_name,
        gstin=record.gstin,
        address_line1=record.address_line1,
        address_line2=record.address_line2,
        city=record.city,
        region=record.region,
        postal_code=record.postal_code,
        submitted_at=record.submitted_at,
        rejection_reason=record.rejection_reason,
        carrier_rejection_reason=record.carrier_rejection_reason,
        required_documents=tuple(
            sorted(k.value for k in required_documents(record.business_type))
        ),
        missing_documents=tuple(sorted(k.value for k in missing)),
        documents=tuple(_document_summary(d) for d in (record.documents or [])),
        # Submitting with documents missing would only produce a rejection, so
        # the button is off until the set is complete.
        can_submit=(
            not missing
            and record.business_type is not None
            and record.status
            in {
                KycStatus.NOT_STARTED.value,
                KycStatus.REJECTED.value,
                KycStatus.CARRIER_REJECTED.value,
                # An expired application cannot be amended, so the only way
                # back is a fresh submission. Suspension is absent: only the
                # licensee lifts one, and inviting a resubmission would have
                # the customer re-uploading documents that will change nothing.
                KycStatus.EXPIRED.value,
            }
        ),
        telephony_enabled=may_place_telephony_calls(record.status),
    )


async def get_view(organization_id: int) -> KycView:
    record = await db_client.get_or_create_kyc(organization_id)
    return build_view(record)


async def set_business_details(
    *,
    organization_id: int,
    business_type: str,
    legal_name: str,
    gstin: str | None,
    address_line1: str | None = None,
    address_line2: str | None = None,
    city: str | None = None,
    region: str | None = None,
    postal_code: str | None = None,
) -> KycView:
    """Record who the customer is. Decides which documents are required.

    The address is part of this rather than a second form because the carrier
    needs it on the same application, and splitting it across two screens is
    how half of it ends up unfilled.
    """
    try:
        KycBusinessType(business_type)
    except ValueError as exc:
        raise ValueError("Choose a business type.") from exc

    await db_client.get_or_create_kyc(organization_id)
    record = await db_client.update_kyc(
        organization_id,
        business_type=business_type,
        # Kept exactly as typed apart from surrounding whitespace. The carrier
        # compares this against the registration certificate character for
        # character, so normalising case or punctuation here would silently
        # change what we submit.
        legal_name=(legal_name or "").strip() or None,
        gstin=(gstin or "").strip() or None,
        address_line1=(address_line1 or "").strip() or None,
        address_line2=(address_line2 or "").strip() or None,
        city=(city or "").strip() or None,
        region=(region or "").strip() or None,
        postal_code=(postal_code or "").strip() or None,
    )
    return build_view(record)


async def upload_document(
    *,
    organization_id: int,
    uploaded_by: int | None,
    kind: str,
    filename: str,
    content: bytes,
    content_type: str | None,
) -> KycView:
    """Store a document and attach it to this account's record.

    Uploading a kind that already exists replaces it, because the common case
    is a customer fixing a rejected scan and two copies would only make a
    reviewer guess which one counts.
    """
    try:
        KycDocumentKind(kind)
    except ValueError as exc:
        raise ValueError("Unknown document type.") from exc

    record = await db_client.get_or_create_kyc(organization_id)

    # Approved records are frozen. Re-verification means the carrier moved the
    # account back, not a customer quietly swapping a certificate.
    if record.status == KycStatus.CARRIER_APPROVED.value:
        raise KycTransitionError("Verification is complete; documents are locked.")

    key = await document_store.store_document(
        organization_id=organization_id,
        kind=kind,
        filename=filename,
        content=content,
        content_type=content_type,
    )

    superseded = [d for d in (record.documents or []) if d.kind == kind]

    await db_client.add_document(
        organization_id=organization_id,
        organization_kyc_id=record.id,
        kind=kind,
        storage_key=key,
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        uploaded_by=uploaded_by,
        # Recorded now, while the bytes are in hand. Computing it at submission
        # time would mean pulling every document back out of the bucket to
        # answer a question we could have answered here for free.
        content_sha256=validation.content_digest(content),
    )

    # Delete the row first: an orphaned object costs storage, but a row
    # pointing at a deleted object shows a reviewer a document that will not
    # open, which is worse.
    for old in superseded:
        removed_key = await db_client.delete_document(
            old.id, organization_id=organization_id
        )
        if removed_key is not None:
            await document_store.delete_document(removed_key)

    return build_view(await db_client.get_kyc(organization_id))


async def remove_document(*, organization_id: int, document_id: int) -> KycView:
    record = await db_client.get_or_create_kyc(organization_id)
    if record.status == KycStatus.CARRIER_APPROVED.value:
        raise KycTransitionError("Verification is complete; documents are locked.")

    removed_key = await db_client.delete_document(
        document_id, organization_id=organization_id
    )
    if removed_key is not None:
        await document_store.delete_document(removed_key)
    return build_view(await db_client.get_kyc(organization_id))


async def submit(organization_id: int) -> KycView:
    """Hand the account to us for review."""
    if not MANAGED_TELEPHONY_ENABLED:
        # Enforced here and not only in the UI: accepting documents we cannot
        # forward would take on DPDP custody of identity records in exchange
        # for nothing.
        raise ValueError(
            "Telephony verification is not open yet. We are completing the "
            "carrier arrangement and will let you know the moment it is."
        )

    record = await db_client.get_or_create_kyc(organization_id)

    supplied = {d.kind for d in (record.documents or [])}
    missing = missing_documents(record.business_type, supplied)
    if record.business_type is None:
        raise ValueError("Tell us your business type first.")
    if missing:
        readable = ", ".join(sorted(k.value.replace("_", " ") for k in missing))
        raise ValueError(f"Still needed: {readable}.")

    # Everything the carrier will reject for, caught while the customer is
    # still on the page. Reported together rather than one at a time — three
    # rejections spaced days apart is how a signup is abandoned.
    problems = validation.validate_for_submission(record)
    if problems:
        raise KycValidationError(problems)

    target = assert_transition(record.status, KycStatus.SUBMITTED)

    updated = await db_client.update_kyc(
        organization_id,
        status=target.value,
        submitted_at=datetime.now(UTC),
        # A resubmission answers the previous rejection, so the old reason
        # stops applying.
        rejection_reason=None,
        carrier_rejection_reason=None,
    )
    logger.info("KYC submitted for review by org {}", organization_id)
    return build_view(updated)


class TelephonyNotVerified(PermissionError):
    """Raised when an account tries to use a phone number before the licensee
    has verified it.

    Carries the current status so a caller can tell "you have not started" from
    "we are waiting on the carrier" without a second lookup.
    """

    def __init__(self, message: str, *, status: str) -> None:
        super().__init__(message)
        self.status = status


#: What to tell a customer at each point in the flow. Phrased as the next thing
#: they can do, because a bare "not verified" sends them to support.
_BLOCKED_MESSAGES = {
    KycStatus.NOT_STARTED.value: (
        "Phone calls need telephony verification. Upload your business "
        "documents to get started."
    ),
    KycStatus.SUBMITTED.value: (
        "Your verification documents are with our team. You can keep building "
        "and testing over the browser in the meantime."
    ),
    KycStatus.UNDER_REVIEW.value: (
        "Your verification is being reviewed. You can keep building and testing "
        "over the browser in the meantime."
    ),
    KycStatus.REJECTED.value: (
        "Your verification needs attention before you can place phone calls. "
        "See the reason on your verification page."
    ),
    KycStatus.FORWARDED.value: (
        "Your verification is with the telecom operator, who has to approve it "
        "before calls can be placed. This usually takes a few working days."
    ),
    KycStatus.CARRIER_REJECTED.value: (
        "The telecom operator did not approve your verification. See the reason "
        "on your verification page and submit again."
    ),
    KycStatus.SUSPENDED.value: (
        "The telecom operator has suspended your verification, so phone calls "
        "are paused. Your numbers are still yours. See the reason on your "
        "verification page — most suspensions are lifted once the document in "
        "question is updated."
    ),
    KycStatus.EXPIRED.value: (
        "Your telephony verification has expired and needs to be done again. "
        "Your numbers are still yours — upload current documents on your "
        "verification page to restore calling."
    ),
}

#: Carrier verdicts, mapped to the status each one puts an account into. A
#: table rather than a conditional because there are now five verdicts and
#: three of them are failures that mean different things: rejected is "fix and
#: resubmit", suspended is "reversible, we are paused", expired is "start
#: again". Collapsing them loses the sentence the customer needs.
_VERDICT_TO_STATUS: dict[CarrierVerdict, KycStatus] = {
    CarrierVerdict.APPROVED: KycStatus.CARRIER_APPROVED,
    CarrierVerdict.REJECTED: KycStatus.CARRIER_REJECTED,
    CarrierVerdict.SUSPENDED: KycStatus.SUSPENDED,
    CarrierVerdict.EXPIRED: KycStatus.EXPIRED,
}


async def assert_may_place_calls(organization_id: int) -> None:
    """Refuse a phone call from an account the licensee has not verified.

    Called at the points where *we* originate a call on a phone number. The
    carrier blocks these sub-accounts upstream anyway, so this is not the only
    thing standing between an unverified account and the PSTN — its job is to
    fail early with a sentence the customer can act on, instead of burning a
    concurrency slot on a call that dies at the trunk.

    WebRTC is deliberately not covered. No number, no licensee, so an account
    can build and test from minute one.
    """
    record = await db_client.get_kyc(organization_id)
    status = record.status if record else KycStatus.NOT_STARTED.value

    if may_place_telephony_calls(status):
        return

    raise TelephonyNotVerified(
        _BLOCKED_MESSAGES.get(
            status, "Phone calls need telephony verification to be completed."
        ),
        status=status,
    )


async def assert_configuration_may_place_calls(configuration) -> None:
    """Gate an outbound call about to go out on a specific telephony config.

    Bring-your-own configurations pass straight through. Their numbers sit
    under the customer's own carrier account, already verified in the
    customer's own name — our verification has nothing to say about them, and
    refusing those calls would break every self-hosted deployment.
    """
    if configuration is None or not getattr(
        configuration, "is_platform_managed", False
    ):
        return
    await assert_may_place_calls(configuration.organization_id)


# ---------------------------------------------------------------------------
# Staff review
#
# Our decisions. None of these unblocks calling on its own — forwarding sends
# the application to the licensee, and only the licensee's approval, recorded
# by record_carrier_verdict, opens telephony.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewItem:
    """One row of the staff review queue."""

    organization_id: int
    organization_name: str | None
    status: str
    business_type: str | None
    legal_name: str | None
    gstin: str | None
    submitted_at: datetime | None
    forwarded_at: datetime | None
    carrier: str | None
    carrier_status: str | None
    rejection_reason: str | None
    carrier_rejection_reason: str | None
    documents: tuple[dict, ...]


async def list_review_queue(*, statuses: list[str] | None = None) -> list[ReviewItem]:
    """Everything a reviewer might act on, oldest submission first.

    Defaults to the two states waiting on us plus the one waiting on the
    carrier, because a reviewer needs to see what is stuck on their side as
    well as what is stuck on ours.
    """
    statuses = statuses or [
        KycStatus.SUBMITTED.value,
        KycStatus.UNDER_REVIEW.value,
        KycStatus.FORWARDED.value,
    ]
    rows = await db_client.list_kyc_for_review(statuses=statuses)

    return [
        ReviewItem(
            organization_id=record.organization_id,
            # Organizations carry no display name of their own — provider_id is
            # the handle an operator can look up in the auth provider. The
            # human-readable name is legal_name, which the customer supplied.
            organization_name=org.provider_id,
            status=record.status,
            business_type=record.business_type,
            legal_name=record.legal_name,
            gstin=record.gstin,
            submitted_at=record.submitted_at,
            forwarded_at=record.forwarded_at,
            carrier=record.carrier,
            carrier_status=record.carrier_status,
            rejection_reason=record.rejection_reason,
            carrier_rejection_reason=record.carrier_rejection_reason,
            documents=tuple(_document_summary(d) for d in (record.documents or [])),
        )
        for record, org in rows
    ]


async def claim_for_review(*, organization_id: int, reviewer_id: int) -> KycView:
    """Mark that a reviewer has picked this up."""
    record = await db_client.get_or_create_kyc(organization_id)
    target = assert_transition(record.status, KycStatus.UNDER_REVIEW)
    updated = await db_client.update_kyc(
        organization_id,
        status=target.value,
        reviewed_by=reviewer_id,
    )
    return build_view(updated)


async def reject(*, organization_id: int, reviewer_id: int, reason: str) -> KycView:
    """Send it back to the customer with a reason.

    The reason is required: a rejection without one turns into a support
    conversation, which is the thing this queue exists to avoid.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Give a reason so the customer knows what to fix.")

    record = await db_client.get_or_create_kyc(organization_id)
    target = assert_transition(record.status, KycStatus.REJECTED)
    updated = await db_client.update_kyc(
        organization_id,
        status=target.value,
        reviewed_by=reviewer_id,
        reviewed_at=datetime.now(UTC),
        rejection_reason=reason,
    )
    logger.info("KYC rejected for org {} by user {}", organization_id, reviewer_id)
    return build_view(updated)


async def approve_and_forward(
    *, organization_id: int, reviewer_id: int, carrier_name: str | None = None
) -> KycView:
    """Accept the documents and send them to the carrier.

    Our approval is a pre-screen, so this does not enable calling. It moves the
    account to FORWARDED, where it waits for the licensee.
    """
    record = await db_client.get_or_create_kyc(organization_id)

    supplied = {d.kind for d in (record.documents or [])}
    missing = missing_documents(record.business_type, supplied)
    if missing:
        readable = ", ".join(sorted(k.value.replace("_", " ") for k in missing))
        raise ValueError(f"Cannot forward — still missing: {readable}.")

    problems = validation.validate_for_submission(record)
    if problems:
        raise KycValidationError(problems)

    # Validate before calling out, so a rejected transition does not leave an
    # application sitting at the carrier with no record on our side.
    target = assert_transition(record.status, KycStatus.FORWARDED)

    carrier = get_carrier(carrier_name)
    payload = await _carrier_documents(record)
    end_user = build_end_user(record)
    callback_url = await compliance_callback_url()

    # A previously rejected application is amended in place rather than filed
    # again. Plivo keeps the id and the history; filing a second application
    # for the same customer leaves two open and the carrier acting on whichever
    # it sees first.
    amendable = (
        record.status == KycStatus.CARRIER_REJECTED.value
        and record.carrier_reference
        and record.carrier == carrier.name
        and hasattr(carrier, "update")
    )
    if amendable:
        submission = await carrier.update(
            reference=record.carrier_reference,
            organization_id=organization_id,
            documents=payload,
            business_type=record.business_type or "",
            end_user=end_user,
            callback_url=callback_url,
        )
    else:
        submission = await carrier.submit(
            organization_id=organization_id,
            business_type=record.business_type or "",
            legal_name=record.legal_name,
            gstin=record.gstin,
            documents=payload,
            end_user=end_user,
            callback_url=callback_url,
        )

    updated = await db_client.update_kyc(
        organization_id,
        status=target.value,
        reviewed_by=reviewer_id,
        reviewed_at=datetime.now(UTC),
        rejection_reason=None,
        forwarded_at=datetime.now(UTC),
        carrier=carrier.name,
        carrier_reference=submission.reference,
        carrier_status=submission.status.value,
    )
    logger.info(
        "KYC for org {} forwarded to {} as {} by user {}",
        organization_id,
        carrier.name,
        submission.reference,
        reviewer_id,
    )
    return build_view(updated)


async def record_carrier_verdict(
    *,
    organization_id: int,
    verdict: CarrierVerdict,
    raw_status: str | None = None,
    rejection_reason: str | None = None,
) -> KycView | None:
    """Apply the licensee's decision. This is the call that opens telephony.

    Returns None when the verdict is still pending, so a poll can tell "nothing
    changed" from "moved on" without inspecting statuses itself.
    """
    record = await db_client.get_or_create_kyc(organization_id)

    if verdict is CarrierVerdict.PENDING:
        await db_client.update_kyc(
            organization_id,
            carrier_status=raw_status,
            carrier_checked_at=datetime.now(UTC),
        )
        return None

    target = _VERDICT_TO_STATUS[verdict]

    # An idempotent no-op, not an error. Plivo re-delivers callbacks, and a
    # second delivery of a verdict we already recorded must not raise — the
    # state machine would refuse carrier_approved -> carrier_approved, and a
    # 500 back to Plivo earns another redelivery.
    if record.status == target.value:
        await db_client.update_kyc(
            organization_id,
            carrier_status=raw_status,
            carrier_checked_at=datetime.now(UTC),
        )
        logger.debug(
            "Carrier verdict {} for org {} already recorded; ignoring repeat",
            verdict.value,
            organization_id,
        )
        return None

    assert_transition(record.status, target)

    fields = {
        "status": target.value,
        "carrier_status": raw_status,
        "carrier_checked_at": datetime.now(UTC),
    }
    if target is KycStatus.CARRIER_APPROVED:
        fields["carrier_approved_at"] = datetime.now(UTC)
        fields["carrier_rejection_reason"] = None
    else:
        # Suspension and expiry carry a reason too, and it is the sentence the
        # customer needs — "your GST certificate has lapsed" rather than a bare
        # status word on a dashboard.
        fields["carrier_rejection_reason"] = rejection_reason

    updated = await db_client.update_kyc(organization_id, **fields)
    logger.info("Carrier {} KYC for org {}", verdict.value, organization_id)
    return build_view(updated)
