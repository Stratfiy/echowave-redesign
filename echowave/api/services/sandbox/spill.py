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


def preview(data: Any, *, stored_as: str) -> dict[str, Any]:
    rows = _rows(data)
    out: dict[str, Any] = {
        "spilled": True,
        "stored_as": stored_as,
        "note": (
            "This response was too large to show in full. It is stored on the "
            "run; to work through all of it, run a script and read it with "
            "tools.spilled(stored_as) or call the tool again from inside."
        ),
    }
    if rows is not None:
        items, count = rows
        out["rows"] = count
        first: list[Any] = []
        spent = 0
        for item in items[:PREVIEW_ITEMS]:
            text = json.dumps(item, default=str)
            if spent + len(text) > PREVIEW_CHARS:
                # A single row can be a document. The preview stays small
                # whatever the rows are; the store has them whole.
                first.append(text[: max(0, PREVIEW_CHARS - spent)] + " …")
                break
            first.append(json.loads(text))
            spent += len(text)
        out["first"] = first
    else:
        text = json.dumps(data, default=str)
        out["head"] = text[:PREVIEW_CHARS] + (" …" if len(text) > PREVIEW_CHARS else "")
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
    shown = preview(data, stored_as=key)
    if not key:
        shown["note"] = (
            "This response was too large to show in full and could not be stored."
        )
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
