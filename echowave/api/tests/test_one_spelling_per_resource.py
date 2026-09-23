"""One spelling per resource in the public API (API-1), with nothing broken.

The reference shows /workflows, /campaigns, /folders, /organizations and
/users; the app answers both those and the singular paths every existing
integration was written against; a path that was already plural is left
exactly as it was; and the UI's own client keeps the paths it was built on.
"""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from api.services import api_paths
from api.services.api_paths import PluralPaths, plural_of, singular_of


def test_the_spellings_map_both_ways_and_only_whole_segments():
    assert plural_of("/api/v1/workflow/12/runs") == "/api/v1/workflows/12/runs"
    assert plural_of("/api/v1/workflow") == "/api/v1/workflows"
    assert plural_of("/api/v1/organisation/memory") == "/api/v1/organizations/memory"
    # A longer word that merely starts the same is not the resource.
    assert plural_of("/api/v1/workflow-recordings/x") == "/api/v1/workflow-recordings/x"
    assert plural_of("/api/v1/user-stuff") == "/api/v1/user-stuff"
    assert singular_of("/api/v1/workflows/12/runs") == "/api/v1/workflow/12/runs"
    assert singular_of("/api/v1/tasks") is None


def _app():
    async def singular(request):
        return PlainTextResponse(f"singular {request.path_params['id']}")

    async def native(request):
        return PlainTextResponse("native plural")

    async def collection(request):
        return PlainTextResponse("campaigns")

    inner = Starlette(
        routes=[
            Route("/api/v1/campaign/", collection),
            Route("/api/v1/workflow/{id}/runs", singular),
            Route("/api/v1/workflows/{id}/routines", native),
        ]
    )
    return TestClient(PluralPaths(inner, routes_of=lambda: inner.routes))


def test_the_app_answers_both_spellings():
    client = _app()
    assert client.get("/api/v1/workflow/7/runs").text == "singular 7"
    assert client.get("/api/v1/workflows/7/runs").text == "singular 7"


def test_a_collection_written_with_a_slash_answers_without_one():
    client = _app()
    assert client.get("/api/v1/campaigns").text == "campaigns"
    assert client.get("/api/v1/campaigns/").text == "campaigns"


def test_a_natively_plural_path_is_never_rewritten():
    assert _app().get("/api/v1/workflows/7/routines").text == "native plural"


def test_a_plural_path_with_no_singular_route_is_an_ordinary_404():
    assert _app().get("/api/v1/workflows/7/nothing").status_code == 404


@pytest.fixture(scope="module")
def app():
    from api.app import app

    return app


def test_the_public_reference_shows_only_the_plural(app):
    from api.services import openapi_surface

    paths = openapi_surface.public_spec(app)["paths"]
    for singular in api_paths.PLURAL:
        stale = [
            p
            for p in paths
            if p == f"/api/v1/{singular}" or p.startswith(f"/api/v1/{singular}/")
        ]
        assert stale == [], stale[:3]
    assert any(p.startswith("/api/v1/workflows/") for p in paths)


def test_the_ui_client_keeps_the_paths_it_was_built_on(app):
    from api.services import openapi_surface

    paths = openapi_surface.full_spec(app)["paths"]
    assert any(p.startswith("/api/v1/workflow/") for p in paths)


def test_every_singular_route_has_a_free_plural_twin(app):
    """No plural spelling may already mean something else, or the alias
    would shadow it."""
    own = {
        (route.path, method)
        for route in app.routes
        for method in (getattr(route, "methods", None) or ())
    }
    for path, method in own:
        twin = plural_of(path)
        if twin != path:
            assert (twin, method) not in own, (method, path)


def test_the_app_is_wrapped(app):
    assert any(m.cls is PluralPaths for m in app.user_middleware)
