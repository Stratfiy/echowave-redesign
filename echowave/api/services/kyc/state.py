"""The telephony KYC state machine.

Two stages that must not be collapsed. Our review is a pre-screen: staff check
the documents are complete and legible so a carrier round-trip is not wasted on
a blurry scan. The carrier holds the telecom licence, and under the DoT
directive of June 2025 it is the licensee that must verify the end user — so
only the carrier's verdict may unblock calling.

That distinction is enforced here rather than left to callers:
:func:`may_place_telephony_calls` accepts exactly one status, and it is not the
one our own staff set.
"""

from __future__ import annotations

from api.enums import KycBusinessType, KycDocumentKind, KycStatus

#: Legal transitions. Anything absent is rejected, so a typo in a route cannot
#: quietly move an account into a state it did not earn.
_TRANSITIONS: dict[KycStatus, frozenset[KycStatus]] = {
    KycStatus.NOT_STARTED: frozenset({KycStatus.SUBMITTED}),
    # Staff can pick it up, or act on it directly without claiming it first.
    KycStatus.SUBMITTED: frozenset(
        {KycStatus.UNDER_REVIEW, KycStatus.REJECTED, KycStatus.FORWARDED}
    ),
    KycStatus.UNDER_REVIEW: frozenset({KycStatus.REJECTED, KycStatus.FORWARDED}),
    # A rejected account fixes its documents and submits again.
    KycStatus.REJECTED: frozenset({KycStatus.SUBMITTED}),
    KycStatus.FORWARDED: frozenset(
        {KycStatus.CARRIER_APPROVED, KycStatus.CARRIER_REJECTED}
    ),
    KycStatus.CARRIER_REJECTED: frozenset({KycStatus.SUBMITTED}),
    # Approval is terminal. Revocation is a carrier-side action we would learn
    # about through a status poll, and it is not modelled as a transition
    # anyone here can make.
    KycStatus.CARRIER_APPROVED: frozenset(),
}

#: Documents each business type must supply before we will forward.
_REQUIRED_DOCUMENTS: dict[KycBusinessType, frozenset[KycDocumentKind]] = {
    KycBusinessType.COMPANY: frozenset(
        {
            KycDocumentKind.CERTIFICATE_OF_INCORPORATION,
            KycDocumentKind.GST_CERTIFICATE,
        }
    ),
    KycBusinessType.INDIVIDUAL: frozenset(
        {
            KycDocumentKind.AUTHORISED_SIGNATORY_ID,
            KycDocumentKind.ADDRESS_PROOF,
        }
    ),
}


class KycTransitionError(ValueError):
    """Raised when a caller asks for a transition the machine does not allow."""


def can_transition(current: KycStatus | str, target: KycStatus | str) -> bool:
    """Whether ``current -> target`` is legal."""
    try:
        current = KycStatus(current)
        target = KycStatus(target)
    except ValueError:
        return False
    return target in _TRANSITIONS.get(current, frozenset())


def assert_transition(current: KycStatus | str, target: KycStatus | str) -> KycStatus:
    """Validate a transition and return the target, or raise."""
    if not can_transition(current, target):
        raise KycTransitionError(
            f"Cannot move KYC from {getattr(current, 'value', current)!r} "
            f"to {getattr(target, 'value', target)!r}"
        )
    return KycStatus(target)


def required_documents(
    business_type: KycBusinessType | str | None,
) -> frozenset[KycDocumentKind]:
    """Documents this business type must supply.

    An unknown or unset business type requires nothing, because we cannot say
    what is missing until the customer tells us what they are.
    """
    if business_type is None:
        return frozenset()
    try:
        return _REQUIRED_DOCUMENTS.get(KycBusinessType(business_type), frozenset())
    except ValueError:
        return frozenset()


def missing_documents(
    business_type: KycBusinessType | str | None,
    supplied: set[KycDocumentKind | str],
) -> frozenset[KycDocumentKind]:
    """What is still outstanding before this can be forwarded."""
    normalized: set[KycDocumentKind] = set()
    for kind in supplied:
        try:
            normalized.add(KycDocumentKind(kind))
        except ValueError:
            continue
    return frozenset(required_documents(business_type) - normalized)


def may_place_telephony_calls(status: KycStatus | str | None) -> bool:
    """Whether this account may place calls on a phone number.

    Exactly one status qualifies, and it is the carrier's, not ours. Our own
    ``FORWARDED`` means we are satisfied and waiting — the carrier still has
    the sub-account blocked, so permitting calls here would produce failures at
    best and a licence problem at worst.

    WebRTC is deliberately not covered: it involves no telephone number and no
    licensee, so agents can be built and tested from minute one.
    """
    try:
        return KycStatus(status) is KycStatus.CARRIER_APPROVED
    except ValueError:
        return False


def is_awaiting_our_review(status: KycStatus | str | None) -> bool:
    """Whether this sits in the staff review queue."""
    try:
        return KycStatus(status) in {KycStatus.SUBMITTED, KycStatus.UNDER_REVIEW}
    except ValueError:
        return False


def is_awaiting_carrier(status: KycStatus | str | None) -> bool:
    """Whether we are waiting on the carrier rather than on ourselves."""
    try:
        return KycStatus(status) is KycStatus.FORWARDED
    except ValueError:
        return False
