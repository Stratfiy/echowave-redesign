"""ADMIN-1: feature switches set from the staff console.

What these defend: the order in which a flag is decided (console row for the
organisation, console global row, environment, ``FEATURE_ORG_OVERRIDES``),
that an expired row decides nothing, that only a superadmin can write, that
every write leaves an audit row and tells the other workers, and that the
screens the UI reads (``/features`` and ``/health``) see the change.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from api import constants
from api.db.feature_override_models import FeatureOverrideModel
from api.db.models import AdminActionLogModel, OrganizationModel, UserModel
from api.enums import StaffRole
from api.services import feature_admin, features
from api.services.features import Override


@pytest.fixture(autouse=True)
def _clean_snapshot(monkeypatch):
    """The snapshot is module-level: a row left in it by one test would
    change what every later test resolves."""
    features.clear_snapshot()
    monkeypatch.setattr(constants, "PROJECTS_ENABLED", False)
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "")
    yield
    features.clear_snapshot()


# ---------------------------------------------------------------------------
# Resolution order (no database)
# ---------------------------------------------------------------------------


class TestPrecedence:
    def test_empty_snapshot_means_the_environment_decides(self, monkeypatch):
        monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "projects:42")
        assert features.is_on("projects") is False
        assert features.is_on("projects", 42) is True
        monkeypatch.setattr(constants, "PROJECTS_ENABLED", True)
        assert features.is_on("projects", 7) is True

    def test_org_row_beats_global_row_beats_environment(self, monkeypatch):
        monkeypatch.setattr(constants, "PROJECTS_ENABLED", True)
        features.set_snapshot(
            {
                ("projects", None): Override(enabled=False),
                ("projects", 42): Override(enabled=True),
            }
        )
        assert features.is_on("projects", 42) is True
        assert features.is_on("projects", 43) is False
        assert features.is_on("projects") is False
        assert features.global_state("projects") == (False, "console")

    def test_global_row_beats_env_org_list(self, monkeypatch):
        monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "projects:42")
        features.set_snapshot({("projects", None): Override(enabled=False)})
        assert features.is_on("projects", 42) is False

    def test_org_row_can_hold_a_feature_off_that_is_on_for_everyone(self, monkeypatch):
        monkeypatch.setattr(constants, "PROJECTS_ENABLED", True)
        features.set_snapshot({("projects", 42): Override(enabled=False)})
        assert features.is_on("projects", 42) is False
        assert features.is_on("projects", 43) is True

    def test_an_expired_row_decides_nothing(self, monkeypatch):
        past = datetime.now(UTC) - timedelta(minutes=1)
        future = datetime.now(UTC) + timedelta(days=1)
        features.set_snapshot(
            {
                ("projects", 42): Override(enabled=True, expires_at=past),
                ("projects", 43): Override(enabled=True, expires_at=future),
                ("projects", None): Override(enabled=True, expires_at=past),
            }
        )
        assert features.is_on("projects", 42) is False
        assert features.is_on("projects", 43) is True
        assert features.is_on("projects") is False
        assert features.global_state("projects") == (False, "environment")

    def test_maps_for_health_and_features_read_the_snapshot(self):
        features.set_snapshot(
            {
                ("projects", 42): Override(enabled=True),
                ("trial_plan", None): Override(enabled=True),
            }
        )
        assert features.for_organization(42)["projects"] is True
        assert features.for_organization(43)["projects"] is False
        assert features.public()["projects"] is False
        assert features.public()["trial_plan"] is True

    def test_every_flag_has_a_console_line(self):
        for name in features.FLAGS:
            assert features.describe(name)


# ---------------------------------------------------------------------------
# Through the HTTP routes, against the database
# ---------------------------------------------------------------------------


async def _org(session, slug: str, name: str | None = None) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"flag-console-{slug}", name=name)
    session.add(org)
    await session.flush()
    return org


async def _user(session, slug: str, role: str | None, org=None) -> UserModel:
    user = UserModel(
        provider_id=f"flag-console-user-{slug}",
        staff_role=role,
        selected_organization_id=org.id if org else None,
    )
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
def as_user(monkeypatch, test_client_factory):
    """A client whose every request is ``user`` -- both ``get_user`` and the
    staff gate, which calls the module-level ``get_user`` itself."""

    def _make(user):
        async def _fake_get_user(*_args, **_kwargs):
            return user

        monkeypatch.setattr("api.services.auth.depends.get_user", _fake_get_user)
        return test_client_factory(user)

    return _make


@pytest.fixture
def sync_manager(monkeypatch):
    manager = AsyncMock()
    monkeypatch.setattr(
        "api.services.worker_sync.manager.get_worker_sync_manager", lambda: manager
    )
    return manager


@pytest.mark.asyncio
class TestConsoleRoutes:
    async def test_a_non_superuser_is_refused(self, db_session, async_session, as_user):
        org = await _org(async_session, "refused")
        for slug, role in (("plain", None), ("support", StaffRole.SUPPORT.value)):
            user = await _user(async_session, slug, role)
            async with as_user(user) as client:
                listed = await client.get("/api/v1/admin/features")
                put = await client.put(
                    f"/api/v1/admin/features/projects/organizations/{org.id}",
                    json={"enabled": True},
                )
            assert listed.status_code == 403
            assert put.status_code == 403
        rows = (await async_session.scalars(select(FeatureOverrideModel))).all()
        assert rows == []

    async def test_unknown_flag_and_unknown_org_are_404(
        self, db_session, async_session, as_user, sync_manager
    ):
        org = await _org(async_session, "unknown")
        admin = await _user(async_session, "admin-404", StaffRole.SUPERADMIN.value)
        async with as_user(admin) as client:
            no_flag = await client.put(
                f"/api/v1/admin/features/no_such_flag/organizations/{org.id}",
                json={"enabled": True},
            )
            no_org = await client.put(
                "/api/v1/admin/features/projects/organizations/999999999",
                json={"enabled": True},
            )
            no_flag_global = await client.put(
                "/api/v1/admin/features/no_such_flag/global",
                json={"enabled": True, "confirm": "no_such_flag"},
            )
            no_flag_delete = await client.delete(
                f"/api/v1/admin/features/no_such_flag/organizations/{org.id}"
            )
        assert no_flag.status_code == 404
        assert no_org.status_code == 404
        assert no_flag_global.status_code == 404
        assert no_flag_delete.status_code == 404
        sync_manager.broadcast.assert_not_called()

    async def test_set_for_an_org_is_audited_broadcast_and_seen_by_features(
        self, db_session, async_session, as_user, sync_manager
    ):
        org = await _org(async_session, "on", name="Asha Clinic")
        other = await _org(async_session, "off")
        admin = await _user(async_session, "admin-set", StaffRole.SUPERADMIN.value)
        member = await _user(async_session, "member", None, org=org)
        outsider = await _user(async_session, "outsider", None, org=other)

        async with as_user(admin) as client:
            put = await client.put(
                f"/api/v1/admin/features/projects/organizations/{org.id}",
                json={"enabled": True, "note": "pilot"},
            )
            again = await client.put(
                f"/api/v1/admin/features/projects/organizations/{org.id}",
                json={"enabled": True, "note": "pilot, again"},
            )
            registry = await client.get("/api/v1/admin/features")
            for_org = await client.get(f"/api/v1/admin/features/organizations/{org.id}")
        assert put.status_code == 200, put.text
        assert again.status_code == 200, again.text

        rows = (
            await async_session.scalars(
                select(FeatureOverrideModel).where(
                    FeatureOverrideModel.feature == "projects"
                )
            )
        ).all()
        assert len(rows) == 1  # an upsert, not a second row
        assert rows[0].organization_id == org.id and rows[0].enabled is True
        assert rows[0].note == "pilot, again"
        assert rows[0].set_by_user_id == admin.id

        audit = (
            await async_session.scalars(
                select(AdminActionLogModel).where(
                    AdminActionLogModel.actor_user_id == admin.id
                )
            )
        ).all()
        assert [a.action for a in audit] == ["flag_set", "flag_set"]
        assert audit[0].target_organization_id == org.id
        assert "feature=projects" in audit[0].note

        assert sync_manager.broadcast.await_count == 2
        assert sync_manager.broadcast.await_args.args[0] == "feature_overrides"

        flag = next(f for f in registry.json()["flags"] if f["name"] == "projects")
        assert flag["global_enabled"] is False
        assert flag["global_source"] == "environment"
        assert flag["overrides"][0]["organization_name"] == "Asha Clinic"
        assert flag["description"]
        org_flag = next(f for f in for_org.json()["flags"] if f["name"] == "projects")
        assert org_flag["enabled"] is True and org_flag["override"]["enabled"] is True

        # The org map the UI's OrgConfigProvider reads.
        async with as_user(member) as client:
            mine = await client.get("/api/v1/features")
        async with as_user(outsider) as client:
            theirs = await client.get("/api/v1/features")
        assert mine.json()["projects"] is True
        assert theirs.json()["projects"] is False

    async def test_clearing_returns_to_the_environment(
        self, db_session, async_session, as_user, sync_manager
    ):
        org = await _org(async_session, "clear")
        admin = await _user(async_session, "admin-clear", StaffRole.SUPERADMIN.value)
        async with as_user(admin) as client:
            await client.put(
                f"/api/v1/admin/features/projects/organizations/{org.id}",
                json={"enabled": True},
            )
            assert features.is_on("projects", org.id) is True
            cleared = await client.delete(
                f"/api/v1/admin/features/projects/organizations/{org.id}"
            )
        assert cleared.status_code == 200
        assert cleared.json()["removed"] is True
        assert features.is_on("projects", org.id) is False
        actions = (
            await async_session.scalars(
                select(AdminActionLogModel.action).where(
                    AdminActionLogModel.actor_user_id == admin.id
                )
            )
        ).all()
        assert actions == ["flag_set", "flag_cleared"]

    async def test_an_expired_override_is_listed_but_does_not_apply(
        self, db_session, async_session, as_user, sync_manager
    ):
        org = await _org(async_session, "expired")
        admin = await _user(async_session, "admin-exp", StaffRole.SUPERADMIN.value)
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        async with as_user(admin) as client:
            await client.put(
                f"/api/v1/admin/features/projects/organizations/{org.id}",
                json={"enabled": True, "expires_at": past},
            )
            registry = await client.get("/api/v1/admin/features")
        assert features.is_on("projects", org.id) is False
        flag = next(f for f in registry.json()["flags"] if f["name"] == "projects")
        assert flag["overrides"][0]["expired"] is True

    async def test_global_needs_the_name_typed_and_reaches_health(
        self, db_session, async_session, as_user, sync_manager
    ):
        admin = await _user(async_session, "admin-global", StaffRole.SUPERADMIN.value)
        async with as_user(admin) as client:
            wrong = await client.put(
                "/api/v1/admin/features/projects/global",
                json={"enabled": True, "confirm": "yes"},
            )
            assert wrong.status_code == 400
            assert features.is_on("projects") is False

            right = await client.put(
                "/api/v1/admin/features/projects/global",
                json={"enabled": True, "confirm": "projects"},
            )
            assert right.status_code == 200, right.text
            health = await client.get("/api/v1/health")
            assert health.json()["features"]["projects"] is True

            refused_clear = await client.delete(
                "/api/v1/admin/features/projects/global", params={"confirm": "x"}
            )
            assert refused_clear.status_code == 400
            cleared = await client.delete(
                "/api/v1/admin/features/projects/global",
                params={"confirm": "projects"},
            )
            assert cleared.status_code == 200
            health = await client.get("/api/v1/health")
            assert health.json()["features"]["projects"] is False

        actions = (
            await async_session.scalars(
                select(AdminActionLogModel.action).where(
                    AdminActionLogModel.actor_user_id == admin.id
                )
            )
        ).all()
        assert actions == ["flag_set", "flag_cleared"]

    async def test_a_second_global_row_is_impossible(self, db_session, async_session):
        admin = await _user(async_session, "admin-uniq", StaffRole.SUPERADMIN.value)
        for enabled in (True, False):
            await feature_admin.set_override(
                async_session,
                name="projects",
                organization_id=None,
                enabled=enabled,
                note=None,
                expires_at=None,
                actor_user_id=admin.id,
            )
        rows = (
            await async_session.scalars(
                select(FeatureOverrideModel).where(
                    FeatureOverrideModel.feature == "projects",
                    FeatureOverrideModel.organization_id.is_(None),
                )
            )
        ).all()
        assert len(rows) == 1 and rows[0].enabled is False


@pytest.mark.asyncio
async def test_refresh_replaces_rather_than_merges(db_session, async_session):
    features.set_snapshot({("projects", 1): Override(enabled=True)})
    await features.refresh_overrides()
    assert ("projects", 1) not in features._SNAPSHOT


@pytest.mark.asyncio
async def test_the_sync_handler_reloads(monkeypatch):
    from api import app as app_module

    reload = AsyncMock(return_value=0)
    monkeypatch.setattr(features, "refresh_overrides", reload)
    await app_module._handle_feature_override_sync(object())
    reload.assert_awaited_once()


def test_the_console_is_not_in_the_public_spec():
    from api.app import app
    from api.services import openapi_surface

    public = openapi_surface.public_spec(app)
    internal = openapi_surface.full_spec(app)
    assert not any(p.startswith("/api/v1/admin/features") for p in public["paths"])
    assert any(p.startswith("/api/v1/admin/features") for p in internal["paths"])
