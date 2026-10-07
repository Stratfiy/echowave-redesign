"""Launch stream `agents`: the helper picker, saved reports, commitments,
trading interests and trackers.

Thin by design (api/AGENTS.md): each handler checks its flag, resolves the
person and their workspace, and delegates to ``services/helpers``. Every
read is scoped to the selected workspace and, for a person's own things,
to what they own or what was shared with that workspace.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.helpers import (
    catalogue,
    commitments,
    interests,
    reports,
    sharing,
    states,
    trackers,
)

router = APIRouter(prefix="/helpers", tags=["helpers"])

_helpers_flag = Depends(features.require(states.FLAG, per_organization=True))
_reports_flag = Depends(features.require(reports.FLAG, per_organization=True))
_ledger_flag = Depends(features.require(commitments.FLAG, per_organization=True))
_trading_flag = Depends(features.require(interests.FLAG, per_organization=True))
_builder_flag = Depends(features.require(trackers.FLAG, per_organization=True))


def _org(user: UserModel) -> int:
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return int(user.selected_organization_id)


async def _is_admin(user: UserModel, organization_id: int) -> bool:
    from api.routes.agent_timeline import _is_admin as is_admin

    return await is_admin(user, organization_id)


# ---------------------------------------------------------------------------
# Screen 06: the helper picker
# ---------------------------------------------------------------------------


class HelperSetup(BaseModel):
    kind: str
    label: str
    app: Optional[str] = None


class HelperAdvanced(BaseModel):
    tools: list[str]
    skills: list[str]
    model: str
    voice: str


class HelperOut(BaseModel):
    key: str
    name: str
    job: str
    evidence: str
    boundary: str
    example: str
    permissions: list[str]
    connections: list[str]
    templates: list[str]
    #: ``available``, ``needs_setup``, ``disabled_by_policy`` or ``unavailable``.
    state: str
    reason: Optional[str] = None
    setup: Optional[HelperSetup] = None
    notes: list[str]
    advanced: Optional[HelperAdvanced] = None


class HelpersResponse(BaseModel):
    #: The five, in the handoff's order.
    helpers: list[HelperOut]
    #: The describe-it builder, when switched on.
    builder: Optional[HelperOut] = None
    #: Whether the person may switch helpers off for the workspace.
    can_manage: bool


@router.get("", response_model=HelpersResponse, dependencies=[_helpers_flag])
async def list_helpers(
    user: Annotated[UserModel, Depends(get_user)],
) -> HelpersResponse:
    organization_id = _org(user)
    admin = await _is_admin(user, organization_id)
    readings = await states.read(organization_id)
    out = {
        key: HelperOut(
            **states.describe(
                catalogue.BY_KEY[key],
                states.evaluate(catalogue.BY_KEY[key], readings),
                advanced=admin,
            )
        )
        for key in catalogue.BY_KEY
    }
    return HelpersResponse(
        helpers=[out[k] for k in catalogue.FIVE],
        builder=out[catalogue.BUILDER] if trackers.enabled(organization_id) else None,
        can_manage=admin,
    )


class WorkspaceSwitch(BaseModel):
    enabled: bool


@router.put(
    "/{key}/workspace", response_model=HelpersResponse, dependencies=[_helpers_flag]
)
async def set_workspace_switch(
    key: str,
    body: WorkspaceSwitch,
    user: Annotated[UserModel, Depends(get_user)],
) -> HelpersResponse:
    """An admin turns a helper on or off for everyone in the workspace."""
    organization_id = _org(user)
    if key not in catalogue.BY_KEY:
        raise HTTPException(status_code=404, detail="No such helper")
    if not await _is_admin(user, organization_id):
        raise HTTPException(status_code=403, detail="Managed by your workspace admins")
    await states.set_workspace_switch(
        organization_id, key, enabled=body.enabled, user_id=user.id
    )
    return await list_helpers(user)


class SetupRequest(BaseModel):
    thread_id: Optional[str] = Field(default=None, max_length=36)


class SetupResponse(BaseModel):
    status: str
    note: Optional[str] = None


@router.post("/{key}/setup", response_model=SetupResponse, dependencies=[_helpers_flag])
async def setup_helper(
    key: str,
    body: SetupRequest,
    user: Annotated[UserModel, Depends(get_user)],
) -> SetupResponse:
    """The next step, in the thread the person is in: a connect card for the
    app the helper needs. The composer keeps its draft; nothing navigates."""
    from api.routes.agent_timeline import _assert_thread_is_theirs
    from api.services.workflow import agent_timeline, connector_offer

    organization_id = _org(user)
    state = await states.state_of(organization_id, key)
    if state is None:
        raise HTTPException(status_code=404, detail="No such helper")
    if state.setup is None or state.setup.kind != "connect" or not state.setup.app:
        raise HTTPException(
            status_code=409, detail=state.reason or "Nothing to set up here."
        )
    await _assert_thread_is_theirs(user, organization_id, body.thread_id)
    with agent_timeline.in_thread(body.thread_id):
        result = await connector_offer.offer(
            organization_id=organization_id,
            arguments={"app": state.setup.app, "why": state.reason or ""},
        )
    return SetupResponse(
        status=str(result.get("status")),
        note=str(result.get("note") or result.get("reason") or "") or None,
    )


# ---------------------------------------------------------------------------
# Research: saved reports
# ---------------------------------------------------------------------------


class ReportFinding(BaseModel):
    statement: str
    basis: str
    sources: list[int] = []
    as_of: Optional[str] = None


class ReportConflict(BaseModel):
    statement: str
    sources: list[int] = []


class ReportInaccessible(BaseModel):
    url: str
    reason: str


class ReportSource(BaseModel):
    n: int
    url: str
    title: Optional[str] = None
    accessed: Optional[str] = None


class ReportOut(BaseModel):
    uuid: str
    kind: str
    title: str
    question: Optional[str] = None
    summary: Optional[str] = None
    findings: list[ReportFinding]
    conflicts: list[ReportConflict]
    inaccessible: list[ReportInaccessible]
    sources: list[ReportSource]
    #: The one rendering the export is, byte for byte.
    body: str
    content_hash: str
    visibility: str
    mine: bool
    thread_id: Optional[str] = None
    created_at: Optional[str] = None
    notice: Optional[str] = None


class ReportSummary(BaseModel):
    uuid: str
    kind: str
    title: str
    visibility: str
    mine: bool
    created_at: Optional[str] = None


@router.get(
    "/reports", response_model=list[ReportSummary], dependencies=[_reports_flag]
)
async def list_reports(
    user: Annotated[UserModel, Depends(get_user)],
) -> list[ReportSummary]:
    rows = await reports.list_visible(organization_id=_org(user), user_id=user.id)
    return [
        ReportSummary(
            **{
                k: v
                for k, v in reports.describe(r, user_id=user.id).items()
                if k in ReportSummary.model_fields
            }
        )
        for r in rows
    ]


async def _report(user: UserModel, report_uuid: str):
    row = await reports.get_visible(
        report_uuid, organization_id=_org(user), user_id=user.id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return row


@router.get(
    "/reports/{report_uuid}", response_model=ReportOut, dependencies=[_reports_flag]
)
async def get_report(
    report_uuid: str, user: Annotated[UserModel, Depends(get_user)]
) -> ReportOut:
    return ReportOut(
        **reports.describe(await _report(user, report_uuid), user_id=user.id)
    )


@router.get("/reports/{report_uuid}/export", dependencies=[_reports_flag])
async def export_report(
    report_uuid: str,
    user: Annotated[UserModel, Depends(get_user)],
    format: Literal["md", "html"] = "md",
) -> Response:
    """The report as a file: exactly the rendering the screen shows, with
    its hash in ``X-Content-Hash`` so the two can be compared."""
    row = await _report(user, report_uuid)
    content, media_type, filename = reports.export(row, format)
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Hash": row.content_hash,
        },
    )


class VisibilityRequest(BaseModel):
    visibility: Literal["private", "workspace"]


@router.put(
    "/reports/{report_uuid}/visibility",
    response_model=ReportOut,
    dependencies=[_reports_flag],
)
async def share_report(
    report_uuid: str,
    body: VisibilityRequest,
    user: Annotated[UserModel, Depends(get_user)],
) -> ReportOut:
    row = await reports.set_visibility(
        report_uuid, body.visibility, organization_id=_org(user), user_id=user.id
    )
    if row is None:
        # Not theirs (or not here): only the owner shares.
        raise HTTPException(status_code=404, detail="Report not found")
    return ReportOut(**reports.describe(row, user_id=user.id))


class FromReplyRequest(BaseModel):
    event_id: int


@router.post(
    "/reports/from-reply", response_model=ReportOut, dependencies=[_reports_flag]
)
async def save_reply_as_report(
    body: FromReplyRequest, user: Annotated[UserModel, Depends(get_user)]
) -> ReportOut:
    """Keep one of Decibyl's replies as a report, as it was shown."""
    organization_id = _org(user)
    found = await reports.reply_for(
        organization_id=organization_id, user_id=user.id, event_id=body.event_id
    )
    if found is None:
        raise HTTPException(status_code=404, detail="Reply not found")
    question, answer, thread_id = found
    try:
        draft = reports.draft_from_reply(question, answer)
    except reports.Invalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = await reports.save(
        draft, organization_id=organization_id, user_id=user.id, thread_id=thread_id
    )
    return ReportOut(**reports.describe(row, user_id=user.id))


