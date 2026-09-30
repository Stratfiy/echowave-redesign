"""Work with a whole spreadsheet a person attached (pilot: Netoyed, 30 Sep 2026).

The thread reads an attachment as text, clipped at 24,000 characters, which is
the first hundred or so rows of a real account list. Enough to talk about a
file; not enough to answer "which of these 3,000 accounts should we go for". So a
CSV the person attached is also readable here as a table: every row, parsed
from the original file in storage, never the clipped text.

Four tools, all reads, all run in the turn:

* ``describe_table`` -- the columns, how many rows, how full each column is,
  its most common values and, for a numeric column, its range. What the model
  needs before it can say anything true about the file.
* ``query_table`` -- rows that match conditions, sorted, a page at a time.
* ``rank_table`` -- every row scored by weighted rules the model writes from
  what the person told it (the solution deck, the ideal customer), with the
  rules each row met kept beside its score, so a ranking can be explained row
  by row rather than asserted.
* ``export_table`` -- the ranked or filtered rows as an .xlsx on the thread,
  through the same spreadsheet builder and register the documents use.

Deterministic on purpose: the scoring is code, the judgement is in the rules,
and the rules are shown with the result. A model asked to rank 3,000 rows by
reading them would rank the 100 it could see.
"""

from __future__ import annotations

import csv
import io
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from api.services import features

FLAG = "table_tools"

DESCRIBE_TOOL_NAME = "describe_table"
QUERY_TOOL_NAME = "query_table"
RANK_TOOL_NAME = "rank_table"
EXPORT_TOOL_NAME = "export_table"
NAMES = frozenset(
    {DESCRIBE_TOOL_NAME, QUERY_TOOL_NAME, RANK_TOOL_NAME, EXPORT_TOOL_NAME}
)
#: Reads keep Decibyl's tools open for the next step; export hands a file
#: over and ends the round the way a card does.
READS = frozenset({DESCRIBE_TOOL_NAME, QUERY_TOOL_NAME, RANK_TOOL_NAME})

#: Largest file read as a table. Bigger than any account list a person
#: attaches to a chat; small enough that parsing it in the turn is instant.
MAX_FILE_BYTES = 20_000_000
#: Rows parsed. Past this the rest are counted and said, never silently lost.
MAX_ROWS = 50_000
#: Rows handed back to the model in one call. A page, not the file.
MAX_ROWS_SHOWN = 50
#: Rows written to an export.
MAX_EXPORT_ROWS = 5_000
MAX_RULES = 30
MAX_PEOPLE = 300
PEOPLE_SHEET = "People to reach"
PEOPLE_COLUMNS = (
    "Account",
    "Name",
    "Role",
    "Why them",
    "How to reach",
    "Draft opener",
    "Source",
)
RULES = (
    "- Spreadsheets: an attached Excel or CSV file is shown clipped. describe_table, "
    "query_table and rank_table read every row. To say which accounts to go "
    "for, write the rules from the person's own material and show them. "
    "export_table puts the ranked list in an .xlsx.\n"
    "- Who to reach: name people only from public pages (the company's "
    "leadership or management page, annual report, press release, "
    "regulator filing, news) that you read with the web tools. Never use "
    "LinkedIn. Give every named person the page they came from as their "
    "source. Never guess an email or a phone number. If none is published, "
    "say how to reach them (the switchboard, the investor-relations desk, "
    "a named assistant). Put them in export_table's people so they are on "
    "the same workbook, and tell the person which ones were refused and why. "
    "Give each person a short opener drawn from the person's own material, "
    "written for them to send; an email you send from here goes on a card "
    "they confirm, never straight out.\n"
)
MAX_CONDITIONS = 12
#: Distinct values listed per column when describing.
TOP_VALUES = 8
#: Characters of any one cell shown to the model.
MAX_CELL_CHARS = 200

