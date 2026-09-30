"""Whole-table tools for Decibyl (pilot: Netoyed Solutions, 30 Sep 2026).

The thread reads an attached CSV clipped at 24,000 characters. These tools
read every row from the original upload, so "which accounts should we go
for" is answered over the whole list, with the reasons kept per row.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api import constants
from api.services.workflow import tables

ACCOUNTS = """Bank Name,Type,Region,Branches,Total Assets,Core Banking,IT Head
State Bank of Example,Public Sector,Pan India,"22,000",₹62 lakh Cr,Finacle,
Axis Demo Bank,Private,Pan India,"5,100",₹14 lakh Cr,Finacle,R. Sharma
Small Coop Bank,Co-operative,Karnataka,12,₹900 Cr,Legacy in-house,

Kotak Sample Bank,Private,West,"1,900",₹7 lakh Cr,Flexcube,
Rural Gramin Bank,Regional Rural,Bihar,"1,100",₹45000 Cr,Legacy in-house,A. Kumar
"""


def _table():
    return tables.parse(ACCOUNTS, name="Netoyed accounts.csv")


class TestReading:
    def test_every_row_is_read_and_blank_rows_are_skipped(self):
        t = _table()
        assert len(t.rows) == 5
        assert t.columns[0] == "Bank Name"
        assert t.rows[1]["Bank Name"] == "Axis Demo Bank"
        assert t.rows[2]["IT Head"] == ""

    def test_a_missing_or_repeated_header_keeps_every_column(self):
        t = tables.parse("Name,,Name\na,b,c\n")
        assert t.columns == ["Name", "Column 2", "Name (2)"]
        assert t.rows[0] == {"Name": "a", "Column 2": "b", "Name (2)": "c"}

    def test_rows_past_the_cap_are_counted_not_lost_silently(self, monkeypatch):
        monkeypatch.setattr(tables, "MAX_ROWS", 3)
        t = _table()
        assert len(t.rows) == 3 and t.truncated_rows == 2

    def test_an_empty_file_says_so(self):
        with pytest.raises(tables.TableError):
            tables.parse("\n\n")

    @pytest.mark.parametrize(
        "cell, expected",
        [
            ("₹1,20,000", 120000),
            ("3.2 Cr", 3.2e7),
            ("45 L", 4.5e6),
            ("12.5%", 12.5),
            ("2.1bn", 2.1e9),
            ("5,100", 5100),
            ("none", None),
            ("", None),
        ],
    )
    def test_numbers_as_people_write_them(self, cell, expected):
        assert tables.number(cell) == expected


class TestDescribe:
    def test_columns_fill_values_and_ranges(self):
        d = tables.describe(_table())
        by = {c["name"]: c for c in d["columns"]}
        assert d["rows"] == 5 and d["rows_not_read"] == 0
        assert by["IT Head"]["filled"] == 2
        assert by["Core Banking"]["top_values"][0] == {"value": "Finacle", "rows": 2}
        assert by["Branches"]["numeric"] == {"min": 12, "max": 22000}
        assert "numeric" not in by["Bank Name"]
        assert len(d["sample"]) == 3


class TestFilterAndRank:
    def test_conditions_all_hold(self):
        t = _table()
        rows = tables.filter_rows(
            t,
            [("Type", "in", ["Private", "Public Sector"]), ("Branches", "gte", "2000")],
        )
        assert [r["Bank Name"] for r in rows] == [
            "State Bank of Example",
            "Axis Demo Bank",
        ]

    def test_every_row_is_scored_with_the_rules_it_met(self):
        t = _table()
        rules = [
            tables.Rule(
                "Core Banking", "contains", "legacy", 3, "legacy core to modernise"
            ),
            tables.Rule("Branches", "gte", "1000", 2, "large branch network"),
            tables.Rule(
                "Type", "equals", "Public Sector", -1, "long procurement cycle"
            ),
        ]
        ranked = tables.rank(t, rules)
        assert [(r["row"]["Bank Name"], r["score"]) for r in ranked] == [
            ("Rural Gramin Bank", 5.0),
            ("Small Coop Bank", 3.0),
            ("Axis Demo Bank", 2.0),
            ("Kotak Sample Bank", 2.0),
            ("State Bank of Example", 1.0),
        ]
        assert ranked[0]["met"] == ["legacy core to modernise", "large branch network"]

    def test_ties_keep_the_files_own_order(self):
        t = _table()
        ranked = tables.rank(t, [tables.Rule("Region", "not_empty", None, 1, "x")])
        assert [r["row"]["Bank Name"] for r in ranked] == [
            r["Bank Name"] for r in t.rows
        ]


def _run(name, arguments, table=None):
    loaded = AsyncMock(return_value=table or _table())
    return patch.object(tables, "load", loaded), name, arguments


@pytest.mark.asyncio
class TestTheToolCalls:
    async def _call(self, name, arguments):
        with patch.object(tables, "load", AsyncMock(return_value=_table())):
            return await tables.run(name, organization_id=7, arguments=arguments)

    async def test_describe(self):
        out = await self._call("describe_table", {"file": "Netoyed accounts.csv"})
        assert out["status"] == "success" and out["rows"] == 5

    async def test_query_sorts_pages_and_totals(self):
        out = await self._call(
            "query_table",
            {
                "file": "x",
                "where": [{"column": "type", "op": "equals", "value": "private"}],
                "sort_by": "Branches",
                "descending": True,
                "columns": ["Bank Name", "Branches"],
                "limit": 1,
            },
        )
        assert out["matched"] == 2 and out["of"] == 5
        assert out["rows"] == [{"Bank Name": "Axis Demo Bank", "Branches": "5,100"}]

    async def test_rank_returns_rules_top_rows_and_reasons(self):
        out = await self._call(
            "rank_table",
            {
                "file": "x",
                "rules": [
                    {
                        "column": "Core Banking",
                        "op": "contains",
                        "value": "legacy",
                        "points": 3,
                        "why": "legacy core",
                    },
                ],
                "show": 2,
            },
        )
        assert out["ranked"] == 5 and out["scoring_rows"] == 2
        assert [t["row"]["Bank Name"] for t in out["top"]] == [
            "Small Coop Bank",
            "Rural Gramin Bank",
        ]
        assert out["top"][0]["met"] == ["legacy core"]

    async def test_an_unknown_column_names_the_real_ones(self):
        out = await self._call(
            "rank_table",
            {
                "file": "x",
                "rules": [
                    {
                        "column": "Revenue",
                        "op": "gt",
                        "value": 1,
                        "points": 1,
                        "why": "big",
                    }
                ],
            },
        )
        assert out["status"] == "error"
        assert "Bank Name" in out["error"] and "Revenue" in out["error"]

    async def test_rank_without_rules_is_refused(self):
        out = await self._call("rank_table", {"file": "x", "rules": []})
        assert out["status"] == "error"

    async def test_an_unknown_condition_is_refused(self):
        out = await self._call(
            "query_table", {"file": "x", "where": [{"column": "Type", "op": "like"}]}
        )
        assert out["status"] == "error" and "equals" in out["error"]

    async def test_export_writes_a_ranked_sheet_and_the_rules(self):
        build = AsyncMock(
            return_value={"status": "success", "register_id": 3, "number": "OT-1"}
        )
        with (
            patch.object(tables, "load", AsyncMock(return_value=_table())),
            patch("api.services.documents.tools.build_spreadsheet", build),
        ):
            out = await tables.run(
                "export_table",
                organization_id=7,
                arguments={
                    "file": "x",
                    "title": "Netoyed target accounts",
                    "rules": [
                        {
                            "column": "Branches",
                            "op": "gte",
                            "value": "1000",
                            "points": 2,
                            "why": "large network",
                        }
                    ],
                    "columns": ["Bank Name", "Branches"],
                    "top": 3,
                },
            )
        assert out["status"] == "success" and out["rows"] == 3
        sheets = build.await_args.args[1]["sheets"]
        assert sheets[0]["columns"] == ["Rank", "Score", "Why", "Bank Name", "Branches"]
        assert sheets[0]["rows"][0] == [
            1,
            2.0,
            "large network",
            "State Bank of Example",
            "22,000",
        ]
        assert sheets[1]["name"] == "Rules"
        assert sheets[1]["rows"] == [["Branches", "gte", "1000", 2.0, "large network"]]

    async def test_a_failure_never_raises(self):
        with patch.object(tables, "load", AsyncMock(side_effect=RuntimeError("boom"))):
            out = await tables.run(
                "describe_table", organization_id=7, arguments={"file": "x"}
            )
        assert out["status"] == "error"


@pytest.mark.asyncio
class TestLoading:
    async def test_the_original_upload_is_read_not_the_clipped_text(self):
        document = SimpleNamespace(
            filename="Netoyed accounts.csv",
            organization_id=7,
            document_uuid="0f8c3b6e-1a2b-4c3d-9e8f-001122334455",
            custom_metadata={"s3_key": "kb/7/doc/Netoyed accounts.csv"},
        )
        download = AsyncMock(return_value=ACCOUNTS.encode("utf-8"))
        with (
            patch.object(tables, "_find_document", AsyncMock(return_value=document)),
            patch.object(tables, "_download", download),
        ):
            t = await tables.load(7, "Netoyed accounts.csv")
        assert len(t.rows) == 5
        download.assert_awaited_once_with("kb/7/doc/Netoyed accounts.csv")

    async def test_a_pdf_is_not_a_table(self):
        document = SimpleNamespace(
            filename="Netoyed Solutions Deck.pdf",
            organization_id=7,
            document_uuid="x",
            custom_metadata={},
        )
        with patch.object(tables, "_find_document", AsyncMock(return_value=document)):
            with pytest.raises(tables.TableError, match="not a CSV"):
                await tables.load(7, "Netoyed Solutions Deck.pdf")

    async def test_a_file_in_another_workspace_is_not_found(self, db_session):
        """Scoped by organisation in the query: another workspace's file of
        the same name is simply not there. Runs in the rolled-back test
        session, so the new organisation and its onboarding credit leave
        nothing behind."""
        from uuid import uuid4

        user, _ = await db_session.get_or_create_user_by_provider_id(
            f"tbl-{uuid4().hex[:8]}"
        )
        org, _ = await db_session.get_or_create_organization_by_provider_id(
            f"tbl-org-{uuid4().hex[:8]}", user.id
        )
        with pytest.raises(tables.TableError, match="No file called"):
            await tables._find_document(org.id, "Netoyed accounts.csv")


class TestDecibylOffersThem:
    def test_off_they_are_not_offered(self):
        from api.services.workflow import decibyl

        names = {t["name"] for t in decibyl.office_tools()}
        assert not names & tables.NAMES

    def test_on_they_are(self, monkeypatch):
        from api.services.workflow import decibyl

        monkeypatch.setattr(constants, "TABLE_TOOLS_ENABLED", True)
        names = {t["name"] for t in decibyl.office_tools()}
        assert tables.NAMES <= names

    def test_the_flag_is_registered_and_off_by_default(self):
        from api.services import features

        assert features.FLAGS["table_tools"] == "TABLE_TOOLS_ENABLED"
        assert constants.TABLE_TOOLS_ENABLED is False


class TestPeopleToReach:
    """Step 2: who to reach at each account, from public pages only.

    The contact book needs an email or a phone, and a named IT head found on
    a bank's leadership page usually has neither. They go on a People sheet
    in the same workbook, each with the page they came from; anyone without
    a public source is reported back, never dropped silently."""

    def test_a_person_with_a_public_source_is_kept(self):
        rows, rejected = tables.people_rows(
            [
                {
                    "account": "Axis Demo Bank",
                    "name": "R. Sharma",
                    "title": "Chief Information Officer",
                    "source_url": "https://www.axisdemo.example/leadership",
                    "route": "Board office, number on the leadership page",
                    "why": "Owns core banking and branch IT",
                    "opener": "Saw your Finacle upgrade in the annual report.",
                }
            ]
        )
        assert rejected == []
        assert rows == [
            [
                "Axis Demo Bank",
                "R. Sharma",
                "Chief Information Officer",
                "Owns core banking and branch IT",
                "Board office, number on the leadership page",
                "Saw your Finacle upgrade in the annual report.",
                "https://www.axisdemo.example/leadership",
            ]
        ]

    def test_the_sheet_has_a_column_for_every_field(self):
        rows, _ = tables.people_rows(
            [{"account": "A", "name": "B", "source_url": "https://a.example/b"}]
        )
        assert len(rows[0]) == len(tables.PEOPLE_COLUMNS)
        assert tables.PEOPLE_COLUMNS[-1] == "Source"

    def test_no_source_is_rejected_with_the_reason(self):
        rows, rejected = tables.people_rows(
            [{"account": "Small Coop Bank", "name": "A. Rao", "title": "CEO"}]
        )
        assert rows == []
        assert rejected == [{"name": "A. Rao", "reason": "no public source page"}]

    def test_linkedin_is_never_a_source(self):
        rows, rejected = tables.people_rows(
            [
                {
                    "account": "Kotak Sample Bank",
                    "name": "S. Iyer",
                    "title": "CTO",
                    "source_url": "https://in.linkedin.com/in/siyer",
                }
            ]
        )
        assert rows == []
        assert rejected[0]["reason"] == "linkedin.com is not a source we use"

    def test_a_source_that_is_not_a_web_page_is_rejected(self):
        _, rejected = tables.people_rows(
            [{"account": "X", "name": "Y", "source_url": "my notes"}]
        )
        assert rejected[0]["reason"] == "no public source page"

    def test_a_row_without_a_name_or_account_is_rejected(self):
        _, rejected = tables.people_rows(
            [{"title": "CIO", "source_url": "https://bank.example/team"}, "junk"]
        )
        assert [r["reason"] for r in rejected] == [
            "needs an account and a name",
            "needs an account and a name",
        ]

    def test_people_past_the_cap_are_counted(self, monkeypatch):
        monkeypatch.setattr(tables, "MAX_PEOPLE", 1)
        person = {"account": "A", "name": "B", "source_url": "https://a.example/b"}
        rows, rejected = tables.people_rows([person, person])
        assert len(rows) == 1
        assert rejected == [{"name": "B", "reason": "over the limit of 1"}]

    async def test_export_adds_a_people_sheet_and_reports_rejections(self):
        build = AsyncMock(
            return_value={"status": "success", "register_id": 3, "number": "OT-1"}
        )
        with (
            patch.object(tables, "load", AsyncMock(return_value=_table())),
            patch("api.services.documents.tools.build_spreadsheet", build),
        ):
            out = await tables.run(
                "export_table",
                organization_id=7,
                arguments={
                    "file": "x",
                    "title": "Netoyed target accounts",
                    "rules": [
                        {
                            "column": "Type",
                            "op": "equals",
                            "value": "Private",
                            "points": 1,
                            "why": "private bank",
                        }
                    ],
                    "people": [
                        {
                            "account": "Axis Demo Bank",
                            "name": "R. Sharma",
                            "title": "CIO",
                            "source_url": "https://axisdemo.example/leadership",
                        },
                        {
                            "account": "Kotak Sample Bank",
                            "name": "S. Iyer",
                            "source_url": "https://linkedin.com/in/siyer",
                        },
                    ],
                },
            )
        sheets = build.await_args.args[1]["sheets"]
        assert [s["name"] for s in sheets] == ["Ranked", "People to reach", "Rules"]
        assert sheets[1]["columns"] == list(tables.PEOPLE_COLUMNS)
        assert len(sheets[1]["rows"]) == 1
        assert out["people"] == 1
        assert out["people_rejected"] == [
            {"name": "S. Iyer", "reason": "linkedin.com is not a source we use"}
        ]

    async def test_export_without_people_has_no_people_sheet(self):
        build = AsyncMock(return_value={"status": "success"})
        with (
            patch.object(tables, "load", AsyncMock(return_value=_table())),
            patch("api.services.documents.tools.build_spreadsheet", build),
        ):
            out = await tables.run(
                "export_table", organization_id=7, arguments={"file": "x", "title": "t"}
            )
        sheets = build.await_args.args[1]["sheets"]
        assert [s["name"] for s in sheets] == ["Rows"]
        assert "people" not in out

    def test_the_rules_are_said_only_while_the_tools_are_on(self, monkeypatch):
        from api.services.workflow import decibyl

        monkeypatch.setattr(constants, "TABLE_TOOLS_ENABLED", False)
        assert tables.RULES not in decibyl.system_prompt()
        monkeypatch.setattr(constants, "TABLE_TOOLS_ENABLED", True)
        prompt = decibyl.system_prompt()
        assert tables.RULES in prompt
        assert "LinkedIn" in tables.RULES and "source" in tables.RULES
        assert "card" in tables.RULES