# ---------------------------------------------------------------------------
# Follow-up: commitments and who owes me
# ---------------------------------------------------------------------------


class FollowUpState(BaseModel):
    card_id: int
    #: The card's state as a delivery state: awaiting_approval, scheduled,
    #: sending, sent, failed, cancelled, outcome_unknown.
    delivery: str
    label: Optional[str] = None
    error: Optional[str] = None


class CommitmentOut(BaseModel):
    uuid: str
    id: int
    direction: str
    counterparty: str
    contact: Optional[str] = None
    description: str
    amount_minor: Optional[int] = None
    currency: Optional[str] = None
    amount: Optional[str] = None
    due_on: Optional[str] = None
    overdue: bool
    status: str
    visibility: str
    mine: bool
    revision: int
    follow_up: Optional[FollowUpState] = None


class MoneyTotal(BaseModel):
    currency: str
    amount_minor: int
    amount: Optional[str] = None


class WhoOwesMeResponse(BaseModel):
    items: list[CommitmentOut]
    totals: list[MoneyTotal]
    overdue: int


@router.get(
    "/who-owes-me", response_model=WhoOwesMeResponse, dependencies=[_ledger_flag]
)
async def who_owes_me(
    user: Annotated[UserModel, Depends(get_user)],
) -> WhoOwesMeResponse:
    return WhoOwesMeResponse(
        **await commitments.who_owes_me(organization_id=_org(user), user_id=user.id)
    )