TABLE_EXTENSIONS = {".csv": ",", ".tsv": "\t"}
#: Read with openpyxl. The old binary .xls is not; saving it as .xlsx is.
WORKBOOK_EXTENSIONS = frozenset({".xlsx", ".xlsm"})
OPS = (
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "in",
    "gt",
    "gte",
    "lt",
    "lte",
    "empty",
    "not_empty",
)


def enabled() -> bool:
    return features.is_on(FLAG)


class TableError(Exception):
    """Something the person or the model can act on, said plainly."""


@dataclass(frozen=True)
class Table:
    name: str
    columns: list[str]
    rows: list[dict[str, str]]
    truncated_rows: int = 0
    #: For a workbook: the sheet read, and every sheet it has.
    sheet: str = ""
    sheets: tuple[str, ...] = ()


# --- Reading ----------------------------------------------------------------


def parse(text: str, *, delimiter: str = ",", name: str = "table") -> Table:
    """Rows as dicts keyed by the header. Blank rows are skipped; a header
    cell that is empty or repeated is named so no column is lost."""
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return _from_rows(reader, name=name)


def _from_rows(raw_rows, *, name: str) -> Table:
    header: Optional[list[str]] = None
    rows: list[dict[str, str]] = []
    extra = 0
    for raw in raw_rows:
        if not any(cell.strip() for cell in raw):
            continue
        if header is None:
            header = _unique_header(raw)
            continue
        if len(rows) >= MAX_ROWS:
            extra += 1
            continue
        cells = [c.strip() for c in raw] + [""] * (len(header) - len(raw))
        rows.append({column: cells[i] for i, column in enumerate(header)})
    if header is None:
        raise TableError(f"{name} has no rows.")
    return Table(name=name, columns=header, rows=rows, truncated_rows=extra)


