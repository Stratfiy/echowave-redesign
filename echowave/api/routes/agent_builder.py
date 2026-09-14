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

        allowance = await _allowance(session, organization_id)
    state = await limits.peek(organization_id, allowance=allowance)
    return {
        "available": available,
        "provider": provider,
        "unavailable_reason": unavailable_reason,
        "usage": _usage(state, charged_credits=0),
    }


async def _allowance(session, organization_id: int) -> int | None:
    """This month's included builder messages, from the plan (KAN-56)."""
    from api.services.billing import plan_limits

    limit = await plan_limits.limit_for_organization(
        session, organization_id=organization_id, key="builder_messages"
    )
    return limit.value


def _usage(state: limits.LimitState, *, charged_credits: int) -> dict[str, Any]:
    return {
        "used": state.used,
        # ``limit`` kept for the screen that reads it; 0 means unlimited, as
        # it always did. ``allowance`` is the same figure with None meaning
        # unlimited, which is what the plan says.
        "limit": state.limit or 0,
        "allowance": state.limit,
        "remaining": state.remaining if state.remaining is not None else 0,
        "resets_at": state.resets_at.isoformat(),
        "past_allowance": state.past_allowance,
        "per_message_credits": limits.PAST_ALLOWANCE_CREDITS,
        "charged_credits": charged_credits,
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
        allowance = await _allowance(session, organization_id)
        state = await limits.check_and_consume(organization_id, allowance=allowance)
        if state.unavailable:
            raise HTTPException(
                status_code=503,
                detail="The builder is briefly unavailable. Try again in a minute.",
                headers={"Retry-After": "60"},
            )
        charged_credits = 0
        if state.past_allowance:
            # Past the plan's allowance a message is five credits (KAN-56),
            # taken before the model runs. Refused, with the way out named,
            # when the balance cannot cover it.
            from api.services.billing import credits as credit_units
            from api.services.billing import events as billing_events
            from api.services.billing.payments import current_balance_paise

            balance = await current_balance_paise(
                session, organization_id=organization_id
            )
            price = billing_events.paise_for(billing_events.BUILDER_MESSAGE)
            if balance < price:
                raise HTTPException(
                    status_code=402,
                    detail=(
                        f"You have used this month's {allowance} included builder "
                        f"messages. Each further message is "
                        f"{limits.PAST_ALLOWANCE_CREDITS} credits; you have "
                        f"{credit_units.credits_of_balance(balance)}. Add credit, "
                        "or upgrade for a larger allowance."
                    ),
                )
            await billing_events.charge(
                session,
                organization_id=organization_id,
                event=billing_events.BUILDER_MESSAGE,
                ref_id=f"{organization_id}:{limits.ist_month()}:{state.used}",
            )
            await session.commit()
            charged_credits = limits.PAST_ALLOWANCE_CREDITS

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
        "usage": _usage(state, charged_credits=charged_credits),
    }
