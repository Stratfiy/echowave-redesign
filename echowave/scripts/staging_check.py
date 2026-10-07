"""Check every core flow against a running Decibyl, usually staging.

Run after each staging deploy (the workflow does), or by hand:

    STAGING_URL=https://staging.decibyl.ai \\
    STAGING_EMAIL_A=... STAGING_PASSWORD_A=... \\
    STAGING_EMAIL_B=... STAGING_PASSWORD_B=... \\
    python3 echowave/scripts/staging_check.py

A and B are two test accounts in the same staging workspace (B invited by A,
once, by hand). Nothing here uses a customer's data, and every check talks to
the API exactly as the app does.

Standard library only, so it runs on any runner without an install step.

What it proves, in order: the API is up and says which features are on; both
people can sign in; Decibyl answers with a real model; one person's thread is
invisible to the other; inviting a teammate works; the screens the app opens
on answer. What it cannot prove -- a call ringing, a WhatsApp arriving on a
phone, Gmail connecting through a browser consent -- is printed at the end as
a short list for a person to tick.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("STAGING_URL", "https://staging.decibyl.ai").rstrip("/")
API = f"{BASE}/api/v1"
REPLY_WAIT_SECONDS = int(os.environ.get("STAGING_REPLY_WAIT", "120"))

results: list[tuple[str, bool, str]] = []


def call(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{API}{path}", data=data, method=method)
    request.add_header("content-type", "application/json")
    if token:
        request.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except ValueError:
            return exc.code, None
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, {"detail": str(exc)}


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f' -- {detail}' if detail else ''}")
    return ok


def skip(name: str, why: str) -> None:
    """Not run, and said so: a check that cannot apply is neither a pass nor
    a failure, and must never be counted as either."""
    print(f"SKIP  {name} -- {why}")


#: What Decibyl says when it could not reach a model. It is an agent message
#: like any other, so a check that only looks for one passes on it.
FAILURE_REPLY = "I could not think that through"


def _real_reply(event: dict) -> bool:
    payload = event.get("payload") or {}
    if payload.get("failed") or payload.get("stopped"):
        return False
    return not str(payload.get("body") or "").startswith(FAILURE_REPLY)


def login(email_var: str, password_var: str) -> str | None:
    email, password = os.environ.get(email_var), os.environ.get(password_var)
    if not email or not password:
        check(f"sign in ({email_var})", False, f"{email_var}/{password_var} not set")
        return None
    status, body = call(
        "POST", "/auth/login", body={"email": email, "password": password}
    )
    token = (body or {}).get("token") or (body or {}).get("access_token")
    check(f"sign in as {email}", status == 200 and bool(token), f"HTTP {status}")
    return token


def main() -> int:
    status, health = call("GET", "/health")
    up = check(
        "API is up",
        status == 200 and (health or {}).get("status") == "ok",
        f"HTTP {status}",
    )
    if not up:
        return report()
    features = (health or {}).get("features") or {}
    on = sorted(k for k, v in features.items() if v)
    check("features reported", True, ", ".join(on) or "none on")

    a = login("STAGING_EMAIL_A", "STAGING_PASSWORD_A")
    b = login("STAGING_EMAIL_B", "STAGING_PASSWORD_B")
    if not a:
        return report()

    _, me_a = call("GET", "/auth/me", a)
    _, me_b = call("GET", "/auth/me", b) if b else (0, None)
    same_workspace = bool(
        me_a and me_b and me_a.get("organization_id") == me_b.get("organization_id")
    )
    if b:
        check(
            "A and B share a workspace", same_workspace, "invite B from A once, by hand"
        )

    # A real model reply: the one thing no local test can show.
    thread = str(uuid.uuid4())
    status, _ = call(
        "POST",
        "/timeline/message",
        a,
        {
            "assistant": True,
            "thread_id": thread,
            "text": "Staging check: reply with one short sentence.",
        },
    )
    check("A can message Decibyl", status == 200, f"HTTP {status}")
    replies: list[dict] = []
    deadline = time.time() + REPLY_WAIT_SECONDS
    while time.time() < deadline and not replies:
        _, page = call("GET", f"/timeline?assistant=true&thread_id={thread}", a)
        events = (page or {}).get("events") or []
        replies = [
            e
            for e in events
            if e.get("actor") == "agent" and e.get("kind") == "message"
        ]
        if not replies:
            time.sleep(4)
    real = any(_real_reply(e) for e in replies)
    check(
        "Decibyl answers with a real model",
        real,
        ""
        if real
        else (
            "it replied that it could not reach a model: add a model key"
            if replies
            else f"no reply in {REPLY_WAIT_SECONDS}s: check model keys and the worker"
        ),
    )

    if b and same_workspace and not features.get("decibyl_private_threads"):
        skip(
            "thread and draft privacy between A and B",
            "decibyl_private_threads is off here, so threads are shared by design",
        )
    elif b and same_workspace:
        _, mine = call("GET", "/timeline/threads?limit=50", b)
        ids = {t.get("thread_id") for t in (mine or {}).get("threads") or []}
        check("A's thread is not in B's list", thread not in ids)
        _, theirs = call("GET", f"/timeline?assistant=true&thread_id={thread}", b)
        check("B cannot read A's thread", not (theirs or {}).get("events"))
        draft_status, draft = call("GET", f"/timeline/draft?thread_id={thread}", b)
        check(
            "B sees no draft from A's thread",
            draft_status == 200 and not (draft or {}).get("text"),
            f"HTTP {draft_status}",
        )

    # Inviting a teammate: a 500 on every call until 7 Oct 2026.
    email = f"staging-check-{uuid.uuid4().hex[:8]}@example.com"
    status, invite = call(
        "POST", "/organizations/invitations", a, {"email": email, "role": "member"}
    )
    invited = status == 200 and "token=" in ((invite or {}).get("link") or "")
    check("A can invite a teammate", invited, f"HTTP {status}")
    if invited:
        invitation_id = ((invite or {}).get("invitation") or {}).get("id")
        if invitation_id:
            call("DELETE", f"/organizations/invitations/{invitation_id}", a)

    for name, path in [
        ("Memory loads", "/organisation/memory"),
        ("Models load", "/organizations/models"),
        ("Recents load", "/timeline/recents"),
        ("Usage per agent loads", "/organizations/usage/agents"),
    ]:
        status, _ = call("GET", path, a)
        check(name, status == 200, f"HTTP {status}")

    return report()


MANUAL = [
    "Call the staging number: an agent answers in the right language and voice.",
    "Miss a call on purpose: the call-back or WhatsApp follow-up arrives.",
    "Send WhatsApp to Decibyl from A's verified number: it answers on the thread and on WhatsApp.",
    "Connect Gmail as A, then ask Decibyl about your inbox: it reads it; B cannot see A's connection.",
    "Ask Decibyl to send something: a card shows exactly what will be sent; Confirm sends it once.",
    "Open the app on a phone: Chat, Today and Menu at the bottom; nothing off the edge.",
]


def report() -> int:
    failed = [name for name, ok, _ in results if not ok]
    print()
    print(f"{len(results) - len(failed)} passed, {len(failed)} failed against {BASE}")
    print()
    print("Still to check by hand (needs a phone or a browser consent):")
    for line in MANUAL:
        print(f"  [ ] {line}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
