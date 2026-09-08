"""Did the go-live configuration actually land? Ask the deployment, not a list.

Every item on a go-live checklist has the same failure mode: it is ticked from
memory. The variables in question are ones nothing complains about when they
are wrong — an unset grievance officer serves a blank field, a Razorpay test
key takes no money while producing perfect orders, a plan pinned at the net
collects no GST every month by standing instruction. None of that raises an
error anywhere.

So this runs the same assessment the Readiness screen runs
(`services/billing/readiness.py` and `services/privacy/readiness.py`) against
the live database and prints it. Same answers as the screen; no browser, no
staff login, no session. Run it on the box after a deploy, or in CI against a
staging database.

    set -a && source api/.env && set +a && python -m scripts.check_go_live

    # inside docker compose
    docker compose exec -T api python -m scripts.check_go_live

Exit code is the point: **0 when nothing is blocking**, 1 otherwise, so this
can gate a release step rather than being read by eye.

    --json      machine-readable, for a pipeline
    --privacy   include the DPDP/GDPR checks as well as billing
    --probe     let the billing assessment make its outbound webhook probe

`needs_a_human` items never turn green and never affect the exit code. They are
obligations no code can discharge — somebody signing a breach notification, a
real payment carried end to end — and folding them into the count would make it
permanently non-zero and therefore ignored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from api.db import db_client  # noqa: E402
from api.services.readiness import (  # noqa: E402
    ACTION_REQUIRED,
    NEEDS_A_HUMAN,
    READY,
    UNKNOWN,
    Check,
)

#: Printed against each check. Deliberately plain ASCII: this is read over ssh
#: on a box whose terminal encoding nobody has checked.
MARK = {
    READY: "PASS",
    ACTION_REQUIRED: "FAIL",
    UNKNOWN: "????",
    NEEDS_A_HUMAN: "YOURS",
}

#: The order that reads best when working down the output, rather than the
#: order the assessment happens to return.
ORDER = [ACTION_REQUIRED, UNKNOWN, NEEDS_A_HUMAN, READY]


def _render(title: str, checks: list[Check]) -> None:
    print()
    print(title)
    print("=" * len(title))
    if not checks:
        print("  (no checks ran)")
        return

    ordered = sorted(
        checks, key=lambda c: ORDER.index(c.status) if c.status in ORDER else 99
    )
    for check in ordered:
        print(f"\n[{MARK.get(check.status, check.status):^5}] {check.title}")
        print(f"        {check.detail}")
        if check.remedy:
            print(f"        -> {check.remedy}")


def _as_dict(check: Check) -> dict:
    return {
        "key": check.key,
        "title": check.title,
        "status": check.status,
        "detail": check.detail,
        "remedy": check.remedy,
        "reference": check.reference,
    }


async def run(*, want_privacy: bool, probe: bool, as_json: bool) -> int:
    from api.services.billing import readiness as billing_readiness

    async with db_client.async_session() as session:
        billing = await billing_readiness.assess(session, probe_network=probe)

        privacy = None
        if want_privacy:
            from api.services.privacy import readiness as privacy_readiness

            privacy = await privacy_readiness.assess(session)

    blocking = list(billing.blocking) + list(privacy.blocking if privacy else [])

    if as_json:
        print(
            json.dumps(
                {
                    "blocking": len(blocking),
                    "billing": [_as_dict(c) for c in billing.checks],
                    "privacy": [
                        _as_dict(c) for c in (privacy.checks if privacy else ())
                    ],
                },
                indent=2,
            )
        )
        return 0 if not blocking else 1

    _render("Billing and tax", list(billing.checks))
    if privacy is not None:
        _render("Privacy", list(privacy.checks))

    print()
    if blocking:
        print(f"{len(blocking)} blocking item(s). Not ready to take money.")
        for check in blocking:
            print(f"  - {check.title}")
    else:
        # Said explicitly rather than by silence, because "no output" and
        # "everything passed" look identical and only one of them is good news.
        print("Nothing blocking. Every check that code can answer, answers yes.")

    unresolved = list(billing.unresolvable) + list(
        privacy.unresolvable if privacy else []
    )
    if unresolved:
        print(
            f"\n{len(unresolved)} item(s) no code can discharge — they never turn "
            "green and do not affect the exit code:"
        )
        for check in unresolved:
            print(f"  - {check.title}")

    return 0 if not blocking else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the go-live readiness checks against this deployment."
    )
    parser.add_argument(
        "--json", action="store_true", help="Machine-readable output for a pipeline."
    )
    parser.add_argument(
        "--privacy",
        action="store_true",
        help="Include the DPDP/GDPR checks as well as billing.",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help=(
            "Let the billing assessment post an unsigned request to this "
            "deployment's own webhook endpoint, to prove it is reachable and "
            "rejects unsigned callbacks. Off by default: it makes a real "
            "outbound request."
        ),
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print(
            "DATABASE_URL is not set. Source the environment first:\n"
            "    set -a && source api/.env && set +a",
            file=sys.stderr,
        )
        return 2

    return asyncio.run(
        run(want_privacy=args.privacy, probe=args.probe, as_json=args.json)
    )


if __name__ == "__main__":
    raise SystemExit(main())
