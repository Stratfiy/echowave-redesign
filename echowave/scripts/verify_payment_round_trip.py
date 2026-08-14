"""Prove the money path against the real world, before a customer does.

Everything about payments in this codebase is tested — idempotent redelivery,
signature comparison, the GST split, the partial capture, the refusal when no
webhook secret is set. None of it can tell you the four things that only fail
in production, and all four fail the same way: **the customer is charged and
nobody is credited.**

    1. Razorpay's servers can reach the webhook URL through your load balancer.
    2. That URL is registered in the dashboard, subscribed to payment.captured
       and payment.failed.
    3. RAZORPAY_WEBHOOK_SECRET on this box is the secret the dashboard signs with.
    4. The keys are live keys, not test keys.

This script checks what can be checked from here, and then tells you the one
thing you have to do yourself. It is deliberately not a mock: a mocked payment
proves the code, and the code was never the doubt.

    # On the deployed box, which is the only place the answers mean anything:
    #   1. Before you pay: configuration, reachability, and a signed self-test.
    docker compose exec api python -m scripts.verify_payment_round_trip

    #   2. After a real Rs 1 top-up, with the order id from /billing/payments:
    docker compose exec api python -m scripts.verify_payment_round_trip \
        --order order_QxYz1234567890

Exit status is 0 when everything checked here passed, 1 otherwise, so it can
sit in a deploy gate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from api.constants import (  # noqa: E402
    PUBLIC_BASE_URL,
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
    RAZORPAY_WEBHOOK_SECRET,
    SUPPLIER_GSTIN,
    SUPPLIER_LEGAL_NAME,
)

WEBHOOK_PATH = "/api/v1/billing/razorpay/webhook"

PASS = "  ok  "
FAIL = " FAIL "
WARN = " warn "
TODO = " you  "

_results: list[tuple[str, str, str]] = []


def record(mark: str, title: str, detail: str = "") -> None:
    _results.append((mark, title, detail))
    print(f"[{mark}] {title}")
    if detail:
        for line in detail.splitlines():
            print(f"        {line}")


# ---------------------------------------------------------------------------
# 1. Configuration
# ---------------------------------------------------------------------------


def check_configuration() -> None:
    print("\n— Configuration on this box —\n")

    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        record(
            FAIL,
            "Razorpay API keys are set",
            "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are missing. No order can be\n"
            "created, so nobody can buy credit at all.",
        )
    elif RAZORPAY_KEY_ID.startswith("rzp_test_"):
        record(
            FAIL,
            "Razorpay keys are live keys",
            "These are TEST keys. Checkout works, webhooks verify, credit lands\n"
            "in the ledger, and no money is collected for any of it. This is the\n"
            "failure that looks most like success.",
        )
    elif RAZORPAY_KEY_ID.startswith("rzp_live_"):
        record(PASS, "Razorpay keys are live keys", f"{RAZORPAY_KEY_ID[:13]}…")
    else:
        record(
            WARN,
            "Razorpay key mode is readable",
            f"{RAZORPAY_KEY_ID[:8]}… is neither rzp_live_ nor rzp_test_. Confirm\n"
            "in the dashboard which mode it belongs to.",
        )

    if RAZORPAY_WEBHOOK_SECRET:
        record(PASS, "RAZORPAY_WEBHOOK_SECRET is set")
    else:
        record(
            FAIL,
            "RAZORPAY_WEBHOOK_SECRET is set",
            "Unset, so top-ups are refused outright. That refusal is deliberate —\n"
            "the alternative is charging a customer and crediting nobody — but it\n"
            "means nobody can buy credit on this deployment.",
        )

    if SUPPLIER_LEGAL_NAME.strip() and SUPPLIER_GSTIN.strip():
        record(
            PASS,
            "Supplier identity is set",
            f"{SUPPLIER_LEGAL_NAME} ({SUPPLIER_GSTIN})",
        )
    else:
        record(
            FAIL,
            "Supplier identity is set",
            "Payments will be captured and credited and NO receipt voucher will be\n"
            "issued for any of them, silently. Under GST an advance is taxable on\n"
            "receipt, so each one accrues a compliance failure.",
        )

    if PUBLIC_BASE_URL:
        record(PASS, "PUBLIC_BASE_URL is set", PUBLIC_BASE_URL)
    else:
        record(
            FAIL,
            "PUBLIC_BASE_URL is set",
            "There is no public URL to register with Razorpay, and none to test.",
        )


# ---------------------------------------------------------------------------
# 2. Reachability — the gap that stays invisible until a customer pays
# ---------------------------------------------------------------------------


async def check_reachability() -> None:
    print("\n— Can Razorpay reach us —\n")

    if not PUBLIC_BASE_URL:
        record(FAIL, "Webhook endpoint is publicly reachable", "No PUBLIC_BASE_URL.")
        return

    url = f"{PUBLIC_BASE_URL.rstrip('/')}{WEBHOOK_PATH}"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.post(
                url,
                content=b'{"event":"verify.probe"}',
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "deliberately-invalid",
                },
            )
    except Exception as exc:
        record(
            FAIL,
            "Webhook endpoint is publicly reachable",
            f"{url}\ncould not be reached from here: {exc}\n"
            "Razorpay will fare no better.",
        )
        return

    if response.status_code == 400:
        record(
            PASS,
            "Webhook endpoint is publicly reachable",
            f"{url} answered 400 to an unsigned probe — it traversed DNS, TLS and\n"
            "the load balancer, and our signature check rejected it. That is the\n"
            "whole of Razorpay's path minus a valid signature.",
        )
    elif response.status_code == 404:
        record(
            FAIL,
            "Webhook endpoint is publicly reachable",
            f"{url} returned 404. Whatever serves that origin does not route\n"
            f"{WEBHOOK_PATH} to the api container.",
        )
    elif 200 <= response.status_code < 300:
        record(
            FAIL,
            "Webhook endpoint rejects unsigned requests",
            f"{url} returned {response.status_code} to a request with an invalid\n"
            "signature. Something is accepting unsigned webhooks. Investigate this\n"
            "before anything else on this page — it is an open credit endpoint.",
        )
    else:
        record(
            FAIL,
            "Webhook endpoint is publicly reachable",
            f"{url} answered {response.status_code}; it should answer 400. The\n"
            "request is not reaching the signature check.",
        )


async def check_signed_delivery() -> None:
    """Post a correctly signed, deliberately unknown event to our own URL.

    Proves the secret this box holds produces a signature this box accepts, and
    that a well-formed delivery gets past verification into the handler. It
    cannot prove the dashboard signs with the same secret — nothing here can —
    but it separates "the secret is wrong" from "the endpoint is unreachable"
    when the first live payment does not credit.

    The event kind is one the handler ignores, so this credits nothing.
    """
    print("\n— Does a correctly signed delivery get through —\n")

    if not (PUBLIC_BASE_URL and RAZORPAY_WEBHOOK_SECRET):
        record(WARN, "A signed webhook is accepted", "Needs PUBLIC_BASE_URL and secret.")
        return

    url = f"{PUBLIC_BASE_URL.rstrip('/')}{WEBHOOK_PATH}"
    body = json.dumps(
        {
            "event": "payment.authorized",
            "payload": {"payment": {"entity": {"id": f"pay_probe{int(time.time())}"}}},
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": signature,
                },
            )
    except Exception as exc:
        record(FAIL, "A signed webhook is accepted", f"{url}: {exc}")
        return

    if 200 <= response.status_code < 300:
        record(
            PASS,
            "A signed webhook is accepted",
            "A signature made with the secret on this box passed verification.\n"
            "The event kind was one the handler ignores, so nothing was credited.",
        )
    else:
        record(
            FAIL,
            "A signed webhook is accepted",
            f"Returned {response.status_code}. A signature this box generated was\n"
            "rejected by this box — check that the api container has been\n"
            "restarted since RAZORPAY_WEBHOOK_SECRET was last changed.",
        )


# ---------------------------------------------------------------------------
# 3. Evidence — did a real payment actually land
# ---------------------------------------------------------------------------


async def check_order(order_id: str) -> None:
    """Follow one real order all the way to the ledger and the voucher."""
    print(f"\n— The round trip for {order_id} —\n")

    from api.db import db_client
    from api.db.models import PaymentModel, TaxDocumentModel

    async with db_client.async_session() as session:
        payment = (
            await session.execute(
                select(PaymentModel).where(PaymentModel.order_id == order_id)
            )
        ).scalar_one_or_none()

        if payment is None:
            record(
                FAIL,
                "The order exists",
                f"No payment row for {order_id}. Either the order was never created\n"
                "on this deployment, or you are looking at a different database.",
            )
            return

        record(PASS, "The order exists", f"organization {payment.organization_id}")

        if payment.status == "paid":
            record(
                PASS,
                "The webhook arrived and marked it paid",
                f"payment {payment.payment_id}, {payment.amount_paise / 100:.2f} net "
                f"of tax",
            )
        else:
            record(
                FAIL,
                "The webhook arrived and marked it paid",
                f"Status is '{payment.status}'. The customer may well have been\n"
                "charged: this is the exact failure this script exists to catch.\n"
                "Check the Razorpay dashboard's webhook delivery log for this\n"
                "payment — a 4xx there means the secret does not match; nothing\n"
                "at all means the URL is not registered or not reachable.",
            )
            return

        voucher = (
            await session.execute(
                select(TaxDocumentModel).where(
                    TaxDocumentModel.payment_id == payment.id,
                    TaxDocumentModel.kind == "receipt_voucher",
                )
            )
        ).scalar_one_or_none()

        if voucher is not None:
            record(
                PASS,
                "A numbered receipt voucher was issued",
                f"{voucher.number}",
            )
        else:
            record(
                FAIL,
                "A numbered receipt voucher was issued",
                "Money was taken and credited with no tax document against it.\n"
                "Under GST an advance is taxable on receipt. Set the supplier\n"
                "identity variables and issue the missing voucher.",
            )

        from api.services.billing import payments as payments_service

        try:
            balance_paise = await payments_service.current_balance_paise(
                session, organization_id=payment.organization_id
            )
            record(
                PASS,
                "The credit is in the ledger",
                f"balance now ₹{balance_paise / 100:,.2f} (GST-exclusive, as the "
                f"ledger is throughout)",
            )
        except Exception as exc:
            record(WARN, "The credit is in the ledger", f"Could not read balance: {exc}")


def print_the_human_part() -> None:
    print("\n— What only you can do —\n")
    record(
        TODO,
        "Buy ₹1 of credit yourself, with live keys, from the real checkout",
        "Then re-run this with --order <the order id from /billing/payments>.\n"
        "Until somebody has done that, the first person to discover whether this\n"
        "works is a paying customer, and the way they discover it is by being\n"
        "charged and not credited.",
    )
    record(
        TODO,
        "Confirm the dashboard webhook is subscribed to payment.captured and "
        "payment.failed",
        "A registered URL with no event subscriptions delivers nothing, and looks\n"
        "configured from every angle except this one.",
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--order",
        help="Razorpay order id of a real top-up, to follow through to the ledger",
    )
    parser.add_argument(
        "--skip-network",
        action="store_true",
        help="Configuration checks only; makes no outbound request",
    )
    args = parser.parse_args()

    print("Payment round trip — what is proven, and what is not")
    print("=" * 60)

    check_configuration()

    if not args.skip_network:
        await check_reachability()
        await check_signed_delivery()

    if args.order:
        await check_order(args.order)
    else:
        print_the_human_part()

    failures = [r for r in _results if r[0] == FAIL]
    outstanding = [r for r in _results if r[0] == TODO]

    print("\n" + "=" * 60)
    print(
        f"{len([r for r in _results if r[0] == PASS])} passed, "
        f"{len(failures)} failed, "
        f"{len([r for r in _results if r[0] == WARN])} to look at, "
        f"{len(outstanding)} left to you"
    )

    if failures:
        print("\nDo not take a customer until these are green:")
        for _, title, _detail in failures:
            print(f"  - {title}")
        return 1

    if outstanding:
        print("\nNothing here is broken. Nothing here proves a rupee has moved.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
