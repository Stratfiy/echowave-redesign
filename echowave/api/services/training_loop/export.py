"""A workspace's training data, as JSONL it can take away.

Three shapes, one workspace per file:

``sft``
    One line per suggestion the owner ended up with:
    ``{"prompt", "completion", "meta"}``. The completion is the owner's final
    version -- what they approved, or what they changed it to -- never the
    model's first attempt when the owner replaced it.

``preference``
    One line per pair: ``{"prompt", "chosen", "rejected", "meta"}``. The
    chosen text is the owner's final version (approved or owner-edited); the
    rejected text is what the model offered instead:

    * when the owner edited a suggestion, the model's original;
    * when the owner approved it as it was, the latest earlier suggestion for
      the same situation (same agent, same ``group_key``) that the owner
      rejected or undid, if there is one. Each rejected suggestion is used
      once.

    A suggestion approved with nothing to set against it makes no pair.

``kto``
    One line per *output*, not per pair: ``{"prompt", "completion", "label",
    "meta"}`` with ``label`` true for an output the owner or a user wanted and
    false for one they did not. It needs no pairing, which is why a thumb is
    enough on its own:

    * desirable: the owner's final version of a suggestion (approved, or
      edited), and a reply that got a "Yes";
    * undesirable: a suggestion that was rejected or undone, the model's
      original where the owner edited it, a reply that got "Not quite", and
      an agent's answer in a failed eval.

    A row needs its prompt, so a thumb kept before the prompt was recorded
    with it (``reply_thumb`` now does) has nothing to make a line of. The
    same output with the same label twice is one line.

What is left out, and why:

* Rows without words (``declined`` rows from before "Stop collecting" wrote
  nothing, or events that carry none: a thumb, an escalation with no model
  output).
* Archived rows (a workspace's "Stop and delete"): never exported, by
  anyone, staff included.
* Anything the owner undid or rejected *last*: the final word on a card is
  the one that counts, so an approved-then-undone suggestion is not a good
  example.
* Everything is read through one ``organization_id``. This module has no way
  to ask for more than one workspace.

Every line's ``meta.organization_id`` names its workspace, so a file that
was ever mixed with another's can be told apart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from api.db import db_client
from api.db.models import AdminActionLogModel
from api.services import training_loop as loop
from api.services.workflow import audit_log

SFT = "sft"
PREFERENCE = "preference"
KTO = "kto"
SHAPES = (SFT, PREFERENCE, KTO)

#: The most events one export reads. Past it the file says so
#: (``Export.truncated``) rather than silently stopping.
MAX_EVENTS = 50_000

#: The events that decide what became of a suggestion.
_DECISIVE = (*loop.OWNER_VERSION_TYPES, *loop.TURNED_DOWN_TYPES)


@dataclass
class Export:
    shape: str
    lines: list[str] = field(default_factory=list)
    truncated: bool = False

    def body(self) -> str:
        return "".join(line + "\n" for line in self.lines)


def _meta(row: Any) -> dict[str, Any]:
    return {
        "organization_id": row.organization_id,
        "agent_id": row.workflow_id,
        "scope": row.scope,
        "event_id": row.id,
        "event_type": row.event_type,
        "source": row.source,
        "model": row.model,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _line(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _final_choices(rows: list[Any]) -> list[Any]:
    """Per suggestion, the last decisive event, if it is one where the owner
    ended up with a version of their own that has words to train on."""
    last: dict[Any, Any] = {}
    for row in rows:  # ordered by id
        if row.event_type in _DECISIVE:
            last[row.input_ref or ("row", row.id)] = row
    return [
        row
        for row in last.values()
        if row.event_type in loop.OWNER_VERSION_TYPES
        and row.input_text
        and row.owner_final
    ]


def _pairs(rows: list[Any], chosen: list[Any]) -> list[dict[str, Any]]:
    turned_down = [
        r
        for r in rows
        if r.event_type in loop.TURNED_DOWN_TYPES and r.model_output and r.group_key
    ]
    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for row in chosen:
        rejected = None
        if row.model_output and row.model_output != row.owner_final:
            rejected = row.model_output
        else:
            for earlier in reversed(turned_down):
                if (
                    earlier.id < row.id
                    and earlier.id not in used
                    and earlier.workflow_id == row.workflow_id
                    and earlier.group_key == row.group_key
                    and earlier.input_ref != row.input_ref
                    and earlier.model_output != row.owner_final
                ):
                    rejected = earlier.model_output
                    used.add(earlier.id)
                    break
        if rejected:
            out.append(
                {
                    "prompt": row.input_text,
                    "chosen": row.owner_final,
                    "rejected": rejected,
                    "meta": _meta(row),
                }
            )
    return out


def _kto(rows: list[Any]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, bool]] = set()
    out: list[dict[str, Any]] = []

    def add(row: Any, completion: str | None, label: bool) -> None:
        if not row.input_text or not completion:
            return
        key = (row.input_text, completion, label)
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "prompt": row.input_text,
                "completion": completion,
                "label": label,
                "meta": _meta(row),
            }
        )

    last: dict[Any, Any] = {}
    for row in rows:  # ordered by id
        if row.event_type in _DECISIVE:
            last[row.input_ref or ("row", row.id)] = row
    for row in last.values():
        if row.event_type in loop.OWNER_VERSION_TYPES:
            add(row, row.owner_final, True)
            if row.model_output != row.owner_final:
                add(row, row.model_output, False)
        else:
            add(row, row.model_output, False)
    for row in rows:
        if row.event_type == loop.THUMBS_UP:
            add(row, row.model_output, True)
        elif row.event_type in (loop.THUMBS_DOWN, loop.EVAL_FAIL):
            add(row, row.model_output, False)
    return out


async def build(
    *,
    organization_id: int,
    shape: str,
    workflow_id: int | None = None,
    limit: int = MAX_EVENTS,
) -> Export:
    """This workspace's rows, in one of the two shapes."""
    if shape not in SHAPES:
        raise ValueError(f"shape must be one of {', '.join(SHAPES)}")
    rows = await db_client.list_learning_events_for_export(
        organization_id=organization_id, workflow_id=workflow_id, limit=limit + 1
    )
    export = Export(shape=shape, truncated=len(rows) > limit)
    rows = rows[:limit]
    if shape == KTO:
        export.lines = [_line(x) for x in _kto(rows)]
        return export
    chosen = _final_choices(rows)
    if shape == SFT:
        export.lines = [
            _line(
                {
                    "prompt": row.input_text,
                    "completion": row.owner_final,
                    "meta": _meta(row),
                }
            )
            for row in chosen
        ]
    else:
        export.lines = [_line(pair) for pair in _pairs(rows, chosen)]
    return export


async def note_staff_export(
    *, organization_id: int, staff: Any, shape: str, rows: int
) -> None:
    """A staff member took a workspace's training data: written to the staff
    audit log, and to the workspace's own so its owners can see it. Never
    raises (``audit_log.record`` does not, and the staff row is best effort):
    the export is the thing asked for."""
    note = f"training data export; shape={shape}; rows={rows}"
    try:
        async with db_client.async_session() as session:
            session.add(
                AdminActionLogModel(
                    actor_user_id=staff.id,
                    action="training_data_export",
                    target_organization_id=organization_id,
                    note=note[:500],
                )
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        from loguru import logger

        logger.warning("training_loop: staff export not logged: {}", exc)
    await audit_log.record(
        organization_id,
        action="training_data_export",
        subject_kind="workspace",
        subject_id=organization_id,
        subject="Training data export by Decibyl staff",
        actor_user_id=getattr(staff, "id", None),
        actor="Decibyl staff",
        note=note,
    )
