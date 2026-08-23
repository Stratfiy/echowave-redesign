"""Routes that read an ORM row after committing the session.

``async_sessionmaker`` is built without ``expire_on_commit=False``
(``db/base_client.py``), so a commit expires every loaded attribute. Reading one
afterwards makes SQLAlchemy reload it, and in async that reload raises
``MissingGreenlet`` rather than quietly fetching — a 500 on a route whose work
already succeeded and committed.

``db/organization_client.py`` names the trap in a comment and reads before its
commit for exactly this reason. These routes did not, and each is a superadmin
screen's only endpoint. The GET is the one that mattered most: it needs no
request body and no state, so ``/superadmin/billing/bundles`` answered 500 on
every load, for everyone, always.

**These tests deliberately do not take the ``db_session`` fixture.** That
fixture points ``db_client.async_session`` at the test session, which conftest
builds with ``expire_on_commit=False`` (conftest.py) — the one setting that
makes this bug impossible to reproduce. Production builds its sessionmaker with
the default. So a test written the usual way passes against broken code, which
is exactly how five of these shipped. Letting the route open its own real
session is the whole point of the file.

The assertions are deliberately about the status code and not the payload. What
is being pinned is "reading a row after the commit does not blow up", and a
route can serve any shape it likes as long as it survives.

The cost of that is real writes outside the savepoint every other test relies
on, so anything written here is removed again in a ``finally``. Skipping that
leaves an extra bundle in the shared test database and breaks every test that
asserts on the exact seeded set — which is a worse failure than the one this
file exists to catch, because it lands on someone else's test.


Every test here takes ``async_session`` and never uses it. That fixture creates
the schema and orders this file against the rest of the suite; without it these
tests pass alone and fail in a full run. It is *not* ``db_session``, which would
repoint ``db_client`` at the savepoint and hide the bug again.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from api.db.models import OrganizationModel, PartnerApplicationModel, UserModel


async def _staff(suffix: str):
    """An organization and a staff user, committed through a *real* session.

    Not the ``async_session`` fixture: that runs inside a savepoint the route's
    own session cannot see, so anything created there is invisible to the code
    under test and every route answers 404 — a green test that ran nothing.
    """
    from api.db import db_client

    # These rows are committed for real, outside any savepoint, so they survive
    # the test that made them. Unique per run or the second run collides on
    # organizations.provider_id.
    suffix = f"{suffix}_{uuid.uuid4().hex[:8]}"

    async with db_client.async_session() as session:
        org = OrganizationModel(provider_id=f"org_commit_probe_{suffix}")
        session.add(org)
        await session.flush()
        user = UserModel(
            provider_id=f"user_commit_probe_{suffix}",
            selected_organization_id=org.id,
        )
        session.add(user)
        await session.flush()
        org_id, user_id = org.id, user.id
        await session.commit()

    # Detached and expired after that commit, so hand back a plain instance the
    # dependency override can return without touching the database again.
    return org_id, UserModel(id=user_id, selected_organization_id=org_id)


async def _forget_bundle(slug: str):
    """Remove a bundle this file created.

    These writes are real and outside the savepoint, so without this the row
    survives the run and every test asserting on the exact seeded set fails.
    """
    from sqlalchemy import delete

    from api.db import db_client
    from api.db.models import ManagedBundleModel

    async with db_client.async_session() as session:
        await session.execute(
            delete(ManagedBundleModel).where(ManagedBundleModel.slug == slug)
        )
        await session.commit()


def _as_staff(user):
    from contextlib import asynccontextmanager

    from api.app import app
    from api.services.auth.depends import get_superuser

    @asynccontextmanager
    async def _ctx():
        app.dependency_overrides[get_superuser] = lambda: user
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_superuser, None)

    return _ctx()


class TestBundles:
    async def test_listing_bundles_survives_the_seed_commit(self, async_session):
        """``list_bundles`` seeds, commits, then reads ``row.slug`` off the
        seeded rows. Always at least one bundle, so always a 500."""
        _, user = await _staff("list")
        async with _as_staff(user) as client:
            response = await client.get("/api/v1/admin/billing/bundles")
        assert response.status_code == 200, response.text[:400]
        assert "bundles" in response.json()

    async def test_upserting_a_bundle_survives_its_own_commit(self, async_session):
        _, user = await _staff("upsert")
        slug = f"probe-bundle-{uuid.uuid4().hex[:8]}"
        try:
            async with _as_staff(user) as client:
                response = await client.put(
                    "/api/v1/admin/billing/bundles",
                    json={
                        "slug": slug,
                        "label": "Probe",
                        "architecture": "pipeline",
                        # A pipeline bundle is refused without both, and a
                        # refusal never reaches the commit under test.
                        "stt_tier": "default",
                        "tts_tier": "default",
                        "is_enabled": False,
                    },
                )
            # Must actually reach the commit: a 400 here means the request was
            # rejected before the line under test ran, and the test proved
            # nothing.
            assert response.status_code == 200, response.text[:400]
        finally:
            await _forget_bundle(slug)


class TestPartnerDecisions:
    @pytest.mark.parametrize("decision", ["approve", "reject"])
    async def test_deciding_an_application_survives_its_own_commit(
        self, async_session, decision
    ):
        """Both hand the committed row straight to ``_queue_view``."""
        org_id, user = await _staff(decision)

        from api.db import db_client

        async with db_client.async_session() as setup:
            application = PartnerApplicationModel(
                organization_id=org_id,
                kind="reseller",
                status="pending",
            )
            setup.add(application)
            await setup.flush()
            application_id = application.id
            await setup.commit()

        body = (
            {"commission_bps": 1000, "basis": "platform_fee"}
            if decision == "approve"
            else {"note": "not this time"}
        )
        async with _as_staff(user) as client:
            response = await client.post(
                f"/api/v1/admin/partners/{application_id}/{decision}", json=body
            )
        # A 404 means the row was invisible to the route and the decision code
        # never ran, so it is as much a failed test as a 500.
        assert response.status_code == 200, response.text[:400]


class TestOfferedModels:
    async def test_setting_offered_models_survives_the_closed_session(
        self, async_session
    ):
        """Reads its result *outside* the ``async with`` block, which looks like
        the same bug and is not: ``model_catalogue.set_offered`` returns frozen
        ``CatalogueEntry`` dataclasses rather than ORM rows, so there is nothing
        left attached to a session to expire. Kept as the counter-example — the
        pattern to look for is an ORM row crossing a commit, not a read that
        happens to sit after one."""
        _, user = await _staff("models")
        # A provider name nothing else uses. `set_offered` is a *replace*, so
        # naming a real one here would empty its catalogue for the rest of the
        # run and break the tiers that resolve to it.
        provider = f"probe-vendor-{uuid.uuid4().hex[:8]}"
        try:
            async with _as_staff(user) as client:
                response = await client.put(
                    "/api/v1/admin/provider-keys/models",
                    json={
                        "component": "llm",
                        "provider": provider,
                        "models": ["probe-model"],
                        "labels": {"probe-model": "Probe model"},
                    },
                )
            assert response.status_code != 500, response.text[:400]
        finally:
            from sqlalchemy import delete

            from api.db import db_client
            from api.db.models import PlatformModelModel

            async with db_client.async_session() as session:
                await session.execute(
                    delete(PlatformModelModel).where(
                        PlatformModelModel.provider == provider
                    )
                )
                await session.commit()
