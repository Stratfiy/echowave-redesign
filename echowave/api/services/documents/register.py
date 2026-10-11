"""The procurement register: numbering, and the rows agents move along.

**Numbering is gapless.** A PO number printed on a document a vendor holds
cannot be taken back, and a gap in the series is a question from the
auditor. So the next number comes from ``procurement_series`` under a row
lock -- the pattern ``billing.documents._next_number`` uses for our own
invoices -- in the same transaction that inserts the register row. Two
drafts at the same instant take consecutive numbers; a draft whose
transaction fails takes none. A draft whose *files* fail after the number
is committed keeps its row, marked cancelled with the reason, so the number
is accounted for rather than missing.

**Per workspace, per kind, per financial year, per prefix.** The Indian
financial year runs April to March, so the series restarts at 0001 each
April: ``PO/26-27/0001``. A workspace may use its own prefix; each prefix is
its own consecutive series.

**Every query takes the organization.** Nothing here reads or writes a row
by id alone (api/AGENTS.md, organization scoping).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import ProcurementDocumentModel, ProcurementSeriesModel
from api.services.billing.documents import financial_year
from api.services.documents import formats, money

IST = ZoneInfo("Asia/Kolkata")
SERIAL_WIDTH = 4
MAX_LIST = 50


ISSUED = "issued"


class RegisterError(ValueError):
    """Said to the model as it is."""


def today() -> date:
    return datetime.now(IST).date()


def clean_prefix(prefix: Any, kind: str) -> str:
    """A workspace's own prefix, or the kind's default. Letters, digits and
    hyphens, upper-cased, at most 16 -- it is printed on every document."""
    text = re.sub(r"[^A-Za-z0-9-]", "", str(prefix or "")).upper()[:16]
    return text or formats.FORMATS[kind].prefix


async def allocate(
    session: AsyncSession,
    *,
    organization_id: int,
    kind: str,
    fy: str,
    prefix: str,
) -> tuple[str, str]:
    """(number, series): the next number in this series, under a row lock.

    ON CONFLICT DO NOTHING rather than check-then-insert: the first two
    drafts of a financial year would otherwise both miss the row, and the
    loser's unique violation would poison its transaction.
    """
    await session.execute(
        pg_insert(ProcurementSeriesModel)
        .values(
            organization_id=organization_id,
            kind=kind,
            fy=fy,
            prefix=prefix,
            next_value=1,
        )
        .on_conflict_do_nothing(
            index_elements=["organization_id", "kind", "fy", "prefix"]
        )
    )
    row = await session.scalar(
        select(ProcurementSeriesModel)
        .where(
            ProcurementSeriesModel.organization_id == organization_id,
            ProcurementSeriesModel.kind == kind,
            ProcurementSeriesModel.fy == fy,
            ProcurementSeriesModel.prefix == prefix,
        )
        .with_for_update()
    )
    if row is None:  # pragma: no cover - the insert above guarantees a row
        raise RegisterError(f"Could not allocate a {kind} number for {fy}.")
    value = int(row.next_value or 1)
    row.next_value = value + 1
    await session.flush()
    series = f"{prefix}/{fy}"
    return f"{series}/{value:0{SERIAL_WIDTH}d}", series


async def create(
    session: AsyncSession,
    *,
    organization_id: int,
    kind: str,
    issue_date: date,
    prefix: Any = None,
    workflow_id: int | None = None,
    counterparty_name: str | None = None,
    counterparty_gstin: str | None = None,
    reference: str | None = None,
    amount_paise: int | None = None,
    currency: str = "INR",
    due_date: date | None = None,
    status: str = "draft",
    data: dict[str, Any] | None = None,
) -> ProcurementDocumentModel:
    """Number and insert one register row, in the caller's transaction."""
    if kind not in formats.FORMATS:
        raise RegisterError(f"kind must be one of {', '.join(formats.KINDS)}")
    number, series = await allocate(
        session,
        organization_id=organization_id,
        kind=kind,
        fy=financial_year(issue_date),
        prefix=clean_prefix(prefix, kind),
    )
    row = ProcurementDocumentModel(
        organization_id=organization_id,
        workflow_id=workflow_id,
        kind=kind,
        number=number,
        series=series,
        counterparty_name=(counterparty_name or None) and counterparty_name[:255],
        counterparty_gstin=(counterparty_gstin or None) and counterparty_gstin[:15],
        reference=(reference or None) and reference[:255],
        amount_paise=amount_paise,
        currency=currency,
        issue_date=issue_date,
        due_date=due_date,
        status=status,
        data=data or {},
    )
    session.add(row)
    await session.flush()
    return row


