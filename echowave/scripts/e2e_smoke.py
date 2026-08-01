"""End-to-end: signup -> agreements -> billing profile -> top-up -> ledger -> invoice.

Drives the real HTTP API, not the service layer, because the question is
whether a customer can get from nothing to a paid, invoiced account — and every
gap between a working service and a broken route lives in that difference.
"""

import hashlib
import hmac
import json
import sys
import time

import requests

BASE = "http://127.0.0.1:8100/api/v1"
WEBHOOK_SECRET = "e2ewebhooksecret"
EMAIL = f"e2e-{int(time.time())}@decibyl-e2e.com"
PASSWORD = "Test-Passw0rd!"

PASS, FAIL, WARN = [], [], []


def step(name, ok, detail=""):
    bucket = PASS if ok is True else (WARN if ok == "warn" else FAIL)
    bucket.append(f"{name}: {detail}" if detail else name)
    mark = {True: "PASS", "warn": "WARN"}.get(ok, "FAIL")
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def main():
    s = requests.Session()

    print("\n1. SIGNUP")
    r = s.post(f"{BASE}/auth/signup", json={"email": EMAIL, "password": PASSWORD})
    if r.status_code != 200:
        step("signup", False, f"{r.status_code} {r.text[:200]}")
        return
    token = r.json()["token"]
    s.headers["Authorization"] = f"Bearer {token}"
    step("signup", True, f"token issued, org={r.json()['user'].get('organization_id')}")

    print("\n2. COMPLIANCE — agreements outstanding at signup")
    r = s.get(f"{BASE}/privacy/agreements")
    if r.status_code != 200:
        step("GET /privacy/agreements", False, f"{r.status_code} {r.text[:200]}")
    else:
        outstanding = r.json().get("outstanding", [])
        step(
            "new account owes the DPA",
            "dpa" in outstanding,
            f"outstanding={outstanding}",
        )

    print("\n3. COMPLIANCE — accept the DPA")
    r = s.post(f"{BASE}/privacy/agreements/accept", json={"agreement": "dpa"})
    if r.status_code != 200:
        step("accept DPA", False, f"{r.status_code} {r.text[:200]}")
    else:
        step("accept DPA", True, f"version={r.json().get('version')}")
        r2 = s.get(f"{BASE}/privacy/agreements")
        step(
            "DPA clears from outstanding",
            "dpa" not in r2.json().get("outstanding", []),
            f"remaining={r2.json().get('outstanding')}",
        )

    print("\n4. BILLING PROFILE — Telangana customer (inter-state from TN supplier)")
    profile = {
        "legal_name": "Test Agri Dept",
        "gstin": "36AABCT1332L1ZZ",  # 36 = Telangana
        "address_line1": "Collectorate",
        "address_line2": "",
        "city": "Hyderabad",
        "state_code": "36",
        "postal_code": "500001",
        "country_code": "IN",
        "billing_email": EMAIL,
    }
    r = s.put(f"{BASE}/billing/profile", json=profile)
    if r.status_code != 200:
        step("save billing profile", False, f"{r.status_code} {r.text[:300]}")
    else:
        step("save billing profile", True, f"complete={r.json().get('is_complete')}")

    print("\n5. BALANCE — before payment")
    r = s.get(f"{BASE}/billing/balance")
    if r.status_code != 200:
        step("GET balance", False, f"{r.status_code} {r.text[:200]}")
        opening = None
    else:
        opening = r.json()
        step("GET balance", True, json.dumps(opening)[:160])

    print("\n6. TOP-UP — create the order")
    r = s.post(f"{BASE}/billing/topup", json={"amount_paise": 100000})  # Rs 1000 net
    if r.status_code != 200:
        step("POST topup", False, f"{r.status_code} {r.text[:300]}")
        order = None
    else:
        order = r.json()
        step("POST topup", True, json.dumps(order)[:220])

    print("\n7. WEBHOOK — unsigned must be refused")
    payload = {"event": "payment.captured", "payload": {"payment": {"entity": {}}}}
    body = json.dumps(payload)
    r = requests.post(
        f"{BASE}/billing/razorpay/webhook",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    step(
        "unsigned webhook rejected",
        r.status_code in (400, 401, 403),
        f"status={r.status_code}",
    )

    print("\n8. WEBHOOK — bad signature must be refused")
    r = requests.post(
        f"{BASE}/billing/razorpay/webhook",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": "deadbeef",
        },
    )
    step(
        "bad-signature webhook rejected",
        r.status_code in (400, 401, 403),
        f"status={r.status_code}",
    )

    print("\n9. WEBHOOK — correctly signed capture must credit")
    if order:
        rzp_order_id = order.get("order_id") or order.get("razorpay_order_id")
        amount = order.get("amount_paise_gross") or order.get("gross_paise") or 118000
        event = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": f"pay_e2e{int(time.time())}",
                        "order_id": rzp_order_id,
                        "amount": amount,
                        "currency": "INR",
                        "status": "captured",
                    }
                }
            },
        }
        raw = json.dumps(event)
        sig = hmac.new(
            WEBHOOK_SECRET.encode(), raw.encode(), hashlib.sha256
        ).hexdigest()
        r = requests.post(
            f"{BASE}/billing/razorpay/webhook",
            data=raw,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": sig,
            },
        )
        step(
            "signed webhook accepted",
            r.status_code == 200,
            f"status={r.status_code} {r.text[:200]}",
        )

        r = s.get(f"{BASE}/billing/balance")
        after = r.json() if r.status_code == 200 else {}
        credited = (after.get("balance_paise") or 0) > (
            (opening or {}).get("balance_paise") or 0
        )
        step("balance increased after capture", credited, json.dumps(after)[:200])

    print("\n10. TAX DOCUMENTS — receipt voucher issued")
    r = s.get(f"{BASE}/billing/documents")
    if r.status_code != 200:
        step("GET documents", False, f"{r.status_code} {r.text[:200]}")
    else:
        docs = r.json().get("documents", [])
        step(
            "receipt voucher issued for the payment",
            len(docs) > 0,
            f"{len(docs)} document(s): {[d.get('kind') or d.get('document_type') for d in docs][:3]}",
        )
        if docs:
            d = docs[0]
            has_gst = any(
                k in json.dumps(d) for k in ("igst", "cgst", "sgst", "tax_paise")
            )
            step("document carries a GST split", has_gst, json.dumps(d)[:240])

    print("\n11. PRIVACY READINESS")
    r = s.get(f"{BASE}/privacy/readiness")
    if r.status_code != 200:
        step("GET readiness", False, f"{r.status_code} {r.text[:200]}")
    else:
        body = r.json()
        step(
            "no blocking privacy actions",
            body.get("action_required") == 0,
            f"action_required={body.get('action_required')}, needs_a_human={body.get('needs_a_human')}",
        )

    print("\n12. SUB-PROCESSORS + GRIEVANCE OFFICER published")
    r = s.get(f"{BASE}/privacy/subprocessors")
    if r.status_code != 200:
        step("GET subprocessors", False, f"{r.status_code} {r.text[:200]}")
    else:
        officer = r.json().get("grievance_officer", {})
        step("grievance officer named", bool(officer.get("name")), json.dumps(officer))

    print("\n13. COST ESTIMATE — can a customer see the price before calling?")
    r = s.post(
        f"{BASE}/cost-estimate/per-minute",
        json={
            "stt_provider": "sarvam",
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
            "tts_provider": "sarvam",
            "telephony_provider": "plivo",
        },
    )
    if r.status_code != 200:
        step("cost estimate", False, f"{r.status_code} {r.text[:200]}")
    else:
        body = r.json()
        step(
            "estimate returns a price",
            body.get("total_paise_per_minute", 0) > 0,
            f"Rs {body.get('total_paise_per_minute', 0) / 100:.2f}/min, unpriced={body.get('unpriced')}",
        )

    print("\n" + "=" * 64)
    print(f"PASS {len(PASS)}   WARN {len(WARN)}   FAIL {len(FAIL)}")
    if FAIL:
        print("\nFAILURES:")
        for f in FAIL:
            print(f"  - {f}")
    if WARN:
        print("\nWARNINGS:")
        for w in WARN:
            print(f"  - {w}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
