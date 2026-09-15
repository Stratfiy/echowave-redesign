"""Impersonation is guarded and audited (KAN-82).

A superadmin borrowing a customer's session is the most powerful action in
the product. It must never target another staff account, and every start
must leave a durable row -- so an account can be told who looked at it, and
a review can answer "when, and by whom" without trawling rotating logs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes import superuser
from api.routes.superuser import ImpersonateRequest


def _superadmin():
    return SimpleNamespace(id=1, staff_role="superadmin")


def _http():
    return SimpleNamespace(client=SimpleNamespace(host="10.0.0.9"))


class _AuditSession:
    """Captures the row the route writes, as an async context manager."""

    def __init__(self, sink: list):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def add(self, row):
        self._sink.append(row)

    async def commit(self):
        pass


@pytest.mark.asyncio
class TestTheGuard:
    async def test_a_staff_target_is_refused_and_never_reaches_stack_auth(self):
        target = SimpleNamespace(
            id=5, staff_role="support", email="s@x.com", selected_organization_id=3
        )
        with (
            patch.object(
                superuser.db_client,
                "get_user_by_provider_id",
                AsyncMock(return_value=target),
            ),
            patch.object(superuser.stackauth, "impersonate", AsyncMock()) as imp,
            pytest.raises(HTTPException) as exc,
        ):
            await superuser.impersonate(
                ImpersonateRequest(provider_user_id="prov-5"),
                _http(),
                _superadmin(),
            )
        assert exc.value.status_code == 403
        imp.assert_not_awaited()

    async def test_a_customer_is_impersonated_and_the_start_is_audited(self):
        target = SimpleNamespace(
            id=5, staff_role=None, email="c@x.com", selected_organization_id=3
        )
        rows: list = []
        with (
            patch.object(
                superuser.db_client,
                "get_user_by_provider_id",
                AsyncMock(return_value=target),
            ),
            patch.object(
                superuser.db_client,
                "async_session",
                lambda: _AuditSession(rows),
            ),
            patch.object(
                superuser.stackauth,
                "impersonate",
                AsyncMock(
                    return_value={
                        "refresh_token": "rt",
                        "access_token": "at",
                    }
                ),
            ),
        ):
            resp = await superuser.impersonate(
                ImpersonateRequest(provider_user_id="prov-5"),
                _http(),
                _superadmin(),
            )
        assert resp.refresh_token == "rt" and resp.access_token == "at"
        assert len(rows) == 1
        row = rows[0]
        assert row.action == "impersonation_started"
        assert row.actor_user_id == 1
        assert row.target_user_id == 5
        assert row.target_provider_id == "prov-5"
        assert row.actor_ip == "10.0.0.9"

    async def test_an_audit_write_failure_stops_the_impersonation(self):
        target = SimpleNamespace(
            id=5, staff_role=None, email="c@x.com", selected_organization_id=3
        )

        class _Boom:
            async def __aenter__(self):
                raise RuntimeError("db down")

            async def __aexit__(self, *_):
                return False

        with (
            patch.object(
                superuser.db_client,
                "get_user_by_provider_id",
                AsyncMock(return_value=target),
            ),
            patch.object(superuser.db_client, "async_session", lambda: _Boom()),
            patch.object(superuser.stackauth, "impersonate", AsyncMock()) as imp,
            pytest.raises(HTTPException) as exc,
        ):
            await superuser.impersonate(
                ImpersonateRequest(provider_user_id="prov-5"),
                _http(),
                _superadmin(),
            )
        assert exc.value.status_code == 503
        imp.assert_not_awaited()
