"""Shared setting for the launch stream `support` tests: two workspaces, a
customer in each (and a colleague in the first), two support staff and a
superadmin, an HTTP client signed in as any of them, and a cleanup that
removes every row the tests made."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.db.models import OrganizationMembershipModel, OrganizationModel, UserModel

FLAGS = (
    "SUPPORT_HELP_ENABLED",
    "SUPPORT_INBOX_ENABLED",
    "SUPPORT_ACTIONS_ENABLED",
)


def switch_on(monkeypatch, *names: str) -> None:
    for name in names or FLAGS:
        monkeypatch.setattr(constants, name, True)


async def _user(session, slug: str, *, staff_role: str | None = None) -> UserModel:
    user = UserModel(
        provider_id=f"support-{slug}",
        email=f"{slug}@example.test",
        staff_role=staff_role,
    )
    session.add(user)
    await session.flush()
    return user


async def make_setting() -> SimpleNamespace:
    run = uuid4().hex[:8]
    async with db_client.async_session() as session:
        org_a = OrganizationModel(provider_id=f"support-a-{run}", name="Acme Clinic")
        org_b = OrganizationModel(provider_id=f"support-b-{run}", name="Other Shop")
        session.add_all([org_a, org_b])
        await session.flush()
        customer = await _user(session, f"cust-{run}")
        colleague = await _user(session, f"coll-{run}")
        stranger = await _user(session, f"strg-{run}")
        agent = await _user(session, f"agent-{run}", staff_role="support")
        agent2 = await _user(session, f"agent2-{run}", staff_role="support")
        boss = await _user(session, f"boss-{run}", staff_role="superadmin")
        for user, org in ((customer, org_a), (colleague, org_a), (stranger, org_b)):
            user.selected_organization_id = org.id
            session.add(
                OrganizationMembershipModel(
                    user_id=user.id, organization_id=org.id, role="owner"
                )
            )
        # The customer is also in the second workspace: scope is the pair.
        session.add(
            OrganizationMembershipModel(
                user_id=customer.id, organization_id=org_b.id, role="member"
            )
        )
        ids = SimpleNamespace(
            org_a=org_a.id,
            org_b=org_b.id,
            customer=customer.id,
            colleague=colleague.id,
            stranger=stranger.id,
            agent=agent.id,
            agent2=agent2.id,
            boss=boss.id,
        )
        await session.commit()
    return ids


def person(user_id: int, organization_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id, selected_organization_id=organization_id, staff_role=None
    )


def staff(user_id: int, role: str = "support") -> SimpleNamespace:
    return SimpleNamespace(id=user_id, selected_organization_id=None, staff_role=role)


@asynccontextmanager
async def client_as(user):
    from api.app import app
    from api.services.auth.depends import get_staff, get_user

    app.dependency_overrides[get_user] = lambda: user
    if getattr(user, "staff_role", None):
        app.dependency_overrides[get_staff] = lambda: user
    else:

        def _refused():
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Support privileges required.")

        app.dependency_overrides[get_staff] = _refused
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_user, None)
        app.dependency_overrides.pop(get_staff, None)


async def cleanup(ids: SimpleNamespace) -> None:
    orgs = {"a": ids.org_a, "b": ids.org_b}
    users = [
        ids.customer,
        ids.colleague,
        ids.stranger,
        ids.agent,
        ids.agent2,
        ids.boss,
    ]
    async with db_client.async_session() as session:
        for org in orgs.values():
            for table in (
                "support_actions",
                "support_attachments",
                "support_messages",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org}
                )
            await session.execute(
                text(
                    "DELETE FROM support_notes WHERE ticket_id IN "
                    "(SELECT id FROM support_tickets WHERE organization_id = :o)"
                ),
                {"o": org},
            )
            await session.execute(
                text("DELETE FROM support_tickets WHERE organization_id = :o"),
                {"o": org},
            )
            for table in (
                "agent_task_transitions",
                "agent_tasks",
                "agent_events",
                "agent_routines",
                "organization_memberships",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org}
                )
        await session.execute(
            text(
                "DELETE FROM admin_action_log WHERE actor_user_id = ANY(:u) "
                "OR target_user_id = ANY(:u)"
            ),
            {"u": users},
        )
        for table in ("quota_allowances", "operational_usage", "member_preferences"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE user_id = ANY(:u)"), {"u": users}
            )
        await session.execute(
            text("UPDATE users SET selected_organization_id = NULL WHERE id = ANY(:u)"),
            {"u": users},
        )
        await session.execute(
            text("DELETE FROM users WHERE id = ANY(:u)"), {"u": users}
        )
        await session.execute(
            text("DELETE FROM organizations WHERE id = ANY(:o)"),
            {"o": list(orgs.values())},
        )
        await session.commit()
