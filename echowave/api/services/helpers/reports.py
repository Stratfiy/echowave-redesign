"""Saved research reports (handoff 6, Research; screen 04 "A saved report
matches its export").

A report keeps its structure -- findings marked ``source`` or
``inference`` with the sources each rests on, dates on dated facts,
conflicting evidence, the sources that could not be read -- and one
rendering of it, ``body``. The screen shows that rendering's parts and the
export *is* that rendering, byte for byte, with ``content_hash`` on both,
so what a person downloads is what they read.

Rules a report is held to at save, not hoped for:

* a ``source`` finding cites at least one source the report lists;
* every source is an http(s) link;
* nothing is dropped: conflicts and unreadable sources are kept as given;
* a trading summary is information only (``guard.scrub``).

A report belongs to the person who saved it, in the workspace it was saved
in, private until they share it with the workspace.
"""

from __future__ import annotations

import hashlib
import re
import uuid as uuid_lib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select

from api.db import db_client
from api.db.agents_models import SavedReportModel
from api.services import features
from api.services.helpers import guard, sharing

FLAG = "research_reports"
RESEARCH = "research"
TRADING_SUMMARY = "trading_summary"
KINDS = (RESEARCH, TRADING_SUMMARY)

MAX_FINDINGS = 60
MAX_SOURCES = 60
MAX_TEXT = 4000

Invalid = sharing.Invalid


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return re.sub(r"[ \t]+", " ", str(value or "")).strip()[:limit]


def _url(value: Any) -> str:
    url = str(value or "").strip()[:2000]
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise Invalid(f"A source must be a web link: {url[:80]!r}.")
    return url


@dataclass
class Draft:
    title: str
    question: str | None
    summary: str | None
    findings: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    inaccessible: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    kind: str = RESEARCH


def clean(raw: dict[str, Any], *, kind: str = RESEARCH) -> Draft:
    """Validate what a tool call or a request handed over. Raises Invalid."""
    if kind not in KINDS:
        raise Invalid("Unknown kind of report.")
    title = _text(raw.get("title"), 200)
    if not title:
        raise Invalid("A report needs a title.")
    sources: list[dict[str, Any]] = []
    for i, item in enumerate((raw.get("sources") or [])[:MAX_SOURCES], start=1):
        if not isinstance(item, dict):
            raise Invalid("Each source needs a link.")
        sources.append(
            {
                "n": i,
                "url": _url(item.get("url")),
                "title": _text(item.get("title"), 300) or None,
                "accessed": _text(item.get("accessed"), 40) or None,
            }
        )
    numbers = {s["n"] for s in sources}

    def refs(item: dict[str, Any]) -> list[int]:
        out = []
        for value in item.get("sources") or []:
            try:
                n = int(value)
            except (TypeError, ValueError):
                raise Invalid("Cite sources by their number.") from None
            if n not in numbers:
                raise Invalid(f"Source {n} is not in the list of sources.")
            out.append(n)
        return sorted(set(out))

    findings = []
    for item in (raw.get("findings") or [])[:MAX_FINDINGS]:
        if not isinstance(item, dict):
            continue
        statement = _text(item.get("statement"))
        if not statement:
            continue
        basis = str(item.get("basis") or "source").strip().lower()
        if basis not in ("source", "inference"):
            raise Invalid("A finding is from a source or an inference.")
        cited = refs(item)
        if basis == "source" and not cited:
            raise Invalid(
                f"The finding {statement[:60]!r} says it is from a source but "
                "cites none. Cite it, or mark it as an inference."
            )
        findings.append(
            {
                "statement": statement,
                "basis": basis,
                "sources": cited,
                "as_of": _text(item.get("as_of"), 40) or None,
            }
        )
    conflicts = [
        {"statement": _text(c.get("statement")), "sources": refs(c)}
        for c in (raw.get("conflicts") or [])[:MAX_FINDINGS]
        if isinstance(c, dict) and _text(c.get("statement"))
    ]
    inaccessible = [
        {
            "url": str(c.get("url") or "").strip()[:2000],
            "reason": _text(c.get("reason"), 300) or "Could not be read.",
        }
        for c in (raw.get("inaccessible") or [])[:MAX_SOURCES]
        if isinstance(c, dict) and str(c.get("url") or "").strip()
    ]
    summary = _text(raw.get("summary")) or None
    if kind == TRADING_SUMMARY:
        summary = guard.scrub(summary) if summary else summary
        for f in findings:
            f["statement"] = guard.scrub(f["statement"])
        findings = [f for f in findings if f["statement"]]
    return Draft(
        title=title,
        question=_text(raw.get("question")) or None,
        summary=summary,
        findings=findings,
        conflicts=conflicts,
        inaccessible=inaccessible,
        sources=sources,
        kind=kind,
    )


