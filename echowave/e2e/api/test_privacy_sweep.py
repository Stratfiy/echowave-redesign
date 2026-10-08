"""Privacy sweep: what A keeps private never reaches B, on any screen.

A creates one of every private thing the API lets a person make, each
carrying its own unique marker. Then B -- a plain member of the same
workspace -- reads every GET route with no path parameter (the list comes
from the OpenAPI document, so a route added later is swept too), plus each
item directly by id, and no marker may appear in any answer.

A's own reads are the control: every marker must be visible to A somewhere,
or the sweep would pass for the wrong reason (a marker nobody can see proves
nothing about B). Search routes are searched for the markers' common prefix,
and a body that echoes the query back is not a leak -- only a whole marker
counts.

Known by design and so not created here: the workspace task board is
shared with every member (``test_journeys.test_tasks`` asserts that).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest
from conftest import API, BASE, expect
from routes import Route, flags_for, get_routes, read

pytestmark = pytest.mark.privacy


@dataclass
class Private:
    kind: str
    marker: str
    #: Where A reads it back directly (the control), and where B must not.
    direct: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)


@pytest.fixture(scope="module")
def prefix(run_id) -> str:
    return f"e2epriv{run_id}"


@pytest.fixture(scope="module")
def made(a, prefix, require_flag) -> dict[str, Private]:
    """Create A's private things. Each kind whose flag is off is recorded as
    skipped (and reported by ``test_a_made_every_private_kind``)."""
    items: dict[str, Private] = {}
    skipped: dict[str, str] = {}
    cleanups: list = []

    def mark(kind: str) -> str:
        return f"{prefix}{kind}{uuid.uuid4().hex[:6]}"

    def flag_on(*names: str) -> str | None:
        try:
            require_flag(*names)
        except pytest.skip.Exception as exc:
            return str(exc)
        return None

    # A private Decibyl thread.
    if why := flag_on("decibyl_private_threads"):
        skipped["thread"] = why
    else:
        m, thread_id = mark("thread"), str(uuid.uuid4())
        expect(
            a.post(
                "/timeline/message",
                {"assistant": True, "thread_id": thread_id, "text": m},
            )
        )
        items["thread"] = Private(
            "thread",
            m,
            ["/timeline"],
            {"assistant": "true", "thread_id": thread_id},
        )

    # A temporary conversation: private and short-lived by design.
    if why := flag_on("memory_manager"):
        skipped["temporary"] = why
    else:
        m = mark("temporary")
        temp = expect(a.post("/me/temporary-conversations"))
        expect(
            a.post(
                "/timeline/message",
                {"assistant": True, "thread_id": temp["thread_id"], "text": m},
            )
        )
        items["temporary"] = Private(
            "temporary",
            m,
            ["/timeline", f"/me/temporary-conversations/{temp['thread_id']}"],
            {"assistant": "true", "thread_id": temp["thread_id"]},
        )

    # A saved item, private.
    if why := flag_on("saved_items"):
        skipped["saved"] = why
    else:
        m = mark("saved")
        saved = expect(
            a.post(
                "/me/saved",
                {"title": m, "kind": "note", "body": m, "visibility": "private"},
            )
        )
        items["saved"] = Private("saved", m, [f"/me/saved/{saved['id']}"])
        cleanups.append(lambda: _delete_saved(a, saved["id"]))

    # A reminder and an event on Today.
    if why := flag_on("today_reminders"):
        skipped["reminder"] = skipped["event"] = why
    else:
        m = mark("reminder")
        draft = {
            "title": m,
            "date": "tomorrow",
            "local_time": "08:30",
            "channel": "in_app",
        }
        preview = expect(a.post("/today/reminders/preview", draft))
        reminder = expect(
            a.post(
                "/today/reminders", {**draft, "schedule_key": preview["schedule_key"]}
            )
        )
        items["reminder"] = Private(
            "reminder", m, [f"/today/reminders/{reminder['id']}"]
        )
        cleanups.append(
            lambda: a.post(
                f"/today/reminders/{reminder['id']}/status", {"verb": "cancel"}
            )
        )

        m = mark("event")
        event = expect(
            a.post(
                "/today/events",
                {
                    "title": m,
                    "date": "tomorrow",
                    "local_time": "11:00",
                    "reminders": [0],
                    "channel": "in_app",
                },
            )
        )
        items["event"] = Private("event", m, ["/today/reminders"])
        cleanups.append(lambda: a.post(f"/today/events/{event['id']}/cancel"))

    # A meeting from notes.
    if why := flag_on("meeting_capture"):
        skipped["meeting"] = why
    else:
        m = mark("meeting")
        meeting = expect(
            a.post(
                "/meetings",
                {
                    "source": "notes",
                    "title": m,
                    "notes": f"{m} notes",
                    "language": "unknown",
                },
            ),
            200,
            201,
        )
        items["meeting"] = Private("meeting", m, [f"/meetings/{meeting['id']}"])
        cleanups.append(lambda: a.delete(f"/meetings/{meeting['id']}"))

    # Something A owes, kept private.
    if why := flag_on("follow_up_ledger"):
        skipped["commitment"] = why
    else:
        m = mark("commitment")
        owed = expect(
            a.post(
                "/helpers/commitments",
                {
                    "direction": "i_owe",
                    "counterparty": m,
                    "description": m,
                    "visibility": "private",
                },
            ),
            200,
            201,
        )
        items["commitment"] = Private(
            "commitment", m, [f"/helpers/commitments/{owed['uuid']}"]
        )
        cleanups.append(
            lambda: a.patch(
                f"/helpers/commitments/{owed['uuid']}",
                {"revision": owed["revision"], "status": "cancelled"},
            )
        )

    # A's own instructions to Decibyl: a personal setting.
    if why := flag_on("settings_shell", "member_preferences"):
        skipped["instructions"] = why
    else:
        m = mark("instructions")
        before = expect(a.get("/me/settings/profile"))
        after = expect(
            a.put(
                "/me/settings/profile",
                {"revision": before["revision"], "custom_instructions": m},
            )
        )
        items["instructions"] = Private("instructions", m, ["/me/settings/profile"])
        cleanups.append(
            lambda: a.put(
                "/me/settings/profile",
                {
                    "revision": expect(a.get("/me/settings/profile"))["revision"],
                    "custom_instructions": before.get("custom_instructions") or "",
                },
            )
        )
        assert after.get("custom_instructions") == m

    # A's Decibyl address (IDENTITY.md, screen 23). Reserved, never set up,
    # released afterwards; a name once held is not given out again, which
    # is why the marker is unique per run.
    if why := flag_on("identity_email"):
        skipped["email_alias"] = why
    else:
        m = mark("alias")[-32:].lower()
        reserved = a.post("/me/email-identity/reserve", {"alias": m})
        if reserved.status_code == 200:
            items["email_alias"] = Private("email_alias", m, ["/me/email-identity"])
            cleanups.append(lambda: a.post("/me/email-identity/release"))
        else:
            skipped["email_alias"] = (
                f"could not reserve: HTTP {reserved.status_code} {reserved.text[:120]}"
            )

    # A's own browser for push, named by A (screen 21). Needs the operator's
    # push keys; without them the route says so and nothing is stored.
    if why := flag_on("identity_notifications"):
        skipped["push_device"] = why
    else:
        m = mark("device")
        added = a.post(
            "/me/push-subscriptions",
            {
                "endpoint": f"https://fcm.googleapis.com/fcm/send/{m}",
                "keys": {
                    "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
                    "auth": "tBHItJI5svbpez7KI4CCXg",
                },
                "device_label": m,
            },
        )
        if added.status_code == 200:
            items["push_device"] = Private("push_device", m, ["/me/notifications"])
            ids = [d["id"] for d in added.json()["devices"] if d["label"] == m]
            cleanups.extend(
                lambda i=i: a.delete(f"/me/push-subscriptions/{i}") for i in ids
            )
        else:
            skipped["push_device"] = (
                f"not configured here: HTTP {added.status_code} {added.text[:120]}"
            )

    # Why A connected an app for themselves: the consent is recorded before
    # the app's sign-in, which is never finished here (screen 22).
    if why := flag_on("identity_connections", "connections_per_person"):
        skipped["consent"] = why
    else:
        m = mark("consent")
        started = a.post(
            "/me/connections/start",
            {"toolkit": "gmail", "scope": "mine", "purpose": m},
        )
        if started.status_code == 200:
            items["consent"] = Private("consent", m, ["/me/connections"])
        else:
            skipped["consent"] = (
                f"not configured here: HTTP {started.status_code} {started.text[:120]}"
            )

    made_items = dict(items)
    made_items["__skipped__"] = skipped  # type: ignore[assignment]
    yield made_items
    for cleanup in reversed(cleanups):
        try:
            cleanup()
        except Exception:  # a failed cleanup must not hide the result
            pass


def _delete_saved(client, item_id) -> None:
    card = client.post(f"/me/saved/{item_id}/delete")
    if card.status_code == 200:
        body = card.json()
        client.post(
            f"/me/settings/cards/{body['event_id']}/settle",
            json={
                "organization_id": client.organization_id,
                "verb": "confirm",
                "version": body["version"],
            },
        )


def _items(made) -> list[Private]:
    return [v for k, v in made.items() if k != "__skipped__"]


def _leaks(body: str, made) -> list[str]:
    return [p.kind for p in _items(made) if p.marker in body]


@pytest.fixture(scope="module")
def sweep_routes(prefix) -> list[Route]:
    return get_routes(API, marker=prefix)


@pytest.fixture(scope="module")
def a_sweep(a, made, sweep_routes) -> dict[str, list[str]]:
    """What A sees on the swept routes: marker kind -> routes it showed on."""
    seen: dict[str, list[str]] = {}
    for route in sweep_routes:
        answer = read(a.http, BASE, route, timeout=30)
        assert answer.status != 429, f"A's control read of {route.id} was rate-limited"
        body = answer.body
        for kind in _leaks(body, made):
            seen.setdefault(kind, []).append(route.id)
    return seen


def test_a_made_every_private_kind(made):
    skipped = made["__skipped__"]
    if skipped:
        pytest.skip(
            "not created, so not swept: "
            + "; ".join(f"{kind} ({why})" for kind, why in sorted(skipped.items()))
        )


KINDS = [
    "thread",
    "temporary",
    "saved",
    "reminder",
    "event",
    "meeting",
    "commitment",
    "instructions",
    "email_alias",
    "push_device",
    "consent",
]


@pytest.mark.parametrize("kind", KINDS)
def test_control_a_sees_their_own(a, made, a_sweep, kind):
    if kind not in made:
        pytest.skip(made["__skipped__"].get(kind, "not created"))
    item = made[kind]
    direct = [
        path
        for path in item.direct
        if item.marker in a.get(path, params=item.params).text
    ]
    assert a_sweep.get(kind) or direct, (
        f"A cannot see their own {kind} anywhere: the sweep would prove nothing"
    )


@pytest.mark.parametrize("kind", KINDS)
def test_b_cannot_read_it_directly(b, made, kind):
    if kind not in made:
        pytest.skip(made["__skipped__"].get(kind, "not created"))
    item = made[kind]
    for path in item.direct:
        response = b.get(path, params=item.params)
        assert item.marker not in response.text, (
            f"B read A's {kind} at {path}: HTTP {response.status_code}"
        )


def pytest_generate_tests(metafunc):
    if "route" in metafunc.fixturenames:
        try:
            routes = get_routes(API)
        except Exception as exc:
            metafunc.parametrize("route", [pytest.param(None, id=f"openapi: {exc}")])
            return
        metafunc.parametrize(
            "route", [r.path for r in routes], ids=[r.id for r in routes]
        )


def test_b_never_sees_a_private_marker(b, made, sweep_routes, route, require_flag):
    if route is None:
        pytest.fail(f"could not read {API}/openapi.json")
    if not _items(made):
        pytest.skip("A could make nothing private here (every flag off)")
    swept = next(r for r in sweep_routes if r.path == route)
    answer = read(b.http, BASE, swept, timeout=30)
    assert answer.status != 429, "still rate-limited after waiting: this proves nothing"
    if answer.status == 404 and flags_for(route):
        require_flag(*flags_for(route), where=route.removeprefix("/api/v1"))
    leaked = _leaks(answer.body, made)
    assert not leaked, f"B saw A's private {', '.join(leaked)} (HTTP {answer.status})"
