"""Tech help, one step at a time, with "did that work?".

A session walks one guide (services/care/guides.py). The screen shows one
step and two answers: Yes, it worked -> the next step; No -> one other
thing to try; No again -> stop, kindly, and (if the person shares help
requests with family) tell the family they would like a hand. Nothing here
asks for a password, and a guide never needs one but the home Wi-Fi's.

Every answer names the ``version`` of the step it answers, so a double tap
answers once.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from api.db import db_client
from api.db.care_models import CareHelpSessionModel
from api.services.care import TECH_HELP, CareError, NotFound, guides, on

ACTIVE = "active"
DONE = "done"
STUCK = "stuck"


class StaleAnswer(CareError):
    """The answer named a step that has already been answered."""


def enabled(organization_id: int | None = None) -> bool:
    return on(TECH_HELP, organization_id)


def topics() -> list[dict[str, str]]:
    return [{"slug": g.slug, "title": g.title} for g in guides.GUIDES]


def _view(row: Any, *, note: str | None = None, told: list[str] | None = None) -> dict:
    guide = guides.BY_SLUG[row.guide]
    total = len(guide.steps)
    step = guide.steps[min(row.step, total - 1)]
    showing_instead = row.state == ACTIVE and row.tries > 0 and step.instead
    return {
        "id": row.id,
        "guide": guide.slug,
        "title": guide.title,
        "state": row.state,
        "step_number": min(row.step, total - 1) + 1,
        "steps_total": total,
        "say": step.instead if showing_instead else step.say,
        "is_alternative": bool(showing_instead),
        "version": row.version,
        "note": note,
        "family_told": told or [],
    }


async def start(
    organization_id: int, user_id: int, *, guide: str | None = None, question: str = ""
) -> dict[str, Any]:
    """Begin a guide, by its slug or by what the person asked. When nothing
    fits, says so and offers the closest topics -- never a made-up answer."""
    chosen = guides.BY_SLUG.get(guide or "")
    if chosen is None:
        found = guides.match(question)
        if not found:
            return {
                "matched": False,
                "note": (
                    "I do not have steps for that yet. Here are the things I can "
                    "help with one step at a time."
                ),
                "topics": topics(),
            }
        chosen = found[0]
    async with db_client.async_session() as session:
        row = CareHelpSessionModel(
            organization_id=organization_id,
            user_id=user_id,
            guide=chosen.slug,
            step=0,
            tries=0,
            state=ACTIVE,
            version=0,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return {"matched": True, "session": _view(row)}


async def _own(session, organization_id: int, user_id: int, session_id: int) -> Any:
    row = (
        await session.execute(
            select(CareHelpSessionModel).where(
                CareHelpSessionModel.id == session_id,
                CareHelpSessionModel.organization_id == organization_id,
                CareHelpSessionModel.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None or row.guide not in guides.BY_SLUG:
        raise NotFound("That help is not here any more. Start again.")
    return row


async def get(organization_id: int, user_id: int, session_id: int) -> dict[str, Any]:
    async with db_client.async_session() as session:
        return _view(await _own(session, organization_id, user_id, session_id))


async def answer(
    organization_id: int,
    user_id: int,
    session_id: int,
    *,
    worked: bool,
    version: int,
) -> dict[str, Any]:
    async with db_client.async_session() as session:
        row = await _own(session, organization_id, user_id, session_id)
        if row.state != ACTIVE:
            return _view(row)
        guide = guides.BY_SLUG[row.guide]
        step = guide.steps[row.step]
        note = None
        values: dict[str, Any]
        if worked:
            if row.step + 1 >= len(guide.steps):
                values = {"state": DONE}
                note = "That is everything. Well done."
            else:
                values = {"step": row.step + 1, "tries": 0}
        elif row.tries == 0 and step.instead:
            values = {"tries": 1}
            note = "Let us try another way."
        else:
            values = {"state": STUCK}
        moved = (
            await session.execute(
                update(CareHelpSessionModel)
                .where(
                    CareHelpSessionModel.id == row.id,
                    CareHelpSessionModel.organization_id == organization_id,
                    CareHelpSessionModel.version == version,
                )
                .values(
                    **values,
                    version=CareHelpSessionModel.version + 1,
                    updated_at=datetime.now(UTC),
                )
                .returning(CareHelpSessionModel.id)
            )
        ).first()
        if moved is None:
            await session.rollback()
            raise StaleAnswer("That step was already answered.")
        await session.commit()
        row = await _own(session, organization_id, user_id, session_id)
        await session.refresh(row)
    told: list[str] = []
    if row.state == STUCK:
        told = await _tell_family(organization_id, user_id, row, guide)
        note = "Let us stop here; this one needs a hand. " + (
            f"I have let {', '.join(told)} know you would like help with it."
            if told
            else "Someone in your family could help, or ask me again later."
        )
    return _view(row, note=note, told=told)


async def _tell_family(
    organization_id: int, user_id: int, row: Any, guide: Any
) -> list[str]:
    """Tell the members who share help requests; return their names."""
    from api.services.care import circle

    if not circle.enabled(organization_id):
        return []
    person = await circle.person_name(organization_id, user_id)
    told = await circle.alert(
        organization_id,
        user_id,
        kind="help_needed",
        share="help_requests",
        title=f"{person} would like a hand with: {guide.title} (step {row.step + 1}).",
        subject_id=row.id,
    )
    if not told:
        return []
    circle_row = await circle.ensure_circle(organization_id, user_id)
    async with db_client.async_session() as session:
        return [
            m.name
            for m in await circle._members(session, circle_row.id, organization_id)
            if m.status == circle.ACTIVE and "help_requests" in (m.shares or [])
        ]
