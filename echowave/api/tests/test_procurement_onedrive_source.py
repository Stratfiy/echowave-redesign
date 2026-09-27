"""A OneDrive or SharePoint link is a template source (KAN-159, slice 1).

The founder's own formats live on OneDrive, not Google Drive. A sharing link
is resolved to an item through the connected OneDrive account and the file is
downloaded, the same two-call shape the Google Doc path already has. Nothing
is guessed from the link itself beyond "this is a Microsoft link".
"""

from __future__ import annotations

import base64
import io
from unittest.mock import AsyncMock, patch

import docx
import pytest

from api.services.documents import sources

pytestmark = pytest.mark.asyncio

SHARE = "https://nautomationlabs-my.sharepoint.com/:w:/g/personal/nk_x/EaBcD?e=abc"


def _docx_bytes() -> bytes:
    document = docx.Document()
    document.add_paragraph("Invoice for {{client_name}}")
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


class TestRecognisingTheLink:
    @pytest.mark.parametrize(
        "link",
        [
            SHARE,
            "https://1drv.ms/w/s!AbCdEf",
            "https://onedrive.live.com/?id=ABC&cid=DEF",
            "https://contoso.sharepoint.com/sites/ops/Shared%20Documents/PO.docx",
        ],
    )
    def test_microsoft_links_are_onedrive(self, link):
        assert sources.onedrive_link(link) == link

    @pytest.mark.parametrize(
        "text",
        [
            "https://docs.google.com/document/d/1234567890123456789012345/edit",
            "standard",
            "5b3c1d2e-1111-2222-3333-444455556666",
            "sharepoint.com is where it lives",
        ],
    )
    def test_anything_else_is_not(self, text):
        assert sources.onedrive_link(text) is None


class TestReadingTheTemplate:
    async def test_the_link_is_resolved_then_the_file_downloaded(self):
        accounts = [{"app": "one_drive", "connected_account_id": "ca_od"}]
        calls = []

        async def execute(**kwargs):
            calls.append(kwargs)
            if kwargs["tool_slug"] == sources.ONEDRIVE_BY_SHARING_URL:
                return {
                    "status": "success",
                    "data": {
                        "id": "01ITEM",
                        "name": "PO format.docx",
                        "parentReference": {"driveId": "b!DRIVE"},
                    },
                }
            return {
                "status": "success",
                "data": {
                    "file": {
                        "name": "PO format.docx",
                        "mimetype": sources.DOCX_MIME,
                        "data": base64.b64encode(_docx_bytes()).decode(),
                    }
                },
            }

        with (
            patch(
                "api.services.integrations.composio.client.connected_accounts",
                new=AsyncMock(return_value=accounts),
            ),
            patch(
                "api.services.integrations.composio.client.execute_tool", new=execute
            ),
        ):
            template = await sources.resolve_template(7, SHARE, kind="purchase_order")

        assert template.label == "onedrive:01ITEM"
        assert template.note is None
        assert template.data[:2] == b"PK"
        assert [c["tool_slug"] for c in calls] == [
            sources.ONEDRIVE_BY_SHARING_URL,
            sources.ONEDRIVE_DOWNLOAD,
        ]
        assert calls[0]["arguments"] == {"sharing_url": SHARE}
        assert calls[1]["arguments"] == {
            "item_id": "01ITEM",
            "drive_id": "b!DRIVE",
            "file_name": "PO format.docx",
        }
        assert all(c["connected_account_id"] == "ca_od" for c in calls)

    async def test_not_connected_names_the_app_to_connect(self):
        with patch(
            "api.services.integrations.composio.client.connected_accounts",
            new=AsyncMock(return_value=[]),
        ):
            with pytest.raises(sources.SourceError) as exc:
                await sources.resolve_template(7, SHARE, kind="purchase_order")
        assert exc.value.needs_app == "one_drive"
        assert "OneDrive is not connected" in str(exc.value)

    async def test_a_spreadsheet_is_refused_by_name_until_slice_two(self):
        accounts = [{"app": "one_drive", "connected_account_id": "ca_od"}]

        async def execute(**kwargs):
            if kwargs["tool_slug"] == sources.ONEDRIVE_BY_SHARING_URL:
                return {
                    "status": "success",
                    "data": {"id": "01X", "name": "001 Copy Hero LLC.xlsx"},
                }
            return {
                "status": "success",
                "data": {
                    "file": {"name": "001 Copy Hero LLC.xlsx", "data": "UEsDBBQA"}
                },
            }

        with (
            patch(
                "api.services.integrations.composio.client.connected_accounts",
                new=AsyncMock(return_value=accounts),
            ),
            patch(
                "api.services.integrations.composio.client.execute_tool", new=execute
            ),
        ):
            with pytest.raises(sources.SourceError) as exc:
                await sources.resolve_template(7, SHARE, kind="purchase_order")
        assert "001 Copy Hero LLC.xlsx" in str(exc.value)
        assert ".docx" in str(exc.value)

    async def test_a_link_that_resolves_to_nothing_is_a_plain_refusal(self):
        accounts = [{"app": "one_drive", "connected_account_id": "ca_od"}]

        async def execute(**kwargs):
            return {"status": "failed", "error": "itemNotFound"}

        with (
            patch(
                "api.services.integrations.composio.client.connected_accounts",
                new=AsyncMock(return_value=accounts),
            ),
            patch(
                "api.services.integrations.composio.client.execute_tool", new=execute
            ),
        ):
            with pytest.raises(sources.SourceError) as exc:
                await sources.resolve_template(7, SHARE, kind="purchase_order")
        assert "itemNotFound" in str(exc.value)
