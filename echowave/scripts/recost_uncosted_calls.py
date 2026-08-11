"""Recost calls whose receipt recorded usage with no rate on file.

`cost_workflow_run` writes every component it could not price into a run's
`uncosted_usage` -- the call still bills the platform fee, but any provider
cost with no rate on file is missing from the receipt, and margin reads
overstated for exactly that call. Once the missing rate is added to the price
book, nothing revisits those old receipts on its own:
`cost_workflow_run(recost=True)` exists and nothing calls it. This does.

    python -m scripts.recost_uncosted_calls                     # show candidates
    python -m scripts.recost_uncosted_calls --confirm           # recost them
    python -m scripts.recost_uncosted_calls --confirm --workflow-run-id 123

Scope is deliberately narrow: only runs where `uncosted_usage` is a *non-empty*
list -- a known, flagged gap. `uncosted_usage IS NULL` means the run predates
this column entirely (see the model's own comment) and is not touched here;
recosting every call ever placed on the chance an old one had a silent gap is
a different, far riskier operation than fixing the ones we already know about.

Recosting replaces the old receipt's line items and ledger debit with the
corrected ones -- see `cost_workflow_run`'s recost path -- so a run's balance
impact can go either direction: crediting a rate that undercounted usage
before charges the account more; one that overcounted charges less. This is
real money moving on real accounts. Nothing is written without `--confirm`.

Requires DATABASE_URL, like every repo-owned script:

    set -a && source api/.env && set +a && python -m scripts.recost_uncosted_calls

In Docker, where there is no checkout to run from:

    docker compose exec api python -m scripts.recost_uncosted_calls --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from api.db import db_client
from api.db.models import WorkflowRunModel
from api.services.billing.costing import cost_workflow_run


async def _candidates(
    session, *, workflow_run_id: int | None
) -> list[WorkflowRunModel]:
    """Costed runs that recorded usage with no rate on file.

    `uncosted_usage IS NOT NULL` alone is not enough -- it also matches a run
    that was costed cleanly and has an empty list on record (see the model's
    comment on the column). The non-empty check is what actually means "had a
    gap".
    """
    query = select(WorkflowRunModel).where(
        WorkflowRunModel.costed_at.isnot(None),
        WorkflowRunModel.uncosted_usage.isnot(None),
    )
    if workflow_run_id is not None:
        query = query.where(WorkflowRunModel.id == workflow_run_id)
    rows = (await session.scalars(query)).all()
    return [r for r in rows if r.uncosted_usage]


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recost calls whose receipt recorded usage with no rate on file."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually recost. Without it, prints the candidates and exits.",
    )
    parser.add_argument(
        "--workflow-run-id",
        type=int,
        default=None,
        help="Recost one specific run instead of every flagged candidate.",
    )
    args = parser.parse_args()

    async with db_client.async_session() as session:
        candidates = await _candidates(session, workflow_run_id=args.workflow_run_id)

        if not candidates:
            print("Nothing to recost.")
            return 0

        print(f"{len(candidates)} run(s) with usage that had no rate on file:\n")
        for run in candidates:
            labels = ", ".join(run.uncosted_usage or [])
            charged = (
                f"₹{run.total_charged_paise / 100:.2f}"
                if run.total_charged_paise is not None
                else "?"
            )
            print(f"  run {run.id}: charged {charged} so far, missing {labels}")

        if not args.confirm:
            print(
                f"\n{len(candidates)} run(s) would be recosted. "
                "Nothing changed -- re-run with --confirm."
            )
            return 0

        still_uncosted = 0
        for run in candidates:
            before_paise = run.total_charged_paise
            cost = await cost_workflow_run(session, run.id, recost=True)
            if cost is None:
                # Recosted by a concurrent job between the select above and
                # here, or the run vanished -- either way, nothing to report.
                continue
            if cost.uncosted:
                still_uncosted += 1
            delta = cost.total_charged_paise - (before_paise or 0)
            sign = "+" if delta >= 0 else "-"
            print(f"  run {run.id}: {sign}₹{abs(delta) / 100:.2f}")

        await session.commit()

        print(
            f"\nRecosted {len(candidates)} run(s). "
            f"{still_uncosted} still have usage with no rate on file."
        )
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