def _cell(value: Any) -> str:
    """A workbook cell as a person would type it: 1200 not 1200.0, a date
    as 2027-03-31, nothing as an empty string."""
    from datetime import date, datetime

    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        if (value.hour, value.minute, value.second) == (0, 0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def parse_xlsx(
    data: bytes, *, sheet: Optional[str] = None, name: str = "table"
) -> Table:
    """One sheet of a workbook, read whole: the one named (any case), or the
    first. Formulas are read as their last saved values."""
    from openpyxl import load_workbook

    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - any unreadable file says the same
        raise TableError(f"{name} could not be opened as an Excel workbook.") from exc
    try:
        names = tuple(book.sheetnames)
        if not names:
            raise TableError(f"{name} has no sheets.")
        wanted = str(sheet or "").strip()
        chosen = names[0]
        if wanted:
            match = [n for n in names if n.lower() == wanted.lower()]
            if not match:
                raise TableError(
                    f"{name} has no sheet called {wanted!r}. Its sheets: "
                    + ", ".join(names)
                    + "."
                )
            chosen = match[0]
        rows = (
            [_cell(v) for v in row] for row in book[chosen].iter_rows(values_only=True)
        )
        table = _from_rows(rows, name=name)
    finally:
        book.close()
    return Table(
        name=table.name,
        columns=table.columns,
        rows=table.rows,
        truncated_rows=table.truncated_rows,
        sheet=chosen,
        sheets=names,
    )


def _unique_header(raw: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for index, cell in enumerate(raw, start=1):
        base = cell.strip() or f"Column {index}"
        count = seen.get(base.lower(), 0)
        seen[base.lower()] = count + 1
        out.append(base if count == 0 else f"{base} ({count + 1})")
    return out


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


async def _find_document(organization_id: int, file: str):
    """The workspace's document by id, or else its newest by file name."""
    from sqlalchemy import select

    from api.db import db_client
    from api.db.models import KnowledgeBaseDocumentModel

    wanted = str(file or "").strip()
    if not wanted:
        raise TableError("Say which file: its name as attached, or its id.")
    if re.fullmatch(r"[0-9a-fA-F-]{32,36}", wanted):
        document = await db_client.get_document_by_uuid(
            wanted, organization_id=organization_id
        )
        if document is not None:
            return document
    async with db_client.async_session() as session:
        rows = (
            (
                await session.execute(
                    select(KnowledgeBaseDocumentModel)
                    .where(
                        KnowledgeBaseDocumentModel.organization_id == organization_id,
                        KnowledgeBaseDocumentModel.filename.ilike(wanted),
                    )
                    .order_by(KnowledgeBaseDocumentModel.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .all()
        )
    if not rows:
        raise TableError(f"No file called {wanted!r} in this workspace.")
    return rows[0]


def _storage_key(document) -> str:
    from api.services.knowledge_base import upload_keys

    metadata = getattr(document, "custom_metadata", None) or {}
    key = metadata.get("s3_key") if isinstance(metadata, dict) else None
    return key or upload_keys.build_document_key(
        document.organization_id, str(document.document_uuid), document.filename
    )


async def _download(key: str) -> bytes:
    from api.services.storage import storage_fs

    handle, path = tempfile.mkstemp(suffix=".table")
    os.close(handle)
    try:
        if not await storage_fs.adownload_file(key, path):
            raise TableError("The file could not be read from storage just now.")
        if os.path.getsize(path) > MAX_FILE_BYTES:
            raise TableError("That file is too large to read as a table here.")
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def load(
    organization_id: int, file: str, *, sheet: Optional[str] = None
) -> Table:
    """The whole table, parsed from the original upload."""
    document = await _find_document(organization_id, file)
    extension = os.path.splitext(document.filename or "")[1].lower()
    if extension == ".xls":
        raise TableError(
            f"{document.filename} is the old Excel format. Save it as .xlsx "
            "(or CSV) and attach it again to work with every row."
        )
    if extension not in TABLE_EXTENSIONS and extension not in WORKBOOK_EXTENSIONS:
        raise TableError(
            f"{document.filename} is not a spreadsheet (Excel, CSV or TSV). "
            "Attach the list as one of those to work with every row."
        )
    data = await _download(_storage_key(document))
    if extension in WORKBOOK_EXTENSIONS:
        return parse_xlsx(data, sheet=sheet, name=document.filename)
    return parse(
        _decode(data),
        delimiter=TABLE_EXTENSIONS[extension],
        name=document.filename,
    )


# --- Conditions and scores ---------------------------------------------------

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def number(value: Any) -> Optional[float]:
    """A number from a cell as a person writes it: "₹1,20,000", "12.5%",
    "3.2 Cr" (crore), "45 L" (lakh), "2.1bn". None when there is none."""
    if value is None:
        return None
    text = str(value).strip().lower().replace(",", "")
    if not text:
        return None
    match = _NUMBER.search(text)
    if not match:
        return None
    amount = float(match.group())
    tail = text[match.end() :].strip()
    for suffix, factor in (
        ("crore", 1e7),
        ("cr", 1e7),
        ("lakh", 1e5),
        ("lac", 1e5),
        ("l", 1e5),
        ("bn", 1e9),
        ("b", 1e9),
        ("mn", 1e6),
        ("m", 1e6),
        ("k", 1e3),
    ):
        if tail.startswith(suffix):
            return amount * factor
    return amount


def _column(table: Table, name: Any) -> str:
    wanted = str(name or "").strip()
    for column in table.columns:
        if column == wanted:
            return column
    for column in table.columns:
        if column.lower() == wanted.lower():
            return column
    raise TableError(
        f"No column {wanted!r}. The columns are: " + ", ".join(table.columns)
    )


def _matches(cell: str, op: str, value: Any) -> bool:
    text = (cell or "").strip()
    if op == "empty":
        return not text
    if op == "not_empty":
        return bool(text)
    if op in ("gt", "gte", "lt", "lte"):
        left, right = number(text), number(value)
        if left is None or right is None:
            return False
        return {
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
        }[op]
    low = text.lower()
    if op == "in":
        options = value if isinstance(value, list) else str(value or "").split(",")
        return low in {str(o).strip().lower() for o in options}
    wanted = str(value if value is not None else "").strip().lower()
    if op == "equals":
        return low == wanted
    if op == "not_equals":
        return low != wanted
    if op == "contains":
        return wanted in low
    if op == "not_contains":
        return wanted not in low
    raise TableError(f"Unknown condition {op!r}. Use one of: " + ", ".join(OPS))


def _conditions(table: Table, raw: Any) -> list[tuple[str, str, Any]]:
    out = []
    for item in list(raw or [])[:MAX_CONDITIONS]:
        if not isinstance(item, dict):
            continue
        op = str(item.get("op") or "equals").strip().lower()
        if op not in OPS:
            raise TableError(f"Unknown condition {op!r}. Use one of: " + ", ".join(OPS))
        out.append((_column(table, item.get("column")), op, item.get("value")))
    return out


def filter_rows(table: Table, conditions: list[tuple[str, str, Any]]) -> list[dict]:
    return [
        row
        for row in table.rows
        if all(_matches(row.get(c, ""), op, v) for c, op, v in conditions)
    ]


@dataclass(frozen=True)
class Rule:
    column: str
    op: str
    value: Any
    points: float
    why: str


def _rules(table: Table, raw: Any) -> list[Rule]:
    rules = []
    for item in list(raw or [])[:MAX_RULES]:
        if not isinstance(item, dict):
            continue
        op = str(item.get("op") or "contains").strip().lower()
        if op not in OPS:
            raise TableError(f"Unknown condition {op!r}. Use one of: " + ", ".join(OPS))
        try:
            points = float(item.get("points", 1))
        except (TypeError, ValueError):
            raise TableError("Each rule's points must be a number.") from None
        column = _column(table, item.get("column"))
        why = str(item.get("why") or "").strip()[:120] or (
            f"{column} {op} {item.get('value')}"
        )
        rules.append(Rule(column, op, item.get("value"), points, why))
    if not rules:
        raise TableError(
            "Give at least one rule: a column, a condition, points, and why it "
            "matters for this business."
        )
    return rules


def rank(table: Table, rules: list[Rule], rows: Optional[list[dict]] = None):
    """Every row with its score and the rules it met, best first. Ties keep
    the file's own order, so a ranking is reproducible."""
    scored = []
    for index, row in enumerate(rows if rows is not None else table.rows):
        met = [r for r in rules if _matches(row.get(r.column, ""), r.op, r.value)]
        score = round(sum(r.points for r in met), 2)
        scored.append((score, index, row, [r.why for r in met]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [{"score": score, "met": met, "row": row} for score, _, row, met in scored]


# --- Presentation ------------------------------------------------------------


def _clip(row: dict[str, str], columns: Optional[list[str]] = None) -> dict[str, str]:
    keys = columns or list(row.keys())
    return {k: str(row.get(k, ""))[:MAX_CELL_CHARS] for k in keys}


def describe(table: Table) -> dict[str, Any]:
    from collections import Counter

    columns = []
    for column in table.columns:
        values = [row.get(column, "") for row in table.rows]
        filled = [v for v in values if v]
        numbers = [n for n in (number(v) for v in filled) if n is not None]
        entry: dict[str, Any] = {
            "name": column,
            "filled": len(filled),
            "distinct": len(set(v.lower() for v in filled)),
            "top_values": [
                {"value": v[:MAX_CELL_CHARS], "rows": n}
                for v, n in Counter(filled).most_common(TOP_VALUES)
            ],
        }
        if filled and len(numbers) >= 0.8 * len(filled):
            entry["numeric"] = {"min": min(numbers), "max": max(numbers)}
        columns.append(entry)
    return {
        "file": table.name,
        **(
            {"sheet": table.sheet, "sheets": list(table.sheets)} if table.sheets else {}
        ),
        "rows": len(table.rows),
        "rows_not_read": table.truncated_rows,
        "columns": columns,
        "sample": [_clip(row) for row in table.rows[:3]],
    }


# --- Tool calls ---------------------------------------------------------------


def _file_arg() -> dict[str, Any]:
    return {
        "type": "string",
        "description": "The attached file's name exactly as shown, or its id.",
    }


def _sheet_arg() -> dict[str, Any]:
    return {
        "type": "string",
        "description": "For an Excel file: which sheet. Default the first.",
    }


def _conditions_arg() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "column": {"type": "string"},
                "op": {"type": "string", "enum": list(OPS)},
                "value": {},
            },
            "required": ["column", "op"],
        },
    }


def schemas() -> list[dict[str, Any]]:
    rules = {
        "type": "array",
        "description": (
            "How to score each row, from what the person's business sells and "
            "who it sells to. Each rule: a column, a condition, points "
            "(negative to push a row down) and why it matters, in a few words."
        ),
        "items": {
            "type": "object",
            "properties": {
                "column": {"type": "string"},
                "op": {"type": "string", "enum": list(OPS)},
                "value": {},
                "points": {"type": "number"},
                "why": {"type": "string"},
            },
            "required": ["column", "op", "points", "why"],
        },
    }
    return [
        {
            "name": DESCRIBE_TOOL_NAME,
            "description": (
                "Read an attached spreadsheet (Excel, CSV or TSV) as a whole "
                "table: its sheets, its columns, row "
                "count, how full each column is, common values and numeric "
                "ranges. Call this first for any spreadsheet the person "
                "attached; the text you were shown is only its first rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {"file": _file_arg(), "sheet": _sheet_arg()},
                "required": ["file"],
            },
        },
        {
            "name": QUERY_TOOL_NAME,
            "description": (
                "Rows of an attached table that match every condition, sorted, "
                f"a page of at most {MAX_ROWS_SHOWN} at a time, with the total."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": _file_arg(),
                    "sheet": _sheet_arg(),
                    "where": _conditions_arg(),
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "sort_by": {"type": "string"},
                    "descending": {"type": "boolean"},
                    "limit": {"type": "integer"},
                    "offset": {"type": "integer"},
                },
                "required": ["file"],
            },
        },
        {
            "name": RANK_TOOL_NAME,
            "description": (
                "Score EVERY row of an attached table by weighted rules and "
                "return the best first, each with the rules it met. Use it to "
                "answer which accounts to go for: write the rules from the "
                "person's own material (their deck, their ideal customer), "
                "show the rules with the result, and say what the top rows "
                "have in common."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": _file_arg(),
                    "sheet": _sheet_arg(),
                    "rules": rules,
                    "where": _conditions_arg(),
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "show": {"type": "integer"},
                },
                "required": ["file", "rules"],
            },
        },
        {
            "name": EXPORT_TOOL_NAME,
            "description": (
                "Put an attached table's rows -- ranked by rules, or filtered -- "
                "into an .xlsx on the thread, with a Score and Why column when "
                "ranked and a second sheet listing the rules."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": _file_arg(),
                    "sheet": _sheet_arg(),
                    "title": {"type": "string"},
                    "rules": rules,
                    "where": _conditions_arg(),
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "top": {"type": "integer"},
                    "people": {
                        "type": "array",
                        "description": (
                            "Who to reach at the ranked accounts, each from a "
                            "public page you read (never LinkedIn). Added as "
                            f"a '{PEOPLE_SHEET}' sheet."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "account": {"type": "string"},
                                "name": {"type": "string"},
                                "title": {"type": "string"},
                                "why": {"type": "string"},
                                "route": {
                                    "type": "string",
                                    "description": "How to reach them.",
                                },
                                "opener": {
                                    "type": "string",
                                    "description": (
                                        "Two or three sentences to open with, "
                                        "from the person's own material."
                                    ),
                                },
                                "source_url": {"type": "string"},
                            },
                            "required": ["account", "name", "source_url"],
                        },
                    },
                },
                "required": ["file", "title"],
            },
        },
    ]


