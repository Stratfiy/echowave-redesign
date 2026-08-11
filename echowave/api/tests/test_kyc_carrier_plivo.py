"""Plivo compliance: status mapping, document encoding, and pre-submit checks.

No database and no network. Everything here is about the boundary between
Plivo's vocabulary and ours, which is where a wrong answer either opens
telephony for an unapproved account or rejects a customer who did nothing
wrong.
"""

from __future__ import annotations

import json

import pytest

from api.enums import KycDocumentKind, KycStatus
from api.services.kyc import validation
from api.services.kyc.carrier import (
    CarrierVerdict,
    PlivoCarrier,
    UnknownCarrierStatus,
    plivo_status_to_verdict,
    plivo_user_type,
    resolve_document_type_id,
)
from api.services.kyc.plivo_compliance import (
    MAX_DOCUMENT_BYTES,
    MAX_FILENAME_CHARS,
    ComplianceDocument,
    DocumentTooLarge,
    PlivoComplianceClient,
    PlivoComplianceError,
    _RateLimiter,
)
from api.services.kyc.state import can_transition


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plivo_status,expected",
    [
        ("draft", CarrierVerdict.PENDING),
        ("submitted", CarrierVerdict.PENDING),
        ("accepted", CarrierVerdict.APPROVED),
        ("rejected", CarrierVerdict.REJECTED),
        ("suspended", CarrierVerdict.SUSPENDED),
        ("expired", CarrierVerdict.EXPIRED),
    ],
)
def test_every_documented_plivo_status_maps(plivo_status, expected):
    assert plivo_status_to_verdict(plivo_status) is expected


def test_status_mapping_is_case_and_whitespace_insensitive():
    assert plivo_status_to_verdict("  ACCEPTED ") is CarrierVerdict.APPROVED


def test_unknown_status_raises_rather_than_guessing():
    """The failure that matters: a status we invent an approval for."""
    with pytest.raises(UnknownCarrierStatus):
        plivo_status_to_verdict("under_manual_review")


def test_unknown_status_never_silently_becomes_pending():
    # A "safe" default of PENDING would be wrong in the other direction too:
    # it would leave an approved account blocked with no signal.
    with pytest.raises(UnknownCarrierStatus):
        plivo_status_to_verdict(None)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def test_approval_is_no_longer_terminal():
    assert can_transition(KycStatus.CARRIER_APPROVED, KycStatus.SUSPENDED)
    assert can_transition(KycStatus.CARRIER_APPROVED, KycStatus.EXPIRED)


def test_suspension_is_reversible_and_can_lapse():
    assert can_transition(KycStatus.SUSPENDED, KycStatus.CARRIER_APPROVED)
    assert can_transition(KycStatus.SUSPENDED, KycStatus.EXPIRED)


def test_expired_requires_a_fresh_application():
    assert can_transition(KycStatus.EXPIRED, KycStatus.SUBMITTED)
    # Not straight back to approved — an expired application cannot be amended.
    assert not can_transition(KycStatus.EXPIRED, KycStatus.CARRIER_APPROVED)


def test_suspended_cannot_jump_to_submitted():
    """Only the licensee lifts a suspension; resubmitting changes nothing."""
    assert not can_transition(KycStatus.SUSPENDED, KycStatus.SUBMITTED)


def test_only_carrier_approved_permits_calls():
    from api.services.kyc.state import may_place_telephony_calls

    assert may_place_telephony_calls(KycStatus.CARRIER_APPROVED)
    for blocked in (
        KycStatus.SUSPENDED,
        KycStatus.EXPIRED,
        KycStatus.FORWARDED,
        KycStatus.CARRIER_REJECTED,
    ):
        assert not may_place_telephony_calls(blocked), blocked


# ---------------------------------------------------------------------------
# Document type resolution
# ---------------------------------------------------------------------------


def test_document_type_ids_are_resolved_never_hardcoded():
    available = {
        "Registration Certificate": "uuid-reg",
        "Address Proof": "uuid-addr",
    }
    assert (
        resolve_document_type_id(
            KycDocumentKind.CERTIFICATE_OF_INCORPORATION, available
        )
        == "uuid-reg"
    )


