"""The core journeys, one person at a time, over real HTTP.

Each test does what a person does in the app and checks what they would see.
What it creates it cleans up where the API offers a way; where it does not
(a Decibyl thread has no delete), the marker in the title says which run left
it behind.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

import pytest
from conftest import expect, wait_for

#: What Decibyl says when it could not reach a model. It is an agent message
#: like any other, so a check that only looks for "a reply" passes on it.
FAILURE_REPLY = "I could not think that through"
REPLY_WAIT_SECONDS = float(os.environ.get("E2E_REPLY_WAIT", "120"))
#: Staging has a model key, so a missing reply there is a failure. Elsewhere
#: (a laptop, a fresh container) it is a skip that says why.
REQUIRE_MODEL = os.environ.get("E2E_REQUIRE_MODEL", "").lower() in {"1", "true"}


def _agent_replies(client, thread_id: str) -> list[dict]:
    page = expect(client.get("/timeline", params=_thread(thread_id)))
    return [
        event
        for event in page.get("events") or []
        if event.get("actor") == "agent" and event.get("kind") == "message"
    ]


def _thread(thread_id: str) -> dict:
    return {"assistant": "true", "thread_id": thread_id}


def _say(client, thread_id: str, text: str) -> None:
    expect(
        client.post(
            "/timeline/message",
            {"assistant": True, "thread_id": thread_id, "text": text},
        )
    )


# --- sign in -----------------------------------------------------------------


def test_both_people_sign_in_to_one_workspace(a, b):
    assert a.user_id and b.user_id and a.user_id != b.user_id
    assert a.organization_id == b.organization_id
    assert a.me.get("email", "").lower() == a.email.lower()


def test_b_is_a_plain_member(a, b):
    members = expect(a.get("/organizations/members"))
    rows = members.get("members", members) if isinstance(members, dict) else members
    roles = {
        str(row.get("email", "")).lower(): row.get("role")
        for row in rows
        if isinstance(row, dict)
    }
    assert roles.get(b.email.lower()) == "member", roles.get(b.email.lower())


# --- Decibyl -----------------------------------------------------------------


@pytest.mark.requires_model
def test_decibyl_answers_with_a_real_reply(a, run_id):
    thread_id = str(uuid.uuid4())
    _say(a, thread_id, f"End-to-end check {run_id}: reply with one short sentence.")
    replies = wait_for(lambda: _agent_replies(a, thread_id), timeout=REPLY_WAIT_SECONDS)
    assert replies, f"no reply in {REPLY_WAIT_SECONDS:.0f}s: check the worker"
    payload = replies[-1].get("payload") or {}
    body = str(payload.get("body") or "")
    could_not = payload.get("failed") or body.startswith(FAILURE_REPLY)
    if could_not and not REQUIRE_MODEL:
        pytest.skip(
            "requires a model: no provider key is configured here (Decibyl "
            "answered with its could-not-reach-a-model apology). Staging sets "
            "E2E_REQUIRE_MODEL=1, where this is a failure."
        )
    assert not payload.get("failed"), f"reply marked failed: {body[:200]}"
    assert not payload.get("stopped"), f"reply marked stopped: {body[:200]}"
    assert not body.startswith(FAILURE_REPLY), body[:200]
    assert body.strip(), "empty reply"


def test_a_new_thread_is_private(a, b, require_flag, marker):
    require_flag("decibyl_private_threads")
    thread_id = str(uuid.uuid4())
    _say(a, thread_id, f"Private thread {marker}")

    mine = expect(a.get("/timeline/threads", params={"limit": 50}))
    assert thread_id in {t.get("thread_id") for t in mine.get("threads") or []}
    own = expect(a.get("/timeline", params=_thread(thread_id)))
    assert any(marker in str(e.get("payload")) for e in own.get("events") or [])

    theirs = expect(b.get("/timeline/threads", params={"limit": 50}))
    assert thread_id not in {t.get("thread_id") for t in theirs.get("threads") or []}
    read = b.get("/timeline", params=_thread(thread_id))
    assert read.status_code in (403, 404) or not (read.json().get("events")), (
        f"B read A's thread: HTTP {read.status_code}"
    )
    assert marker not in read.text
    draft = b.get("/timeline/draft", params={"thread_id": thread_id})
    assert marker not in draft.text and not (
        draft.status_code == 200 and (draft.json() or {}).get("text")
    )
    # Writing into somebody else's thread is refused, not appended.
    intrude = b.post(
        "/timeline/message",
        {"assistant": True, "thread_id": thread_id, "text": "B was here"},
    )
    assert intrude.status_code in (403, 404), f"HTTP {intrude.status_code}"
    recents = expect(b.get("/timeline/recents"))
    assert marker not in str(recents)


# --- an action card: exactly what will happen, run once ---------------------


def _card(client, event_id: int, organization_id: int) -> dict:
    return expect(
        client.get(
            f"/me/settings/cards/{event_id}",
            params={"organization_id": organization_id},
        )
    )


def test_action_card_shows_the_exact_effect_and_runs_once(a, b, require_flag, marker):
    """Delete a saved item through its card: the card names the item, a
    confirm sent twice at once settles once, and the item is gone once."""
    require_flag("saved_items")
    item = expect(
        a.post(
            "/me/saved",
            {"title": marker, "kind": "note", "body": marker, "visibility": "private"},
        )
    )
    card = expect(a.post(f"/me/saved/{item['id']}/delete"))
    assert card["state"] == "proposed"
    assert card["args"]["title"] == marker, card["args"]
    assert card["effect"], "a card must say what it will do"
    org = a.organization_id

    # Nobody else can see or settle it.
    other = b.get(
        f"/me/settings/cards/{card['event_id']}", params={"organization_id": org}
    )
    assert other.status_code in (403, 404), f"B read A's card: HTTP {other.status_code}"
    assert marker not in other.text
    stolen = b.post(
        f"/me/settings/cards/{card['event_id']}/settle",
        json={"organization_id": org, "verb": "confirm", "version": card["version"]},
    )
    assert stolen.status_code in (403, 404, 409), f"HTTP {stolen.status_code}"

    # Two confirms at the same moment: one wins, the other is told so.
    results: list[int] = []
    gate = threading.Barrier(2)

    def confirm():
        gate.wait()
        response = a.post(
            f"/me/settings/cards/{card['event_id']}/settle",
            json={
                "organization_id": org,
                "verb": "confirm",
                "version": card["version"],
            },
        )
        results.append(response.status_code)

    workers = [threading.Thread(target=confirm) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert sorted(results) == [200, 409], results
    again = a.post(
        f"/me/settings/cards/{card['event_id']}/settle",
        json={"organization_id": org, "verb": "confirm", "version": card["version"]},
    )
    assert again.status_code == 409, f"third confirm: HTTP {again.status_code}"

    settled = wait_for(
        lambda: (
            lambda c: c if c["state"] in ("done", "failed", "outcome_unknown") else None
        )(_card(a, card["event_id"], org)),
        timeout=60,
    )
    assert settled, "the card never ran: is the worker up?"
    assert settled["state"] == "done", settled
    listed = expect(a.get("/me/saved", params={"scope": "personal"}))
    assert item["id"] not in {i["id"] for i in listed.get("items") or []}


def test_email_send_card_shows_exactly_what_will_be_sent(a, require_flag, marker):
    """The send card carries the exact account, recipient, subject and words.
    Only proposed and declined here: confirming would send real mail."""
    require_flag("identity_email")
    proposed = a.post(
        "/me/email-identity/send",
        json={"to": a.email, "subject": marker, "body": f"Body {marker}"},
    )
    if proposed.status_code == 409:
        pytest.skip(
            f"no sendable Decibyl address here: {proposed.json().get('detail')}"
        )
    card = expect(proposed)
    text = str(card)
    assert (
        a.email.lower() in text.lower() and marker in text and f"Body {marker}" in text
    )
    settled = a.post(
        "/timeline/actions/settle",
        json={
            "event_id": card["event_id"],
            "verb": "decline",
            "version": card.get("version"),
        },
    )
    assert settled.status_code == 200, settled.text[:300]


# --- Today -------------------------------------------------------------------


def test_today_loads(a, require_flag):
    require_flag("today_list")
    today = expect(a.get("/today"))
    sections = today.get("sections") or {}
    assert sections, today
    failed = [
        name for name, s in sections.items() if (s or {}).get("state") == "failed"
    ]
    assert not failed, f"Today sections failed: {failed}"


def test_reminder_preview_then_save(a, require_flag, marker):
    require_flag("today_reminders")
    draft = {
        "title": marker,
        "date": "tomorrow",
        "local_time": "09:00",
        "channel": "in_app",
    }
    preview = expect(a.post("/today/reminders/preview", draft))
    assert not preview.get("problems"), preview["problems"]
    assert preview.get("sentence") and preview.get("schedule_key")

    stale = a.post("/today/reminders", {**draft, "schedule_key": "not-the-preview"})
    assert stale.status_code == 422, "a save must match what was previewed"

    saved = expect(
        a.post("/today/reminders", {**draft, "schedule_key": preview["schedule_key"]})
    )
    try:
        assert saved["status"] == "active" and saved["title"] == marker
        listed = expect(a.get("/today/reminders"))
        assert saved["id"] in {r["id"] for r in listed.get("reminders") or []}
    finally:
        expect(a.post(f"/today/reminders/{saved['id']}/status", {"verb": "cancel"}))


def test_event_on_today(a, require_flag, marker):
    require_flag("today_reminders")
    event = expect(
        a.post(
            "/today/events",
            {
                "title": marker,
                "date": "tomorrow",
                "local_time": "10:00",
                "reminders": [0],
                "channel": "in_app",
            },
        )
    )
    try:
        assert event["title"] == marker
        listed = expect(a.get("/today/reminders"))
        assert marker in str(listed.get("events")), "the event is not on Today"
    finally:
        expect(a.post(f"/today/events/{event['id']}/cancel"))


def test_daily_brief(a, require_flag):
    require_flag("daily_brief")
    expect(a.get("/today/brief"))
    brief = expect(a.post("/today/brief/refresh"))
    assert brief.get("status") != "failed", brief
    assert "sections" in brief and "sources" in brief


# --- saved items, tasks, meetings, memory -----------------------------------


def test_saved_item(a, b, require_flag, marker):
    require_flag("saved_items")
    item = expect(
        a.post(
            "/me/saved",
            {"title": marker, "kind": "note", "body": "x", "visibility": "private"},
        )
    )
    try:
        assert item["visibility"] == "private" and item["mine"]
        listed = expect(a.get("/me/saved", params={"scope": "personal"}))
        assert item["id"] in {i["id"] for i in listed["items"]}
        renamed = expect(
            a.patch(f"/me/saved/{item['id']}", {"title": f"{marker} renamed"})
        )
        assert renamed["title"] == f"{marker} renamed"
        other = b.get(f"/me/saved/{item['id']}")
        assert other.status_code in (403, 404) and marker not in other.text
    finally:
        _delete_saved(a, item["id"])


def _delete_saved(client, item_id) -> None:
    card = client.post(f"/me/saved/{item_id}/delete")
    if card.status_code != 200:
        return
    card = card.json()
    client.post(
        f"/me/settings/cards/{card['event_id']}/settle",
        json={
            "organization_id": client.organization_id,
            "verb": "confirm",
            "version": card["version"],
        },
    )


def test_tasks(a, b, marker):
    task = expect(
        a.post("/tasks", {"title": marker, "brief": f"Check {marker}"}), 200, 201
    )
    try:
        listed = expect(a.get("/tasks"))
        assert task["id"] in {t["id"] for t in listed["tasks"]}
        expect(a.get(f"/tasks/{task['id']}"))
        # Known by design: the workspace task board is shared.
        theirs = expect(b.get("/tasks"))
        assert task["id"] in {t["id"] for t in theirs["tasks"]}
    finally:
        expect(a.delete(f"/tasks/{task['id']}"), 200, 204)
    assert a.get(f"/tasks/{task['id']}").status_code == 404


def test_meeting_from_notes(a, b, require_flag, marker):
    require_flag("meeting_capture")
    meeting = expect(
        a.post(
            "/meetings",
            {
                "source": "notes",
                "title": marker,
                "notes": f"{marker}: we agreed to send the quote on Friday.",
                "language": "unknown",
            },
        ),
        200,
        201,
    )
    try:
        assert meeting["title"] == marker
        listed = expect(a.get("/meetings"))
        assert meeting["id"] in {m["id"] for m in listed["meetings"]}
        read = expect(a.get(f"/meetings/{meeting['id']}"))
        assert marker in str(read)
        other = b.get(f"/meetings/{meeting['id']}")
        assert other.status_code in (403, 404) and marker not in other.text
    finally:
        expect(a.delete(f"/meetings/{meeting['id']}"))


def test_memory_manager(a, require_flag):
    require_flag("memory_manager")
    mine = expect(a.get("/me/memory"))
    assert "mine" in mine and "workspace" in mine


def test_workspace_memory(a, marker):
    expect(a.get("/organisation/memory"))
    key = marker.replace("-", "_")
    added = expect(a.post("/organisation/memory/facts", {"facts": {key: marker}}))
    facts = added.get("facts") or []
    assert facts, added
    try:
        listed = expect(a.get("/organisation/memory"))
        assert marker in str(listed)
    finally:
        for fact in facts:
            a.post(f"/organisation/memory/{fact['id']}/status", {"status": "rejected"})


# --- settings, models, recents, usage ----------------------------------------


@pytest.mark.parametrize(
    "path,flag",
    [
        ("/organizations/models", None),
        ("/timeline/brains?assistant=true", None),
        ("/me/settings/profile", "settings_shell"),
        ("/me/preferences", "member_preferences"),
        ("/settings/models/inheritance", "model_inheritance"),
    ],
)
def test_settings_and_models_load(a, b, require_flag, path, flag):
    if flag:
        require_flag(flag)
    for client in (a, b):
        started = time.monotonic()
        expect(client.get(path))
        assert time.monotonic() - started < 5


def test_recents(a):
    recents = expect(a.get("/timeline/recents", params={"limit": 12}))
    assert isinstance(recents.get("items"), list)


def test_usage(a):
    usage = expect(a.get("/organizations/usage/agents", params={"days": 30}))
    assert {"agents", "models", "totals"} <= set(usage)
