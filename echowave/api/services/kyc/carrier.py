"""Submitting a verification to the telecom carrier, and reading its verdict.

The carrier is the licensee. Under the DoT directive of June 2025 it is the
licensee that must verify the end user, so this is the leg that actually
unblocks calling — our staff review only decides what gets sent.

Plivo's compliance statuses do not line up one-for-one with ours, and the
mismatch is the interesting part of this module. Plivo has ``draft`` and
``submitted`` where we have one waiting state, and ``suspended`` and
``expired`` where we, until recently, had nothing at all. Everything crosses
that boundary through :data:`_PLIVO_STATUS_TO_VERDICT` — one table, total over
Plivo's documented set, raising on anything outside it. A status we have never
seen must not be guessed at: guessing ``accepted`` opens telephony for an
account the licensee did not approve.

Reference: https://www.plivo.com/docs/numbers/compliance
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from loguru import logger

from api.constants import (
    PLATFORM_PLIVO_AUTH_ID,
    PLATFORM_PLIVO_AUTH_TOKEN,
)
from api.enums import KycBusinessType, KycDocumentKind
from api.services.kyc.plivo_compliance import (
    ComplianceDocument,
    PlivoComplianceClient,
    PlivoComplianceError,
)


class CarrierVerdict(str, Enum):
    """What the carrier says about an application.

    Mirrors the set of outcomes a licensee can hand back, not the set our own
    review produces. ``SUSPENDED`` and ``EXPIRED`` exist because Plivo can move
    an already-accepted application into either without warning.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUSPENDED = "suspended"
    EXPIRED = "expired"


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


# ---------------------------------------------------------------------------
# Plivo
# ---------------------------------------------------------------------------

#: Plivo's documented compliance statuses, mapped to our verdicts. Total over
#: the documented set and consulted by exactly one function, so adding a status
#: Plivo introduces later is a one-line change in a place a reader will find.
#:
#: ``draft`` maps to PENDING rather than to an error: an application we created
#: but Plivo has not yet moved to ``submitted`` is genuinely still in flight,
#: and treating it as a fault would page someone over normal latency.
_PLIVO_STATUS_TO_VERDICT: dict[str, CarrierVerdict] = {
    "draft": CarrierVerdict.PENDING,
    "submitted": CarrierVerdict.PENDING,
    "accepted": CarrierVerdict.APPROVED,
    "rejected": CarrierVerdict.REJECTED,
    "suspended": CarrierVerdict.SUSPENDED,
    "expired": CarrierVerdict.EXPIRED,
}


class UnknownCarrierStatus(RuntimeError):
    """Plivo returned a status outside its documented set.

    Not mapped to "pending" on purpose. A status we do not understand is a
    signal that Plivo changed something, and the safe reading of an unknown
    status is that we do not know whether this account may dial.
    """


def plivo_status_to_verdict(status: str | None) -> CarrierVerdict:
    """Translate one Plivo compliance status into our verdict vocabulary."""
    key = (status or "").strip().lower()
    try:
        return _PLIVO_STATUS_TO_VERDICT[key]
    except KeyError:
        raise UnknownCarrierStatus(
            f"Plivo returned compliance status {status!r}, which is not one of "
            f"{sorted(_PLIVO_STATUS_TO_VERDICT)}. Refusing to guess what it "
            "means for calling."
        ) from None


#: Our document kinds, mapped to the Plivo requirement names that satisfy them.
#: Matched case-insensitively against whatever the Requirements endpoint
#: returns, first candidate wins.
#:
#: The Registration Certificate slot deliberately lists Udyam alongside
#: incorporation. A large share of Indian small businesses have a Udyam (MSME)
#: registration and no Certificate of Incorporation, and a flow that only names
#: the latter reads to them as "you cannot use this product".
_PLIVO_DOCUMENT_CANDIDATES: dict[KycDocumentKind, tuple[str, ...]] = {
    KycDocumentKind.CERTIFICATE_OF_INCORPORATION: (
        "registration certificate",
        "certificate of incorporation",
        "udyam registration",
        "business registration",
    ),
    KycDocumentKind.GST_CERTIFICATE: (
        "gst certificate",
        "gstin",
        "tax certificate",
    ),
    KycDocumentKind.AUTHORISED_SIGNATORY_ID: (
        "authorized representative id",
        "authorised representative id",
        "id proof",
        "identity proof",
    ),
    KycDocumentKind.ADDRESS_PROOF: (
        "address proof",
        "proof of address",
        "utility bill",
    ),
}

_PLIVO_USER_TYPE = {
    KycBusinessType.COMPANY: "business",
    KycBusinessType.INDIVIDUAL: "individual",
}


