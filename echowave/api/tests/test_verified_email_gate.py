"""Buying a number and starting a campaign wait for a proved address.

Only where proof is possible, and only those two: everything an existing
account already does stays open, because every account that predates
verification has no proof and locking them out would be an outage."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.services.auth import depends, email_verification


@pytest.mark.asyncio
async def test_an_unproved_address_is_refused_where_proof_is_possible(monkeypatch):
    monkeypatch.setattr(email_verification, "verification_is_enforceable", lambda: True)
    with pytest.raises(HTTPException) as exc:
        await depends.require_verified_email(SimpleNamespace(email_verified_at=None))
    assert exc.value.status_code == 403
    assert "Verify your email" in exc.value.detail


@pytest.mark.asyncio
async def test_a_proved_address_passes(monkeypatch):
    monkeypatch.setattr(email_verification, "verification_is_enforceable", lambda: True)
    user = SimpleNamespace(email_verified_at="2026-09-10")
    assert await depends.require_verified_email(user) is user


@pytest.mark.asyncio
async def test_a_door_that_cannot_ask_does_not_gate(monkeypatch):
    """Google, Stack, or no mail server: nothing to prove, nothing refused."""
    monkeypatch.setattr(
        email_verification, "verification_is_enforceable", lambda: False
    )
    user = SimpleNamespace(email_verified_at=None)
    assert await depends.require_verified_email(user) is user
