"""One Decibyl turn, run as a helper.

``decibyl._answer`` asks three things of this module:

* ``instructions(helper)`` -- the helper's section of the system prompt;
* ``narrow(helper, tools, connected)`` -- the tools it may hold this turn:
  the intersection of what Decibyl holds and the helper's allowlist, so a
  helper can only ever have fewer tools than Automatic, never more;
* ``refusal(helper, call)`` -- whether a call it makes anyway is outside
  that list (or a ``propose_action`` kind outside its own), answered as a
  refusal the model can read, never run.

The helper in force is also kept in a context variable for the turn, so a
card it proposes carries which helper asked (``actions.propose`` stamps it)
-- organisation, member, session and helper on every action (handoff 6).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from api.services.helpers import catalogue

_current: ContextVar[str | None] = ContextVar("current_helper", default=None)


@contextmanager
def running_as(helper: str | None) -> Iterator[None]:
    token = _current.set(helper if catalogue.get(helper) else None)
    try:
        yield
    finally:
        _current.reset(token)


def current() -> str | None:
    return _current.get()


def instructions(helper: str | None) -> str:
    h = catalogue.get(helper)
    if h is None:
        return ""
    return (
        f"\n## Working as {h.name}\n{h.instructions}\n"
        "Stay within this helper's job and tools. If the person asks for "
        "something outside it, say which helper or Automatic would do it, "
        "in one line.\n"
    )


async def context(helper: str | None, organization_id: int, user_id: int | None) -> str:
    """What a helper reads before it answers, from the stream that owns it.

    The Learning Guide reads the person's learning record through the
    `learning` stream's own seam (``services/learning/guide.py``): goals,
    marked practice, what needs another attempt, reviews due. It never keeps
    a record of its own and never marks practice; ``core`` does that.
    """
    if helper != catalogue.LEARNING_GUIDE or not user_id:
        return ""
    from api.services.learning import guide

    try:
        return await guide.context_for(organization_id, int(user_id))
    except Exception as exc:  # noqa: BLE001 - the lesson goes on without it
        from loguru import logger

        logger.warning("Learning Guide could not read the learning record: {}", exc)
        return ""


def _app_name(schema: dict[str, Any]) -> str:
    return str(schema.get("name") or "")


def narrow(
    helper: str | None,
    tools: list[dict[str, Any]],
    *,
    app_toolkits: dict[str, str | None],
) -> list[dict[str, Any]]:
    """``tools`` as the helper may hold them. ``app_toolkits`` maps each
    connected-app function name to its toolkit."""
    h = catalogue.get(helper)
    if h is None:
        return tools
    from api.services.workflow import connected_tools

    kept = []
    for schema in tools:
        name = _app_name(schema)
        if name.startswith(connected_tools.PREFIX):
            if app_toolkits.get(name) in h.apps:
                kept.append(schema)
        elif name in h.tools:
            kept.append(schema)
    return kept


def refusal(
    helper: str | None,
    call: Any,
    *,
    app_toolkits: dict[str, str | None],
) -> dict[str, Any] | None:
    h = catalogue.get(helper)
    if h is None:
        return None
    from api.services.workflow import connected_tools

    name = str(getattr(call, "name", "") or "")
    if name.startswith(connected_tools.PREFIX):
        allowed = app_toolkits.get(name) in h.apps
    else:
        allowed = name in h.tools
    if allowed and name == catalogue.PROPOSE_ACTION:
        action = str((getattr(call, "arguments", None) or {}).get("action") or "")
        allowed = action in h.actions
    if allowed:
        return None
    return {
        "status": "refused",
        "reason": (
            f"{h.name} cannot use {name}. Say what you can do as {h.name}, or "
            "that Automatic can do the rest."
        ),
    }
