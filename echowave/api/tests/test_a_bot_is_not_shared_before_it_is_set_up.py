"""Seen live: a clinic bot shared with its setup blank greeted a prospect
"Namaste, ." and invented opening hours. The setup screen already knows
what is still needed; the share link now asks the same question before
handing out a door to strangers, and names the fields.

Existing links are untouched: only creating or switching one back on is
gated, so a customer's live demo does not vanish when this ships.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes import workflow_embed

CLINIC = {
    "nodes": [
        {
            "data": {
                "greeting": "Namaste, {{clinic_name}}. How may I help you today?",
                "prompt": "The clinic is open {{opening_hours}} and is at {{clinic_address}}.",
            }
        }
    ]
}


def _workflow(values: dict | None):
    return SimpleNamespace(
        id=35,
        released_definition=SimpleNamespace(
            workflow_json=CLINIC, template_context_variables=values
        ),
    )


def _user():
    return SimpleNamespace(id=1, selected_organization_id=7, provider_id="p1")


@pytest.mark.asyncio
class TestSharing:
    async def _create(self, values):
        with (
            patch.object(
                workflow_embed.db_client,
                "get_workflow",
                AsyncMock(return_value=_workflow(values)),
            ),
            patch.object(
                workflow_embed.db_client,
                "get_draft_version",
                AsyncMock(return_value=None),
            ),
            patch.object(
                workflow_embed.db_client,
                "get_embed_tokens_by_workflow",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                workflow_embed.db_client,
                "create_embed_token",
                AsyncMock(return_value=SimpleNamespace(id=9, daily_minutes_cap=30)),
            ) as create,
            patch.object(
                workflow_embed, "_share_link_response", AsyncMock(return_value="link")
            ),
            patch.object(workflow_embed, "capture_event", lambda **kw: None),
        ):
            try:
                return await workflow_embed.create_share_link(35, user=_user()), create
            except HTTPException as exc:
                return exc, create

    async def test_a_bot_with_blank_setup_is_not_shared_and_the_fields_are_named(self):
        result, create = await self._create({"clinic_name": "", "opening_hours": None})
        assert isinstance(result, HTTPException) and result.status_code == 409
        assert result.detail.startswith("Finish setting up first: ")
        for label in ("Clinic address", "Clinic name"):
            assert label in result.detail
        create.assert_not_awaited()

    async def test_a_set_up_bot_is_shared(self):
        result, create = await self._create(
            {
                "clinic_name": "Narayani Dental",
                "opening_hours": "Mon to Sat 9 to 6",
                "clinic_address": "Hosur",
            }
        )
        assert result == "link"
        create.assert_awaited_once()

    async def test_a_bot_with_no_placeholders_needs_no_setup(self):
        plain = SimpleNamespace(
            id=36,
            released_definition=SimpleNamespace(
                workflow_json={"nodes": [{"data": {"prompt": "Be helpful."}}]},
                template_context_variables={},
            ),
        )
        with (
            patch.object(
                workflow_embed.db_client, "get_workflow", AsyncMock(return_value=plain)
            ),
            patch.object(
                workflow_embed.db_client,
                "get_draft_version",
                AsyncMock(return_value=None),
            ),
        ):
            assert await workflow_embed._unfilled_setup(plain) == []
