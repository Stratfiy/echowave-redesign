"""The People two-person check against a running stack (PEOPLE.md).

As A: sync Google (the fake provider in a local run), import a vCard, add a
contact and write a brief, each carrying one unique marker; then make
interactions through the real paths that are reachable over HTTP here --
mail arriving at A's Decibyl address through the signed inbound webhook, and
a meeting from pasted notes processed by the worker. As B (a colleague in
the same workspace): read every GET route in ``ui/openapi.internal.json``
that takes no path parameter. The marker must never appear.

    python -m scripts.people_privacy_check --base http://127.0.0.1:8000 \\
        --token-a "$A" --token-b "$B" --webhook-secret local-secret

Read-only towards B; A's writes are its own contacts. Never point it at
staging or production.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
import uuid
from email.message import EmailMessage
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "ui" / "openapi.internal.json"


def _signed(secret: str, body: dict) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return raw, {
        "X-Decibyl-Signature": f"sha256={sig}",
        "X-Decibyl-Timestamp": ts,
        "Content-Type": "application/json",
    }


def _wait(check, what: str, seconds: int = 90):
    deadline = time.time() + seconds
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep(2)
    raise SystemExit(f"Timed out waiting for {what}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--token-a", required=True)
    parser.add_argument("--token-b", required=True)
    parser.add_argument("--webhook-secret", default=None)
    parser.add_argument("--alias", default=None, help="A's active Decibyl address")
    args = parser.parse_args()
    marker = f"PPLMARK{uuid.uuid4().hex[:10]}"
    a = httpx.Client(
        base_url=args.base,
        headers={"Authorization": f"Bearer {args.token_a}"},
        timeout=60,
    )
    b = httpx.Client(
        base_url=args.base,
        headers={"Authorization": f"Bearer {args.token_b}"},
        timeout=30,
    )

    # --- A: sources ---------------------------------------------------------
    r = a.post("/api/v1/people/sync/google")
    print("A sync google:", r.status_code, r.json())
    _wait(
        lambda: next(
            (
                p
                for p in a.get("/api/v1/people/status").json()["providers"]
                if p["provider"] == "google" and p["state"] in ("ok", "error")
            ),
            None,
        ),
        "the Google sync",
    )
    print(
        "A google:",
        [
            p
            for p in a.get("/api/v1/people/status").json()["providers"]
            if p["provider"] == "google"
        ][0],
    )
    vcard = (
        "BEGIN:VCARD\nVERSION:3.0\nFN:Meena " + marker + "\nTEL:+91 90000 11111\n"
        "EMAIL:meena@example.org\nORG:Iyer " + marker + "\nEND:VCARD\n"
    ).encode()
    r = a.post(
        "/api/v1/people/import", files={"file": ("contacts.vcf", vcard, "text/vcard")}
    )
    print("A vCard import:", r.status_code, r.json())
    made = a.post(
        "/api/v1/people", json={"name": f"Ravi {marker}", "phones": ["98765 43210"]}
    ).json()
    a.patch(
        f"/api/v1/people/{made['id']}",
        json={"brief": f"Supplier. {marker} owes a revised quote."},
    )

    # --- A: interactions through the real paths -------------------------------
    if args.webhook_secret and args.alias:
        message = EmailMessage()
        message["From"] = f"Priya Sharma <priya@example.in>"
        message["To"] = args.alias
        message["Subject"] = f"Invoice {marker}"
        message["Message-ID"] = f"<{marker}@example.in>"
        message.set_content("Attached.")
        raw, headers = _signed(
            args.webhook_secret,
            {
                "recipient": args.alias,
                "raw_base64": base64.b64encode(message.as_bytes()).decode(),
            },
        )
        r = httpx.post(
            f"{args.base}/api/v1/public/email-identity/inbound",
            content=raw,
            headers=headers,
            timeout=30,
        )
        print("Mail to A's Decibyl address:", r.status_code, r.text[:120])
    meeting = a.post(
        "/api/v1/meetings",
        json={
            "source": "notes",
            "title": f"Rates review {marker}",
            "participants": [f"Ravi {marker}", "Sunita Rao"],
            "notes": "Agreed the new rates with Ravi. Sunita to send the contract.",
        },
    )
    print("A meeting from notes:", meeting.status_code)
    _wait(
        lambda: any(
            i["channel"] == "meeting"
            for i in a.get(f"/api/v1/people/{made['id']}").json()["interactions"]
        ),
        "the meeting on Ravi's page",
        seconds=120,
    )
    listed = a.get("/api/v1/people").json()
    print(f"A has {listed['total']} contacts:", [p["name"] for p in listed["people"]])
    page = a.get(f"/api/v1/people/{made['id']}").json()
    print("Ravi's timeline:", [(i["channel"], i["line"]) for i in page["interactions"]])
    assert marker in json.dumps(listed), "the marker must be visible to its owner"

    # --- B: every GET route with no path parameter ------------------------------
    spec = json.loads(SPEC.read_text())
    routes = sorted(
        p for p, ops in spec["paths"].items() if "get" in ops and "{" not in p
    )
    leaked, statuses = [], {}
    for path in routes:
        try:
            response = b.get(path)
        except httpx.HTTPError as exc:
            statuses["unreachable"] = statuses.get("unreachable", 0) + 1
            print("  could not read", path, type(exc).__name__)
            continue
        statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
        if marker in response.text or marker.lower() in response.text:
            leaked.append(f"{path} -> {response.status_code}")
    print(
        f"B read {len(routes)} GET routes without path parameters from {SPEC.name}: {statuses}"
    )
    if leaked:
        print("LEAKED:", leaked)
        return 1
    print(f"Marker {marker} appeared in none of B's answers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