async def get(
    session: AsyncSession,
    *,
    organization_id: int,
    register_id: Any = None,
    number: Any = None,
) -> ProcurementDocumentModel | None:
    query = select(ProcurementDocumentModel).where(
        ProcurementDocumentModel.organization_id == organization_id
    )
    if register_id not in (None, ""):
        try:
            query = query.where(ProcurementDocumentModel.id == int(register_id))
        except (TypeError, ValueError):
            return None
    elif number:
        query = query.where(
            ProcurementDocumentModel.number == str(number).strip().upper()
        )
    else:
        return None
    return await session.scalar(query.limit(1))


#: A draft that is still the agent's to change. Past this, a person has issued,
#: followed up or closed it, and an agent calling draft_document again must not
#: rewrite a document a vendor may already hold.
REVISABLE = ("draft", "awaiting_approval")


def draft_key(
    *, workflow_run_id: int | None, template: str, recipient: str
) -> str | None:
    """Identity of a draft within one run: the same template to the same
    recipient. None outside a run (nothing to be idempotent within).

    The recipient is compared as a person reads it -- case and spacing do not
    make "Acme  Traders" a different vendor from "acme traders".
    """
    if workflow_run_id is None:
        return None
    who = " ".join(str(recipient or "").casefold().split())
    return f"{int(workflow_run_id)}|{' '.join(str(template or '').casefold().split())}|{who}"


async def lock_run_drafts(
    session: AsyncSession, *, organization_id: int, workflow_run_id: int
) -> None:
    """Serialize drafting within one run until the transaction ends.

    Two tool calls of one turn can run side by side; without this both look
    for an existing draft, both find none, and both take a number -- the
    duplicate this exists to prevent. A transaction-scoped advisory lock, so
    it is released by the commit that makes the first draft visible.
    """
    import hashlib

    from sqlalchemy import text

    digest = hashlib.sha256(
        f"draft:{organization_id}:{workflow_run_id}".encode()
    ).digest()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"),
        {"k": int.from_bytes(digest[:8], "big", signed=True)},
    )


async def find_draft(
    session: AsyncSession, *, organization_id: int, key: str, kind: str
) -> ProcurementDocumentModel | None:
    """The live (not cancelled) draft this run already made for ``key``."""
    return await session.scalar(
        select(ProcurementDocumentModel)
        .where(
            ProcurementDocumentModel.organization_id == organization_id,
            ProcurementDocumentModel.kind == kind,
            ProcurementDocumentModel.status != "cancelled",
            ProcurementDocumentModel.data["draft_key"].as_string() == key,
        )
        .order_by(ProcurementDocumentModel.id)
        .limit(1)
    )


