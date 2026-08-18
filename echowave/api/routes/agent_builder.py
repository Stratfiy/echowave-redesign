"""The in-product chat that builds an agent.

Thin by design: resolve the caller and their organization, check the day's
allowance, hand off to the service, shape the reply.

Two things are enforced here rather than deeper down, because this is the seam
where a request becomes spend.

**The allowance is consumed before the model is called.** A turn that fails
halfway still cost us tokens. Counting only successes would let a failing loop
run all day free.

**The transcript is bounded.** It arrives from the client, so it is user input
and could be any length. An unbounded history is an unbounded prompt on our own
key.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.services.agent_builder import limits, settings
from api.services.agent_builder.client import BuilderClientError
from api.services.agent_builder.session import run_turn
from api.services.auth.depends import get_user

router = APIRouter(prefix="/agent-builder", tags=["agent-builder"])

#: The longest transcript a turn may carry. Generous for a build conversation
#: — a few dozen exchanges — and a hard stop on a client replaying an
#: ever-growing history against our key.
MAX_HISTORY_MESSAGES = 80

#: One message's ceiling. Long enough to paste a list of doctors or opening
#: hours, short enough that nobody pastes a book.
MAX_MESSAGE_CHARS = 4000


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_CHARS)
    #: The transcript from the previous reply, sent back unchanged. The server
    #: keeps no session state.
    history: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/config")
async def get_builder_config(user: UserModel = Depends(get_user)) -> dict[str, Any]:
    """Whether the builder is usable, and what is left of today's allowance.

    Called before the panel renders, so it can show the real state rather than
    letting someone type a message into something that will refuse it.
    """
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")

    async with db_client.async_session() as session:
        try:
            model = await settings.resolve_model(session)
            available = True
            provider = model.provider
            unavailable_reason = None
        except settings.BuilderUnavailable as exc:
            available = False
            provider = None
            # Shown to an administrator on the same screen; every case is
            # something they fix in the provider keys screen.
            unavailable_reason = str(exc)

    state = await limits.peek(organization_id)
    return {
        "available": available,
        "provider": provider,
        "unavailable_reason": unavailable_reason,
        "usage": {
            "used": state.used,
            "limit": state.limit,
            "remaining": state.remaining,
            "resets_at": state.resets_at.isoformat(),
        },
    }


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    user: UserModel = Depends(get_user),
) -> dict[str, Any]:
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(status_code=400, detail="No organization selected")

    history = payload.history[-MAX_HISTORY_MESSAGES:]

    async with db_client.async_session() as session:
        try:
            model = await settings.resolve_model(session)
        except settings.BuilderUnavailable as exc:
            # 503 rather than 500: nothing is broken, the feature is not
            # configured, and the message says what to configure.
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        # Consumed before the model is called — see the module docstring.
        state = await limits.check_and_consume(organization_id)
        if state.exhausted:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"You have used today's {state.limit} builder messages. "
                    "The allowance resets at midnight."
                ),
                headers={"Retry-After": "3600"},
            )

        try:
            result = await run_turn(
                session=session,
                model=model,
                organization_id=organization_id,
                user_id=user.id,
                message=payload.message,
                history=history,
            )
        except BuilderClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "reply": result.reply,
        "history": result.conversation,
        "actions": result.actions,
        "created_workflow_id": result.created_workflow_id,
        "usage": {
            "used": state.used,
            "limit": state.limit,
            "remaining": state.remaining,
        },
    }
