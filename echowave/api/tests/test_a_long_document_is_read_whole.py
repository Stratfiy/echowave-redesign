"""U-2: a long attachment is read in full, by anyone, a part at a time.

A deck, contract or report attached in the thread is shown clipped.
read_document read the rest but cut at 20,000 characters and said "ask for
the part you need" with no way to ask; and it was only offered while the
procurement tools were on. Now it takes a start, says where the next part
begins, and is offered whenever the table tools are on.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api import constants
from api.services.documents import reading
from api.services.documents import tools as procurement
from api.services.workflow import decibyl, tables


def _long_text(monkeypatch, n: int = 25):
    monkeypatch.setattr(reading, "MAX_TEXT_CHARS", 10)
    return ("0123456789" * 3)[:n].encode()


class TestAPartAtATime:
    def test_the_first_part_says_where_the_next_begins(self, monkeypatch):
        out = reading.extract(_long_text(monkeypatch), "notes.txt")
        assert out["text"] == "0123456789"
        assert out["next_start"] == 10 and out["length"] == 25

    def test_the_next_part_is_read_from_its_start(self, monkeypatch):
        out = reading.extract(_long_text(monkeypatch), "notes.txt", start=20)
        assert out["text"] == "01234"
        assert "next_start" not in out

    def test_a_start_past_the_end_says_so(self, monkeypatch):
        out = reading.extract(_long_text(monkeypatch), "notes.txt", start=99)
        assert out["text"] == ""
        assert "past the end" in out["note"]

    def test_a_short_document_is_one_part(self):
        out = reading.extract(b"short", "notes.txt")
        assert out["text"] == "short" and "next_start" not in out

    def test_the_schema_takes_a_start(self):
        schema = next(s for s in procurement.schemas() if s["name"] == procurement.READ)
        assert "start" in schema["parameters"]["properties"]


@pytest.fixture
def tables_only(monkeypatch):
    monkeypatch.setattr(constants, "TABLE_TOOLS_ENABLED", True)
    monkeypatch.setattr(constants, "PROCUREMENT_DOCS_2026_09_ENABLED", False)


class TestOfferedToEveryone:
    def test_with_table_tools_on_it_is_offered_without_procurement(self, tables_only):
        names = [t["name"] for t in decibyl.office_tools()]
        assert names.count(procurement.READ) == 1
        assert procurement.DRAFT not in names

    def test_with_both_on_it_is_offered_once(self, monkeypatch):
        monkeypatch.setattr(constants, "TABLE_TOOLS_ENABLED", True)
        monkeypatch.setattr(constants, "PROCUREMENT_DOCS_2026_09_ENABLED", True)
        names = [t["name"] for t in decibyl.office_tools()]
        assert names.count(procurement.READ) == 1

    def test_with_both_off_it_is_not_offered(self, monkeypatch):
        monkeypatch.setattr(constants, "TABLE_TOOLS_ENABLED", False)
        monkeypatch.setattr(constants, "PROCUREMENT_DOCS_2026_09_ENABLED", False)
        names = [t["name"] for t in decibyl.office_tools()]
        assert procurement.READ not in names

    def test_the_rules_name_it(self, tables_only):
        prompt = decibyl.system_prompt()
        for tool in decibyl.office_tools():
            assert tool["name"] in prompt, tool["name"]

    async def test_a_call_reads_the_part_asked_for(self, tables_only):
        read = AsyncMock(return_value={"filename": "Deck.pdf", "text": "…"})
        with patch.object(reading, "read", read):
            out = await tables.read_document(
                7, {"document": "Deck.pdf", "start": 20000}
            )
        assert out["status"] == "success"
        read.assert_awaited_once_with(7, "Deck.pdf", start=20000)

    async def test_a_failure_is_a_sentence_not_a_raise(self, tables_only):
        from api.services.documents import sources

        with patch.object(
            reading, "read", AsyncMock(side_effect=sources.SourceError("gone"))
        ):
            out = await tables.read_document(7, {"document": "x"})
        assert out == {"status": "error", "error": "gone"}
