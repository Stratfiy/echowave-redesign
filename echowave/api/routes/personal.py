"""What Decibyl keeps about a person, and what a conversation uses.

Every route is a 404 while ``evolve_personal`` is off. Every route reads and
writes the signed-in person's own rows and takes no other person's id: a
colleague asking for a preference id, a card id or a fact id that is not
theirs gets the same 404 as one that does not exist. Handlers stay thin; the
rules live in ``services/personal/``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.db.models import UserModel
from api.services import features
from api.services.auth.depends import get_user
from api.services.personal import FLAG, cards, context_control, preferences

router = APIRouter(
    prefix="/personal",
    tags=["personal"],
    dependencies=[Depends(features.require(FLAG, per_organization=True))],
)

User = Annotated[UserModel, Depends(get_user)]


def _organization_id(user: UserModel) -> int:
    organization_id = getattr(user, "selected_organization_id", None)
    if not organization_id:
        raise HTTPException(status_code=400, detail="No workspace selected")
    return organization_id


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: The person's own words: "Hindi", "11", "between 10 and 6".
    text: str = Field(min_length=1, max_length=400)


class ProposalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    save: bool


class SourceChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str | None = Field(default=None, max_length=40)
    source: str = Field(min_length=1, max_length=64)
    included: bool


@router.get("/about-me")
async def about_me(user: User) -> dict[str, Any]:
    """Everything kept about the person asking: their preferences, with
    where each came from and what it replaced, and what was learned from
    their own conversations in this workspace."""
    return await cards.about_me(organization_id=_organization_id(user), user_id=user.id)


@router.get("/cards/{event_id}")
async def card(event_id: int, user: User) -> dict[str, Any]:
    """One memory card on the thread, drawn from the person's own store."""
    try:
        return await cards.card(
            organization_id=_organization_id(user), user_id=user.id, event_id=event_id
        )
    except cards.CardNotFound as exc:
        raise HTTPException(status_code=404, detail="That card is not here.") from exc


@router.post("/cards/{event_id}/answer")
async def answer_proposal(
    event_id: int, body: ProposalAnswer, user: User
) -> dict[str, Any]:
    """Save, or No thanks, on a preference Decibyl offered."""
    try:
        return await cards.settle_proposal(
            organization_id=_organization_id(user),
            user_id=user.id,
            event_id=event_id,
            accept=body.save,
        )
    except cards.CardNotFound as exc:
        raise HTTPException(status_code=404, detail="That card is not here.") from exc
    except cards.CardSettled as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/preferences")
async def my_preferences(user: User) -> dict[str, Any]:
    return {"preferences": await preferences.mine(user.id)}


@router.get("/preferences/{preference_id}/history")
async def preference_history(preference_id: int, user: User) -> dict[str, Any]:
    try:
        return {
            "history": await preferences.history(
                user_id=user.id, preference_id=preference_id
            )
        }
    except preferences.NotFound as exc:
        raise HTTPException(status_code=404, detail="That is not here.") from exc


@router.post("/preferences/{preference_id}/correct")
async def correct_preference(
    preference_id: int, body: Correction, user: User
) -> dict[str, Any]:
    """Correct a preference in the person's own words. The old value is
    kept as history; the corrected one is what is applied from now on."""
    try:
        saved = await preferences.correct(
            user_id=user.id,
            organization_id=_organization_id(user),
            preference_id=preference_id,
            text=body.text,
        )
    except preferences.NotFound as exc:
        raise HTTPException(status_code=404, detail="That is not here.") from exc
    except preferences.Unreadable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "preference": saved.preference,
        "changed": saved.changed,
        "replaced": saved.replaced,
    }


@router.delete("/preferences/{preference_id}")
async def forget_preference(preference_id: int, user: User) -> dict[str, Any]:
    """Forget a preference: it and every earlier value it replaced are
    deleted."""
    try:
        gone = await preferences.forget(user_id=user.id, preference_id=preference_id)
    except preferences.NotFound as exc:
        raise HTTPException(status_code=404, detail="That is not here.") from exc
    return {"forgotten": gone}


@router.post("/learned/{fact_id}/correct")
async def correct_learned(fact_id: int, body: Correction, user: User) -> dict[str, Any]:
    """Correct something learned about the person (their own memory only)."""
    from api.services.settings import memory

    try:
        return await cards.correct_fact(
            organization_id=_organization_id(user),
            user_id=user.id,
            fact_id=fact_id,
            value=body.text,
        )
    except cards.CardNotFound as exc:
        raise HTTPException(status_code=404, detail="That is not here.") from exc
    except memory.MemoryInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except memory.Conflict as exc:
        raise HTTPException(
            status_code=409, detail="That changed since you opened it."
        ) from exc


@router.delete("/learned/{fact_id}")
async def forget_learned(fact_id: int, user: User) -> dict[str, Any]:
    """Forget something learned about the person: deleted, with its history."""
    try:
        await cards.forget_fact(
            organization_id=_organization_id(user), user_id=user.id, fact_id=fact_id
        )
    except cards.CardNotFound as exc:
        raise HTTPException(status_code=404, detail="That is not here.") from exc
    return {"forgotten": 1}


@router.get("/context")
async def conversation_context(
    user: User, thread_id: str | None = None
) -> dict[str, Any]:
    """What this conversation uses: the chip's line and each source."""
    return await context_control.describe(
        organization_id=_organization_id(user), user_id=user.id, thread_id=thread_id
    )


@router.put("/context")
async def choose_context(body: SourceChoice, user: User) -> dict[str, Any]:
    """Leave a source out of this conversation, or put it back. Only the
    person's own turns in this conversation change."""
    organization_id = _organization_id(user)
    try:
        await context_control.set_included(
            organization_id=organization_id,
            user_id=user.id,
            thread_id=body.thread_id,
            source=body.source,
            included=body.included,
        )
    except context_control.InvalidSource as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await context_control.describe(
        organization_id=organization_id, user_id=user.id, thread_id=body.thread_id
    )