def people_rows(raw: Any) -> tuple[list[list[str]], list[dict[str, str]]]:
    """(rows for the People sheet, the ones refused with why). A person
    without a public web page behind them is refused, and so is anyone
    whose source is a site we never read (LinkedIn among them)."""
    from api.services.workflow import web_tools

    rows: list[list[str]] = []
    rejected: list[dict[str, str]] = []
    for item in list(raw or []):
        person = item if isinstance(item, dict) else {}

        def field(key: str, limit: int = 300) -> str:
            return str(person.get(key) or "").strip()[:limit]

        name, account, source = field("name"), field("account"), field("source_url")
        if not name or not account:
            rejected.append({"name": name, "reason": "needs an account and a name"})
        elif not source.lower().startswith(("http://", "https://")):
            rejected.append({"name": name, "reason": "no public source page"})
        elif web_tools.is_blocked(source):
            host = web_tools.domain_of(source)
            site = next(
                d
                for d in web_tools.BLOCKED_DOMAINS
                if host == d or host.endswith("." + d)
            )
            rejected.append({"name": name, "reason": f"{site} is not a source we use"})
        elif len(rows) >= MAX_PEOPLE:
            rejected.append({"name": name, "reason": f"over the limit of {MAX_PEOPLE}"})
        else:
            rows.append(
                [
                    account,
                    name,
                    field("title"),
                    field("why"),
                    field("route"),
                    field("opener", 600),
                    source,
                ]
            )
    return rows, rejected


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _sorted(rows: list[dict], column: Optional[str], descending: bool) -> list[dict]:
    if not column:
        return rows

    def key(row: dict) -> tuple:
        cell = row.get(column, "")
        amount = number(cell)
        return (amount is None, amount if amount is not None else 0, cell.lower())

    ordered = sorted(rows, key=key)
    if descending:
        present = [r for r in ordered if number(r.get(column, "")) is not None]
        absent = [r for r in ordered if number(r.get(column, "")) is None]
        if present:
            return list(reversed(present)) + absent
        return list(reversed(ordered))
    return ordered


