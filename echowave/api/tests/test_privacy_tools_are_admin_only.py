"""Erasing and exporting an account's data is not an ordinary member's act.

Three routes under ``/privacy`` took any authenticated member of the
organization:

* ``PUT /privacy/retention`` sets how long call data is kept. Shortening the
  window makes the next nightly sweep delete recordings and transcripts that
  were inside their window this morning. It is a deletion with a delay on it.
* ``POST /privacy/erasure`` erases one person's data across the account's
  calls, and its own docstring says the deletion is irreversible on purpose --
  "a right to erasure satisfied by something recoverable is not satisfied".
* ``GET /privacy/export`` returns everything held. With no ``phone_number`` it
  is the whole account: every call, every transcript, every field an agent
  extracted from a caller, as one JSON download.

The enum already describes the tier that fits. ADMIN is defined as the
surfaces "where one person's action binds the whole account", and lists
removing a number from the do-not-disturb list -- a smaller regulatory act than
either erasing the account's data or downloading all of it.

What made it worth checking rather than assuming: these routes are correctly
scoped to the caller's own organization, and the module docstring says so at
length. Tenant isolation was never the gap. The gap is that inside one tenant,
the newest member could erase the account's history or take a copy of all of
it home.

The reads that only describe the account -- the current window, the erasure
history, the grievance officer's details -- stay open to members. A member who
cannot see what the retention window is cannot do their job, and nothing about
reading it binds anybody.
"""

from __future__ import annotations

import pytest

from api.db.models import (
    OrganizationMembershipModel,
    OrganizationModel,
    UserModel,
)
from api.enums import OrganizationRole


async def _member(async_session, slug: str, *, role: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}", selected_organization_id=None)
    async_session.add_all([org, user])
    await async_session.flush()
    user.selected_organization_id = org.id
    async_session.add(
        OrganizationMembershipModel(user_id=user.id, organization_id=org.id, role=role)
    )
    await async_session.flush()
    return user, org


#: (label, method, path, body) for each route that now needs ADMIN.
GATED = [
    ("retention", "put", "/api/v1/privacy/retention", {"recording_retention_days": 1}),
    ("erasure", "post", "/api/v1/privacy/erasure", {"phone_number": "+919876543210"}),
    ("export", "get", "/api/v1/privacy/export", None),
]


async def _call(client, method: str, path: str, body):
    if body is None:
        return await getattr(client, method)(path)
    return await getattr(client, method)(path, json=body)


@pytest.mark.asyncio
class TestAMemberIsRefused:
    @pytest.mark.parametrize("label,method,path,body", GATED)
    async def test_a_member_cannot_reach_it(
        self, async_session, db_session, test_client_factory, label, method, path, body
    ):
        user, _org = await _member(
            async_session, f"member-{label}", role=OrganizationRole.MEMBER.value
        )

        async with test_client_factory(user) as client:
            response = await _call(client, method, path, body)

        assert response.status_code == 403, (
            f"a member reached {path} and got {response.status_code}"
        )

    async def test_the_whole_account_export_is_the_one_that_matters_most(
        self, async_session, db_session, test_client_factory
    ):
        """Without ``phone_number`` this is every call the account has ever
        made, in one response."""
        user, _org = await _member(
            async_session, "member-export-all", role=OrganizationRole.MEMBER.value
        )

        async with test_client_factory(user) as client:
            response = await client.get("/api/v1/privacy/export")

        assert response.status_code == 403


@pytest.mark.asyncio
class TestAnAdminIsNot:
    """A gate that refuses everybody is not a gate, it is an outage."""

    @pytest.mark.parametrize("label,method,path,body", GATED)
    async def test_an_admin_is_not_refused_by_the_role_check(
        self, async_session, db_session, test_client_factory, label, method, path, body
    ):
        user, _org = await _member(
            async_session, f"admin-{label}", role=OrganizationRole.ADMIN.value
        )

        async with test_client_factory(user) as client:
            response = await _call(client, method, path, body)

        assert response.status_code != 403, (
            f"an admin was refused {path}; the tier gates nothing it should"
        )

    @pytest.mark.parametrize("label,method,path,body", GATED)
    async def test_an_owner_has_everything_an_admin_has(
        self, async_session, db_session, test_client_factory, label, method, path, body
    ):
        user, _org = await _member(
            async_session, f"owner-{label}", role=OrganizationRole.OWNER.value
        )

        async with test_client_factory(user) as client:
            response = await _call(client, method, path, body)

        assert response.status_code != 403


@pytest.mark.asyncio
class TestWhatAMemberKeeps:
    """Gating the reads too would stop a member doing ordinary work, and
    nothing about reading these binds anybody."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/privacy/retention",
            "/api/v1/privacy/erasure",
        ],
    )
    async def test_a_member_can_still_read_it(
        self, async_session, db_session, test_client_factory, path
    ):
        user, _org = await _member(
            async_session,
            f"member-reads-{path.rsplit('/', 1)[-1]}",
            role=OrganizationRole.MEMBER.value,
        )

        async with test_client_factory(user) as client:
            response = await client.get(path)

        assert response.status_code != 403


class TestThePolicyCanBeReadBackRatherThanOnlyEnforced:
    """``require_organization_role`` stamps its minimum on the dependency so a
    route's policy can be enumerated. A permission that cannot be enumerated
    cannot be tested, and an ungated route looks exactly like a gated one from
    the outside -- which is how these three went to launch."""

    @pytest.mark.parametrize(
        "name", ["set_retention", "request_erasure", "export_data"]
    )
    def test_the_route_declares_the_admin_minimum(self, name):
        from api.routes import privacy

        handler = getattr(privacy, name)
        minimums = {
            getattr(default.dependency, "__org_role_minimum__", None)
            for default in handler.__defaults__ or ()
            if hasattr(default, "dependency")
        }
        assert OrganizationRole.ADMIN.value in minimums, (
            f"{name} does not declare an admin minimum"
        )