@router.get(
    "/commitments", response_model=list[CommitmentOut], dependencies=[_ledger_flag]
)
async def list_commitments(
    user: Annotated[UserModel, Depends(get_user)],
    direction: Optional[Literal["owed_to_me", "i_owe"]] = None,
    status: Annotated[
        Optional[Literal["open", "settled", "cancelled"]], Query()
    ] = "open",
) -> list[CommitmentOut]:
    rows = await commitments.list_visible(
        organization_id=_org(user), user_id=user.id, direction=direction, status=status
    )
    return [
        CommitmentOut(**await commitments.describe(r, user_id=user.id)) for r in rows
    ]


class CommitmentCreate(BaseModel):
    direction: Literal["owed_to_me", "i_owe"] = "owed_to_me"
    counterparty: str = Field(max_length=200)
    description: str = Field(max_length=2000)
    contact: Optional[str] = Field(default=None, max_length=320)
    amount: Optional[str] = Field(default=None, max_length=32)
    currency: Optional[str] = Field(default=None, max_length=3)
    due_on: Optional[str] = Field(default=None, max_length=10)
    visibility: Literal["private", "workspace"] = "private"


@router.post("/commitments", response_model=CommitmentOut, dependencies=[_ledger_flag])
async def add_commitment(
    body: CommitmentCreate, user: Annotated[UserModel, Depends(get_user)]
) -> CommitmentOut:
    """A person adding one themselves: their entry is the approval."""
    try:
        fields = commitments.clean(body.model_dump())
    except commitments.Invalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = await commitments.create(
        fields, organization_id=_org(user), user_id=user.id, visibility=body.visibility
    )
    return CommitmentOut(**await commitments.describe(row, user_id=user.id))


class CommitmentChange(BaseModel):
    revision: int
    status: Optional[Literal["open", "settled", "cancelled"]] = None
    visibility: Optional[Literal["private", "workspace"]] = None


@router.patch(
    "/commitments/{commitment_uuid}",
    response_model=CommitmentOut,
    dependencies=[_ledger_flag],
)
async def change_commitment(
    commitment_uuid: str,
    body: CommitmentChange,
    user: Annotated[UserModel, Depends(get_user)],
) -> CommitmentOut:
    try:
        row = await commitments.change(
            commitment_uuid,
            organization_id=_org(user),
            user_id=user.id,
            revision=body.revision,
            status=body.status,
            visibility=body.visibility,
        )
    except commitments.Conflict as exc:
        if exc.current is None:
            raise HTTPException(status_code=404, detail="Not found") from exc
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This changed since you looked at it.",
                "stored": (await commitments.describe(exc.current, user_id=user.id)),
            },
        ) from exc
    return CommitmentOut(**await commitments.describe(row, user_id=user.id))


# ---------------------------------------------------------------------------
# Research: trading summaries by interest (information only)
# ---------------------------------------------------------------------------


class Interest(BaseModel):
    label: str = Field(max_length=60)
    kind: Literal["ticker", "sector", "topic"] = "topic"