def test_registration_certificate_accepts_udyam():
    """Many Indian small businesses have Udyam and no incorporation cert."""
    available = {"Udyam Registration": "uuid-udyam"}
    assert (
        resolve_document_type_id(
            KycDocumentKind.CERTIFICATE_OF_INCORPORATION, available
        )
        == "uuid-udyam"
    )


def test_document_type_matches_plivo_name_qualifiers():
    available = {"Registration Certificate (Business)": "uuid-reg"}
    assert (
        resolve_document_type_id(
            KycDocumentKind.CERTIFICATE_OF_INCORPORATION, available
        )
        == "uuid-reg"
    )


def test_unresolvable_document_type_raises_with_what_was_offered():
    with pytest.raises(PlivoComplianceError) as exc:
        resolve_document_type_id(KycDocumentKind.GST_CERTIFICATE, {"Passport": "x"})
    assert "Passport" in str(exc.value)


def test_user_type_maps_from_business_type():
    assert plivo_user_type("company") == "business"
    assert plivo_user_type("individual") == "individual"
    with pytest.raises(PlivoComplianceError):
        plivo_user_type("charity")


# ---------------------------------------------------------------------------
# Document constraints
# ---------------------------------------------------------------------------


def _doc(**overrides):
    base = dict(
        document_type_id="uuid-1",
        filename="cert.pdf",
        content=b"%PDF-1.4 hello",
        content_type="application/pdf",
    )
    base.update(overrides)
    return ComplianceDocument(**base)


def test_oversized_document_is_rejected_before_sending():
    with pytest.raises(DocumentTooLarge):
        _doc(content=b"x" * (MAX_DOCUMENT_BYTES + 1)).validate()


def test_document_at_the_limit_is_accepted():
    _doc(content=b"x" * MAX_DOCUMENT_BYTES).validate()


def test_disallowed_content_type_is_rejected():
    with pytest.raises(PlivoComplianceError):
        _doc(content_type="image/svg+xml").validate()


def test_empty_document_is_rejected():
    with pytest.raises(PlivoComplianceError):
        _doc(content=b"").validate()


def test_missing_document_type_id_is_rejected():
    with pytest.raises(PlivoComplianceError):
        _doc(document_type_id="").validate()


def test_long_filename_is_truncated_but_keeps_its_extension():
    document = _doc(filename="a" * 200 + ".pdf")
    safe = document.safe_filename()
    assert len(safe) <= MAX_FILENAME_CHARS
    assert safe.endswith(".pdf")


def test_short_filename_is_untouched():
    assert _doc(filename="gst.pdf").safe_filename() == "gst.pdf"


# ---------------------------------------------------------------------------
# Multipart encoding
# ---------------------------------------------------------------------------


def test_form_pairs_each_file_with_its_type_id_positionally():
    """The two lists must stay in step, or documents attach under wrong types."""
    client = PlivoComplianceClient("MA123", "token")
    documents = [
        _doc(document_type_id="type-a", filename="a.pdf"),
        _doc(document_type_id="type-b", filename="b.png", content_type="image/png"),
    ]
    form = client._build_form(data={"alias": "org-1"}, documents=documents)

    fields = {}
    files = []
    for info, _headers, value in form._fields:
        name = info.get("name")
        if name == "data":
            fields["data"] = value
        elif name and name.startswith("documents["):
            files.append(name)

    payload = json.loads(fields["data"])
    assert [d["document_type_id"] for d in payload["documents"]] == [
        "type-a",
        "type-b",
    ]
    assert files == ["documents[0].file", "documents[1].file"]


def test_create_application_requires_https_callback():
    client = PlivoComplianceClient("MA123", "token")
    with pytest.raises(PlivoComplianceError) as exc:
        import asyncio

        asyncio.run(
            client.create_application(
                alias="org-1",
                country_iso="IN",
                number_type="local",
                end_user={"name": "Acme"},
                documents=[_doc()],
                callback_url="http://insecure.example.com/cb",
            )
        )
    assert "HTTPS" in str(exc.value)


@pytest.mark.asyncio
async def test_update_refuses_an_empty_document_set():
    """PATCH replaces every document; sending one would drop the rest."""
    client = PlivoComplianceClient("MA123", "token")
    with pytest.raises(PlivoComplianceError) as exc:
        await client.update_application("cid", documents=[])
    assert "full set" in str(exc.value)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limiter_permits_up_to_the_budget_without_waiting():
    limiter = _RateLimiter(3)
    for _ in range(3):
        await limiter.acquire()


