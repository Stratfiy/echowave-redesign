"""The manual knowledge-base search, and the keys it never had.

``POST /knowledge-base/search`` answered 500 to every query on this deployment.
The documents were fine — a 117-chunk PDF, fully processed. The vault was fine.
The call path was fine. The route was the one place that asked for the
account's configuration *without* asking for its keys: it called
``get_resolved_ai_model_configuration``, which compiles a configuration and
stops, so every managed section came back naming a vendor and carrying an
empty string. That went to the embedding factory and threw.

What makes it worth tests rather than a one-line fix is the shape of the
mistake. Two resolutions exist, one of them loads keys, and there was no
org-level entry point that did — so a caller outside a workflow reached for the
one that looked right and got a configuration nobody had filled in. These pin
both halves: the org-level resolution applies the same two steps the workflow
path does, and the route refuses honestly when there is genuinely no key.
"""

from unittest.mock import AsyncMock, patch

import pytest


class TestTheOrgLevelResolutionAppliesKeys:
    """It must do what the workflow path does, or it will drift again."""

    @pytest.mark.asyncio
    async def test_it_applies_byok_then_managed_in_that_order(self):
        from api.services.configuration import ai_model_configuration as amc

        calls: list[str] = []

        class _Resolved:
            effective = object()

        async def fake_byok(effective, *, organization_id, allow_managed_fallback):
            calls.append("byok")

        async def fake_managed(effective):
            calls.append("managed")

        with (
            patch.object(
                amc,
                "get_resolved_ai_model_configuration",
                AsyncMock(return_value=_Resolved()),
            ),
            patch.object(
                amc, "_managed_fallback_allowed", AsyncMock(return_value=False)
            ),
            patch.object(amc.byok_resolution, "apply", fake_byok),
            patch.object(amc.managed_resolution, "apply", fake_managed),
        ):
            await amc.get_effective_ai_model_configuration_for_organization(30)

        # BYOK first: after managed resolution a managed section also names a
        # vendor, so running it second sends it to the customer's vault for a
        # key they were never asked for.
        assert calls == ["byok", "managed"]

    @pytest.mark.asyncio
    async def test_it_passes_the_accounts_fallback_preference_through(self):
        from api.services.configuration import ai_model_configuration as amc

        seen: dict = {}

        class _Resolved:
            effective = object()

        async def fake_byok(effective, *, organization_id, allow_managed_fallback):
            seen["organization_id"] = organization_id
            seen["allow_managed_fallback"] = allow_managed_fallback

        with (
            patch.object(
                amc,
                "get_resolved_ai_model_configuration",
                AsyncMock(return_value=_Resolved()),
            ),
            patch.object(
                amc, "_managed_fallback_allowed", AsyncMock(return_value=True)
            ),
            patch.object(amc.byok_resolution, "apply", fake_byok),
            patch.object(amc.managed_resolution, "apply", AsyncMock()),
        ):
            await amc.get_effective_ai_model_configuration_for_organization(30)

        assert seen == {"organization_id": 30, "allow_managed_fallback": True}

    @pytest.mark.asyncio
    async def test_it_returns_the_configuration_the_keys_went_into(self):
        """Not a copy: the resolutions mutate in place, so the object handed
        back must be the one they were applied to."""
        from api.services.configuration import ai_model_configuration as amc

        sentinel = object()

        class _Resolved:
            effective = sentinel

        with (
            patch.object(
                amc,
                "get_resolved_ai_model_configuration",
                AsyncMock(return_value=_Resolved()),
            ),
            patch.object(
                amc, "_managed_fallback_allowed", AsyncMock(return_value=False)
            ),
            patch.object(amc.byok_resolution, "apply", AsyncMock()),
            patch.object(amc.managed_resolution, "apply", AsyncMock()),
        ):
            result = await amc.get_effective_ai_model_configuration_for_organization(30)

        assert result is sentinel


def _search_route_source() -> str:
    """The body of ``search_chunks``, read from the module that is imported.

    Via ``__file__`` rather than a relative path. The first version of these
    tests opened "api/routes/knowledge_base.py", which resolves from the repo
    root and not from ``api/`` — where pytest's rootdir actually is — so they
    passed locally and failed in CI with FileNotFoundError. A test that depends
    on the working directory tests the working directory.
    """
    from pathlib import Path

    import api.routes.knowledge_base as route_module

    source = Path(route_module.__file__).read_text(encoding="utf-8")
    return source[source.index("async def search_chunks") :]


class TestTheRouteStillResolvesWithKeys:
    def test_the_search_route_uses_the_key_applying_resolution(self):
        """A grep test, deliberately.

        The bug was invisible at the call site: both functions take an
        organization and return a configuration, and only one of them puts the
        keys in. Nothing in the route's shape said which it had. This fails if
        somebody swaps it back.
        """
        search = _search_route_source()

        assert "get_effective_ai_model_configuration_for_organization(" in search
        # The call form, not the name: the comment above the call names the old
        # function to explain what it replaced, and that mention is the point.
        assert "await get_resolved_ai_model_configuration(" not in search

    def test_a_missing_key_is_a_409_with_a_reason_not_a_blanket_500(self):
        search = _search_route_source()

        assert "status_code=409" in search
        assert "No embeddings key is configured" in search
        # The reason has to survive its own handler: a bare `except Exception`
        # below it would turn this 409 back into the 500 it replaced.
        assert "except HTTPException:" in search
