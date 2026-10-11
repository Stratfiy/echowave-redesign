"""What a person's line to Decibyl means for their own memory, before any
model sees it.

Called by ``decibyl.ask`` once the line is recorded. Two things, both
deterministic:

* "What do you know about me?" -- the whole line -- puts the card on the
  thread and the turn ends there: there is nothing for a model to add, and
  a model listing it from a prompt could list it wrong.
* A preference said outright ("Tamil for calls, English for email") is
  saved now, in the person's own store, and a card shows exactly what was
  kept with Correct and Forget. The turn then goes on to the model, which
  reads the preference from the store like every other turn.

Memory's own promises hold: in a temporary conversation, or with "Remember
things from my conversations" off, nothing is saved (services/settings/
temporary.py), and the model is already told to say so.
"""

from __future__ import annotations

from loguru import logger

from api.services import personal
from api.services.personal import capture, cards, preferences


async def on_line(
    *,
    organization_id: int,
    user_id: int | None,
    thread_id: str | None,
    text: str,
    line_id: int | None,
) -> bool:
    """Returns True when the line was answered here and needs no reply."""
    if not user_id or not personal.enabled(organization_id):
        return False
    if capture.asks_about_me(text):
        await cards.show_about_me(
            organization_id=organization_id, user_id=user_id, thread_id=thread_id
        )
        return True
    reading = capture.read(text)
    if not reading.found and not reading.refused:
        return False
    from api.services.settings import temporary

    try:
        paused = await temporary.reason_for_turn(organization_id, user_id, thread_id)
    except Exception as exc:  # noqa: BLE001 - when unsure, keep nothing
        logger.warning("Memory choice for {} not read: {}", user_id, exc)
        paused = "unknown"
    if paused:
        return False
    saved: list[int] = []
    for candidate in reading.found:
        try:
            done = await preferences.save(
                user_id=user_id,
                organization_id=organization_id,
                candidate=candidate,
                source_kind=preferences.FROM_MESSAGE,
                source_event_id=line_id,
                source_thread_id=thread_id,
            )
        except Exception as exc:  # noqa: BLE001 - said on the card, not lost
            logger.error("Preference not saved for {}: {}", user_id, exc)
            reading.refused.append(f"“{candidate.label}” could not be saved just now.")
            continue
        saved.append(int(done.preference["id"]))
    await cards.show_saved(
        organization_id=organization_id,
        user_id=user_id,
        thread_id=thread_id,
        ids=saved,
        refused=reading.refused,
    )
    return False
