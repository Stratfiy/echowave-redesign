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
