"""Measure a Prospecting agent's runs for the Credits Plan (OP-6).

Reads what the runs cost and did, off the rows the platform keeps, and
prints the block the plan takes. Nothing is written.

    source venv/bin/activate && set -a && . api/.env && set +a
    python -m scripts.replay_prospecting --organization 12 --workflow 345 --days 7
    python -m scripts.replay_prospecting --organization 12 --workflow 345 --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--organization", type=int, required=True)
    parser.add_argument("--workflow", type=int, required=True)
    parser.add_argument("--days", type=int, default=7, help="Window ending now.")
    parser.add_argument("--json", action="store_true", help="Machine-readable.")
    args = parser.parse_args()

    from api.db import db_client
    from api.services.billing import replay

    end = datetime.now(UTC)
    start = end - timedelta(days=max(1, args.days))
    async with db_client.async_session() as session:
        measured = await replay.measure(
            session,
            organization_id=args.organization,
            workflow_id=args.workflow,
            start=start,
            end=end,
        )
    print(
        json.dumps(measured.as_dict(), indent=2)
        if args.json
        else measured.as_markdown()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
