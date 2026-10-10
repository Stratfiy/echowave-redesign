"""A draft invoice can be made before the tax IDs are known.

"[to confirm]" is accepted for a ``*_gstin`` or ``*_pan`` in a draft, a real
value is still checksummed, and a tax invoice still cannot be issued while
the placeholder remains.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.documents import money, register
from api.services.documents.tools import prepare

GSTIN = money.with_checksum("29AABCU9603R1Z")
INSPECTED = {"fields": ["buyer_gstin", "buyer_pan"], "item_columns": []}


class TestDrafts:
    def test_the_placeholder_is_accepted_for_gstin_and_pan(self):
        out = prepare(
            "tax_invoice",
            INSPECTED,
            {"buyer_gstin": "[to confirm]", "buyer_pan": " [To Confirm] "},
            [],
        )
        assert out.errors == []
        assert out.raw["values"]["buyer_gstin"] == "[to confirm]"

    def test_a_real_value_is_still_checked(self):
        wrong = GSTIN[:-1] + ("0" if GSTIN[-1] != "0" else "1")
        out = prepare("tax_invoice", INSPECTED, {"buyer_gstin": wrong}, [])
        assert any("buyer_gstin" in e for e in out.errors)
        out = prepare("tax_invoice", INSPECTED, {"buyer_pan": "NOTAPAN"}, [])
        assert any("buyer_pan" in e for e in out.errors)

    def test_a_valid_value_passes(self):
        out = prepare(
            "tax_invoice",
            INSPECTED,
            {"buyer_gstin": GSTIN, "buyer_pan": "AABCU9603R"},
            [],
        )
        assert out.errors == []

    def test_other_stand_ins_are_still_rejected(self):
        out = prepare("tax_invoice", INSPECTED, {"buyer_gstin": "TBD"}, [])
        assert any("buyer_gstin" in e for e in out.errors)


@pytest.mark.asyncio
class TestIssuing:
    async def _issue(self, kind: str, values: dict):
        row = SimpleNamespace(
            id=3,
            kind=kind,
            number="INV/26-27/0001",
            status="awaiting_approval",
            amount_paise=1000,
            data={"input": {"values": values, "items": []}},
            due_date=None,
        )
        session = SimpleNamespace(flush=AsyncMock())
        with (
            patch(
                "api.services.documents.register.get", new=AsyncMock(return_value=row)
            ),
            patch("api.services.workflow.approvals.check", new=AsyncMock()),
            patch("api.services.workflow.audit_log.record", new=AsyncMock()),
        ):
            await register.update(
                session, organization_id=7, register_id=3, status="issued"
            )
        return row

    async def test_a_tax_invoice_with_a_placeholder_cannot_be_issued(self):
        with pytest.raises(register.RegisterError, match="to confirm"):
            await self._issue("tax_invoice", {"buyer_gstin": "[to confirm]"})

    async def test_a_tax_invoice_with_real_ids_is_issued(self):
        row = await self._issue("tax_invoice", {"buyer_gstin": GSTIN})
        assert row.status == "issued"
