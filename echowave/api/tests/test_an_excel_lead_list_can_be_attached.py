"""A lead list kept in Excel must be attachable in Chat, and read in full.

Found building an outreach agent end to end (October 2026): a business
owner's lead list is as often an .xlsx export as a CSV. The composer's
paperclip refused it ("Please select a supported file type"), document
extraction refused it, and the table tools read CSV and TSV only -- so the
only way in was "save it as CSV and attach it again", which is the owner
doing the platform's job.

Now .xlsx is read the way a CSV is: every row, with its header, as text for
the thread, and as a table for describe/query/rank.
"""

from __future__ import annotations

import io
import os
import tempfile

from openpyxl import Workbook

from api.services.knowledge_base import extraction
from api.services.workflow import tables


def _book(rows: list[list], *, empty_first_sheet: bool = False) -> bytes:
    book = Workbook()
    sheet = book.active
    if empty_first_sheet:
        sheet.title = "Notes"
        sheet = book.create_sheet("Leads")
    else:
        sheet.title = "Leads"
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


ROWS = [
    ["Name", "Company", "Email", "City", "Employees"],
    ["Asha Rao", "Lotus Dental", "asha@lotusdental.example.com", "Pune", 18],
    ["Vikram Shah", "Smile Studio", "vikram@smilestudio.example.com", "Mumbai", 7],
]


def _extract(data: bytes, filename: str = "leads.xlsx"):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, filename)
        with open(path, "wb") as fh:
            fh.write(data)
        return extraction.extract_document(path, filename)


class TestExtraction:
    def test_xlsx_is_a_supported_extension(self):
        assert ".xlsx" in extraction.SUPPORTED_EXTENSIONS

    def test_each_row_reads_with_its_header(self):
        document = _extract(_book(ROWS))
        text = "\n".join(block.text for block in document.blocks)
        assert "Name: Asha Rao" in text
        assert "Email: asha@lotusdental.example.com" in text
        assert "Employees: 7" in text

    def test_the_sheet_with_the_rows_is_read_not_only_the_first(self):
        document = _extract(_book(ROWS, empty_first_sheet=True))
        text = "\n".join(block.text for block in document.blocks)
        assert "Vikram Shah" in text


class TestTables:
    def test_an_xlsx_is_a_table(self):
        assert ".xlsx" in tables.TABLE_EXTENSIONS

    def test_every_row_is_parsed_from_the_workbook(self):
        table = tables.from_upload(_book(ROWS), ".xlsx", name="leads.xlsx")
        assert table.columns[:3] == ["Name", "Company", "Email"]
        assert len(table.rows) == 2
        assert table.rows[1]["City"] == "Mumbai"
        # A number stays readable as a number for rank_table's rules.
        assert tables.number(table.rows[0]["Employees"]) == 18
