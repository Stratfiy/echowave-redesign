"""The two-tier staff gate: support and superadmin.

SUPPORT covers cross-account document review (KYC) and nothing further;
SUPERADMIN implies everything SUPPORT can do, plus billing, platform
provider keys, and impersonation. The property worth guarding is the rank
comparison — SUPERADMIN must pass a SUPPORT-gated route without a separate
grant, and a plain member (no staff_role at all) must fail both.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.db.models import UserModel
from api.enums import StaffRole
from api.services.auth.depends import get_staff, get_superuser


async def _staff(async_session, slug: str, *, role: str | None) -> UserModel:
    user = UserModel(provider_id=f"staff-tier-{slug}", staff_role=role)
    async_session.add(user)
    await async_session.flush()
    return user


@pytest.mark.asyncio
class TestStaffRoleRank:
    async def test_a_plain_user_fails_both_gates(self, async_session, monkeypatch):
        user = await _staff(async_session, "plain", role=None)
        monkeypatch.setattr("api.services.auth.depends.get_user", _fake_get_user(user))

        with pytest.raises(HTTPException) as exc:
            await get_staff()
        assert exc.value.status_code == 403

        with pytest.raises(HTTPException) as exc:
            await get_superuser()
        assert exc.value.status_code == 403

    async def test_support_passes_the_support_gate_but_not_superadmin(
        self, async_session, monkeypatch
    ):
        user = await _staff(async_session, "support", role=StaffRole.SUPPORT.value)
        monkeypatch.setattr("api.services.auth.depends.get_user", _fake_get_user(user))

        result = await get_staff()
        assert result.id == user.id

        with pytest.raises(HTTPException) as exc:
            await get_superuser()
        assert exc.value.status_code == 403

    async def test_superadmin_passes_both_gates(self, async_session, monkeypatch):
        user = await _staff(
            async_session, "superadmin", role=StaffRole.SUPERADMIN.value
        )
        monkeypatch.setattr("api.services.auth.depends.get_user", _fake_get_user(user))

        assert (await get_staff()).id == user.id
        assert (await get_superuser()).id == user.id


def _fake_get_user(user: UserModel):
    async def _get_user(*_args, **_kwargs):
        return user

    return _get_user
