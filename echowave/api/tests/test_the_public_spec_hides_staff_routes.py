"""What staff can do is enforced everywhere and described nowhere public.

Two guards. The public OpenAPI document -- the one served at
/api/v1/openapi.json and checked in for the docs -- carries no staff-only
operation and none of the schemas only they use. And every route mounted
under /admin or /superuser declares a staff dependency, on its router or on
itself, so a route that is hidden is also a route that refuses.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from api.app import app
from api.services import openapi_surface
from api.services.auth.depends import get_staff, get_superuser

STAFF_GATES = {get_staff, get_superuser}


def _staff_gated(route: APIRoute) -> bool:
    # Router-level dependencies are folded into the route's dependant by
    # FastAPI; a gate on the handler's own signature lands there too.
    return any(d.call in STAFF_GATES for d in _walk(route.dependant))


def _walk(dependant):
    for d in dependant.dependencies:
        yield d
        yield from _walk(d)


class TestThePublicDocument:
    def test_has_no_staff_operation(self):
        spec = openapi_surface.public_spec(app)
        leaked = [
            (path, method)
            for path, ops in spec["paths"].items()
            for method, op in ops.items()
            if openapi_surface.is_internal(path, op)
        ]
        assert leaked == []
        assert not any(
            p.startswith(("/api/v1/admin/", "/api/v1/superuser/"))
            for p in spec["paths"]
        )

    def test_has_no_staff_only_schema(self):
        public = openapi_surface.public_spec(app)
        names = set(public["components"]["schemas"])
        assert "ImpersonateRequest" not in names
        # Nothing public was lost: every schema a public path references exists.
        assert openapi_surface._referenced_schemas(public) <= names

    def test_the_whole_app_is_still_available_for_the_client(self):
        internal = openapi_surface.full_spec(app)
        assert any(p.startswith("/api/v1/admin/") for p in internal["paths"])
        assert any(p.startswith("/api/v1/superuser/") for p in internal["paths"])

    def test_the_served_document_is_the_public_one(self):
        served = app.openapi()
        assert not any(p.startswith("/api/v1/admin/") for p in served["paths"])


class TestEveryStaffRouteRefuses:
    def test_admin_and_superuser_routes_carry_a_staff_gate(self):
        ungated = [
            route.path
            for route in app.routes
            if isinstance(route, APIRoute)
            and route.path.startswith(("/api/v1/admin/", "/api/v1/superuser/"))
            and not _staff_gated(route)
        ]
        assert ungated == []

    def test_a_staff_tag_means_a_staff_path_or_gate(self):
        """A router tagged admin-… but mounted elsewhere would be hidden from
        the document and must still be gated."""
        loose = [
            route.path
            for route in app.routes
            if isinstance(route, APIRoute)
            and any(
                str(t).lower().startswith(openapi_surface.INTERNAL_TAG_PREFIXES)
                for t in (route.tags or [])
            )
            and not _staff_gated(route)
        ]
        assert loose == []
