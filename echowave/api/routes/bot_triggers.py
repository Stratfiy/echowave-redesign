"""HTTP surface for a bot's triggers (KAN-137).

Org-scoped from the authenticated user, never from the body: a trigger id
in a URL proves a row exists, not that the caller may touch it. ADMIN for
writes, as with routines -- a trigger acts unsupervised on whatever an
outside system sends, which is closer to granting access than to editing a
prompt.

The compile step is its own endpoint and never refuses: it answers with a
plan or with questions. Nothing is saved until the plan has no questions,
which is how "ask and fill rather than error" is enforced on the server
rather than trusted to the screen.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.db import db_client
from api.db.models import BotTriggerModel, UserModel
from api.enums import OrganizationRole
from api.schemas.bot_trigger import (
    TriggerCompileRequest,
    TriggerCompileResponse,
    TriggerListResponse,
    TriggerResponse,
    TriggerTestRequest,
    TriggerTestResponse,
    TriggerWrite,
)
from api.services.auth.depends import get_user, require_organization_role
from api.services.workflow import bot_triggers
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames

router = APIRouter(prefix="/workflows/{workflow_id}/triggers", tags=["triggers"])


def _organization_id(user: UserModel) -> int:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return organization_id


async def _owned_workflow(workflow_id: int, organization_id: int):
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="No such agent in this account.")
    return workflow


def _render(trigger: BotTriggerModel) -> TriggerResponse:
    return TriggerResponse(
        id=trigger.id,
        workflow_id=trigger.workflow_id,
        uuid=trigger.uuid,
        name=trigger.name,
        source=trigger.source or bot_triggers.SOURCE_WEBHOOK,
        sentence=trigger.sentence or "",
        instruction=trigger.instruction or "",
        fields=list(trigger.fields or []),
        filter=list(trigger.filter or []),
        filter_summary=bot_triggers.describe(trigger.filter),
        is_active=bool(trigger.is_active),
        url=bot_triggers.public_url(trigger.uuid),
        secret=trigger.secret,
        last_fired_at=trigger.last_fired_at,
        fired_count=int(trigger.fired_count or 0),
        created_at=trigger.created_at,
    )


@router.get("", response_model=TriggerListResponse)
async def list_triggers(
    workflow_id: int,
    user: Annotated[UserModel, Depends(get_user)],
) -> TriggerListResponse:
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)
    rows = await db_client.bot_triggers_for_workflow(
        workflow_id, organization_id=organization_id
    )
    return TriggerListResponse(
        triggers=[_render(r) for r in rows],
        max_per_workflow=bot_triggers.MAX_PER_WORKFLOW,
    )


@router.post("/compile", response_model=TriggerCompileResponse)
async def compile_trigger(
    workflow_id: int,
    body: TriggerCompileRequest,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> TriggerCompileResponse:
    """A sentence in; a plan or the questions in its way out. Never 4xx on
    the sentence itself."""
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)
    async with db_client.async_session() as session:
        compiled = await bot_triggers.compile(
            body.sentence, answers=body.answers, session=session
        )
    return TriggerCompileResponse(
        **compiled.as_dict(), filter_summary=bot_triggers.describe(compiled.filter)
    )


@router.post("", response_model=TriggerResponse, status_code=201)
async def create_trigger(
    workflow_id: int,
    body: TriggerWrite,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> TriggerResponse:
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)

    existing = await db_client.bot_triggers_for_workflow(
        workflow_id, organization_id=organization_id
    )
    if len(existing) >= bot_triggers.MAX_PER_WORKFLOW:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This agent already has {bot_triggers.MAX_PER_WORKFLOW} "
                "triggers. Edit or remove one rather than adding another."
            ),
        )
    _check_rules(body)

    trigger = await db_client.create_bot_trigger(
        organization_id=organization_id,
        workflow_id=workflow_id,
        uuid=bot_triggers.new_uuid(),
        secret=bot_triggers.new_secret(),
        source=bot_triggers.SOURCE_WEBHOOK,
        created_by=user.id,
        **body.model_dump(mode="json"),
    )
    logger.info("Trigger {} created on workflow {}", trigger.id, workflow_id)
    return _render(trigger)


def _check_rules(body: TriggerWrite) -> None:
    bad = [r.op for r in body.filter if r.op not in bot_triggers.OPS]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown comparison {bad[0]!r}. Use one of: "
            + ", ".join(bot_triggers.OPS),
        )


@router.put("/{trigger_id}", response_model=TriggerResponse)
async def update_trigger(
    workflow_id: int,
    trigger_id: int,
    body: TriggerWrite,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> TriggerResponse:
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)
    _check_rules(body)
    trigger = await db_client.update_bot_trigger(
        trigger_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        **body.model_dump(mode="json"),
    )
    if trigger is None:
        raise HTTPException(status_code=404, detail="No such trigger on this agent.")
    return _render(trigger)


@router.delete("/{trigger_id}", status_code=204)
async def delete_trigger(
    workflow_id: int,
    trigger_id: int,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> None:
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)
    removed = await db_client.delete_bot_trigger(
        trigger_id, organization_id=organization_id, workflow_id=workflow_id
    )
    if not removed:
        raise HTTPException(status_code=404, detail="No such trigger on this agent.")


@router.post("/{trigger_id}/active", response_model=TriggerResponse)
async def set_active(
    workflow_id: int,
    trigger_id: int,
    active: bool,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> TriggerResponse:
    """Pause or resume. A paused trigger answers its sender 202 and does
    nothing, so the sender does not start retrying."""
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)
    trigger = await db_client.update_bot_trigger(
        trigger_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        is_active=bool(active),
    )
    if trigger is None:
        raise HTTPException(status_code=404, detail="No such trigger on this agent.")
    return _render(trigger)


@router.post("/{trigger_id}/rotate-secret", response_model=TriggerResponse)
async def rotate_secret(
    workflow_id: int,
    trigger_id: int,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> TriggerResponse:
    """New secret, same address. The old one stops working at once."""
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)
    trigger = await db_client.update_bot_trigger(
        trigger_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        secret=bot_triggers.new_secret(),
    )
    if trigger is None:
        raise HTTPException(status_code=404, detail="No such trigger on this agent.")
    return _render(trigger)


@router.post("/{trigger_id}/test", response_model=TriggerTestResponse)
async def test_trigger(
    workflow_id: int,
    trigger_id: int,
    body: TriggerTestRequest,
    user: Annotated[
        UserModel, Depends(require_organization_role(OrganizationRole.ADMIN))
    ],
) -> TriggerTestResponse:
    """Fire it once with a sample event. The real run, on the published bot."""
    organization_id = _organization_id(user)
    await _owned_workflow(workflow_id, organization_id)
    trigger = await db_client.get_bot_trigger(
        trigger_id, organization_id=organization_id, workflow_id=workflow_id
    )
    if trigger is None:
        raise HTTPException(status_code=404, detail="No such trigger on this agent.")

    missing = bot_triggers.missing_fields(trigger.fields, body.payload)
    if not bot_triggers.matches(trigger.filter, body.payload):
        return TriggerTestResponse(
            started=False,
            status="filtered",
            detail=(
                "This sample does not match the filter "
                f"({bot_triggers.describe(trigger.filter)}), so the bot would "
                "not act on it and nothing would be charged."
            ),
            missing_fields=missing,
        )
    try:
        await enqueue_job(FunctionNames.RUN_BOT_TRIGGER, trigger.id, body.payload, None)
    except Exception as exc:
        logger.exception("Could not enqueue test run for trigger {}", trigger_id)
        raise HTTPException(
            status_code=503,
            detail="Could not start the test run just now. Try again in a moment.",
        ) from exc
    return TriggerTestResponse(
        started=True,
        status="accepted",
        detail="Running now. Watch the thread for what the bot did with it.",
        missing_fields=missing,
    )
