"""Submitting a verification to the telecom carrier, and polling its verdict.

The carrier is the licensee. Under the DoT directive of June 2025 it is the
licensee that must verify the end user, so this is the leg that actually
unblocks calling — our staff review only decides what gets sent.

The Plivo implementation is a stub. Its compliance-application API shape has
not been confirmed, and inventing request fields would produce code that looks
finished and fails on first contact. The seam is real and the state machine
around it is tested; filling in :meth:`PlivoCarrier._submit` is a single
function body once the endpoints are confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from loguru import logger


class CarrierVerdict(str, Enum):
    """What the carrier says about an application."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class CarrierSubmission:
    """The carrier accepted our application and gave us a handle for it."""

    reference: str
    status: CarrierVerdict = CarrierVerdict.PENDING


@dataclass(frozen=True)
class CarrierStatus:
    """Where an application stands with the carrier."""

    verdict: CarrierVerdict
    raw_status: str | None = None
    rejection_reason: str | None = None


class CarrierNotConfigured(RuntimeError):
    """Raised when a carrier integration has not been wired up yet.

    Deliberately loud. A silent success here would mark an account approved
    without any licensee ever having seen it.
    """


class KycCarrier(Protocol):
    """What a carrier integration has to provide."""

    name: str

    #: Whether :meth:`check` talks to anything. False for a carrier moved on by
    #: hand — polling one would refresh ``carrier_checked_at`` on a schedule and
    #: imply we asked the licensee when nobody did.
    pollable: bool

    async def submit(
        self,
        *,
        organization_id: int,
        business_type: str,
        legal_name: str | None,
        gstin: str | None,
        documents: list[dict],
    ) -> CarrierSubmission: ...

    async def check(self, reference: str) -> CarrierStatus: ...


class PlivoCarrier:
    """Plivo's India compliance application.

    Plivo supports the ISV model — subaccounts per end customer, with each
    customer verified in its own name — and exposes an in-console compliance
    application for India. Whether the same can be driven fully over the API,
    and with which fields, is the open question blocking this implementation.
    """

    name = "plivo"
    pollable = True

    async def submit(
        self,
        *,
        organization_id: int,
        business_type: str,
        legal_name: str | None,
        gstin: str | None,
        documents: list[dict],
    ) -> CarrierSubmission:
        raise CarrierNotConfigured(
            "Plivo compliance submission is not wired up yet. Confirm whether "
            "the India compliance application can be submitted over the API "
            "and with which fields, then implement PlivoCarrier.submit."
        )

    async def check(self, reference: str) -> CarrierStatus:
        raise CarrierNotConfigured(
            "Plivo compliance status polling is not wired up yet."
        )


class ManualCarrier:
    """A carrier operated by hand, for before an API integration exists.

    Submitting records that we sent it; the verdict is entered by staff after
    they hear back. This is not a shortcut around verification — the account
    still sits in FORWARDED until a real approval arrives, and a human is
    asserting that the licensee approved it.
    """

    name = "manual"
    pollable = False

    async def submit(
        self,
        *,
        organization_id: int,
        business_type: str,
        legal_name: str | None,
        gstin: str | None,
        documents: list[dict],
    ) -> CarrierSubmission:
        logger.info(
            "KYC for org {} forwarded to the carrier manually ({} document(s))",
            organization_id,
            len(documents),
        )
        return CarrierSubmission(
            reference=f"manual:{organization_id}", status=CarrierVerdict.PENDING
        )

    async def check(self, reference: str) -> CarrierStatus:
        # Nothing to poll. A human moves this on.
        return CarrierStatus(verdict=CarrierVerdict.PENDING, raw_status="manual")


_CARRIERS: dict[str, KycCarrier] = {
    PlivoCarrier.name: PlivoCarrier(),
    ManualCarrier.name: ManualCarrier(),
}

#: Until Plivo's compliance API is confirmed, forwarding is recorded and the
#: verdict entered by staff. Change this once PlivoCarrier is implemented.
DEFAULT_CARRIER = ManualCarrier.name


def get_carrier(name: str | None = None) -> KycCarrier:
    carrier = _CARRIERS.get(name or DEFAULT_CARRIER)
    if carrier is None:
        raise CarrierNotConfigured(f"No carrier integration named {name!r}")
    return carrier