class InterestsOut(BaseModel):
    interests: list[Interest]
    revision: int
    notice: str


class InterestsWrite(BaseModel):
    interests: list[Interest] = Field(max_length=interests.MAX_INTERESTS)
    revision: int


@router.get(
    "/research/interests", response_model=InterestsOut, dependencies=[_trading_flag]
)
async def my_interests(user: Annotated[UserModel, Depends(get_user)]) -> InterestsOut:
    from api.services.helpers import guard

    found = await interests.get(user.id)
    return InterestsOut(**found.as_dict(), notice=guard.NOTICE)


@router.put(
    "/research/interests", response_model=InterestsOut, dependencies=[_trading_flag]
)
async def save_my_interests(
    body: InterestsWrite, user: Annotated[UserModel, Depends(get_user)]
) -> InterestsOut:
    from api.services.helpers import guard

    try:
        saved = await interests.save(
            user.id,
            [i.model_dump() for i in body.interests],
            revision=body.revision,
        )
    except interests.Invalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except interests.Conflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "These changed somewhere else. Here is what is saved now.",
                "stored": exc.stored.as_dict(),
            },
        ) from exc
    return InterestsOut(**saved.as_dict(), notice=guard.NOTICE)


class SummaryRequest(BaseModel):
    thread_id: Optional[str] = Field(default=None, max_length=36)


class SummaryResponse(BaseModel):
    #: The line asked of Research, now on the thread.
    asked: str


@router.post(
    "/research/trading-summary",
    response_model=SummaryResponse,
    dependencies=[_trading_flag],
)
async def ask_trading_summary(
    body: SummaryRequest, user: Annotated[UserModel, Depends(get_user)]
) -> SummaryResponse:
    """Ask Research for a summary of what the person follows, in the thread
    they are in. A turn like any other: it counts against their turns."""
    from api.routes.agent_timeline import _assert_thread_is_theirs
    from api.services.workflow import decibyl

    organization_id = _org(user)
    try:
        await states.assert_usable(organization_id, catalogue.RESEARCH)
    except states.Unusable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _assert_thread_is_theirs(user, organization_id, body.thread_id)
    try:
        line = interests.summary_request((await interests.get(user.id)).items)
    except interests.Invalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await decibyl.ask(
        organization_id=organization_id,
        user_id=user.id,
        text=line,
        attachments=[],
        line=line,
        preset=None,
        thread_id=body.thread_id,
        helper=catalogue.RESEARCH,
    )
    return SummaryResponse(asked=line)


# ---------------------------------------------------------------------------
# The builder: trackers
# ---------------------------------------------------------------------------


class TrackerColumn(BaseModel):
    name: str
    type: str


class TrackerOut(BaseModel):
    uuid: str
    name: str
    columns: list[TrackerColumn]
    visibility: str
    mine: bool
    created_at: Optional[str] = None


class TrackerRow(BaseModel):
    id: int
    values: dict[str, Any]
    at: str


class TrackerDetail(TrackerOut):
    rows: list[TrackerRow]


@router.get("/trackers", response_model=list[TrackerOut], dependencies=[_builder_flag])
async def list_trackers(
    user: Annotated[UserModel, Depends(get_user)],
) -> list[TrackerOut]:
    rows = await trackers.list_visible(organization_id=_org(user), user_id=user.id)
    return [TrackerOut(**trackers.describe(r, user_id=user.id)) for r in rows]


async def _tracker(user: UserModel, tracker_uuid: str):
    row = await trackers.find_visible(
        organization_id=_org(user), user_id=user.id, uuid=tracker_uuid
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Tracker not found")
    return row


@router.get(
    "/trackers/{tracker_uuid}",
    response_model=TrackerDetail,
    dependencies=[_builder_flag],
)
async def get_tracker(
    tracker_uuid: str, user: Annotated[UserModel, Depends(get_user)]
) -> TrackerDetail:
    row = await _tracker(user, tracker_uuid)
    return TrackerDetail(
        **trackers.describe(row, user_id=user.id), rows=await trackers.entries(row)
    )


class TrackerRowWrite(BaseModel):
    values: dict[str, Any]


@router.post(
    "/trackers/{tracker_uuid}/rows",
    response_model=TrackerDetail,
    dependencies=[_builder_flag],
)
async def add_tracker_row(
    tracker_uuid: str,
    body: TrackerRowWrite,
    user: Annotated[UserModel, Depends(get_user)],
) -> TrackerDetail:
    row = await _tracker(user, tracker_uuid)
    try:
        values = trackers.clean_values(row, body.values)
    except sharing.Invalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await trackers.add_entry(row, values, user_id=user.id)
    return await get_tracker(tracker_uuid, user)
