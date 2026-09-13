"""Rename bots so their names are the handles people type.

`@` addresses a handle, and a handle ends at a space -- that is what makes
"@sales-bot-india" unambiguous where "@Sales bot India" needed a guess about
where the name ended. This renames existing bots so the name on screen and the
name you type are the same string.

Dry run by default. Nothing is written until --apply, and the preview is the
whole point: these names are read by the people who run the business, and a
rename nobody looked at first is a rename somebody has to undo.

    set -a && source api/.env && set +a
    python -m scripts.rename_bots_to_handles                    # preview
    python -m scripts.rename_bots_to_handles --org 3            # one account
    python -m scripts.rename_bots_to_handles --apply            # write

**What this does not touch, and you should know before running it.**

A bot's *prompt* often names it -- "You are Meera from Decibyl" -- and that
text lives in the workflow definition, not in this column. Renaming here
changes what the screen calls the bot and what people type to reach it. It
does not change what the bot calls itself on a call, and it should not: a
caller hearing "you are speaking to meera-decibyl-sales-assistant" is a worse
outcome than any handle is worth.

Collisions are refused rather than resolved. Two bots whose names reduce to
the same handle stay as they are and are listed, because picking a winner by
id would leave an account with a bot that quietly stopped being addressable.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from sqlalchemy import select  # noqa: E402

from api.db import db_client  # noqa: E402
from api.db.models import WorkflowModel  # noqa: E402
from api.services.workflow.mentions import handle_for  # noqa: E402


async def plan(organization_id: int | None) -> tuple[list, dict]:
    """What would change, and which handles more than one bot wants."""
    async with db_client.async_session() as session:
        query = select(WorkflowModel).order_by(WorkflowModel.id)
        if organization_id is not None:
            query = query.where(WorkflowModel.organization_id == organization_id)
        workflows = (await session.scalars(query)).all()

    wanted: dict[tuple[int, str], list] = defaultdict(list)
    for workflow in workflows:
        handle = handle_for(workflow.name or "")
        if handle:
            wanted[(workflow.organization_id, handle)].append(workflow)

    renames = []
    collisions = {}
    for (org_id, handle), claimants in wanted.items():
        if len(claimants) > 1:
            collisions[(org_id, handle)] = claimants
            continue
        workflow = claimants[0]
        if (workflow.name or "") != handle:
            renames.append((workflow, handle))

    unnameable = [w for w in workflows if not handle_for(w.name or "")]
    return renames, {"collisions": collisions, "unnameable": unnameable}


async def apply(renames: list) -> int:
    async with db_client.async_session() as session:
        for workflow, handle in renames:
            fresh = await session.get(WorkflowModel, workflow.id)
            if fresh is None:
                continue
            fresh.name = handle
        await session.commit()
    return len(renames)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", type=int, default=None, help="one organisation")
    parser.add_argument(
        "--apply", action="store_true", help="write the renames (default: preview)"
    )
    args = parser.parse_args()

    renames, problems = await plan(args.org)

    for workflow, handle in renames:
        print(f"  {workflow.id:>5}  {workflow.name!r}  ->  {handle!r}")

    for (org_id, handle), claimants in problems["collisions"].items():
        names = ", ".join(repr(w.name) for w in claimants)
        print(f"  SKIPPED org {org_id}: {names} all want @{handle}")

    for workflow in problems["unnameable"]:
        print(f"  SKIPPED {workflow.id}: {workflow.name!r} reduces to no handle")

    if not renames:
        print("Nothing to rename.")
        return 0

    if not args.apply:
        print(f"\n{len(renames)} would be renamed. Re-run with --apply to write.")
        return 0

    written = await apply(renames)
    print(f"\nRenamed {written}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
