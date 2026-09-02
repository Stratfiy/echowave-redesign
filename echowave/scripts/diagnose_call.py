"""Why this account cannot place a test call.

The agent saves, the call button does nothing useful, and the message on screen
names one gate without saying which of the ten it is or what comes after it.
This walks them in the order ``routes/telephony.py:initiate_call`` walks them
and prints the first one that would stop the call, with the fix.

    set -a && source api/.env && set +a
    python -m scripts.diagnose_call --list           # which org am I?
    python -m scripts.diagnose_call --org 42
    python -m scripts.diagnose_call --org 42 --to +919900000000 --workflow 7

On a Docker install, the same three with ``docker compose exec api`` in front.

Read-only. It places no call, creates no run, takes no concurrency slot and
writes nothing — so it is safe against production, which is where the question
is actually being asked.

It reports the *first* blocker rather than all of them, because the gates are
ordered and a later one often cannot be evaluated until an earlier one passes.
Fix what it names and run it again.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

BULLET_OK = "  ok   "
BULLET_NO = "BLOCKED"


def _say(ok: bool, title: str, detail: str = "") -> None:
    print(f"[{BULLET_OK if ok else BULLET_NO}] {title}")
    if detail:
        for line in detail.strip().splitlines():
            print(f"          {line}")


async def diagnose(
    organization_id: int, to_number: str | None, workflow_id: int | None
) -> int:
    """Returns a process exit code: 0 when nothing would block the call."""
    from api.db import db_client
    from api.services.organization_preferences import get_organization_preferences
    from api.services.telephony import verification_sender

    preferences = await get_organization_preferences(organization_id, db=db_client)

    # ---- 1. A carrier to dial on -------------------------------------------
    from api.services.telephony.factory import get_default_telephony_provider

    using_shared = False
    provider = None
    try:
        provider = await get_default_telephony_provider(organization_id)
        default_cfg = await db_client.get_default_telephony_configuration(
            organization_id
        )
        _say(
            True,
            "Telephony configuration",
            f"Dialling on your own {provider.PROVIDER_NAME} account "
            f"(configuration {default_cfg.id if default_cfg else '?'}).",
        )
    except ValueError:
        from api.services.telephony import shared_outbound

        try:
            _cfg_id, provider, _numbers = await shared_outbound.get_provider()
            using_shared = True
            _say(
                True,
                "Telephony configuration",
                "No carrier account of your own — falling back to Decibyl's "
                "shared caller ID pool. Note this forces the verified-number "
                "gate on below, whatever REQUIRE_VERIFIED_TEST_NUMBER says.",
            )
        except shared_outbound.NoSharedNumber:
            _say(
                False,
                "Telephony configuration",
                "No telephony configuration for this organization, and no "
                "shared outbound number on the platform either. The API answers "
                "400 telephony_not_configured.\n"
                "FIX: add a provider under Telephony (your own Plivo/Twilio/"
                "Vonage/Telnyx credentials), or have staff publish a shared "
                "outbound number at /superadmin/telephony/shared-outbound.",
            )
            return 1

    if provider is not None and not provider.validate_config():
        # validate_config() means "can place a call", which is credentials AND a
        # caller ID. Worth separating, because the two have different fixes and
        # the route reports both as the same `telephony_not_configured`. A
        # configuration with good credentials and no from-number is the common
        # one: the carrier is connected, the screen looks finished, and nothing
        # says a number is still needed.
        has_creds = getattr(provider, "has_credentials", None)
        creds_ok = has_creds() if callable(has_creds) else None
        if creds_ok:
            _say(
                False,
                "Caller ID",
                "The carrier credentials are fine, but this configuration has no "
                "from-number, so there is nothing to call from. The API answers "
                "400 telephony_not_configured — the same code as bad "
                "credentials, which is why this is worth separating.\n"
                "FIX: add a phone number to this configuration under Telephony.",
            )
        else:
            _say(
                False,
                "Provider credentials",
                "validate_config() rejects this configuration — a missing or "
                "malformed credential. The API answers 400 "
                "telephony_not_configured.\n"
                "FIX: re-enter the carrier credentials under Telephony.",
            )
        return 1
    _say(True, "Provider credentials and caller ID", "validate_config() passes.")

    # ---- 2. A destination ---------------------------------------------------
    phone_number = to_number or preferences.test_phone_number
    if not phone_number:
        _say(
            False,
            "Destination number",
            "No number passed and no test_phone_number in organization "
            "preferences.\n"
            "FIX: set a test number under Settings, or pass --to.",
        )
        return 1
    _say(True, "Destination number", phone_number)

    # ---- 3. The verified-number gate ---------------------------------------
    from api.services.telephony import verified_numbers

    gate_on = (
        verification_sender.test_calls_require_verified_number() or using_shared
    )
    if gate_on:
        verified = await verified_numbers.is_verified(organization_id, phone_number)
        if not verified:
            deliverable = verification_sender.is_deliverable()
            _say(
                False,
                "Verified destination",
                (
                    f"{phone_number} is not verified and the gate is on "
                    f"({'shared caller ID' if using_shared else 'REQUIRE_VERIFIED_TEST_NUMBER'})."
                    + (
                        "\nFIX: verify it under Verified numbers."
                        if deliverable
                        else "\nAND this deployment cannot send a verification code "
                        "(VERIFICATION_CHANNEL=log outside a dev ENVIRONMENT), so "
                        "verifying is impossible here. This is the dead end.\n"
                        "FIX: connect your own telephony provider under Telephony "
                        "— an account on its own trunk does not need this gate — "
                        "or set VERIFICATION_CHANNEL (voice, plivo_sms, "
                        "twilio_sms) with the platform carrier credentials."
                    )
                ),
            )
            return 1
        _say(True, "Verified destination", "Verified.")
    else:
        _say(True, "Verified destination", "Gate is off for this call.")

    # ---- 4. DND and the calling window -------------------------------------
    from api.services.compliance import dnd

    try:
        await dnd.assert_may_call(
            organization_id,
            phone_number,
            timezone_name=preferences.timezone,
            db=db_client,
        )
        _say(True, "Do-not-call and calling window", "Allowed right now.")
    except dnd.CallRefused as exc:
        _say(
            False,
            "Do-not-call and calling window",
            f"{exc}\nThe API answers 451 — a legal refusal, not a permission "
            f"problem.\nFIX: if it is the window, call inside it; if the number "
            f"is suppressed, remove it under Do not call.",
        )
        return 1

    # ---- 5. The agent itself ------------------------------------------------
    if workflow_id is None:
        print("\nNo --workflow given, so the agent-side gates were not checked:")
        print("  * the agent must be taking calls (409)")
        print("  * every model slot set to your own key needs a usable key (409)")
        print("  * the account needs balance above the floor (402)")
        return 0

    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if not workflow:
        _say(False, "Agent", f"No workflow {workflow_id} in this organization (404).")
        return 1

    from api.services.workflow import liveness

    try:
        liveness.assert_workflow_may_take_calls(workflow)
        _say(True, "Agent is taking calls", "")
    except liveness.AgentNotTakingCalls as exc:
        _say(
            False,
            "Agent is taking calls",
            f"{exc}\nThe API answers 409.\nFIX: switch the agent on.",
        )
        return 1

    from api.services.configuration import key_readiness

    try:
        await key_readiness.assert_workflow_may_run(workflow)
        _say(True, "Model keys", "Every slot has a usable key.")
    except key_readiness.ProviderKeyMissing as exc:
        _say(
            False,
            "Model keys",
            f"{exc}\nThe API answers 409 — refused here rather than at pipeline "
            f"start, where it would already have cost a call that answered with "
            f"silence.\nFIX: add the key under Provider keys, or move that slot "
            f"back to a Decibyl-managed model.",
        )
        return 1

    # ---- 6. Money -----------------------------------------------------------
    from api.constants import MIN_BALANCE_PAISE
    from api.services.billing.costing import current_balance_paise

    async with db_client.async_session() as session:
        balance = await current_balance_paise(session, organization_id=organization_id)
    if balance < MIN_BALANCE_PAISE:
        _say(
            False,
            "Balance",
            f"{balance} paise is below the {MIN_BALANCE_PAISE} paise floor, so "
            f"calling is paused. The API answers 402.\n"
            f"FIX: top up from /billing. A new account should have received the "
            f"signup bonus — if the balance is 0, check the credit ledger for a "
            f"'trial' row.",
        )
        return 1
    _say(
        True, "Balance", f"{balance} paise, above the {MIN_BALANCE_PAISE} paise floor."
    )

    print(
        "\nNothing here would block the call. If it still fails, the failure is "
        "after dispatch — check the api logs for the provider's response, and "
        "confirm the worker is running (/superadmin/billing/readiness)."
    )
    return 0


async def list_accounts() -> int:
    """Organizations and their agents, so ``--org`` does not need a SQL prompt.

    The id is the first thing this tool asks for and the last thing anybody has
    to hand; making them go and find it in psql is how a diagnostic goes unused.
    """
    from sqlalchemy import select

    from api.db import db_client
    from api.db.models import OrganizationModel, WorkflowModel

    async with db_client.async_session() as session:
        orgs = list(
            (
                await session.scalars(
                    select(OrganizationModel)
                    .order_by(OrganizationModel.id.desc())
                    .limit(20)
                )
            ).all()
        )
        if not orgs:
            print("No organizations yet. Sign up in the UI first.")
            return 1
        print(f"{'ORG':>6}  {'PROVIDER ID':<34} AGENTS")
        for org in orgs:
            workflows = list(
                (
                    await session.scalars(
                        select(WorkflowModel)
                        .where(WorkflowModel.organization_id == org.id)
                        .order_by(WorkflowModel.id.desc())
                        .limit(5)
                    )
                ).all()
            )
            agents = (
                ", ".join(f"{w.id}:{(w.name or '?')[:24]}" for w in workflows)
                or "(none)"
            )
            print(f"{org.id:>6}  {(org.provider_id or '')[:34]:<34} {agents}")
    print("\nThen: python -m scripts.diagnose_call --org <ORG> --workflow <AGENT>")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list",
        action="store_true",
        help="list organizations and their agents, then exit",
    )
    parser.add_argument("--org", type=int, help="organization id")
    parser.add_argument(
        "--to", help="destination number (default: the org's test number)"
    )
    parser.add_argument(
        "--workflow", type=int, help="workflow id, to check the agent-side gates too"
    )
    args = parser.parse_args()
    if args.list:
        return asyncio.run(list_accounts())
    if args.org is None:
        parser.error("--org is required (or use --list to find it)")
    return asyncio.run(diagnose(args.org, args.to, args.workflow))


if __name__ == "__main__":
    sys.exit(main())
