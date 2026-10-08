"""The learning two-person check against a running stack (LEARNING.md).

As A: make every private thing learning keeps, each carrying one unique
marker -- the learner profile, a goal (title, course, notes), the placement
answer, a marked practice answer, a renamed goal, a course started from
Chat on A's own thread (``start_course``, the Learning Guide's tool, called
through its service because a local run has no model to call it), "Was this
useful?" on a lesson, and the delete card. As B (a colleague in the same
workspace): read every GET route in ``ui/openapi.internal.json`` that takes
no path parameter -- the marker must never appear -- then try A's ids on
every learning route that takes one, and on the cards and threads learning
wrote. Each must be "not found".

    python -m scripts.learning_privacy_check --base http://127.0.0.1:8000 \\
        --token-a "$A" --token-b "$B"

Run with api/.env sourced (the ``start_course`` step writes to the same
database the stack reads). Read-only towards B; A's writes are A's own
learning record. Never point it at staging or production.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "ui" / "openapi.internal.json"


def _start_course_on_thread(
    organization_id: int, user_id: int, thread_id: str, title: str
):
    """The Learning Guide's ``start_course`` as a Chat turn on A's thread
    calls it."""
    from api.services.learning import guide
    from api.services.workflow import agent_timeline

    async def go():
        with agent_timeline.in_thread(thread_id):
            return await guide.start_course(
                organization_id, user_id, {"title": title}, thread_id=thread_id
            )

    return asyncio.run(go())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--token-a", required=True)
    parser.add_argument("--token-b", required=True)
    args = parser.parse_args()
    marker = f"LRNMARK{uuid.uuid4().hex[:10]}"
    api = f"{args.base}/api/v1"
    a = httpx.Client(
        base_url=api, headers={"Authorization": f"Bearer {args.token_a}"}, timeout=60
    )
    b = httpx.Client(
        base_url=api, headers={"Authorization": f"Bearer {args.token_b}"}, timeout=30
    )
    me_a = a.get("/auth/me").json()
    org, a_id = me_a["organization_id"], me_a["id"]
    assert b.get("/auth/me").json()["organization_id"] == org, (
        "B must share A's workspace"
    )

    # --- A: everything learning keeps, each with the marker -------------------
    profile = a.get("/learning/status").json()["profile"]
    r = a.put(
        "/learning/profile",
        json={
            "adult_confirmed": True,
            "studying_for": f"Exam {marker}",
            "revision": profile["revision"],
        },
    )
    print("A profile:", r.status_code)
    thread = str(uuid.uuid4())
    r = a.post(
        "/timeline/message",
        json={"assistant": True, "thread_id": thread, "text": "Teach me something"},
    )
    print("A says something on a new Decibyl thread:", r.status_code)
    goal = a.post(
        "/learning/goals",
        json={
            "title": f"Quadratics {marker}",
            "studying_for": f"Board {marker}",
            "material": f"Notes {marker}. A quadratic has degree two.",
            "thread_id": thread,
        },
    )
    print("A goal:", goal.status_code)
    goal_id = goal.json()["goal"]["goal_id"]
    s = a.post(
        f"/learning/goals/{goal_id}/baseline", json={"answer": f"Placement {marker}"}
    )
    print("A placement:", s.status_code)
    session = s.json()
    exercise = session["exercise"]["exercise_id"]
    lesson_id = session["lesson"]["lesson_id"]
    skill_id = session["lesson"]["skill_id"]
    r = a.post(
        f"/learning/goals/{goal_id}/attempts",
        json={"exercise_id": exercise, "answer": f"Answer {marker}"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    print("A practice answer:", r.status_code)
    rev = a.get(f"/learning/goals/{goal_id}/progress").json()["goal"]["revision"]
    r = a.patch(
        f"/learning/goals/{goal_id}",
        json={
            "title": f"Quadratics renamed {marker}",
            "revision": rev,
            "review_reminders": True,
        },
    )
    print("A renames the goal:", r.status_code)
    r = a.post(
        "/feedback",
        json={"subject_kind": "lesson", "subject_id": lesson_id, "verdict": "yes"},
    )
    print("A: was this lesson useful:", r.status_code)
    course = _start_course_on_thread(org, a_id, thread, f"Course {marker}")
    print("A starts a course from Chat:", course.get("status"))
    card = a.post(f"/learning/goals/{goal_id}/deletion")
    print("A delete card:", card.status_code)
    card_id = card.json().get("event_id")
    mine = json.dumps(a.get("/learning/goals").json()) + json.dumps(
        a.get("/timeline", params={"assistant": True, "thread_id": thread}).json()
    )
    assert marker in mine, "the marker must be visible to its owner"

    # --- B: every GET route with no path parameter ------------------------------
    spec = json.loads(SPEC.read_text())
    routes = sorted(
        p for p, ops in spec["paths"].items() if "get" in ops and "{" not in p
    )
    leaked, statuses = [], {}
    for path in routes:
        try:
            response = b.get(path.removeprefix("/api/v1"))
        except httpx.HTTPError as exc:
            statuses["unreachable"] = statuses.get("unreachable", 0) + 1
            print("  could not read", path, type(exc).__name__)
            continue
        statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
        if marker.lower() in response.text.lower():
            leaked.append(f"{path} -> {response.status_code}")
    print(
        f"B read {len(routes)} GET routes without path parameters from {SPEC.name}: {statuses}"
    )

    # --- B: A's ids on every route that takes one -------------------------------
    ids = {"goal_id": goal_id, "skill_id": str(skill_id), "event_id": str(card_id)}
    bodies = {
        "baseline": {"answer": "x"},
        "next": {},
        "review": {"skill_id": skill_id},
        "attempts": {"exercise_id": exercise, "answer": "x"},
        "deletion": None,
    }
    not_404 = []
    for path, ops in spec["paths"].items():
        if not path.startswith("/api/v1/learning/") or "{" not in path:
            continue
        url = re.sub(r"\{(\w+)\}", lambda m: ids.get(m.group(1), "0"), path)
        url = url.removeprefix("/api/v1")
        for method in ops:
            if method not in ("get", "post", "patch", "put", "delete"):
                continue
            kwargs: dict = {}
            tail = path.rsplit("/", 1)[-1]
            if method == "patch":
                kwargs["json"] = {"title": "B was here", "revision": 0}
            elif method == "post" and bodies.get(tail) is not None:
                kwargs["json"] = bodies[tail]
            if tail == "attempts":
                kwargs["headers"] = {"Idempotency-Key": str(uuid.uuid4())}
            response = b.request(method.upper(), url, **kwargs)
            print(f"  B {method.upper()} {path}: {response.status_code}")
            if response.status_code != 404:
                not_404.append(f"{method.upper()} {path} -> {response.status_code}")
            if marker.lower() in response.text.lower():
                leaked.append(f"{method.upper()} {path} -> {response.status_code}")
    others = [
        ("GET", f"/today/approvals/{card_id}", {}),
        ("GET", "/timeline", {"params": {"assistant": True, "thread_id": thread}}),
        (
            "POST",
            "/timeline/actions/settle",
            {"json": {"event_id": card_id, "verb": "decline"}},
        ),
        (
            "POST",
            "/feedback",
            {
                "json": {
                    "subject_kind": "lesson",
                    "subject_id": lesson_id,
                    "verdict": "yes",
                }
            },
        ),
    ]
    for method, url, kwargs in others:
        response = b.request(method, url, **kwargs)
        print(f"  B {method} {url}: {response.status_code} {response.text[:120]}")
        if response.status_code not in (404, 409):
            not_404.append(f"{method} {url} -> {response.status_code}")
        if marker.lower() in response.text.lower():
            leaked.append(f"{method} {url} -> {response.status_code}")
    # A's record is unchanged by B's attempts.
    after = a.get(f"/learning/goals/{goal_id}/progress")
    assert after.status_code == 200 and "B was here" not in after.text, after.text[:200]
    print("A's goal after B's attempts:", after.json()["goal"]["title"])

    if leaked or not_404:
        print("LEAKED:", leaked)
        print("NOT 404:", not_404)
        return 1
    print(f"Marker {marker} appeared in none of B's answers; every id route was 404.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