async def run(
    name: str,
    *,
    organization_id: int,
    arguments: dict[str, Any],
    workflow_id: int | None = None,
    workflow_run_id: int | None = None,
) -> dict[str, Any]:
    """The tool call. Never raises: the thread must keep answering."""
    try:
        table = await load(
            organization_id,
            str(arguments.get("file") or ""),
            sheet=arguments.get("sheet") or None,
        )
        if name == DESCRIBE_TOOL_NAME:
            return {"status": "success", **describe(table)}

        conditions = _conditions(table, arguments.get("where"))
        columns = [_column(table, c) for c in list(arguments.get("columns") or [])]
        rows = filter_rows(table, conditions) if conditions else table.rows

        if name == QUERY_TOOL_NAME:
            sort_by = arguments.get("sort_by")
            ordered = _sorted(
                rows,
                _column(table, sort_by) if sort_by else None,
                bool(arguments.get("descending")),
            )
            limit = _int(arguments.get("limit"), 20, 1, MAX_ROWS_SHOWN)
            offset = _int(arguments.get("offset"), 0, 0, len(ordered))
            page = ordered[offset : offset + limit]
            return {
                "status": "success",
                "matched": len(ordered),
                "of": len(table.rows),
                "offset": offset,
                "rows": [_clip(r, columns or None) for r in page],
            }

        rules = _rules(table, arguments.get("rules")) if arguments.get("rules") else []
        if name == RANK_TOOL_NAME:
            if not rules:
                raise TableError("Give the rules to rank by.")
            ranked = rank(table, rules, rows)
            show = _int(arguments.get("show"), 20, 1, MAX_ROWS_SHOWN)
            scores = [r["score"] for r in ranked]
            return {
                "status": "success",
                "ranked": len(ranked),
                "of": len(table.rows),
                "rules": [
                    {
                        "column": r.column,
                        "op": r.op,
                        "value": r.value,
                        "points": r.points,
                        "why": r.why,
                    }
                    for r in rules
                ],
                "scoring_rows": sum(1 for s in scores if s > 0),
                "top": [
                    {
                        "score": r["score"],
                        "met": r["met"],
                        "row": _clip(r["row"], columns or None),
                    }
                    for r in ranked[:show]
                ],
                "note": (
                    "Say which rules decided the top rows. Offer the full list "
                    "as a spreadsheet (export_table) rather than reading it out."
                ),
            }

        if name == EXPORT_TOOL_NAME:
            return await _export(
                organization_id,
                table,
                rows,
                rules,
                columns,
                title=str(arguments.get("title") or "").strip()[:100] or table.name,
                top=_int(arguments.get("top"), MAX_EXPORT_ROWS, 1, MAX_EXPORT_ROWS),
                people=arguments.get("people"),
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
            )
        return {"status": "unavailable", "reason": "no such table tool"}
    except TableError as exc:
        return {"status": "error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - the thread must keep answering
        logger.error("Table tool {} failed for org {}: {}", name, organization_id, exc)
        return {"status": "error", "error": "Could not read that table just now."}


async def _export(
    organization_id: int,
    table: Table,
    rows: list[dict],
    rules: list[Rule],
    columns: list[str],
    *,
    title: str,
    top: int,
    people: Any = None,
    workflow_id: int | None,
    workflow_run_id: int | None,
) -> dict[str, Any]:
    from api.services.documents import tools as document_tools

    keep = columns or table.columns
    if rules:
        ranked = rank(table, rules, rows)[:top]
        sheet_columns = ["Rank", "Score", "Why", *keep]
        body = [
            [i, r["score"], "; ".join(r["met"]), *[r["row"].get(c, "") for c in keep]]
            for i, r in enumerate(ranked, start=1)
        ]
        sheets = [
            {"name": "Ranked", "columns": sheet_columns, "rows": body},
            {
                "name": "Rules",
                "columns": ["Column", "Condition", "Value", "Points", "Why"],
                "rows": [
                    [
                        r.column,
                        r.op,
                        "" if r.value is None else str(r.value),
                        r.points,
                        r.why,
                    ]
                    for r in rules
                ],
            },
        ]
    else:
        body = [[row.get(c, "") for c in keep] for row in rows[:top]]
        sheets = [{"name": "Rows", "columns": keep, "rows": body}]
    named, rejected = people_rows(people) if people else ([], [])
    if named:
        sheets.insert(
            1, {"name": PEOPLE_SHEET, "columns": list(PEOPLE_COLUMNS), "rows": named}
        )
    result = await document_tools.build_spreadsheet(
        organization_id,
        {"title": title, "kind": "table", "sheets": sheets},
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
    )
    if result.get("status") == "success":
        result["rows"] = len(body)
        if people:
            result["people"] = len(named)
            result["people_rejected"] = rejected
    return result


__all__ = [
    "DESCRIBE_TOOL_NAME",
    "EXPORT_TOOL_NAME",
    "FLAG",
    "NAMES",
    "PEOPLE_COLUMNS",
    "QUERY_TOOL_NAME",
    "RANK_TOOL_NAME",
    "READS",
    "RULES",
    "Rule",
    "Table",
    "TableError",
    "describe",
    "enabled",
    "filter_rows",
    "load",
    "number",
    "parse",
    "parse_xlsx",
    "people_rows",
    "rank",
    "run",
    "schemas",
]
