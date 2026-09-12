"""Who may grant an app authorization, and who may only use one.

`OrganizationRole` says ADMIN governs "integration credentials (secrets, and
spend under someone else's contract)", and warns in its own docstring about
the failure this file exists to prevent: "a role that appears in a picker and
restricts nobody is a permission an operator believes they have granted."

That is exactly what had happened. The credential vault and the do-not-disturb
list were correctly admin-gated; every connector route took plain `get_user`.
So any member could press Connect and grant the whole organization access to a
Gmail account -- and on a paid app, spend against whoever authorized it -- or
press Disconnect and break every agent that books appointments.

The line: **granting or destroying an authorization is admin; using one stays
member.** A member builds agents, which is what a member is for, and can
already spend the balance by placing calls. Granting the access is the part
that binds the account.
"""

import pytest

from api.app import app
from api.enums import ORGANIZATION_ROLE_RANK, OrganizationRole


def _minimum_role(path: str, method: str) -> str | None:
    """The role a route requires, read off the dependency itself.

    Introspected rather than asserted from a list kept here, so a route added
    later without a guard fails this rather than quietly joining the ones that
    had none.
    """
    for route in app.routes:
        if getattr(route, "path", None) != path:
            continue
        if method.upper() not in (getattr(route, "methods", None) or set()):
            continue
        dependant = getattr(route, "dependant", None)
        found: list[str] = []
        stack = [dependant] if dependant else []
        while stack:
            node = stack.pop()
            call = getattr(node, "call", None)
            role = getattr(call, "__org_role_minimum__", None)
            if role:
                found.append(role)
            stack.extend(getattr(node, "dependencies", None) or [])
        if found:
            return max(found, key=lambda r: ORGANIZATION_ROLE_RANK.get(r, -1))
        return None
    raise AssertionError(f"No route {method} {path}")


GRANTS_OR_DESTROYS = [
    # Mints an OAuth link. One press grants every agent in the organization
    # access to that account.
    ("/api/v1/connectors/{slug}/connect", "POST"),
    # Starts the Google OAuth for the whole account.
    ("/api/v1/integrations/google-calendar/authorize-url", "GET"),
    # Tears the connection down. Every booking agent stops working.
    ("/api/v1/integrations/google-calendar/disconnect", "POST"),
    # Decides which calendars count as busy, so it decides whether the account
    # double-books. Not a secret, but it binds everyone's bookings.
    ("/api/v1/integrations/google-calendar/busy-calendars", "PUT"),
]

READS = [
    # A member building an agent has to see what is connected, or they cannot
    # attach anything and have no way to find out why.
    ("/api/v1/connectors", "GET"),
    ("/api/v1/connectors/accounts", "GET"),
    ("/api/v1/connectors/activity", "GET"),
    ("/api/v1/integrations/google-calendar/status", "GET"),
]


class TestGrantingIsAdmin:
    @pytest.mark.parametrize("path,method", GRANTS_OR_DESTROYS)
    def test_it_requires_at_least_admin(self, path, method):
        role = _minimum_role(path, method)
        assert role is not None, f"{method} {path} is ungated"
        assert (
            ORGANIZATION_ROLE_RANK[role]
            >= ORGANIZATION_ROLE_RANK[OrganizationRole.ADMIN.value]
        ), f"{method} {path} requires only {role}"


class TestUsingStaysOpen:
    @pytest.mark.parametrize("path,method", READS)
    def test_a_member_can_still_read_what_is_connected(self, path, method):
        """Over-gating is its own failure. A member who cannot see the
        connected apps cannot build the agent they were hired to build, and
        the screen would say nothing about why."""
        role = _minimum_role(path, method)
        assert role is None or role == OrganizationRole.MEMBER.value, (
            f"{method} {path} requires {role}, which locks members out of "
            "reading what is connected"
        )


class TestThePolicyIsReadable:
    def test_the_dependency_reports_what_it_enforces(self):
        """The mechanism the tests above rely on. Without it a permission can
        only be enforced, never enumerated -- and an ungated route looks
        exactly like a gated one from outside."""
        from api.services.auth.depends import require_organization_role

        built = require_organization_role(OrganizationRole.ADMIN)
        assert built.__org_role_minimum__ == "admin"

    def test_the_vault_that_was_already_right_still_is(self):
        """A regression guard on the routes this change was modelled on."""
        assert _minimum_role("/api/v1/credentials/", "POST") == "admin"
