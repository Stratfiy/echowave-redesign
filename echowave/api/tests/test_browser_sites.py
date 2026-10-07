"""Where the private browser may go: the address rule, the staff's list,
the defaults, and the box's own proxy keeping the same rule.

The proxy (``sandbox/browser/netguard.py``) runs in the box and cannot import
the api, so it carries its own copy of the address rule. The parity test
here is what keeps the two from drifting: one corpus, both answers.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from api.services.browser import sites
from api.services.workflow import web_tools

NETGUARD = Path(__file__).resolve().parents[2] / "sandbox" / "browser" / "netguard.py"


def _netguard():
    spec = importlib.util.spec_from_file_location("browser_netguard", NETGUARD)
    module = importlib.util.module_from_spec(spec)
    sys.modules["browser_netguard"] = module
    spec.loader.exec_module(module)
    return module


HOSTS = [
    "localhost",
    "127.0.0.1",
    "10.0.0.5",
    "172.16.4.1",
    "192.168.1.1",
    "169.254.169.254",
    "100.64.0.1",
    "0.0.0.0",
    "::1",
    "intranet",
    "metadata",
    "2130706433",
    "example.com",
    "bills.example.in",
    "8.8.8.8",
    "1.1.1.1",
    "www.amazon.in",
]


class TestTheAddressRule:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8080/admin",
            "http://127.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.1.2.3/",
            "http://192.168.0.1/router",
            "http://intranet/",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "ftp://example.com/",
        ],
    )
    def test_not_a_page_on_the_web_is_refused(self, url):
        decided = sites.decide(url)
        assert not decided.allowed
        assert decided.source == "address"

    def test_an_ordinary_site_is_allowed(self):
        assert sites.decide("https://www.amazon.in/s?k=rice").allowed

    @pytest.mark.parametrize("host", HOSTS)
    def test_the_box_keeps_the_same_rule(self, host):
        netguard = _netguard()
        assert netguard.is_private_host(host) == web_tools._is_private(host), host

    def test_a_name_resolving_privately_is_not_public(self):
        assert asyncio.run(sites.resolves_public("localhost")) is False

    def test_web_fetch_still_refuses_the_social_networks(self):
        """check_url was split, not loosened: web_fetch keeps its rule."""
        with pytest.raises(web_tools.FetchRefused):
            web_tools.check_url("https://www.linkedin.com/in/someone")
        assert web_tools.check_address("https://www.linkedin.com/in/someone")


class TestTheLists:
    def test_sites_whose_terms_forbid_automation_are_refused_by_default(self):
        decided = sites.decide("https://www.linkedin.com/feed/")
        assert not decided.allowed and decided.source == "default"
        assert "terms" in decided.reason

    def test_staff_can_allow_a_default_and_deny_anything(self):
        rules = [
            sites.Rule("linkedin.com", sites.ALLOW, "reviewed"),
            sites.Rule("shady.example", sites.DENY, "fraud reports"),
        ]
        assert sites.decide("https://www.linkedin.com/", rules).allowed
        refused = sites.decide("https://pay.shady.example/x", rules)
        assert not refused.allowed and "fraud reports" in refused.reason

    def test_the_most_specific_rule_wins(self):
        rules = [
            sites.Rule("example.com", sites.DENY, "no"),
            sites.Rule("help.example.com", sites.ALLOW, ""),
        ]
        assert sites.decide("https://help.example.com/", rules).allowed
        assert not sites.decide("https://shop.example.com/", rules).allowed

    def test_a_lookalike_is_not_the_site(self):
        assert not sites.on_task(
            "https://bills.example.in.evil.example/", ["bills.example.in"]
        )
        assert sites.on_task("https://pay.bills.example.in/", ["bills.example.in"])

    def test_every_default_shows_on_the_staff_list(self):
        """Silent absence: a default the staff screen does not show is a
        rule nobody can review."""
        listed = {r["site"] for r in sites.effective_list([])}
        assert set(sites.DEFAULT_DENY) <= listed

    def test_the_box_proxy_honours_the_list(self):
        netguard = _netguard()
        guard = netguard.Guard(
            sites.effective_list([sites.Rule("shady.example", "deny")])
        )
        assert guard.allows_name("www.linkedin.com", 443)
        assert guard.allows_name("pay.shady.example", 443)
        assert guard.allows_name("example.com", 22) == "only web ports are opened"
        assert guard.allows_name("www.amazon.in", 443) is None
        assert guard.allows_name("169.254.169.254", 80)


class TestTheStaffRoutes:
    async def test_staff_set_and_remove_a_rule_and_each_change_is_audited(
        self, db_session, async_session, test_client_factory
    ):
        from sqlalchemy import select

        from api.db.models import AdminActionLogModel, UserModel

        staff = UserModel(
            provider_id="staff-browser-sites",
            email="staff@browser.example",
            staff_role="superadmin",
        )
        async_session.add(staff)
        await async_session.flush()
        from api.app import app
        from api.services.auth.depends import get_superuser

        app.dependency_overrides[get_superuser] = lambda: staff
        try:
            async with test_client_factory(staff) as client:
                put = await client.put(
                    "/api/v1/admin/browser/sites",
                    json={
                        "site": "https://www.Shady.example/x",
                        "rule": "deny",
                        "reason": "fraud",
                    },
                )
                assert put.status_code == 200, put.text
                assert put.json()["site"] == "shady.example"
                listed = (await client.get("/api/v1/admin/browser/sites")).json()
                assert {
                    "site": "shady.example",
                    "rule": "deny",
                    "reason": "fraud",
                    "source": "staff",
                } in listed
                assert any(
                    r["site"] == "linkedin.com" and r["source"] == "default"
                    for r in listed
                )
                assert (
                    await client.delete("/api/v1/admin/browser/sites/shady.example")
                ).status_code == 200
        finally:
            app.dependency_overrides.pop(get_superuser, None)
        rows = (
            (
                await async_session.execute(
                    select(AdminActionLogModel).where(
                        AdminActionLogModel.actor_user_id == staff.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [r.action for r in rows] == [
            "browser_site_rule_set",
            "browser_site_rule_removed",
        ]

    async def test_a_member_cannot_change_the_list(
        self, db_session, async_session, test_client_factory
    ):
        from api.db.models import UserModel

        member = UserModel(
            provider_id="member-browser-sites", email="m@browser.example"
        )
        async_session.add(member)
        await async_session.flush()
        async with test_client_factory(member) as client:
            response = await client.put(
                "/api/v1/admin/browser/sites",
                json={"site": "x.example", "rule": "allow"},
            )
        assert response.status_code in (401, 403)
