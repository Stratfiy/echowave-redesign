"""What changed in a workspace's Files since a client last looked.

The listing a desktop or mobile client keeps a local copy in step with:
``GET /api/v1/knowledge-base/changes?cursor=...``. The first call has no
cursor and returns everything; every answer carries the cursor to send next
time. Files and folders that were deleted come back marked ``deleted`` --
a client that only ever saw additions could never remove anything.

Two rules make it safe to apply blindly:

* **Nothing newer than a few seconds is returned.** A transaction stamps
  ``updated_at`` before it commits, so a row stamped a moment ago may not be
  visible yet while a later one already is. Stopping ``SETTLE`` short of now
  means the cursor never moves past a change that has yet to appear; it is
  picked up on the next call instead.
* **A row may come back twice, never zero times.** The cursor is a
  position ``(updated_at, id)`` so a page boundary inside many rows stamped
  in the same instant (a folder deleted with its contents) neither repeats
  forever nor skips; folders are re-sent from the cursor's instant. Every
  entry is the whole current state of the thing, so applying one twice
  changes nothing.

Not built here: a sync client. This is the endpoint one would use.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from api.db import db_client
from api.services.knowledge_base import folders as file_folders

#: How far behind now the listing stops (see the module docstring).
SETTLE = timedelta(seconds=10)
MAX_PAGE = 500


class BadCursor(ValueError):
    """The cursor was not one this listing issued."""


def encode(at: datetime, after_id: int) -> str:
    return f"{at.astimezone(UTC).isoformat()}|{int(after_id)}"


def decode(cursor: str | None) -> tuple[datetime | None, int]:
    if not cursor:
        return None, 0
    try:
        at, _, after = cursor.partition("|")
        moment = datetime.fromisoformat(at)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment, int(after or 0)
    except (TypeError, ValueError) as exc:
        raise BadCursor(
            "That cursor was not issued by this listing; start again without one."
        ) from exc


async def changes(
    organization_id: int,
    *,
    cursor: str | None,
    limit: int = MAX_PAGE,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Files and folders changed since ``cursor``: rows, deletions, the next
    cursor, and whether there is more to fetch straight away."""
    since, after_id = decode(cursor)
    limit = max(1, min(int(limit), MAX_PAGE))
    until = (now or datetime.now(UTC)) - SETTLE
    if since is not None and since > until:
        # Asked again within the settle window: nothing new can be told yet.
        return {
            "files": [],
            "folders": [],
            "cursor": encode(since, after_id),
            "has_more": False,
        }

    documents = await db_client.documents_changed_since(
        organization_id, since, after_id=after_id, until=until, limit=limit + 1
    )
    has_more = len(documents) > limit
    documents = documents[:limit]
    changed_folders = await db_client.file_folders_changed_since(
        organization_id, since, until=until
    )
    paths = await file_folders.folder_paths(organization_id)

    if has_more:
        last = documents[-1]
        next_cursor = encode(last.updated_at, last.id)
    else:
        # Everything up to ``until`` has been sent; the next call starts
        # there. Any file stamped exactly then is sent again, harmlessly.
        next_cursor = encode(until, 0)

    return {
        "files": [
            {
                "document": document,
                "folder_path": paths.get(document.file_folder_id, "")
                if document.file_folder_id is not None
                else "",
                "deleted": not document.is_active,
            }
            for document in documents
        ],
        "folders": [
            {
                "id": folder.id,
                "folder_uuid": folder.folder_uuid,
                "name": folder.name,
                "parent_id": folder.parent_id,
                "path": paths.get(folder.id, ""),
                "deleted": folder.deleted_at is not None,
                "updated_at": folder.updated_at,
            }
            for folder in changed_folders
        ],
        "cursor": next_cursor,
        "has_more": has_more,
    }
