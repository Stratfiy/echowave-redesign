"""Large tool responses stay out of the prompt (Step 20, first half).

A connected app can answer with a 400-row sheet. Handed to the model whole,
that is 20,000 tokens on every turn that follows, most of it never read. So
a response past :data:`SPILL_TOKENS` is written to the run's own store and
the model gets a preview: the first rows, the count, where the rest is, and
the one thing it can do about it -- run a script, which reads the whole
result from inside the box.

Nothing is lost. The full response is on the run under a name the preview
carries, and ``tools.call`` inside a script returns the same call's result
unspilled.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

#: About 6,000 tokens, the line the plan draws.
SPILL_TOKENS = 6_000
#: What the preview keeps of the data, in characters.
PREVIEW_CHARS = 2_000
#: How many items of a list the preview shows.
PREVIEW_ITEMS = 5
#: How much of one field's text the preview keeps. Long enough to recognise
#: a subject or the opening of a body, short enough that a row full of prose
#: still leaves room for the fields beside it.
FIELD_CHARS = 160


def _tokens(text: str) -> int:
    try:
        from api.services.knowledge_base.chunking import count_tokens

        return count_tokens(text)
    except Exception:  # noqa: BLE001 - the heuristic is fine here
        return max(1, len(text) // 4)


def is_large(result: Any) -> bool:
    data = result.get("data") if isinstance(result, dict) else result
    if data is None:
        return False
    text = json.dumps(data, default=str)
    return len(text) > SPILL_TOKENS * 3 and _tokens(text) > SPILL_TOKENS


def _rows(data: Any) -> tuple[list[Any], int] | None:
    """The list a response is really about, and its length, if it has one."""
    if isinstance(data, list):
        return data, len(data)
    if isinstance(data, dict):
        best: tuple[list[Any], int] | None = None
        for value in data.values():
            if isinstance(value, list) and (best is None or len(value) > best[1]):
                best = (value, len(value))
        return best
    return None


#: What any caller can do about a spill, script or no script. Named because
#: nothing named it: the bot that hit this was holding
#: GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID and the message_id it needed, and gave
#: up instead of asking for that one message.
_NARROW = (
    "To get what you need: ask for fewer items (a smaller limit, a tighter "
    "query), or fetch one item on its own by the id shown above."
)


def _note(*, stored_as: str, can_run_scripts: bool) -> str:
    """The advice, true for this caller.

    ``can_run_scripts`` defaults to False at every entry point, because the
    caller that has not been taught to pass it must not be the caller that
    advertises a tool it cannot reach. Unknown means no, the direction
    ``is_read`` and ``writes_allowed`` both take.
    """
    if not stored_as:
        # The store failed, so there is no copy to go back for. That makes
        # naming the reachable path more important, not less: without it the
        # model is told only that something went wrong and nothing it can do.
        return (
            "This response was too large to show in full and could not be "
            "stored. " + _NARROW
        )
    head = "This response was too large to show in full. It is stored on the run; "
    if can_run_scripts:
        return (
            head
            + "to work through all of it, run a script and read it with "
            + "tools.spilled(stored_as) or call the tool again from inside. "
            + _NARROW
        )
    return head + "you cannot read the stored copy directly. " + _NARROW


def _slim(value: Any, *, depth: int = 0) -> Any:
    """One row with its prose cut and its identifiers intact.

    Truncating a row as raw JSON -- the first N characters of the serialised
    thing -- keeps whatever happens to come first and loses the rest, which
    makes key order decide what the model can do next. For a Gmail message
    the body comes first and the sender does not survive, so the tool that
    needs an address becomes uncallable and the one that needs only a thread
    id does not. That is how a send got chosen over a draft.

    So every key stays and only long text is shortened. Identifiers are
    short by nature; it is prose that makes a response too big for a prompt,
    and prose is what a preview is for cutting.
    """
    if isinstance(value, str):
        return value if len(value) <= FIELD_CHARS else value[:FIELD_CHARS] + " …"
    if isinstance(value, dict):
        if depth >= 2:
            return {"…": f"{len(value)} more fields"}
        return {k: _slim(v, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        if depth >= 2:
            return [f"{len(value)} items"]
        return [_slim(v, depth=depth + 1) for v in value[:PREVIEW_ITEMS]]
    return value


def preview(
    data: Any, *, stored_as: str, can_run_scripts: bool = False
) -> dict[str, Any]:
    rows = _rows(data)
    out: dict[str, Any] = {
        "spilled": True,
        "stored_as": stored_as,
        "note": _note(stored_as=stored_as, can_run_scripts=can_run_scripts),
    }
    if rows is not None:
        items, count = rows
        out["rows"] = count
        # Slimmed, not truncated: every row keeps its keys and loses only
        # the length of its text, so the fields a later call needs are still
        # there whatever order they were serialised in.
        out["first"] = [_slim(item) for item in items[:PREVIEW_ITEMS]]
        # A record that carries a list is not only its list. Seen live: one
        # Gmail message, spilled, previewed as its five header rows and
        # nothing else, so the thread id and sender beside them were gone
        # and the bot said it could not reply. The record's own fields ride
        # along, with its lists summarised rather than repeated.
        if isinstance(data, dict):
            out["fields"] = {
                k: (f"{len(v)} items" if isinstance(v, list) else _slim(v))
                for k, v in data.items()
            }
    else:
        slimmed = _slim(data)
        text = json.dumps(slimmed, default=str)
        out["head"] = (
            slimmed if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + " …"
        )
    return out


def store_key(organization_id: int, run_id: int | None, name: str, call_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:60]
    return f"sandbox/{organization_id}/{run_id or 'thread'}/{safe}-{call_id}.json"


async def spill_if_large(
    result: Any,
    *,
    organization_id: int,
    run_id: int | None,
    name: str,
    call_id: str,
    can_run_scripts: bool = False,
) -> Any:
    """The result as the model should see it: itself, or a preview with
    the whole thing stored. Never raises; a store that fails hands the
    model a bounded head instead."""
    if not is_large(result):
        return result
    data = result.get("data") if isinstance(result, dict) else result
    key = store_key(organization_id, run_id, name, call_id)
    try:
        from api.services.storage import get_storage

        ok = await get_storage().acreate_file_from_bytes(
            key, json.dumps(data, default=str).encode("utf-8")
        )
        if not ok:
            raise RuntimeError("storage refused the file")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not store a large tool response ({}): {}", key, exc)
        key = ""
    shown = preview(data, stored_as=key, can_run_scripts=can_run_scripts)
    if not key:
        shown.pop("stored_as", None)
    if isinstance(result, dict):
        return {**result, "data": shown}
    return shown


async def read_spilled(organization_id: int, stored_as: str) -> Any:
    """The whole stored response, for a script. Scoped to the organisation
    by the key's own prefix; a key from elsewhere reads nothing."""
    if not stored_as.startswith(f"sandbox/{organization_id}/"):
        return {"status": "error", "error": "no such stored response here"}
    from api.services.storage import get_storage

    raw = await get_storage().aread_bytes(stored_as, 50 * 1024 * 1024)
    if not raw:
        return {"status": "error", "error": "the stored response is gone"}
    try:
        return {"status": "success", "data": json.loads(raw.decode("utf-8"))}
    except ValueError:
        return {"status": "error", "error": "the stored response could not be read"}


__all__ = [
    "PREVIEW_ITEMS",
    "SPILL_TOKENS",
    "is_large",
    "preview",
    "read_spilled",
    "spill_if_large",
    "store_key",
]