def test_rate_limiter_backs_off_when_headers_say_exhausted():
    limiter = _RateLimiter(100)
    limiter.observe({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "30"})
    assert limiter._blocked_until > 0


def test_rate_limiter_ignores_unparseable_headers():
    limiter = _RateLimiter(100)
    limiter.observe({"X-RateLimit-Remaining": "lots"})
    assert limiter._blocked_until == 0


# ---------------------------------------------------------------------------
# Pre-submit validation (the two biggest rejection causes)
# ---------------------------------------------------------------------------


def test_name_mismatch_is_blocked_before_submission():
    problem = validation.check_name_match(
        business_name="Acme Pvt Ltd", end_user_name="Acme"
    )
    assert problem is not None
    assert problem.field == "legal_name"


def test_matching_names_pass():
    assert (
        validation.check_name_match(
            business_name="Acme Pvt Ltd", end_user_name="Acme Pvt Ltd"
        )
        is None
    )


def test_name_match_ignores_case_and_extra_spaces():
    assert (
        validation.check_name_match(
            business_name="  ACME   Pvt Ltd ", end_user_name="acme pvt ltd"
        )
        is None
    )


def test_name_match_still_catches_punctuation_differences():
    """A registrar treats these as different, so we must too."""
    assert (
        validation.check_name_match(
            business_name="Acme Pvt. Ltd.", end_user_name="Acme Pvt Ltd"
        )
        is not None
    )


def test_missing_name_is_a_problem_not_a_pass():
    assert validation.check_name_match(business_name=None, end_user_name=None)


class _Doc:
    def __init__(self, kind, digest):
        self.kind = kind
        self.content_sha256 = digest


def test_same_file_in_two_slots_is_blocked():
    problems = validation.check_distinct_documents(
        [
            _Doc("certificate_of_incorporation", "abc"),
            _Doc("gst_certificate", "abc"),
        ]
    )
    assert len(problems) == 1
    assert "certificate of incorporation" in problems[0].message


def test_distinct_files_pass():
    assert not validation.check_distinct_documents(
        [
            _Doc("certificate_of_incorporation", "abc"),
            _Doc("gst_certificate", "def"),
        ]
    )


def test_documents_without_a_hash_are_skipped_not_failed():
    """Rows predating the column must not block an account that did nothing wrong."""
    assert not validation.check_distinct_documents(
        [
            _Doc("certificate_of_incorporation", None),
            _Doc("gst_certificate", None),
        ]
    )


def test_content_digest_is_stable_and_distinguishing():
    assert validation.content_digest(b"a") == validation.content_digest(b"a")
    assert validation.content_digest(b"a") != validation.content_digest(b"b")


class _Record:
    def __init__(self, **kw):
        self.legal_name = kw.get("legal_name", "Acme Pvt Ltd")
        self.address_line1 = kw.get("address_line1", "1 Road")
        self.city = kw.get("city", "Hyderabad")
        self.postal_code = kw.get("postal_code", "500001")
        self.documents = kw.get("documents", [])


def test_validate_for_submission_reports_every_problem_at_once():
    problems = validation.validate_for_submission(
        _Record(
            legal_name=None,
            address_line1="",
            city="",
            postal_code="",
            documents=[_Doc("a", "x"), _Doc("b", "x")],
        )
    )
    fields = {p.field for p in problems}
    assert {"legal_name", "address_line1", "city", "postal_code", "documents"} <= fields


def test_valid_record_has_no_problems():
    assert validation.validate_for_submission(_Record()) == []


# ---------------------------------------------------------------------------
# Carrier wiring
# ---------------------------------------------------------------------------


def test_plivo_carrier_is_not_pollable_because_callbacks_replaced_the_poll():
    assert PlivoCarrier.pollable is False


def test_plivo_carrier_without_credentials_raises_rather_than_downgrading():
    from api.services.kyc.carrier import CarrierNotConfigured

    carrier = PlivoCarrier(auth_id=None, auth_token=None)
    with pytest.raises(CarrierNotConfigured):
        carrier._api()
