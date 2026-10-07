"""The runtime capability checklist (handoff packet A, sections 3, 4, 29;
launch stream controls).

Done when: staff can read, per capability, its source, configuration and
tested status separately, with an honest state; a setting is reported as
present or not and never by value; and every module, flag, setting and test
the checklist names exists, so a rename fails here instead of quietly
shortening the list.
"""

from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from api import constants
from api.services import capabilities, features


class TestTheListIsTrue:
    def test_every_built_capabilitys_modules_exist(self):
        for cap in capabilities.CAPABILITIES:
            if cap.planned_by:
                continue
            for module in cap.modules:
                assert importlib.util.find_spec(module) is not None, (cap.key, module)

    def test_a_planned_capability_names_its_stream_and_is_not_built(self):
        for cap in capabilities.CAPABILITIES:
            if cap.planned_by:
                row = capabilities.evaluate(cap)
                assert row["state"] == capabilities.UNAVAILABLE
                assert cap.planned_by in row["reason"]

    def test_every_flag_is_registered(self):
        for cap in capabilities.CAPABILITIES:
            for flag in cap.flags:
                assert flag in features.FLAGS, (cap.key, flag)

    def test_every_constant_setting_exists(self):
        for cap in capabilities.CAPABILITIES:
            for setting in cap.settings:
                if not setting.startswith("env:"):
                    assert hasattr(constants, setting), (cap.key, setting)

    def test_every_named_test_file_exists(self):
        for cap in capabilities.CAPABILITIES:
            for name in cap.tests:
                assert (capabilities.TESTS_DIR / name).is_file(), (cap.key, name)

    def test_keys_are_unique(self):
        keys = [c.key for c in capabilities.CAPABILITIES]
        assert len(keys) == len(set(keys))


class TestTheStates:
    def test_switched_off_is_disabled_by_policy(self, monkeypatch):
        monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", False)
        row = capabilities.evaluate(
            next(c for c in capabilities.CAPABILITIES if c.key == "task_ledger")
        )
        assert row["state"] == capabilities.DISABLED
        assert row["source"]["status"] == "present"
        assert row["tested"]["status"] == "unit_tested"
        assert row["tested"]["staging"] == "not_verified"

    def test_on_but_unconfigured_needs_setup_and_never_shows_a_value(self, monkeypatch):
        monkeypatch.setattr(constants, "EVENT_CATALOGUE_ENABLED", True)
        monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "s3cret-value")
        monkeypatch.setattr(constants, "POSTHOG_API_KEY", None)
        row = capabilities.evaluate(
            next(c for c in capabilities.CAPABILITIES if c.key == "event_catalogue")
        )
        assert row["state"] == capabilities.NEEDS_SETUP
        assert row["configuration"]["settings"] == {
            "ANALYTICS_PSEUDONYM_KEY": True,
            "POSTHOG_API_KEY": False,
        }
        assert "s3cret" not in str(row)

    def test_on_and_configured_is_available(self, monkeypatch):
        monkeypatch.setattr(constants, "OPERATIONAL_QUOTAS_ENABLED", True)
        row = capabilities.evaluate(
            next(c for c in capabilities.CAPABILITIES if c.key == "operational_quotas")
        )
        assert row["state"] == capabilities.AVAILABLE and row["reason"] == ""

    def test_partial_source_says_partial(self):
        row = capabilities.evaluate(
            capabilities.Capability(
                "half_built",
                "Half built",
                "3",
                modules=("api.services.knowledge_graph.spaced_recall",),
                extra={"partial": True},
            )
        )
        assert row["source"]["status"] == "partial"

    def test_learning_is_present_now(self):
        # Stream `learning` built it: no longer recalled facts only.
        row = capabilities.evaluate(
            next(c for c in capabilities.CAPABILITIES if c.key == "learning")
        )
        assert row["source"]["status"] == "present"


@asynccontextmanager
async def _client(staff):
    from api.app import app
    from api.services.auth.depends import get_staff

    if staff is not None:
        app.dependency_overrides[get_staff] = lambda: staff
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_staff, None)


@pytest.mark.asyncio
class TestArrival:
    async def test_off_the_endpoint_is_not_there(self):
        async with _client(SimpleNamespace(id=1)) as client:
            response = await client.get("/api/v1/admin/controls/capabilities")
        assert response.status_code == 404

    async def test_staff_read_the_checklist(self, monkeypatch):
        monkeypatch.setattr(constants, "CAPABILITY_CHECKLIST_ENABLED", True)
        async with _client(SimpleNamespace(id=1)) as client:
            response = await client.get("/api/v1/admin/controls/capabilities")
        assert response.status_code == 200
        rows = {r["key"]: r for r in response.json()["capabilities"]}
        assert {"task_ledger", "operational_quotas", "virtual_card"} <= set(rows)
        for row in rows.values():
            assert {"source", "configuration", "tested", "state"} <= set(row)

    async def test_a_customer_cannot_read_it(self, monkeypatch):
        monkeypatch.setattr(constants, "CAPABILITY_CHECKLIST_ENABLED", True)
        async with _client(None) as client:
            response = await client.get("/api/v1/admin/controls/capabilities")
        assert response.status_code in (401, 403)

    def test_the_staff_route_is_not_in_the_public_spec(self):
        from api.app import app
        from api.services.openapi_surface import public_spec

        paths = public_spec(app)["paths"]
        assert not any("/admin/controls" in p for p in paths)
        assert any(p.endswith("/me/preferences") for p in paths)