def _document_meta(end_user: dict) -> dict:
    """Per-document fields Plivo requires alongside the file itself.

    Plivo validates these against the *document type*, not the application, and
    refuses the whole submission when one is missing:

        Field 'business_name' is required for document type '<uuid>'.
        Check GET /Requirements for required fields.

    ``business_name`` is the registered legal name — the same string as
    ``end_user.name`` — so it is taken from there rather than passed
    separately, which keeps the two from ever disagreeing. Plivo compares both
    against the registration certificate, and a submission where they differ is
    a rejection in waiting.

    Only sent for a business. An individual application has no business name,
    and sending an empty one is a field Plivo has to reject.
    """
    if end_user.get("type") != "business":
        return {}
    name = (end_user.get("name") or "").strip()
    return {"business_name": name} if name else {}


def plivo_user_type(business_type: str | None) -> str:
    """Plivo's ``user_type`` for one of our business types."""
    try:
        return _PLIVO_USER_TYPE[KycBusinessType(business_type)]
    except (ValueError, KeyError):
        # Requirements differ by user type, so guessing here would fetch the
        # wrong document list and produce a submission Plivo rejects.
        raise PlivoComplianceError(
            f"Cannot map business type {business_type!r} to a Plivo user type."
        ) from None


def resolve_document_type_id(
    kind: KycDocumentKind | str, available: dict[str, str]
) -> str:
    """Find the Plivo ``document_type_id`` that satisfies one of our kinds.

    ``available`` is ``document_name -> document_type_id`` as returned by the
    Requirements endpoint. Matching is on the name because the ids are
    account- and version-specific — the UUIDs in Plivo's documentation are
    examples, and hardcoding one attaches the file under a type Plivo did not
    ask for.
    """
    try:
        kind = KycDocumentKind(kind)
    except ValueError:
        raise PlivoComplianceError(f"Unknown document kind {kind!r}.") from None

    lowered = {name.strip().lower(): type_id for name, type_id in available.items()}
    for candidate in _PLIVO_DOCUMENT_CANDIDATES.get(kind, ()):
        if candidate in lowered:
            return lowered[candidate]
        # Plivo's names carry qualifiers ("Registration Certificate (Business)"),
        # so a containment match is what actually resolves in practice.
        for name, type_id in lowered.items():
            if candidate in name:
                return type_id

    raise PlivoComplianceError(
        f"Plivo does not list a document type matching {kind.value!r}. It "
        f"offers: {sorted(available)}. Either the requirement set changed or "
        "this document is not needed for this country and number type."
    )