def render(d: Draft | SavedReportModel) -> str:
    """The one rendering: what the screen shows and what the export is."""
    lines = [f"# {d.title}", ""]
    if d.question:
        lines += [f"Question: {d.question}", ""]
    if d.kind == TRADING_SUMMARY:
        lines += [guard.NOTICE, ""]
    if d.summary:
        lines += ["## Summary", "", d.summary, ""]
    if d.findings:
        lines += ["## Findings", ""]
        for f in d.findings:
            tag = "Source" if f["basis"] == "source" else "Inference"
            cites = "".join(f" [{n}]" for n in f.get("sources") or [])
            dated = f" (as of {f['as_of']})" if f.get("as_of") else ""
            lines.append(f"- {tag}: {f['statement']}{dated}{cites}")
        lines.append("")
    if d.conflicts:
        lines += ["## Where sources disagree", ""]
        for c in d.conflicts:
            cites = "".join(f" [{n}]" for n in c.get("sources") or [])
            lines.append(f"- {c['statement']}{cites}")
        lines.append("")
    if d.inaccessible:
        lines += ["## Sources that could not be read", ""]
        for c in d.inaccessible:
            lines.append(f"- {c['url']} -- {c['reason']}")
        lines.append("")
    if d.sources:
        lines += ["## Sources", ""]
        for s in d.sources:
            title = f"{s['title']} -- " if s.get("title") else ""
            accessed = f" (read {s['accessed']})" if s.get("accessed") else ""
            lines.append(f"[{s['n']}] {title}{s['url']}{accessed}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def content_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


async def save(
    draft: Draft,
    *,
    organization_id: int,
    user_id: int,
    thread_id: str | None = None,
    visibility: str = sharing.PRIVATE,
) -> SavedReportModel:
    body = render(draft)
    now = datetime.now(UTC)
    row = SavedReportModel(
        uuid=str(uuid_lib.uuid4()),
        organization_id=organization_id,
        owner_user_id=user_id,
        visibility=sharing.clean(visibility),
        kind=draft.kind,
        thread_id=thread_id,
        title=draft.title,
        question=draft.question,
        summary=draft.summary,
        findings=draft.findings,
        conflicts=draft.conflicts,
        inaccessible=draft.inaccessible,
        sources=draft.sources,
        body=body,
        content_hash=content_hash(body),
        created_at=now,
        updated_at=now,
    )
    async with db_client.async_session() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


_LINK = re.compile(r"https?://[^\s)\]>\"']+")


def draft_from_reply(question: str | None, answer: str) -> Draft:
    """A reply kept as it was shown: the answer is the summary, every link
    in it a source. No finding is invented from the prose."""
    links: list[str] = []
    for url in _LINK.findall(answer or ""):
        url = url.rstrip(".,;:")
        if url not in links:
            links.append(url)
    title = _text(question, 120) or "Saved answer"
    return clean(
        {
            "title": title,
            "question": question,
            "summary": answer,
            "sources": [{"url": u} for u in links[:MAX_SOURCES]],
        }
    )


async def list_visible(
    *, organization_id: int, user_id: int, limit: int = 50
) -> list[SavedReportModel]:
    async with db_client.async_session() as session:
        rows = await session.scalars(
            select(SavedReportModel)
            .where(
                *sharing.visible_to(
                    SavedReportModel, organization_id=organization_id, user_id=user_id
                )
            )
            .order_by(SavedReportModel.created_at.desc(), SavedReportModel.id.desc())
            .limit(limit)
        )
        return list(rows.all())


async def get_visible(
    report_uuid: str, *, organization_id: int, user_id: int
) -> SavedReportModel | None:
    async with db_client.async_session() as session:
        return await session.scalar(
            select(SavedReportModel).where(
                SavedReportModel.uuid == report_uuid,
                *sharing.visible_to(
                    SavedReportModel, organization_id=organization_id, user_id=user_id
                ),
            )
        )


async def set_visibility(
    report_uuid: str, visibility: str, *, organization_id: int, user_id: int
) -> SavedReportModel | None:
    value = sharing.clean(visibility)
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(SavedReportModel).where(
                SavedReportModel.uuid == report_uuid,
                *sharing.owned_by(
                    SavedReportModel, organization_id=organization_id, user_id=user_id
                ),
            )
        )
        if row is None:
            return None
        row.visibility = value
        row.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(row)
        return row


