"""Give an already-connected app the tools the ranking would pick today.

`ensure_tools` chooses the dozen actions an app brings with it. Until the
ranking landed it took the vendor's order, which is **alphabetical**, so an
account that connected Gmail before then holds:

    ADD_LABEL, BATCH_DELETE, BATCH_MODIFY, CREATE_DRAFT, CREATE_FILTER,
    CREATE_LABEL, DELETE_DRAFT, DELETE_FILTER, DELETE_LABEL,
    DELETE_MESSAGE, DELETE_THREAD, FETCH_EMAILS

Seven ways to delete mail, one way to read it, no way to send one.

The ranking fixed which twelve a *new* connection gets. It does not reach a
connection that already exists, and nothing else does either: the only thing
that enqueues a sync asks whether an app has **no** rows, so an app with a
bad twelve is indistinguishable from an app with a good twelve and is never
looked at again. That was wrong in the pull request that shipped the
ranking, which said existing accounts would gain the tools "on their next
sync". For them there is no next sync. This is it.

A script rather than a permanent staleness check, because the gap is
finite: every account that exists now connected its apps under the old
ranking, and every connection made after the deploy already gets the new
one. One run closes it. A runtime check would instead ask the vendor about
every connected app on every catalogue read, forever, to catch a case that
stops occurring.

    python -m scripts.resync_connector_tools                 # show what is missing
    python -m scripts.resync_connector_tools --confirm       # create it
    python -m scripts.resync_connector_tools --confirm --organization-id 3

Additive, like `ensure_tools` itself: rows are matched on action slug and
only what is absent is created. Nothing is renamed, retired or removed, so
a tool somebody attached to a bot by hand keeps working and an account that
already holds the right twelve gains nothing. Safe to run twice.

The cost of that additiveness, stated plainly: an account keeps the
destructive rows the alphabet gave it *as well as* the ones it should have
had. Taking them away is a different operation -- somebody may have pointed
a bot at one -- and is not done here.

Requires DATABASE_URL and the Composio credentials, like every repo-owned
script:

    set -a && source api/.env && set +a && python -m scripts.resync_connector_tools

In Docker, where there is no checkout to run from:

    docker compose exec api python -m scripts.resync_connector_tools --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from api.db import db_client
from api.db.models import ToolModel
from api.enums import ToolCategory, ToolStatus
from api.services.integrations.composio import tool_sync
from api.services.integrations.composio.client import toolkit_actions
from api.services.workflow import connected_tools
from api.tasks.connector_tools import sync_missing_tools


async def _connected_by_organisation(
    *, organization_id: int | None
) -> dict[int, set[str]]:
    """Every organisation's Composio toolkits that already have rows.

    The population is taken from the rows themselves rather than from the
    vendor's connected-accounts list: an app with no rows at all is already
    handled by the existing enqueue path, and this is only about apps whose
    rows exist and are the wrong ones.
    """
    async with db_client.async_session() as session:
        query = select(ToolModel).where(
            ToolModel.category == ToolCategory.COMPOSIO.value,
            ToolModel.status == ToolStatus.ACTIVE.value,
        )
        if organization_id is not None:
            query = query.where(ToolModel.organization_id == organization_id)
        rows = list((await session.execute(query)).scalars().all())

    found: dict[int, set[str]] = {}
    for row in rows:
        toolkit = connected_tools.toolkit_of(row)
        if toolkit is None:
            continue
        found.setdefault(int(row.organization_id), set()).add(toolkit)
    return found


async def _would_create(organization_id: int, app: str) -> list[str] | None:
    """The action slugs a sync would add for one app, without adding them.

    ``None`` means the vendor could not be read -- reported as such rather
    than as "nothing missing", which is the same word for a very different
    thing.
    """
    try:
        actions = await toolkit_actions(app, limit=tool_sync.ACTIONS_CONSIDERED)
    except Exception as exc:  # noqa: BLE001 - one app, not the run
        print(f"    ! could not read {app} from the vendor: {exc}")
        return None
    if actions is None:
        return None
    have = await tool_sync.existing_slugs(organization_id, app)
    wanted = tool_sync.most_useful(actions, tool_sync.MAX_PER_APP)
    return [
        slug
        for action in wanted
        if (slug := str(action.get("slug") or "").strip()) and slug not in have
    ]


async def _report(found: dict[int, set[str]]) -> int:
    """Print what each organisation is missing. Returns the total."""
    total = 0
    for organization_id in sorted(found):
        apps = sorted(found[organization_id])
        print(f"  org {organization_id}: {len(apps)} app(s) with rows")
        for app in apps:
            missing = await _would_create(organization_id, app)
            if missing is None:
                continue
            if not missing:
                print(f"    {app}: already holds the twelve the ranking picks")
                continue
            total += len(missing)
            print(f"    {app}: {len(missing)} missing — {', '.join(missing)}")
    return total


async def _apply(found: dict[int, set[str]]) -> int:
    """Create the missing rows. Returns how many were made."""
    created = 0
    for organization_id in sorted(found):
        apps = sorted(found[organization_id])
        result = await sync_missing_tools(
            organization_id=organization_id, apps=apps, user_id=None
        )
        made = int(result.get("created", 0))
        failed = int(result.get("failed", 0))
        created += made
        note = f", {failed} app(s) could not be read" if failed else ""
        print(f"  org {organization_id}: {made} row(s) created{note}")
    return created


async def _run(*, organization_id: int | None, confirm: bool) -> int:
    found = await _connected_by_organisation(organization_id=organization_id)
    if not found:
        print("No organisation has Composio tool rows. Nothing to re-sync.")
        return 0

    if not confirm:
        print("Dry run. Nothing is written without --confirm.\n")
        total = await _report(found)
        print(f"\n{total} row(s) would be created. Re-run with --confirm.")
        return 0

    print("Creating the missing rows.\n")
    created = await _apply(found)
    print(f"\n{created} row(s) created.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--organization-id",
        type=int,
        default=None,
        help="Only this organisation. Default: every organisation with rows.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually create the rows. Without it, nothing is written.",
    )
    args = parser.parse_args()
    return asyncio.run(_run(organization_id=args.organization_id, confirm=args.confirm))


if __name__ == "__main__":
    sys.exit(main())