class PlivoCarrier:
    """Plivo's India compliance application.

    One application per organization, filed under Decibyl's parent account.
    The application id Plivo returns is stored as ``carrier_reference`` and is
    what a number is later linked against — see
    ``api/services/telephony/provisioning.py``.

    ``pollable`` is False: Plivo calls us back on every status change (see
    ``api/routes/kyc_callback.py``), so a poll would ask a question we have
    already been told the answer to, against the tighter of the two rate
    limits. :meth:`check` still works, for the admin "refresh this one" button
    and for reconciling an account whose callback was lost.
    """

    name = "plivo"
    pollable = False

    def __init__(
        self,
        *,
        auth_id: str | None = None,
        auth_token: str | None = None,
        client: PlivoComplianceClient | None = None,
    ):
        self._auth_id = auth_id or PLATFORM_PLIVO_AUTH_ID
        self._auth_token = auth_token or PLATFORM_PLIVO_AUTH_TOKEN
        self._client = client

    def _api(self) -> PlivoComplianceClient:
        if self._client is not None:
            return self._client
        if not self._auth_id or not self._auth_token:
            raise CarrierNotConfigured(
                "Decibyl's own Plivo account is not configured. Set "
                "PLATFORM_PLIVO_AUTH_ID and PLATFORM_PLIVO_AUTH_TOKEN — "
                "compliance applications are filed under our parent account, "
                "not a customer's."
            )
        self._client = PlivoComplianceClient(self._auth_id, self._auth_token)
        return self._client

    async def submit(
        self,
        *,
        organization_id: int,
        business_type: str,
        legal_name: str | None,
        gstin: str | None,
        documents: list[dict],
        end_user: dict[str, Any] | None = None,
        callback_url: str = "",
        country_iso: str = "IN",
        number_type: str = "local",
    ) -> CarrierSubmission:
        """File a new compliance application and return Plivo's id for it."""
        api = self._api()
        user_type = plivo_user_type(business_type)
        available = await api.document_type_ids(
            country_iso=country_iso, number_type=number_type, user_type=user_type
        )

        document_meta = _document_meta(end_user or {})
        payload_documents = [
            ComplianceDocument(
                document_type_id=resolve_document_type_id(doc["kind"], available),
                filename=doc.get("filename") or f"{doc['kind']}.pdf",
                content=doc["content"],
                content_type=doc.get("content_type") or "application/pdf",
                meta=document_meta,
            )
            for doc in documents
        ]

        response = await api.create_application(
            alias=f"decibyl-org-{organization_id}",
            country_iso=country_iso,
            number_type=number_type,
            end_user=end_user or {},
            documents=payload_documents,
            callback_url=callback_url,
        )

        reference = _application_id(response)
        if not reference:
            raise PlivoComplianceError(
                f"Plivo accepted the application for org {organization_id} but "
                f"returned no compliance id: {response}"
            )

        logger.info(
            "Filed Plivo compliance application {} for org {} ({} document(s))",
            reference,
            organization_id,
            len(payload_documents),
        )
        return CarrierSubmission(
            reference=reference,
            status=plivo_status_to_verdict(_application_status(response) or "draft"),
        )

    async def update(
        self,
        *,
        reference: str,
        organization_id: int,
        documents: list[dict],
        business_type: str,
        end_user: dict[str, Any] | None = None,
        callback_url: str = "",
        country_iso: str = "IN",
        number_type: str = "local",
    ) -> CarrierSubmission:
        """Amend a **rejected** application in place.

        Plivo permits PATCH only while the application is rejected — an
        accepted one is final and an expired one cannot be revived — so the
        caller has to have established that first. Every document is re-sent
        because Plivo replaces rather than merges the set.
        """
        api = self._api()
        user_type = plivo_user_type(business_type)
        available = await api.document_type_ids(
            country_iso=country_iso, number_type=number_type, user_type=user_type
        )

        response = await api.update_application(
            reference,
            end_user=end_user,
            documents=[
                ComplianceDocument(
                    document_type_id=resolve_document_type_id(doc["kind"], available),
                    filename=doc.get("filename") or f"{doc['kind']}.pdf",
                    content=doc["content"],
                    content_type=doc.get("content_type") or "application/pdf",
                    meta=_document_meta(end_user or {}),
                )
                for doc in documents
            ],
            callback_url=callback_url or None,
        )
        logger.info(
            "Amended Plivo compliance application {} for org {}",
            reference,
            organization_id,
        )
        return CarrierSubmission(
            reference=reference,
            status=plivo_status_to_verdict(
                _application_status(response) or "submitted"
            ),
        )

    async def check(self, reference: str) -> CarrierStatus:
        """Read an application's current status straight from Plivo."""
        if not reference:
            raise CarrierNotConfigured("No Plivo compliance id to check.")
        response = await self._api().get_application(reference)
        raw = _application_status(response)
        return CarrierStatus(
            verdict=plivo_status_to_verdict(raw),
            raw_status=raw,
            rejection_reason=_rejection_reason(response),
        )


def _application_id(response: dict) -> str | None:
    for key in ("compliance_application_id", "compliance_id", "id", "application_id"):
        value = response.get(key)
        if value:
            return str(value)
    return None


def _application_status(response: dict) -> str | None:
    for key in ("status", "compliance_status", "application_status"):
        value = response.get(key)
        if value:
            return str(value)
    return None


def _rejection_reason(response: dict) -> str | None:
    for key in ("rejection_reason", "reason", "error", "message"):
        value = response.get(key)
        if value:
            return str(value)[:2000]
    return None


class ManualCarrier:
    """A carrier operated by hand, for before an API integration exists.

    Submitting records that we sent it; the verdict is entered by staff after
    they hear back. This is not a shortcut around verification — the account
    still sits in FORWARDED until a real approval arrives, and a human is
    asserting that the licensee approved it.

    Retained now that Plivo is implemented, because it is the fallback when our
    Plivo account is unreachable or a customer's application has to go through
    a channel the API does not cover.
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
        **_ignored,
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

#: Plivo is the default now that its compliance API is implemented. An account
#: without PLATFORM_PLIVO_AUTH_ID configured raises ``CarrierNotConfigured`` on
#: forward rather than falling through to manual, because a silent downgrade to
#: "a human will handle it" is how applications sit unsent for a fortnight.
DEFAULT_CARRIER = PlivoCarrier.name


def get_carrier(name: str | None = None) -> KycCarrier:
    carrier = _CARRIERS.get(name or DEFAULT_CARRIER)
    if carrier is None:
        raise CarrierNotConfigured(f"No carrier integration named {name!r}")
    return carrier
