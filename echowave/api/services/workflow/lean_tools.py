"""A quick turn does not need every tool.

Behind ``lean_tools``. Every Decibyl turn used to be offered every tool it
has: 17 of its own on a plain account and past 50 with the optional ones on,
plus the person's connected apps. That is 14,000 to 21,000 input tokens on
every turn, including "thanks" and "what time is it".

On a **quick** turn the model is offered:

* a core set of about twenty-five (:data:`CORE`, and :data:`MORE_TOOLS`);
* any tool the thread has already used -- the model that proposed a card two
  messages ago may need the same verb to follow it up;
* any tool the message points at, by its name or by what it is about
  (``services/routing/tool_intents.py``);
* every connected-app and outside tool, unchanged. Those are the person's own
  connections, already one line each, and a quick "what's in my inbox" is
  exactly the turn that wants one.

**Nothing is dropped silently.** Silent Absence (api/AGENTS.md) is the shape
this change could most easily have: a rule that removes things, where being
wrong produces nothing rather than an error. So:

* the tools left out are *named* to the model, in the description of the one
  extra tool it is given, :data:`MORE_TOOLS`, which loads the rest in the next
  round -- a tool that is absent is a tool the model can still ask for;
* a tool the person named, or asked for in plain words, is offered;
* ``test_token_cuts`` fails when a tool the product offers is in neither
  :data:`CORE` nor the intent table, so a new tool is a decision on the pull
  request that adds it rather than a gap found by a customer;
* off, :func:`select` is never called and the list is what it was.

**Why the list must hold still while the cache is warm.** The tool list is the
first thing in the request, ahead of the system prompt and the thread, so a
list that differs from the last turn's makes everything after it a cache miss,
and a cache write costs a quarter more than reading would have saved. Measured
with ``scripts/replay_token_cuts.py``: when a thread's turns are minutes
apart, a list that changed message to message cost *more* than sending
everything; when the cache is cold, the smaller list is a plain saving. So a
thread's list only ever grows while the cache is warm (:data:`WARM_SECONDS`),
and a thread whose last reply was a full-list turn stays on the full list
until the cache has gone cold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterable

from api.services.routing import tool_intents

#: The "more tools" tool: no arguments, loads the full list for the next
#: round. Its description names what was left out.
MORE_TOOLS = "more_tools"

#: How long after a reply the cache is treated as still holding it: Claude's
#: five minutes, with a margin for the call itself.
WARM_SECONDS = 270

#: Decibyl's own tools offered on every quick turn. Chosen as the ones a
#: quick message most often needs, or that cannot be asked for by a word the
#: intent table would catch: the five that end in a card, the board, the
#: document verbs, memory and the two web verbs. A name here that is not
#: offered to this account (its feature is off) is simply absent.
CORE: tuple[str, ...] = (
    "propose_action",
    "propose_edit",
    "test_bot",
    "check_bot",
    "create_task",
    "read_board",
    "schedule_routine",
    "offer_connector",
    "build_bot_from_spec",
    "find_document",
    "send_document",
    "search_files",
    "confirm_document",
    "file_document",
    "recall",
    "correct_memory",
    "memory_messages",
    "web_search",
    "web_fetch",
    "search_records",
    "read_document",
    "track_commitment",
    "who_owes_me",
    "call_for_me",
)

#: Names that are never deferred: the loader for connected apps, and the
#: prefixes of the person's own connections (``connected_tools.PREFIX``,
#: ``reach.outside_tools.PREFIX``).
_ALWAYS_PREFIXES = ("app_", "ext_")
_ALWAYS_NAMES = frozenset({"load_tool", MORE_TOOLS})


def is_always_offered(name: str) -> bool:
    return name in _ALWAYS_NAMES or str(name).startswith(_ALWAYS_PREFIXES)


@dataclass(frozen=True)
class Selection:
    """The tools to offer this round, and what was left out."""

    tools: list[dict[str, Any]]
    #: Names left out, sorted. Empty means nothing was left out, so no
    #: ``more_tools`` was added either.
    deferred: tuple[str, ...] = ()
    #: Tools offered beyond the core and the person's own connections -- used
    #: before, or pointed at by the message. Written on the reply so the next
    #: turn, if the cache is still warm, offers the same list.
    extras: tuple[str, ...] = ()

    @property
    def narrowed(self) -> bool:
        return bool(self.deferred)


def more_tools_schema(deferred: Iterable[str]) -> dict[str, Any]:
    names = ", ".join(sorted(deferred))
    return {
        "name": MORE_TOOLS,
        "description": (
            "Load the tools that were left out of this quick reply. Call it "
            "when the person asks for something none of your tools does, "
            "before you say you cannot do it: you have more, and they are "
            f"loaded for your next step. Not loaded now: {names}."
        ),
        "parameters": {"type": "object", "properties": {}},
    }


def select(
    tools: list[dict[str, Any]],
    *,
    text: str,
    used: Iterable[str] = (),
    warm_extras: Iterable[str] = (),
) -> Selection:
    """The lean list for a quick turn.

    ``tools`` is the full list for this account. ``used`` are tools the
    thread has called; ``warm_extras`` are those the last reply offered, when
    the cache still holds it. The result keeps the full list's order, so a
    stable set of tools is a stable prefix for the cache.
    """
    names = [str(t.get("name") or "") for t in tools]
    wanted = (
        set(CORE) | set(used) | set(warm_extras) | tool_intents.asked_for(text, names)
    )
    kept: list[dict[str, Any]] = []
    deferred: list[str] = []
    extras: list[str] = []
    for tool, name in zip(tools, names):
        if is_always_offered(name) or name in wanted:
            kept.append(tool)
            if name in wanted and name not in CORE and not is_always_offered(name):
                extras.append(name)
        else:
            deferred.append(name)
    if not deferred:
        return Selection(tools=list(tools), extras=tuple(extras))
    kept.append(more_tools_schema(deferred))
    return Selection(tools=kept, deferred=tuple(sorted(deferred)), extras=tuple(extras))


def used_in(messages: Iterable[dict[str, Any]]) -> set[str]:
    """The tool names a conversation's assistant turns have called."""
    names: set[str] = set()
    for entry in messages:
        if entry.get("role") != "assistant":
            continue
        for call in entry.get("tool_calls") or []:
            name = str(call.get("name") or "")
            if name:
                names.add(name)
    return names