def export(row: SavedReportModel, fmt: str = "md") -> tuple[str, str, str]:
    """(content, media type, filename). Markdown is ``body`` itself; HTML
    wraps the same text in a ``<pre>``, so neither can differ from it."""
    import html

    stem = re.sub(r"[^A-Za-z0-9]+", "-", row.title).strip("-")[:60] or "report"
    name = f"{stem}-{row.content_hash[:8]}"
    if fmt == "html":
        page = (
            "<!doctype html><meta charset='utf-8'>"
            f"<title>{html.escape(row.title)}</title>"
            "<pre style='white-space:pre-wrap;font:16px/1.6 system-ui'>"
            f"{html.escape(row.body)}</pre>\n"
        )
        return page, "text/html; charset=utf-8", f"{name}.html"
    return row.body, "text/markdown; charset=utf-8", f"{name}.md"


def describe(row: SavedReportModel, *, user_id: int) -> dict[str, Any]:
    return {
        "uuid": row.uuid,
        "kind": row.kind,
        "title": row.title,
        "question": row.question,
        "summary": row.summary,
        "findings": row.findings or [],
        "conflicts": row.conflicts or [],
        "inaccessible": row.inaccessible or [],
        "sources": row.sources or [],
        "body": row.body,
        "content_hash": row.content_hash,
        "visibility": row.visibility,
        "mine": row.owner_user_id == user_id,
        "thread_id": row.thread_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "notice": guard.NOTICE if row.kind == TRADING_SUMMARY else None,
    }


async def reply_for(
    *, organization_id: int, user_id: int, event_id: int
) -> tuple[str | None, str, str | None] | None:
    """(question, answer, thread_id) for one of Decibyl's replies the person
    can read, or None. With private threads on, only the thread's author
    can keep a reply from it; the row must be Decibyl's own reply."""
    from api import constants
    from api.enums import AgentEventActor, AgentEventKind

    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    if (
        event is None
        or event.kind != AgentEventKind.MESSAGE.value
        or event.actor != AgentEventActor.AGENT.value
        or event.workflow_id is not None
        or event.folder_id is not None
    ):
        return None
    if constants.DECIBYL_PRIVATE_THREADS_ENABLED:
        author = await db_client.thread_author(
            organization_id=organization_id, thread_id=event.thread_id
        )
        if author != user_id:
            return None
    payload = event.payload or {}
    if payload.get("failed"):
        return None
    answer = str(payload.get("body") or event.summary or "")
    earlier = await db_client.agent_events(
        organization_id=organization_id,
        assistant_thread=True,
        thread_id=event.thread_id,
        kinds=[AgentEventKind.MESSAGE.value],
        before_at=event.at,
        before_id=event.id,
        limit=10,
    )
    question = next(
        (
            str((e.payload or {}).get("body") or e.summary or "")
            for e in earlier
            if e.actor == AgentEventActor.HUMAN.value
        ),
        None,
    )
    return question, answer, event.thread_id
