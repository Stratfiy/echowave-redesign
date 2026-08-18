"""What staff are told when forwarding to the operator fails.

The screen used to say "Could not forward to the operator" and nothing else,
for every possible cause, because the two most likely failures escaped the
route uncaught and became a bare 500. That sentence names neither the problem
nor anything to do about it, so the only available next step was a support
ticket about our own product.

Both failures carry the information needed to act:

* ``PlivoComplianceError`` carries the carrier's status and body, and the body
  is where Plivo says which field it disliked.
* ``KycValidationError`` carries every problem, field by field, so the screen
  can mark each one instead of printing a sentence.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.services.kyc.plivo_compliance import PlivoComplianceError


def _detail(exc: HTTPException) -> dict:
    assert isinstance(exc.detail, dict), exc.detail
    return exc.detail


@pytest.mark.asyncio
class TestACarrierRefusal:
    async def test_it_is_a_bad_gateway_not_a_crash(self, monkeypatch):
        """502, because the request was fine and the upstream refused it. A 500
        says the fault is ours and invites a retry that will fail identically."""
        from api.routes import kyc_admin

        async def _refuse(**_kwargs):
            raise PlivoComplianceError(
                "Compliance application rejected",
                status=400,
                body="gstin: does not match the legal name on file",
            )

        monkeypatch.setattr(kyc_admin.kyc_service, "approve_and_forward", _refuse)

        with pytest.raises(HTTPException) as caught:
            await kyc_admin.approve(
                organization_id=7,
                payload=kyc_admin.ForwardRequest(),
                user=type("U", (), {"id": 1})(),
            )

        assert caught.value.status_code == 502

    async def test_the_carriers_own_words_reach_the_screen(self, monkeypatch):
        """The whole point. "does not match the legal name on file" is a thing
        a staff member can fix in a minute; "could not forward" is not."""
        from api.routes import kyc_admin

        async def _refuse(**_kwargs):
            raise PlivoComplianceError(
                "Compliance application rejected",
                status=400,
                body="gstin: does not match the legal name on file",
            )

        monkeypatch.setattr(kyc_admin.kyc_service, "approve_and_forward", _refuse)

        with pytest.raises(HTTPException) as caught:
            await kyc_admin.approve(
                organization_id=7,
                payload=kyc_admin.ForwardRequest(),
                user=type("U", (), {"id": 1})(),
            )

        detail = _detail(caught.value)
        assert "refused" in detail["message"]
        assert "legal name on file" in detail["problems"][0]["message"]

    async def test_a_refusal_with_no_body_still_says_something(self, monkeypatch):
        """Plivo does not always explain itself. An empty problems list is
        correct there — inventing a field would be worse than saying only that
        the operator refused."""
        from api.routes import kyc_admin

        async def _refuse(**_kwargs):
            raise PlivoComplianceError("Upstream timed out", status=504)

        monkeypatch.setattr(kyc_admin.kyc_service, "approve_and_forward", _refuse)

        with pytest.raises(HTTPException) as caught:
            await kyc_admin.approve(
                organization_id=7,
                payload=kyc_admin.ForwardRequest(),
                user=type("U", (), {"id": 1})(),
            )

        detail = _detail(caught.value)
        assert "Upstream timed out" in detail["message"]
        assert detail["problems"] == []


@pytest.mark.asyncio
class TestValidationProblems:
    async def test_they_arrive_field_by_field(self, monkeypatch):
        """Same shape the customer-facing submit route returns, so staff and
        customer are looking at the same list rather than two descriptions of
        one problem."""
        from api.routes import kyc_admin
        from api.services.kyc.service import KycValidationError

        class _Problem:
            def __init__(self, field, message):
                self.field = field
                self.message = message

        async def _invalid(**_kwargs):
            raise KycValidationError(
                [
                    _Problem("gstin", "Not a valid GSTIN."),
                    _Problem("legal_name", "Does not match the incorporation."),
                ]
            )

        monkeypatch.setattr(kyc_admin.kyc_service, "approve_and_forward", _invalid)

        with pytest.raises(HTTPException) as caught:
            await kyc_admin.approve(
                organization_id=7,
                payload=kyc_admin.ForwardRequest(),
                user=type("U", (), {"id": 1})(),
            )

        assert caught.value.status_code == 422
        fields = [p["field"] for p in _detail(caught.value)["problems"]]
        assert fields == ["gstin", "legal_name"]

    async def test_it_is_caught_before_the_bare_value_error_clause(self, monkeypatch):
        """KycValidationError *is* a ValueError. Ordered wrongly, it would be
        flattened to a 400 with a sentence and the fields would be lost."""
        from api.routes import kyc_admin
        from api.services.kyc.service import KycValidationError

        class _Problem:
            field = "gstin"
            message = "Not a valid GSTIN."

        async def _invalid(**_kwargs):
            raise KycValidationError([_Problem()])

        monkeypatch.setattr(kyc_admin.kyc_service, "approve_and_forward", _invalid)

        with pytest.raises(HTTPException) as caught:
            await kyc_admin.approve(
                organization_id=7,
                payload=kyc_admin.ForwardRequest(),
                user=type("U", (), {"id": 1})(),
            )

        assert caught.value.status_code == 422
        assert isinstance(caught.value.detail, dict)


class TestTheEndUserObject:
    """The shape Plivo is actually sent.

    This dict goes to the carrier verbatim as the ``end_user`` object, and it
    reached production with the field named ``user_type`` — which Plivo refuses
    outright: "end_user.type is required. Allowed values: individual,
    business." Every forward failed and nothing tested this shape, so the only
    signal was a 400 nobody could see.

    The wrong name came from a real place: the compliance *requirements* lookup
    on the same client does take ``user_type``. Two Plivo endpoints, two names
    for one idea.
    """

    @staticmethod
    def _record(business_type: str):
        from types import SimpleNamespace

        return SimpleNamespace(
            legal_name="Nautomation Labs Private Limited",
            business_type=business_type,
            country_iso="IN",
            address_line1="1 Example Road",
            address_line2="",
            city="Chennai",
            region="TN",
            postal_code="600001",
            gstin="33AALCN7211L1ZB",
        )

    def test_the_field_is_type_not_user_type(self):
        from api.services.kyc.service import build_end_user

        end_user = build_end_user(self._record("company"))

        assert "type" in end_user
        assert "user_type" not in end_user

    def test_a_company_is_a_business(self):
        from api.services.kyc.service import build_end_user

        assert build_end_user(self._record("company"))["type"] == "business"

    def test_anything_else_is_an_individual(self):
        """Plivo allows exactly two values, so everything that is not a
        registered company has to land on the other one."""
        from api.services.kyc.service import build_end_user

        assert build_end_user(self._record("individual"))["type"] == "individual"
        assert build_end_user(self._record("proprietorship"))["type"] == "individual"

    def test_the_value_is_one_plivo_accepts(self):
        from api.services.kyc.service import build_end_user

        for business_type in ("company", "individual", "proprietorship", ""):
            assert build_end_user(self._record(business_type))["type"] in {
                "individual",
                "business",
            }


class TestPerDocumentFields:
    """Fields Plivo validates against the document type, not the application.

    A business registration document needs ``business_name`` beside the file,
    and without it Plivo refuses the entire submission:

        Field 'business_name' is required for document type '<uuid>'.
        Check GET /Requirements for required fields.

    Nothing carried these fields at all, so every business application failed
    here — one layer past the ``end_user.type`` bug, and invisible until that
    one was fixed.
    """

    def test_a_business_sends_its_registered_name(self):
        from api.services.kyc.carrier import _document_meta

        meta = _document_meta({"type": "business", "name": "Acme Private Limited"})

        assert meta == {"business_name": "Acme Private Limited"}

    def test_it_is_the_same_string_as_the_end_user_name(self):
        """Taken from ``end_user`` rather than passed separately, so the two
        cannot disagree. Plivo compares both against the registration
        certificate and a mismatch is a rejection in waiting."""
        from api.services.kyc.carrier import _document_meta

        end_user = {"type": "business", "name": "Nautomation Labs Private Limited"}

        assert _document_meta(end_user)["business_name"] == end_user["name"]

    def test_an_individual_sends_nothing(self):
        """An individual has no business name, and an empty one is a field
        Plivo has to reject."""
        from api.services.kyc.carrier import _document_meta

        assert _document_meta({"type": "individual", "name": "A Person"}) == {}

    def test_a_business_with_no_name_sends_nothing_rather_than_blank(self):
        from api.services.kyc.carrier import _document_meta

        assert _document_meta({"type": "business", "name": "   "}) == {}

    def test_the_meta_reaches_the_document_payload(self):
        """The mechanism, not just the value: meta is merged into the document
        entry that goes to Plivo, alongside the type id and file name."""
        import json

        from api.services.kyc.plivo_compliance import (
            ComplianceDocument,
            PlivoComplianceClient,
        )

        client = PlivoComplianceClient(auth_id="MA", auth_token="tok")
        form = client._build_form(
            data={"alias": "decibyl-org-1"},
            documents=[
                ComplianceDocument(
                    document_type_id="525a6b3a-2050-4556-8e2c-367cf30cd6ca",
                    filename="incorporation.pdf",
                    content=b"%PDF-1.4 fake",
                    content_type="application/pdf",
                    meta={"business_name": "Acme Private Limited"},
                )
            ],
        )

        data_field = next(f for f in form._fields if f[0].get("name") == "data")
        payload = json.loads(data_field[2])
        document = payload["documents"][0]
        # Nested, not spread. Plivo reads these from `data_fields` and nowhere
        # else — one level up gets the same refusal as sending nothing.
        assert document["data_fields"] == {"business_name": "Acme Private Limited"}
        assert document["file_name"] == "incorporation.pdf"


def test_a_document_with_no_extra_fields_omits_data_fields_entirely():
    """An empty ``data_fields: {}`` is a thing Plivo has to interpret. Leaving
    the key out says the same thing and cannot be misread."""
    import json

    from api.services.kyc.plivo_compliance import (
        ComplianceDocument,
        PlivoComplianceClient,
    )

    client = PlivoComplianceClient(auth_id="MA", auth_token="tok")
    form = client._build_form(
        data={"alias": "decibyl-org-1"},
        documents=[
            ComplianceDocument(
                document_type_id="type-1",
                filename="id.pdf",
                content=b"%PDF-1.4 fake",
                content_type="application/pdf",
            )
        ],
    )

    data_field = next(f for f in form._fields if f[0].get("name") == "data")
    assert "data_fields" not in json.loads(data_field[2])["documents"][0]
