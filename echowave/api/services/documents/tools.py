"""The document engine as agent tools (PROCUREMENT_DOCS_2026_09_ENABLED).

Eight tools, offered to Decibyl on its thread and to every agent on a text
or channel run while the flag is on, and to nobody while it is off:

- ``list_template_fields`` -- what a template asks for.
- ``draft_document`` -- a PO, RFQ, work order, comparative statement or
  award letter, filled, numbered, as Word or Excel plus PDF, in the register.
- ``build_spreadsheet`` -- any table as .xlsx, or the cost-bid analysis.
- ``read_document`` -- an uploaded file's text and tables.
- ``save_email_attachment`` -- a Gmail attachment, stored as an upload.
- ``update_register`` / ``list_register`` -- PO follow-up.
- ``match_invoice`` -- an invoice against its PO and what was received
  (the three-way match), or a receipt against its PO.

**Ask once.** ``draft_document`` never produces a file with a blank in it.
Anything the template needs that was not given and cannot be worked out
comes back as ``status: missing`` with one question per field, and the
model asks them all in one message -- a person answers once, not six times.

**Drafted is not sent.** A draft lands in the register as
``awaiting_approval`` and on the thread as a deliverable with its files.
Sending it to the vendor is the email tools' job, on the send card a person
approves.

**Charged as a tool call**, like every other tool, keyed on the model's
call id so a retried turn pays once; a ``missing`` answer did no work and
is not charged. Every read and write takes the organization from the run,
never from the model's arguments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from loguru import logger

from api.enums import AgentEventActor, AgentEventKind
from api.services import features
from api.services.documents import (
    convert,
    formats,
    matching,
    money,
    reading,
    register,
    sources,
    spreadsheet,
    templates,
)

FEATURE = "procurement_docs"

LIST_FIELDS = "list_template_fields"
DRAFT = "draft_document"
SPREADSHEET = "build_spreadsheet"
READ = "read_document"
SAVE_ATTACHMENT = "save_email_attachment"
UPDATE = "update_register"
LIST = "list_register"
MATCH = "match_invoice"

NAMES = (LIST_FIELDS, DRAFT, SPREADSHEET, READ, SAVE_ATTACHMENT, UPDATE, LIST, MATCH)
#: Tools that leave the model its tools for the next round on Decibyl's
#: thread: nothing was handed over yet, the model is still working.
READS = frozenset({LIST_FIELDS, READ, LIST, SAVE_ATTACHMENT, MATCH})

MAX_ITEMS = 200

#: Said to Decibyl in its rules while the flag is on. Names every tool, so
#: the model is never handed one it has not been told about.
RULES = (
    "- Procurement documents: draft_document fills a purchase order, RFQ, "
    "work order, comparative statement or award letter from the standard "
    "Indian format or the person's own template (an uploaded Word or Excel "
    "file's uuid, a Google Doc link or a OneDrive link), numbers it, and puts the file and PDF "
    "on the thread and in the register as awaiting approval. "
    "list_template_fields says what a template asks for. Call draft_document "
    "with everything you know; when it answers status missing, ask the person "
    "every question it lists in ONE message, then call it again with all the "
    "answers. Never invent a GSTIN, PAN, rate, HSN code or address. Taxable "
    "value, GST, totals, amount in words, number and date are worked out for "
    "you. A draft is not sent: it goes to the vendor only through the email "
    "tools, on a card the person approves. read_document reads an uploaded "
    "quotation's text and tables; save_email_attachment stores a Gmail "
    "attachment so read_document can read it. build_spreadsheet makes an "
    "Excel file, and with kind cost_bid_analysis compares vendors' rates "
    "(landed cost, L1/L2/L3). update_register and list_register follow up "
    "POs: status, due dates, what has been delivered; list_register with a "
    "number gives that document's ordered lines. match_invoice checks a "
    "vendor invoice against its PO and what was received (the three-way "
    "match), or a receipt against its PO; pass the figures as read and use "
    "its verdict rather than your own arithmetic.\n"
)


def enabled() -> bool:
    return features.is_on(FEATURE)


# ---------------------------------------------------------------------------
# Schemas


def _items_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "description": (
            "Line items, in order. Each: description, hsn_sac, qty, unit, rate "
            "(per unit, before tax), discount (% , optional), gst_rate (%). For a "
            "comparative statement: vendor_name, vendor_gstin, quoted_total, "
            "delivery_period, payment_terms, rank, remarks."
        ),
        "items": {"type": "object"},
    }


def schemas() -> list[dict[str, Any]]:
    template_arg = {
        "type": "string",
        "description": (
            f"One of {', '.join(formats.STANDARD)} (the standard format), an "
            "uploaded Word or Excel file's document uuid, a Google Doc id or link, or a OneDrive/SharePoint link. "
            "Empty means the standard format for the kind."
        ),
    }
    return [
        {
            "name": LIST_FIELDS,
            "description": (
                "What a document template asks for: its fields and its line-item "
                "columns. Runs now."
            ),
            "parameters": {
                "type": "object",
                "properties": {"template": template_arg},
                "required": ["template"],
            },
        },
        {
            "name": DRAFT,
            "description": (
                "Draft a procurement document (Word or Excel, plus PDF) from a template, "
                "number it, and put it in the register awaiting approval. Does NOT "
                "send it. If anything is missing it drafts nothing and answers "
                "status missing with a question per field: ask them all at once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(formats.KINDS)},
                    "template": template_arg,
                    "values": {
                        "type": "object",
                        "description": (
                            "Field values by name. For a tax_invoice also: currency "
                            "(INR default; USD, EUR, GBP, AED, SGD, AUD, CAD), supply "
                            "(local, or export_lut for an export under a Letter of "
                            "Undertaking: zero IGST, the Rule 46 endorsement, place of "
                            "supply Outside India), exchange_rate to INR for a foreign "
                            "currency, lut_arn, country_of_destination. E.g. buyer_name, buyer_address, "
                            "buyer_gstin, vendor_name, vendor_address, vendor_gstin, "
                            "vendor_pan, reference, delivery_address, delivery_date, "
                            "payment_terms, validity, terms_and_conditions ('standard' "
                            "for the usual ones), signatory_name, signatory_designation."
                        ),
                    },
                    "items": _items_schema(),
                    "counterparty_email": {
                        "type": "string",
                        "description": "The vendor's email, kept for the send later.",
                    },
                    "prefix": {
                        "type": "string",
                        "description": "This workspace's own number prefix, if not the default (PO, RFQ, WO, CS, AL, INV). Numbers run PREFIX/FY/0001.",
                    },
                },
                "required": ["kind"],
            },
        },
        {
            "name": SPREADSHEET,
            "description": (
                "Make an Excel (.xlsx) file and hand it over on the thread. Either "
                "give sheets, or kind cost_bid_analysis with vendors and items to "
                "compare quotations: landed cost, per-vendor totals, L1/L2/L3 and "
                "the lowest bid per line marked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "kind": {"type": "string", "enum": ["table", "cost_bid_analysis"]},
                    "sheets": {
                        "type": "array",
                        "description": (
                            "Each: {name, columns: [heading or {name, type: money|"
                            "number|percent|text}], rows: [[...]], number_formats?: "
                            "{heading: excel format}, formulas?: [{cell: 'D12', "
                            "formula: 'SUM(D2:D11)'}], column_widths?: {A: 30}}."
                        ),
                        "items": {"type": "object"},
                    },
                    "vendors": {
                        "type": "array",
                        "description": (
                            "For cost_bid_analysis. Each: {name, gstin?, rates: [one "
                            "per item, null if not quoted], freight?: per unit, "
                            "gst_rate?: % (default 18)}."
                        ),
                        "items": {"type": "object"},
                    },
                    "items": {
                        "type": "array",
                        "description": "For cost_bid_analysis. Each: {description, qty, unit}.",
                        "items": {"type": "object"},
                    },
                    "register_id": {
                        "type": "integer",
                        "description": "Attach to this register entry (e.g. the comparative statement).",
                    },
                },
                "required": ["title"],
            },
        },
        {
            "name": READ,
            "description": (
                "Read an uploaded document (PDF, Word, Excel; scans by OCR): its "
                "text and its tables as rows. Runs now."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document": {
                        "type": "string",
                        "description": "The document's uuid or its file name.",
                    }
                },
                "required": ["document"],
            },
        },
        {
            "name": SAVE_ATTACHMENT,
            "description": (
                "Save an attachment from a Gmail message as an uploaded document "
                "so read_document can read it. Needs Gmail connected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"},
                    "attachment_id": {"type": "string"},
                    "filename": {
                        "type": "string",
                        "description": "The attachment's file name, when you do not have its id.",
                    },
                },
                "required": ["message_id"],
            },
        },
        {
            "name": UPDATE,
            "description": (
                "Move a register entry along: status (awaiting_approval, issued, "
                "acknowledged, part_delivered, delivered, closed, cancelled), due "
                "date, or a note (what arrived, a promise, a GRN number)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "register_id": {"type": "integer"},
                    "number": {"type": "string", "description": "e.g. PO/26-27/0001"},
                    "status": {"type": "string", "enum": list(formats.STATUSES)},
                    "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "note": {"type": "string"},
                },
            },
        },
        {
            "name": LIST,
            "description": (
                "The register of drafted and issued documents, newest first: "
                "number, vendor, amount, status, due date, overdue. Runs now."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(formats.KINDS)},
                    "status": {
                        "type": "string",
                        "description": "One status, or several separated by commas.",
                    },
                    "due_before": {"type": "string", "description": "YYYY-MM-DD"},
                    "number": {
                        "type": "string",
                        "description": (
                            "One document by its number, e.g. PO/26-27/0001, "
                            "with its ordered lines."
                        ),
                    },
                },
            },
        },
        {
            "name": MATCH,
            "description": (
                "Check a vendor invoice against its purchase order and what was "
                "received, line by line: invoiced <= received <= ordered, rate "
                "and GST rate against the PO, GSTINs, and the invoice's own "
                "totals. Without an invoice, checks what was received against "
                "the PO (complete, short or over). Runs now; changes nothing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "po": {
                        "type": "string",
                        "description": "The PO's number in the register, e.g. PO/26-27/0001.",
                    },
                    "register_id": {"type": "integer"},
                    "received": {
                        "type": "array",
                        "description": (
                            "Every goods-receipt line so far, as read from the GRN(s) "
                            "or the register notes. Each: {line? (the PO line number), "
                            "description, qty}. The same item on two GRNs is added up "
                            "for you."
                        ),
                        "items": {"type": "object"},
                    },
                    "invoice": {
                        "type": "object",
                        "description": (
                            "As read from the invoice: {number, date, vendor_gstin, "
                            "buyer_gstin, lines: [{line?, description, qty, rate, "
                            "discount?, gst_rate}], taxable_value?, gst_total?, total?}."
                        ),
                    },
                    "price_tolerance_pct": {"type": "number"},
                    "quantity_tolerance_pct": {"type": "number"},
                },
            },
        },
    ]


# ---------------------------------------------------------------------------
# Preparing values: validation, arithmetic, the missing list


@dataclass
class Prepared:
    values: dict[str, str] = field(default_factory=dict)
    items: list[dict[str, str]] = field(default_factory=list)
    missing: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    total: Decimal | None = None
    issue_date: Any = None
    raw: dict[str, Any] = field(default_factory=dict)
    currency: str = "INR"
    supply: str = formats.SUPPLY_LOCAL
    #: The total in rupees when the invoice is in another currency and an
    #: exchange rate was given; the register and GSTR-1 want this figure.
    inr_total: Decimal | None = None


def compliance_gaps(kind: str, inspected: dict[str, Any], *, supply: str) -> list[str]:
    """The Rule 46 particulars this template has no place for, by name --
    sorted, so the sentence is stable. Empty for any kind but a tax invoice:
    a purchase order has no such rule."""
    if kind != "tax_invoice":
        return []
    have = set(inspected.get("fields") or []) | {
        f"items.{c}" for c in (inspected.get("item_columns") or [])
    }
    wanted = dict(formats.RULE_46_PARTICULARS)
    if supply == formats.SUPPLY_EXPORT_LUT:
        wanted.update(formats.EXPORT_PARTICULARS)
    if inspected.get("has_formulas"):
        # A sheet with formulas adds up its own lines, tax and total.
        for name in formats.COMPUTED_PARTICULARS:
            wanted.pop(name, None)
    return sorted(
        name for name, spellings in wanted.items() if not have & set(spellings)
    )


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _norm(mapping: Any) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    out = {}
    for key, value in mapping.items():
        name = str(key).strip().lower().replace(" ", "_").replace("-", "_")
        out[name] = value.strip() if isinstance(value, str) else value
    return out


def prepare(
    kind: str,
    inspected: dict[str, list[str]],
    values: Any,
    items: Any,
) -> Prepared:
    """Everything a template needs as printable strings, or the list of
    what is missing and what is wrong. Pure: no database, no files."""
    given = _norm(values)
    lines = [_norm(i) for i in list(items or [])[:MAX_ITEMS] if isinstance(i, dict)]
    fields = inspected.get("fields") or []
    columns = inspected.get("item_columns") or []
    out = Prepared(raw={"values": dict(given), "items": [dict(i) for i in lines]})

    # Identifiers: every *_gstin and *_pan that was given must be right.
    def check_ids(mapping: dict[str, Any], where: str) -> None:
        for name, value in list(mapping.items()):
            if _blank(value):
                continue
            try:
                if name.endswith("gstin"):
                    mapping[name] = (
                        "Unregistered"
                        if money.is_unregistered(value)
                        else money.validate_gstin(value)
                    )
                elif name.endswith("_pan") or name == "pan":
                    if not money.is_unregistered(value):
                        mapping[name] = money.validate_pan(value)
            except money.MoneyError as exc:
                out.errors.append(f"{where}{name}: {exc}")

    check_ids(given, "")
    for index, line in enumerate(lines, start=1):
        check_ids(line, f"item {index} ")

    # The invoice's currency and mode. Both default to what every document
    # was before they existed: rupees, within India.
    try:
        out.currency = money.currency_code(given.get("currency"))
    except money.MoneyError as exc:
        out.errors.append(str(exc))
    given["currency"] = out.currency
    supply = str(given.get("supply") or formats.SUPPLY_LOCAL).strip().lower()
    if supply not in formats.SUPPLY_MODES:
        out.errors.append(
            f"supply: {supply!r} is not a mode; use {' or '.join(formats.SUPPLY_MODES)}."
        )
        supply = formats.SUPPLY_LOCAL
    out.supply = supply
    given["supply"] = supply
    exporting = supply == formats.SUPPLY_EXPORT_LUT
    if exporting:
        given["export_declaration"] = formats.EXPORT_DECLARATION
        given["place_of_supply"] = "Outside India"
        if any(
            not _blank(line.get("gst_rate")) and money.dec(line["gst_rate"])
            for line in lines
            if not _blank(line.get("gst_rate"))
        ):
            out.notes.append(
                "An export under LUT is without payment of integrated tax, so the "
                "GST rate on the lines was set to 0."
            )
        for line in lines:
            line["gst_rate"] = 0
    exchange_rate: Decimal | None = None
    if out.currency != "INR" and not _blank(given.get("exchange_rate")):
        try:
            exchange_rate = money.dec(given["exchange_rate"])
            if exchange_rate <= 0:
                raise money.MoneyError("must be more than zero")
            given["exchange_rate"] = str(given["exchange_rate"]).strip()
        except money.MoneyError as exc:
            out.errors.append(f"exchange_rate: {exc}")
            exchange_rate = None

    if str(given.get("terms_and_conditions") or "").strip().lower() in (
        "standard",
        "usual",
        "default",
    ):
        given["terms_and_conditions"] = formats.STANDARD_TERMS
    if str(given.get("reference") or "").strip().lower() in (
        "none",
        "na",
        "n/a",
        "-",
        "nil",
    ):
        given["reference"] = "—"

    # Negative figures are refused whether or not the template prints them.
    for index, line in enumerate(lines, start=1):
        for name in ("qty", "rate", "discount", "gst_rate", "quoted_total"):
            if _blank(line.get(name)):
                continue
            try:
                if money.dec(line[name]) < 0:
                    out.errors.append(f"item {index} {name}: cannot be negative.")
            except money.MoneyError:
                out.errors.append(
                    f"item {index} {name}: {line[name]!r} is not a number."
                )

    priced = bool(set(columns) & {"rate", "taxable_value", "gst_amount", "amount"})
    totals: money.Totals | None = None
    if priced and lines and not out.errors:
        if all(
            not _blank(line.get("qty")) and not _blank(line.get("rate"))
            for line in lines
        ):
            buyer = given.get("buyer_gstin") or given.get("recipient_gstin")
            vendor = given.get("vendor_gstin") or given.get("supplier_gstin")
            if exporting:
                # A foreign recipient has no GSTIN and no state; the supply is
                # inter-state by definition and the rate is already zero.
                buyer, vendor = None, None
            elif (money.is_unregistered(vendor) or _blank(vendor)) and _blank(
                given.get("vendor_state_code")
            ):
                out.notes.append(
                    "The vendor has no GSTIN, so tax was split as within one state "
                    "(CGST + SGST); give vendor_state_code if they are in another state."
                )
            try:
                totals = money.compute(
                    [
                        {
                            **line,
                            "discount": line.get("discount") or 0,
                            "gst_rate": line.get("gst_rate") or 0,
                        }
                        for line in lines
                    ],
                    buyer_gstin=buyer,
                    vendor_gstin=vendor,
                    buyer_state_code="96"
                    if exporting
                    else given.get("buyer_state_code"),
                    vendor_state_code="97"
                    if exporting
                    else given.get("vendor_state_code"),
                )
            except money.MoneyError as exc:
                out.errors.append(str(exc))

    # Line items as printed.
    for index, line in enumerate(lines, start=1):
        printed: dict[str, str] = {}
        for name, value in line.items():
            if _blank(value):
                printed[name] = ""
            elif name in formats.MONEY_ITEM_COLUMNS:
                try:
                    printed[name] = money.format_amount(value, out.currency)
                except money.MoneyError:
                    printed[name] = str(value)
            elif name in ("qty", "discount", "gst_rate"):
                try:
                    printed[name] = money.plain_number(money.dec(value))
                except money.MoneyError:
                    printed[name] = str(value)
            else:
                printed[name] = str(value)
        printed["sl"] = str(line.get("sl") or index)
        printed.setdefault("discount", "0")
        if totals is not None:
            computed = totals.lines[index - 1]
            printed["taxable_value"] = money.format_amount(
                computed.taxable_value, out.currency
            )
            printed["gst_amount"] = money.format_amount(
                computed.gst_amount, out.currency
            )
            printed["amount"] = money.format_amount(computed.amount, out.currency)
            printed["cgst"] = money.format_amount(computed.cgst, out.currency)
            printed["sgst"] = money.format_amount(computed.sgst, out.currency)
            printed["igst"] = money.format_amount(computed.igst, out.currency)
        out.items.append(printed)

    # Fields: given, derived, or missing.
    printed_values: dict[str, str] = {
        k: ("" if _blank(v) else str(v))
        for k, v in given.items()
        if not isinstance(v, (dict, list))
    }
    date_value = register.parse_date(given.get("document_date")) or register.today()
    out.issue_date = date_value
    printed_values["document_date"] = register.display_date(date_value)
    if totals is not None:
        out.total = totals.total
        for name in ("subtotal", "cgst", "sgst", "igst", "gst_total", "total"):
            printed_values[name] = money.format_amount(
                getattr(totals, name), out.currency
            )
    elif not _blank(given.get("total")):
        try:
            out.total = money.to_paise(given["total"])
            printed_values["total"] = money.format_amount(out.total, out.currency)
        except money.MoneyError as exc:
            out.errors.append(f"total: {exc}")
    for name in formats.MONEY_FIELDS - {"total"}:
        if totals is None and not _blank(given.get(name)):
            try:
                printed_values[name] = money.format_amount(given[name], out.currency)
            except money.MoneyError as exc:
                out.errors.append(f"{name}: {exc}")
    if out.total is not None:
        printed_values["amount_in_words"] = money.amount_in_words(
            out.total, out.currency
        )
        if out.currency == "INR":
            out.inr_total = out.total
        elif exchange_rate is not None:
            out.inr_total = money.to_paise(out.total * exchange_rate)
            printed_values["inr_equivalent"] = money.format_inr(out.inr_total)
    if (
        out.currency != "INR"
        and exchange_rate is None
        and _blank(given.get("exchange_rate"))
        and "exchange_rate" not in fields
    ):
        out.missing.append(
            {
                "field": "exchange_rate",
                "question": formats.question_for("exchange_rate"),
            }
        )

    derived_money = {"subtotal", "cgst", "sgst", "igst", "gst_total"}
    for name in fields:
        if name in ("document_number", "document_date", "inr_equivalent"):
            continue
        if name in ("export_declaration", "place_of_supply") and exporting:
            continue
        if name in derived_money and priced:
            continue  # comes from the items; their gaps are asked below
        if name in ("total", "amount_in_words") and (priced or out.total is not None):
            continue
        if name == "amount_in_words" and "total" in fields:
            continue  # asked as the total
        if _blank(printed_values.get(name)):
            out.missing.append({"field": name, "question": formats.question_for(name)})

    if columns and not lines:
        wanted = [c for c in columns if c not in formats.DERIVED_ITEM_COLUMNS]
        out.missing.append(
            {
                "field": "items",
                "question": "What are the line items? For each: "
                + ", ".join(c.replace("_", " ") for c in wanted)
                + ".",
            }
        )
    for index, line in enumerate(lines, start=1):
        for column in columns:
            if (
                column in formats.DERIVED_ITEM_COLUMNS
                or column in formats.OPTIONAL_ITEM_COLUMNS
            ):
                continue
            if _blank(line.get(column)):
                out.missing.append(
                    {
                        "field": f"items[{index}].{column}",
                        "question": formats.item_question(
                            index, column, str(line.get("description") or "") or None
                        ),
                    }
                )
    out.values = printed_values
    return out


# ---------------------------------------------------------------------------
# Storage, the thread, the charge


def _storage():
    from api.services import storage

    return storage.storage_fs


async def _put(key: str, data: bytes) -> None:
    if not await _storage().acreate_file_from_bytes(key, data):
        raise RuntimeError(f"storage refused {key}")


async def _link(key: str) -> str | None:
    try:
        return await _storage().aget_signed_url(
            key, expiration=register.PRESIGN_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 - the file is there; the link can be minted later
        logger.warning("Could not sign {}: {}", key, exc)
        return None


def attachment(row_id: int, file_kind: str, filename: str, size: int) -> dict[str, Any]:
    """The shape the thread and /deliverables read: ``document_uuid`` and
    ``filename``, plus what the download needs."""
    return {
        "document_uuid": f"procurement-{row_id}-{file_kind}",
        "filename": filename,
        "size_bytes": size,
        "register_id": row_id,
        "file": file_kind,
    }


async def _hand_over(
    *,
    organization_id: int,
    summary: str,
    attachments: list[dict[str, Any]],
    entry: dict[str, Any],
    workflow_id: int | None,
    workflow_run_id: int | None,
) -> None:
    from api.services.workflow import agent_timeline

    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.DELIVERABLE.value,
        actor=AgentEventActor.AGENT.value,
        summary=summary,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        payload={
            "body": summary,
            "attachments": attachments,
            "procurement": entry,
        },
        in_channel=workflow_id is not None,
    )


async def _charge(
    organization_id: int, name: str, ref_id: str, workflow_id: int | None
) -> None:
    from api.services.billing import events as billing_events

    await billing_events.charge_in_own_session(
        organization_id=organization_id,
        event=billing_events.tool_call_event(None, name),
        ref_id=ref_id,
        note=f"{name} (documents)",
        workflow_id=workflow_id,
    )


def _file_stem(number: str) -> str:
    return number.replace("/", "-")


# ---------------------------------------------------------------------------
# The tools


async def list_template_fields(
    organization_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    template = str(arguments.get("template") or "").strip()
    kind = template.lower() if template.lower() in formats.STANDARD else "other"
    found = await sources.resolve_template(organization_id, template, kind=kind)
    inspected = templates.inspect(found.data)
    derived = sorted(set(inspected["fields"]) & formats.DERIVED_FIELDS)
    out = {
        "status": "success",
        "template": found.label,
        "fields": inspected["fields"],
        "item_columns": inspected["item_columns"],
        "worked_out_for_you": derived
        + [
            f"items.{c}"
            for c in inspected["item_columns"]
            if c in formats.DERIVED_ITEM_COLUMNS
        ],
    }
    if found.note:
        out["note"] = found.note
    gaps = compliance_gaps(
        kind, inspected, supply=str(arguments.get("supply") or formats.SUPPLY_LOCAL)
    )
    if gaps:
        out["compliance_gaps"] = gaps
    return out


async def draft_document(
    organization_id: int,
    arguments: dict[str, Any],
    *,
    workflow_id: int | None = None,
    workflow_run_id: int | None = None,
) -> dict[str, Any]:
    from api.db import db_client

    kind = str(arguments.get("kind") or "").strip().lower()
    if kind not in formats.FORMATS:
        return {
            "status": "error",
            "error": f"kind must be one of {', '.join(formats.KINDS)}",
        }
    found = await sources.resolve_template(
        organization_id, arguments.get("template"), kind=kind
    )
    inspected = templates.inspect(found.data)
    prepared = prepare(kind, inspected, arguments.get("values"), arguments.get("items"))
    if prepared.errors:
        return {
            "status": "invalid",
            "errors": prepared.errors,
            "note": "Nothing was drafted. Tell the person what is wrong and ask for the right values.",
        }
    if prepared.missing:
        return {
            "status": "missing",
            "missing": prepared.missing,
            "note": (
                "Nothing was drafted and no number was used. Ask the person all of "
                "these in ONE message, then call draft_document again with every answer."
            ),
        }

    label = formats.FORMATS[kind].label
    given = prepared.raw["values"]
    due_field = formats.FORMATS[kind].due_field
    counterparty_gstin = prepared.values.get("vendor_gstin") or prepared.values.get(
        "recipient_gstin"
    )
    counterparty_name = prepared.values.get("vendor_name") or prepared.values.get(
        "recipient_name"
    )
    gaps = compliance_gaps(kind, inspected, supply=prepared.supply)
    data = {
        "template": found.label,
        "values": prepared.values,
        "items": prepared.items,
        "input": prepared.raw,
        "currency": prepared.currency,
        "supply": prepared.supply,
        "inr_total_paise": None
        if prepared.inr_total is None
        else money.paise_int(prepared.inr_total),
        "compliance_gaps": gaps,
        "counterparty_email": str(arguments.get("counterparty_email") or "").strip()
        or None,
    }

    # The number and the row, in one short transaction: the series row is
    # locked only while the number is taken, not while files are made.
    async with db_client.async_session() as session:
        row = await register.create(
            session,
            organization_id=organization_id,
            kind=kind,
            issue_date=prepared.issue_date,
            prefix=arguments.get("prefix"),
            workflow_id=workflow_id,
            counterparty_name=counterparty_name or None,
            counterparty_gstin=(
                counterparty_gstin
                if counterparty_gstin and counterparty_gstin != "Unregistered"
                else None
            ),
            reference=(prepared.values.get("reference") or None),
            amount_paise=None
            if prepared.total is None
            else money.paise_int(prepared.total),
            currency=prepared.currency,
            due_date=register.parse_date(given.get(due_field)) if due_field else None,
            status="draft",
            data=data,
        )
        await session.commit()
        row_id, number = row.id, row.number

    stem = _file_stem(number)
    try:
        values = {**prepared.values, "document_number": number}
        ext = templates.extension_of(found.data)
        docx_bytes = templates.fill(found.data, values, prepared.items)
        pdf_bytes = await convert.to_pdf(docx_bytes, filename=f"{stem}.{ext}")
        docx_key = register.storage_key(organization_id, row_id, f"{stem}.{ext}")
        pdf_key = register.storage_key(organization_id, row_id, f"{stem}.pdf")
        await _put(docx_key, docx_bytes)
        await _put(pdf_key, pdf_bytes)
    except Exception as exc:  # noqa: BLE001 - the number is accounted for below
        logger.error(
            "Could not produce files for {} (org {}): {}", number, organization_id, exc
        )
        async with db_client.async_session() as session:
            failed = await register.get(
                session, organization_id=organization_id, register_id=row_id
            )
            if failed is not None:
                failed.status = "cancelled"
                failed.data = {
                    **(failed.data or {}),
                    "failure": "The files could not be produced.",
                }
                await session.commit()
        return {
            "status": "error",
            "error": f"{number} was numbered but its files could not be produced; it is marked cancelled in the register. Try again.",
        }

    async with db_client.async_session() as session:
        row = await register.get(
            session, organization_id=organization_id, register_id=row_id
        )
        # A filled workbook is the register's spreadsheet file, not its Word file.
        if ext == "xlsx":
            row.xlsx_key = docx_key
        else:
            row.docx_key = docx_key
        row.pdf_key, row.status = pdf_key, "awaiting_approval"
        await session.commit()

    files = [
        {"filename": f"{stem}.pdf", "url": await _link(pdf_key)},
        {"filename": f"{stem}.{ext}", "url": await _link(docx_key)},
    ]
    vendor = counterparty_name
    amount = (
        f", {'₹' if prepared.currency == 'INR' else prepared.currency + ' '}"
        f"{money.format_amount(prepared.total, prepared.currency)}"
        if prepared.total is not None
        else ""
    )
    summary = (
        f"Drafted {label.lower()} {number}"
        + (f" for {vendor}" if vendor else "")
        + amount
    )
    await _hand_over(
        organization_id=organization_id,
        summary=summary + " — awaiting approval",
        attachments=[
            attachment(row_id, "pdf", f"{stem}.pdf", len(pdf_bytes)),
            attachment(row_id, ext, f"{stem}.{ext}", len(docx_bytes)),
        ],
        entry={
            "register_id": row_id,
            "number": number,
            "kind": kind,
            "status": "awaiting_approval",
        },
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
    )
    out: dict[str, Any] = {
        "status": "drafted",
        "number": number,
        "register_id": row_id,
        "files": files,
        "total": None if prepared.total is None else money.format_inr(prepared.total),
        "note": (
            "The files are on the thread and the entry awaits approval. It has "
            "NOT been sent; send it with the email tools once the person approves."
        ),
    }
    if gaps:
        prepared.notes.append(
            "The template has no place for these particulars a tax invoice must "
            "carry: " + ", ".join(g.replace("_", " ") for g in gaps) + ". Tell the "
            "person, so they can add the fields to their format."
        )
    if found.note or prepared.notes:
        out["note"] += " " + " ".join(n for n in [found.note, *prepared.notes] if n)
    return out


async def build_spreadsheet(
    organization_id: int,
    arguments: dict[str, Any],
    *,
    workflow_id: int | None = None,
    workflow_run_id: int | None = None,
) -> dict[str, Any]:
    from api.db import db_client

    title = str(arguments.get("title") or "").strip()[:120] or "Spreadsheet"
    kind = str(arguments.get("kind") or "table").strip().lower()
    extra: dict[str, Any] = {}
    if kind == "cost_bid_analysis":
        analysis = spreadsheet.cost_bid_analysis(
            list(arguments.get("vendors") or []), list(arguments.get("items") or [])
        )
        sheets = analysis.sheets
        extra["ranking"] = [
            {**r, "total": money.format_inr(r["total"])} for r in analysis.ranking
        ]
        extra["lowest_per_item"] = analysis.lowest
    else:
        sheets = list(arguments.get("sheets") or [])
    data = spreadsheet.build_workbook(title, sheets)

    register_id = arguments.get("register_id")
    async with db_client.async_session() as session:
        if register_id not in (None, ""):
            row = await register.get(
                session, organization_id=organization_id, register_id=register_id
            )
            if row is None:
                return {
                    "status": "error",
                    "error": "No register entry by that id in this workspace.",
                }
        else:
            row = await register.create(
                session,
                organization_id=organization_id,
                kind="comparative_statement"
                if kind == "cost_bid_analysis"
                else "other",
                issue_date=register.today(),
                workflow_id=workflow_id,
                reference=title,
                status="draft",
                data={"title": title, "spreadsheet": kind},
            )
        await session.commit()
        row_id, number = row.id, row.number

    filename = f"{_file_stem(number)} {title}"[:100].strip() + ".xlsx"
    key = register.storage_key(organization_id, row_id, filename)
    await _put(key, data)
    async with db_client.async_session() as session:
        row = await register.get(
            session, organization_id=organization_id, register_id=row_id
        )
        row.xlsx_key = key
        entry = {
            "register_id": row_id,
            "number": number,
            "kind": row.kind,
            "status": row.status,
        }
        await session.commit()
    await _hand_over(
        organization_id=organization_id,
        summary=f"Built {title} ({number})",
        attachments=[attachment(row_id, "xlsx", filename, len(data))],
        entry=entry,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
    )
    return {
        "status": "success",
        "register_id": row_id,
        "number": number,
        "files": [{"filename": filename, "url": await _link(key)}],
        **extra,
    }


async def save_email_attachment(
    organization_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    message_id = str(arguments.get("message_id") or "").strip()
    if not message_id:
        return {"status": "error", "error": "Give the Gmail message_id."}
    data, filename, mime = await sources.gmail_attachment(
        organization_id,
        message_id=message_id,
        attachment_id=str(arguments.get("attachment_id") or "").strip() or None,
        filename=str(arguments.get("filename") or "").strip() or None,
    )
    stored = await sources.store_as_upload(
        organization_id,
        data=data,
        filename=filename,
        mime=mime,
        source={"source": "gmail", "message_id": message_id},
    )
    return {
        "status": "success",
        **stored,
        "note": "Saved. Read it with read_document using this document_uuid.",
    }


async def update_register(
    organization_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    from api.db import db_client

    async with db_client.async_session() as session:
        row = await register.update(
            session,
            organization_id=organization_id,
            register_id=arguments.get("register_id"),
            number=arguments.get("number"),
            status=arguments.get("status"),
            due_date=arguments.get("due_date"),
            note=arguments.get("note"),
        )
        await session.commit()
        return {"status": "success", "entry": register.summary(row)}


async def list_register(
    organization_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    from api.db import db_client

    number = str(arguments.get("number") or "").strip()
    if number:
        async with db_client.async_session() as session:
            row = await register.get(
                session, organization_id=organization_id, number=number
            )
            if row is None:
                return {
                    "status": "success",
                    "entries": [],
                    "count": 0,
                    "note": f"No document numbered {number} in the register.",
                }
            return {
                "status": "success",
                "entries": [register.summary(row, lines=True)],
                "count": 1,
            }

    async with db_client.async_session() as session:
        rows = await register.list_rows(
            session,
            organization_id=organization_id,
            kind=arguments.get("kind"),
            status=arguments.get("status"),
            due_before=arguments.get("due_before"),
        )
        entries = [register.summary(r) for r in rows]
    out: dict[str, Any] = {
        "status": "success",
        "entries": entries,
        "count": len(entries),
    }
    if not entries:
        out["note"] = "Nothing in the register matches."
    elif len(entries) >= register.MAX_LIST:
        out["note"] = (
            f"Showing the newest {register.MAX_LIST}; narrow by kind, status or date."
        )
    return out


async def match_invoice(
    organization_id: int, arguments: dict[str, Any]
) -> dict[str, Any]:
    """The three-way match against an order in this workspace's register."""
    from api.db import db_client

    async with db_client.async_session() as session:
        row = await register.get(
            session,
            organization_id=organization_id,
            register_id=arguments.get("register_id"),
            number=arguments.get("po") or arguments.get("number"),
        )
        if row is None:
            return {
                "status": "error",
                "error": "No purchase order by that number in this workspace's register.",
            }
        entry = register.summary(row, lines=True)
        given = ((row.data or {}).get("input") or {}).get("values") or {}
        order_items = list(((row.data or {}).get("input") or {}).get("items") or [])
    invoice = arguments.get("invoice")
    received = arguments.get("received")
    result = matching.match(
        order_items,
        received=list(received) if isinstance(received, list) else None,
        invoice=invoice if isinstance(invoice, dict) else None,
        order_vendor_gstin=row.counterparty_gstin,
        order_buyer_gstin=given.get("buyer_gstin"),
        price_tolerance_pct=arguments.get("price_tolerance_pct") or 0,
        quantity_tolerance_pct=arguments.get("quantity_tolerance_pct") or 0,
    )
    return {
        **result,
        "order": {
            k: entry[k] for k in ("register_id", "number", "status", "counterparty")
        },
        "match": result["status"],
        "status": "success",
    }