@dataclass
class Carried:
    """What a thread's earlier replies hand to the next turn."""

    #: Every tool the thread has called, ever. Always offered again.
    used: set[str] = field(default_factory=set)
    #: The extras the last reply was offered, while the cache holds it.
    warm_extras: set[str] = field(default_factory=set)
    #: The last reply was offered the full list, and the cache holds it.
    warm_full: bool = False


def _now() -> datetime:
    """The clock, in one place so a replay can run on its own."""
    return datetime.now(UTC)


def _as_utc(moment: Any) -> datetime | None:
    if not isinstance(moment, datetime):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def carried_from(rows: Iterable[Any], now: datetime | None = None) -> Carried:
    """Read a thread's reply rows, newest first.

    The reply payloads carry ``tools_used`` (what the turn called),
    ``tools_kept`` (the extras it was offered) and ``tools_full`` (it was
    offered everything); written only while ``lean_tools`` is on. Used tools
    are remembered for good. What was *offered* only counts while the cache
    can still hold it (:data:`WARM_SECONDS`), because that is the only time
    keeping the list the same saves anything.
    """
    now = now or _now()
    carried = Carried()
    newest_seen = False
    for row in rows:
        payload = getattr(row, "payload", None)
        if not isinstance(payload, dict):
            continue
        used = payload.get("tools_used")
        if isinstance(used, list):
            carried.used.update(str(n) for n in used if n)
        if newest_seen or not (
            "tools_kept" in payload
            or "tools_full" in payload
            or "tools_used" in payload
        ):
            continue
        newest_seen = True
        at = _as_utc(getattr(row, "at", None))
        if at is None or (now - at).total_seconds() > WARM_SECONDS:
            continue
        kept = payload.get("tools_kept")
        if isinstance(kept, list):
            carried.warm_extras.update(str(n) for n in kept if n)
        carried.warm_full = bool(payload.get("tools_full"))
    return carried


__all__ = [
    "CORE",
    "MORE_TOOLS",
    "WARM_SECONDS",
    "Carried",
    "Selection",
    "carried_from",
    "is_always_offered",
    "more_tools_schema",
    "select",
    "used_in",
]