def parse_date(value: Any) -> date | None:
    """A date from what a person wrote: 2026-10-15, 15-10-2026, 15/10/2026,
    15 Oct 2026, 15 October 2026. None when it is not a date ("30 days")."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%d-%b-%Y",
        "%b %d, %Y",
        "%B %d, %Y",
    ):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def display_date(value: date | None) -> str:
    return value.strftime("%d-%m-%Y") if value else ""


#: How many of a document's lines one register entry shows.
MAX_LINES = 50


def ordered_lines(row: ProcurementDocumentModel) -> list[dict[str, Any]]:
    """The lines as they were given to the draft -- description, quantity,
    unit, rate, discount, GST rate -- which is what a receipt and an invoice
    are checked against. Empty for a document drafted without items."""
    given = ((row.data or {}).get("input") or {}).get("items") or []
    keep = ("description", "hsn_sac", "qty", "unit", "rate", "discount", "gst_rate")
    return [
        {"line": index, **{k: item[k] for k in keep if item.get(k) not in (None, "")}}
        for index, item in enumerate(given[:MAX_LINES], start=1)
        if isinstance(item, dict)
    ]


def summary(row: ProcurementDocumentModel, *, lines: bool = False) -> dict[str, Any]:
    """What the model and the register screen are told about one row. With
    ``lines``, also what was ordered, line by line (one document asked for
    by number; a list of fifty would be mostly lines)."""
    amount = row.amount_paise
    due = row.due_date
    open_ = row.status not in ("delivered", "closed", "cancelled")
    return {
        "register_id": row.id,
        "kind": row.kind,
        "number": row.number,
        "status": row.status,
        "counterparty": row.counterparty_name,
        "counterparty_gstin": row.counterparty_gstin,
        "reference": row.reference,
        "amount": None if amount is None else money.format_inr(money.dec(amount) / 100),
        "amount_paise": amount,
        "issue_date": row.issue_date.isoformat() if row.issue_date else None,
        "due_date": due.isoformat() if due else None,
        "overdue": bool(open_ and due and due < today()),
        "files": [
            name
            for name, key in (
                ("docx", row.docx_key),
                ("pdf", row.pdf_key),
                ("xlsx", row.xlsx_key),
            )
            if key
        ],
        "notes": list((row.data or {}).get("notes") or [])[-5:],
        # Where the follow-up and the send go; kept with the draft.
        "counterparty_email": (row.data or {}).get("counterparty_email"),
        **({"lines": ordered_lines(row)} if lines else {}),
    }


async def update(
    session: AsyncSession,
    *,
    organization_id: int,
    register_id: Any = None,
    number: Any = None,
    status: Any = None,
    due_date: Any = None,
    note: Any = None,
    author: str = "agent",
    user_id: int | None = None,
) -> ProcurementDocumentModel:
    """``user_id`` is the person acting, when one is; an agent passes none,
    and the approval matrix treats nobody as unable to approve."""
    from api.services.workflow import approvals, audit_log

    row = await get(
        session, organization_id=organization_id, register_id=register_id, number=number
    )
    if row is None:
        raise RegisterError("No register entry by that id or number in this workspace.")
    was = row.status
    if status not in (None, ""):
        status = str(status).strip().lower()
        if status not in formats.STATUSES:
            raise RegisterError(f"status must be one of {', '.join(formats.STATUSES)}")
        if status == ISSUED and was != ISSUED:
            if row.kind == "tax_invoice" and money.has_tax_id_placeholder(row.data):
                raise RegisterError(
                    "This tax invoice still has a GSTIN or PAN marked "
                    "[to confirm]; give the real value before issuing it."
                )
            # The approval matrix (KAN-160): issuing is the act a rule is
            # about, by the document's kind and amount. Raises
            # ApprovalRequired naming who must; a no-op while off.
            await approvals.check(
                organization_id,
                subject=row.kind,
                amount_paise=row.amount_paise,
                user_id=user_id,
            )
        row.status = status
    if due_date not in (None, ""):
        parsed = parse_date(due_date)
        if parsed is None:
            raise RegisterError("due_date must be a date, e.g. 2026-10-15.")
        row.due_date = parsed
    text = str(note or "").strip()[:500]
    if text or status:
        data = dict(row.data or {})
        notes = list(data.get("notes") or [])
        notes.append(
            {
                "at": datetime.now(IST).isoformat(timespec="minutes"),
                "by": author,
                **({"status": row.status} if status else {}),
                **({"note": text} if text else {}),
            }
        )
        data["notes"] = notes[-50:]
        row.data = data
    await session.flush()
    if status and status != was:
        await audit_log.record(
            organization_id,
            action=audit_log.DOCUMENT_STATUS,
            subject_kind=row.kind,
            subject_id=row.id,
            subject=row.number,
            actor_user_id=user_id,
            actor=None if user_id else author,
            before={"status": was},
            after={"status": status, "amount_paise": row.amount_paise},
            note=text or None,
        )
    return row


async def list_rows(
    session: AsyncSession,
    *,
    organization_id: int,
    kind: Any = None,
    status: Any = None,
    due_before: Any = None,
    limit: int = MAX_LIST,
) -> list[ProcurementDocumentModel]:
    query = select(ProcurementDocumentModel).where(
        ProcurementDocumentModel.organization_id == organization_id
    )
    if kind:
        query = query.where(ProcurementDocumentModel.kind == str(kind).strip().lower())
    if status:
        statuses = [s.strip().lower() for s in str(status).split(",") if s.strip()]
        query = query.where(ProcurementDocumentModel.status.in_(statuses))
    if due_before not in (None, ""):
        parsed = parse_date(due_before)
        if parsed is None:
            raise RegisterError("due_before must be a date, e.g. 2026-10-15.")
        query = query.where(ProcurementDocumentModel.due_date < parsed)
    query = query.order_by(ProcurementDocumentModel.id.desc()).limit(
        max(1, min(int(limit or MAX_LIST), MAX_LIST))
    )
    return list((await session.scalars(query)).all())


def storage_key(organization_id: int, register_id: int, filename: str) -> str:
    """Org-scoped, row-scoped, so a key can be checked against its owner."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:120] or "file"
    return f"procurement/{organization_id}/{register_id}/{safe}"


def key_belongs_to(organization_id: int, register_id: int, key: str | None) -> bool:
    return bool(key) and str(key).startswith(
        f"procurement/{organization_id}/{register_id}/"
    )


PRESIGN_SECONDS = int(timedelta(days=7).total_seconds())


__all__ = [
    "IST",
    "MAX_LINES",
    "MAX_LIST",
    "PRESIGN_SECONDS",
    "RegisterError",
    "allocate",
    "clean_prefix",
    "create",
    "display_date",
    "get",
    "key_belongs_to",
    "list_rows",
    "ordered_lines",
    "parse_date",
    "storage_key",
    "summary",
    "today",
    "update",
]