async def run(
    name: str,
    *,
    organization_id: int,
    arguments: dict[str, Any] | None,
    ref_id: str,
    workflow_id: int | None = None,
    workflow_run_id: int | None = None,
) -> dict[str, Any]:
    """One tool call. Never raises: every failure is a sentence the model
    can say. Charged as a tool call when it did work."""
    if not enabled():
        return {
            "status": "unavailable",
            "reason": "document tools are not switched on here",
        }
    if not organization_id:
        return {"status": "error", "error": "No workspace for this call."}
    arguments = dict(arguments or {})
    try:
        if name == LIST_FIELDS:
            result = await list_template_fields(organization_id, arguments)
        elif name == DRAFT:
            result = await draft_document(
                organization_id,
                arguments,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
            )
        elif name == SPREADSHEET:
            result = await build_spreadsheet(
                organization_id,
                arguments,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
            )
        elif name == READ:
            result = {
                "status": "success",
                **await reading.read(
                    organization_id, str(arguments.get("document") or "")
                ),
            }
        elif name == SAVE_ATTACHMENT:
            result = await save_email_attachment(organization_id, arguments)
        elif name == UPDATE:
            result = await update_register(organization_id, arguments)
        elif name == LIST:
            result = await list_register(organization_id, arguments)
        elif name == MATCH:
            result = await match_invoice(organization_id, arguments)
        else:
            return {"status": "unavailable", "reason": "no such tool"}
    except sources.SourceError as exc:
        if exc.needs_app:
            return await _offer(organization_id, exc.needs_app, str(exc))
        return {"status": "error", "error": str(exc)}
    except (
        templates.TemplateError,
        register.RegisterError,
        spreadsheet.SpreadsheetError,
        money.MoneyError,
        matching.MatchError,
    ) as exc:
        return {"status": "error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - the turn must finish
        logger.exception("{} failed for org {}: {}", name, organization_id, exc)
        return {"status": "error", "error": f"{name} could not finish just now."}
    if result.get("status") in ("success", "drafted"):
        await _charge(organization_id, name, ref_id, workflow_id)
    return result


async def _offer(organization_id: int, app: str, reason: str) -> dict[str, Any]:
    """The connect card, never a request for keys (connector_offer)."""
    from api.services.workflow import connector_offer

    why = {
        "gmail": "Read the quotations vendors email you",
        "googledrive": "Use your Google Docs as document templates",
        "one_drive": "Use your Word or Excel files on OneDrive as document templates",
    }.get(app, "")
    offered = await connector_offer.offer(
        organization_id=organization_id, arguments={"app": app, "why": why}
    )
    return {
        "status": "needs_connection",
        "app": app,
        "reason": reason,
        "offer": offered,
    }


__all__ = [
    "DRAFT",
    "FEATURE",
    "LIST",
    "LIST_FIELDS",
    "MATCH",
    "NAMES",
    "READ",
    "READS",
    "RULES",
    "SAVE_ATTACHMENT",
    "SPREADSHEET",
    "UPDATE",
    "attachment",
    "enabled",
    "prepare",
    "run",
    "schemas",
]
