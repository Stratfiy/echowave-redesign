"""Escalation v2 over HTTP (services/escalation).

Thin: each route resolves the signed-in person and their workspace and hands
over to the service. The whole group is a 404 while ``escalation_v2`` is off
for the person's workspace.

* ``GET /escalations/policy/{workflow_id}`` -- one agent's policy (draft
  first), and the topics it can send to a person.
* ``PUT /escalations/policy/{workflow_id}`` -- save it into the agent's draft.
* ``GET /escalations/open`` -- handovers still waiting on a person (Today).
* ``GET /escalations/report`` -- precision and recall of escalation within a
  tolerance window of turns, from reviewers' labels (``measure``).
* ``PUT /escalations/outcomes/{workflow_run_id}/label`` -- a reviewer's
  verdict on one call.
* ``GET /escalations/{escalation_uuid}`` -- one handover and its card.
* ``POST /escalations/{escalation_uuid}/accept`` | ``/decline`` |
  ``/hand-back`` -- what the card's buttons do.

Carrier callbacks are not here: they live with their provider
(``services/telephony/providers/plivo/routes.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.escalation import FLAG, actions, measure, settings

router = APIRouter(
    prefix="/escalations",
    tags=["escalations"],
    dependencies=[Depends(features.require(FLAG, per_organization=True))],
)


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


class TopicOption(BaseModel):
    key: str
    label: str


class RuleOption(BaseModel):
    key: str
    label: str
    #: False for the rules that always act (emergency, explicit request).
    can_shadow: bool


class EscalationPolicyResponse(BaseModel):
    workflow_id: int
    policy: dict[str, Any]
    topics: list[TopicOption]
    rules: list[RuleOption] = Field(default_factory=list)
    briefing_languages: list[TopicOption] = Field(default_factory=list)
    #: The draft holds a policy live calls do not use until it is published.
    unpublished: bool


class EscalationPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: dict[str, Any]


class EscalationAttempt(BaseModel):
    n: int | None = None
    target: str | None = None
    outcome: str | None = None
    started_at: str | None = None
    ended_at: str | None = None


class EscalationResponse(BaseModel):
    escalation_uuid: str
    workflow_id: int | None = None
    workflow_run_id: int | None = None
    state: str
    failure_reason: str | None = None
    reason_code: str
    reason_detail: str | None = None
    trigger: str
    attempts: list[EscalationAttempt]
    fallback: str | None = None
    handoff_card: dict[str, Any]
    human_response: str | None = None
    outcome_note: str | None = None
    time_to_human_ms: int | None = None
    requested_at: str | None = None
    bridged_at: str | None = None
    handed_back_at: str | None = None
    completed_at: str | None = None
    can_hand_back: bool = False


class OpenEscalation(BaseModel):
    escalation_uuid: str
    title: str
    at: str | None = None
    state: str


class OpenEscalationsResponse(BaseModel):
    items: list[OpenEscalation]


class EscalationReportResponse(BaseModel):
    window: int
    calls: int
    labelled: int
    unlabelled: int
    outcomes: dict[str, int]
    labels: dict[str, int]
    on_time: int
    early: int
    late: int
    untimed: int
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    resolved_by_ai_rate: float | None = None
    shadow: dict[str, Any]


class OutcomeLabelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: resolved_by_ai | escalated_correctly | escalated_unnecessarily |
    #: should_have_escalated; null clears it.
    label: str | None = None
    #: The caller turn it should have gone to a person on (1-based).
    expected_turn: int | None = Field(default=None, ge=1)


class CallEscalationOutcomeResponse(BaseModel):
    workflow_run_id: int
    workflow_id: int | None = None
    outcome: str
    reason_code: str | None = None
    transfer_result: str | None = None
    caller_turn: int | None = None
    caller_turns: int | None = None
    shadow_escalations: list[dict[str, Any]] = Field(default_factory=list)
    qa_label: str | None = None
    qa_expected_turn: int | None = None
    qa_labelled_at: str | None = None


class HandBackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: What was decided and what is left, for the agent to carry on from.
    note: str = Field(default="", max_length=1000)


def _escalation(data: dict[str, Any]) -> EscalationResponse:
    attempts = [
        EscalationAttempt(**{k: a.get(k) for k in EscalationAttempt.model_fields})
        for a in data.get("attempts") or []
    ]
    return EscalationResponse(**{**data, "attempts": attempts})


def _raise(exc: Exception) -> None:
    if isinstance(exc, (actions.NotFound, settings.AgentNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, actions.NotSupported):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/policy/{workflow_id}", response_model=EscalationPolicyResponse)
async def get_escalation_policy(
    workflow_id: int, user: Annotated[UserModel, Depends(get_user)]
) -> EscalationPolicyResponse:
    try:
        data = await settings.read(_organization_id(user), workflow_id)
    except settings.AgentNotFound as exc:
        _raise(exc)
    return EscalationPolicyResponse(**data)


@router.put("/policy/{workflow_id}", response_model=EscalationPolicyResponse)
async def save_escalation_policy(
    workflow_id: int,
    body: EscalationPolicyRequest,
    user: Annotated[UserModel, Depends(get_user)],
) -> EscalationPolicyResponse:
    try:
        data = await settings.save(_organization_id(user), workflow_id, body.policy)
    except (settings.AgentNotFound, settings.PolicyInvalid) as exc:
        _raise(exc)
    return EscalationPolicyResponse(**data)


@router.get("/open", response_model=OpenEscalationsResponse)
async def open_escalations(
    user: Annotated[UserModel, Depends(get_user)],
) -> OpenEscalationsResponse:
    items = await actions.list_open_for_today(_organization_id(user))
    return OpenEscalationsResponse(items=[OpenEscalation(**i) for i in items])


@router.get("/report", response_model=EscalationReportResponse)
async def escalation_report(
    user: Annotated[UserModel, Depends(get_user)],
    window: Annotated[int, Query(ge=0, le=measure.MAX_WINDOW)] = measure.DEFAULT_WINDOW,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    workflow_id: int | None = None,
) -> EscalationReportResponse:
    data = await measure.report(
        _organization_id(user),
        window=window,
        since=datetime.now(UTC) - timedelta(days=days),
        workflow_id=workflow_id,
    )
    return EscalationReportResponse(**data)


@router.put(
    "/outcomes/{workflow_run_id}/label",
    response_model=CallEscalationOutcomeResponse,
)
async def label_call_escalation_outcome(
    workflow_run_id: int,
    body: OutcomeLabelRequest,
    user: Annotated[UserModel, Depends(get_user)],
) -> CallEscalationOutcomeResponse:
    try:
        data = await measure.set_label(
            _organization_id(user),
            workflow_run_id,
            label=body.label,
            expected_turn=body.expected_turn,
            user_id=user.id,
        )
    except measure.OutcomeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except measure.LabelInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CallEscalationOutcomeResponse(**data)


@router.get("/{escalation_uuid}", response_model=EscalationResponse)
async def get_escalation(
    escalation_uuid: str, user: Annotated[UserModel, Depends(get_user)]
) -> EscalationResponse:
    try:
        return _escalation(await actions.get(_organization_id(user), escalation_uuid))
    except actions.EscalationError as exc:
        _raise(exc)


@router.post("/{escalation_uuid}/accept", response_model=EscalationResponse)
async def accept_escalation(
    escalation_uuid: str, user: Annotated[UserModel, Depends(get_user)]
) -> EscalationResponse:
    try:
        data = await actions.accept(
            _organization_id(user), escalation_uuid, user_id=user.id
        )
    except actions.EscalationError as exc:
        _raise(exc)
    return _escalation(data)


@router.post("/{escalation_uuid}/decline", response_model=EscalationResponse)
async def decline_escalation(
    escalation_uuid: str, user: Annotated[UserModel, Depends(get_user)]
) -> EscalationResponse:
    try:
        data = await actions.decline(
            _organization_id(user), escalation_uuid, user_id=user.id
        )
    except actions.EscalationError as exc:
        _raise(exc)
    return _escalation(data)


@router.post("/{escalation_uuid}/hand-back", response_model=EscalationResponse)
async def hand_back_escalation(
    escalation_uuid: str,
    body: HandBackRequest,
    user: Annotated[UserModel, Depends(get_user)],
) -> EscalationResponse:
    try:
        data = await actions.hand_back(
            _organization_id(user), escalation_uuid, user_id=user.id, note=body.note
        )
    except actions.EscalationError as exc:
        _raise(exc)
    return _escalation(data)
