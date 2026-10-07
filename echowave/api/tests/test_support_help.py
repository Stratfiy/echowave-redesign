"""Help: a person's support request (launch stream `support`, screen 28).

Done when: a person can open a request about a task or a reply, see exactly
what support will be shown before sending (details by default, words only
when chosen), follow it in a thread, reply, resolve and reopen it, and add a
file -- and nobody else (a colleague, another workspace) can see it, and
staff internal notes never reach it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.support import tickets
from api.services.workflow import task_ledger
from api.tests.support.help_desk import (
    cleanup,
    client_as,
    make_setting,
    person,
    staff,
    switch_on,
)

TITLE = "Call Dr Rao about the MRI results"
BRIEF = "Ask whether Thursday 4pm works and confirm the scan room."


@pytest.fixture
async def desk(test_engine):
    ids = await make_setting()
    task, _ = await task_ledger.create(
        organization_id=ids.org_a, title=TITLE, brief=BRIEF, created_by=ids.customer
    )
    ids.task = task.id
    yield ids
    await cleanup(ids)


@pytest.fixture
def help_on(monkeypatch):
    switch_on(monkeypatch, "SUPPORT_HELP_ENABLED")


def _new(**extra):
    return {
        "category": "something_failed",
        "description": "My call task failed and I do not know why.",
        **extra,
    }


@pytest.mark.asyncio
class TestOff:
    async def test_every_help_route_is_not_there_while_off(self, desk):
        async with client_as(person(desk.customer, desk.org_a)) as client:
            for method, path in (
                ("get", "/api/v1/help/options"),
                ("get", "/api/v1/help/tickets"),
                ("post", "/api/v1/help/share-preview"),
            ):
                response = await getattr(client, method)(
                    path, **({"json": {}} if method == "post" else {})
                )
                assert response.status_code == 404, path

    async def test_on_for_one_workspace_only(self, desk, monkeypatch):
        monkeypatch.setattr(
            constants, "FEATURE_ORG_OVERRIDES", f"support_help:{desk.org_a}"
        )
        async with client_as(person(desk.customer, desk.org_a)) as client:
            assert (await client.get("/api/v1/help/options")).status_code == 200
        async with client_as(person(desk.stranger, desk.org_b)) as client:
            assert (await client.get("/api/v1/help/options")).status_code == 404


@pytest.mark.asyncio
class TestWhatIsShared:
    async def test_preview_offers_details_by_default_and_words_only_on_request(
        self, desk, help_on
    ):
        async with client_as(person(desk.customer, desk.org_a)) as client:
            response = await client.post(
                "/api/v1/help/share-preview",
                json={"affected_kind": "task", "affected_id": desk.task},
            )
        assert response.status_code == 200
        body = response.json()
        sections = {s["key"]: s for s in body["sections"]}
        assert sections["account"]["included"] and sections["account"]["required"]
        assert sections["task_metadata"]["included"] is True
        assert sections["content"]["included"] is False
        # The preview shows the words that would go, so the choice is informed.
        content_values = [f["value"] for f in sections["content"]["fields"]]
        assert TITLE in content_values
        meta = {f["label"]: f["value"] for f in sections["task_metadata"]["fields"]}
        assert meta["Task"] == f"#{desk.task}"
        assert meta["State"] == "queued"
        assert TITLE not in str(sections["task_metadata"])
        assert "recordings" in body["not_shared"]

    async def test_ticket_keeps_exactly_the_default_share(self, desk, help_on):
        async with client_as(person(desk.customer, desk.org_a)) as client:
            created = await client.post(
                "/api/v1/help/tickets",
                json=_new(affected_kind="task", affected_id=desk.task),
            )
            assert created.status_code == 201
            ticket_id = created.json()["ticket"]["id"]
            mine = (await client.get(f"/api/v1/help/tickets/{ticket_id}")).json()
        assert [s["key"] for s in mine["shared"]["sections"]] == [
            "account",
            "task_metadata",
        ]
        assert mine["shared"]["left_out"] == ["content"]
        # Staff see what was shared, and not the task's words.
        case = await tickets.case(ticket_id=ticket_id, staff_id=desk.agent)
        assert TITLE not in str(case) and BRIEF not in str(case)
        assert f"#{desk.task}" in str(case["shared"])

    async def test_words_are_shared_only_when_chosen(self, desk, help_on):
        async with client_as(person(desk.customer, desk.org_a)) as client:
            created = await client.post(
                "/api/v1/help/tickets",
                json=_new(
                    affected_kind="task",
                    affected_id=desk.task,
                    share=["task_metadata", "content"],
                ),
            )
        ticket_id = created.json()["ticket"]["id"]
        case = await tickets.case(ticket_id=ticket_id, staff_id=desk.agent)
        assert TITLE in str(case["shared"]) and BRIEF in str(case["shared"])

    async def test_nothing_about_the_task_when_details_are_switched_off(
        self, desk, help_on
    ):
        async with client_as(person(desk.customer, desk.org_a)) as client:
            created = await client.post(
                "/api/v1/help/tickets",
                json=_new(affected_kind="task", affected_id=desk.task, share=[]),
            )
        ticket_id = created.json()["ticket"]["id"]
        case = await tickets.case(ticket_id=ticket_id, staff_id=desk.agent)
        assert [s["key"] for s in case["shared"]["sections"]] == ["account"]
        # Live diagnostics are no wider than the share.
        assert case["diagnostics"]["task"] is None

    async def test_a_failed_reply_shares_the_question_and_reply_only_when_chosen(
        self, desk, help_on
    ):
        await db_client.record_agent_event(
            organization_id=desk.org_a,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.HUMAN.value,
            summary="Book my scan for Thursday",
            payload={"body": "Book my scan for Thursday", "author_id": desk.customer},
        )
        reply = await db_client.record_agent_event(
            organization_id=desk.org_a,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.AGENT.value,
            summary="Sorry, that did not work.",
            payload={
                "body": "Sorry, that did not work.",
                "failed": True,
                "model": "anthropic:sonnet",
            },
        )
        async with client_as(person(desk.customer, desk.org_a)) as client:
            preview = (
                await client.post(
                    "/api/v1/help/share-preview",
                    json={
                        "affected_kind": "reply",
                        "affected_id": reply,
                        "share": ["task_metadata", "content"],
                    },
                )
            ).json()
        sections = {s["key"]: s for s in preview["sections"]}
        meta = {f["label"]: f["value"] for f in sections["task_metadata"]["fields"]}
        assert meta["State"] == "failed" and meta["Model"] == "anthropic:sonnet"
        words = {f["label"]: f["value"] for f in sections["content"]["fields"]}
        assert words == {
            "Your message": "Book my scan for Thursday",
            "Decibyl's reply": "Sorry, that did not work.",
        }

    async def test_a_task_from_another_workspace_cannot_be_attached(
        self, desk, help_on
    ):
        async with client_as(person(desk.stranger, desk.org_b)) as client:
            response = await client.post(
                "/api/v1/help/tickets",
                json=_new(affected_kind="task", affected_id=desk.task),
            )
        assert response.status_code == 404

    async def test_unknown_share_is_refused(self, desk, help_on):
        async with client_as(person(desk.customer, desk.org_a)) as client:
            response = await client.post(
                "/api/v1/help/share-preview",
                json={
                    "affected_kind": "task",
                    "affected_id": desk.task,
                    "share": ["everything"],
                },
            )
        assert response.status_code == 422


@pytest.mark.asyncio
class TestTicket:
    async def test_submitting_twice_with_one_key_is_one_ticket(self, desk, help_on):
        async with client_as(person(desk.customer, desk.org_a)) as client:
            first = await client.post(
                "/api/v1/help/tickets", json=_new(), headers={"Idempotency-Key": "k-1"}
            )
            again = await client.post(
                "/api/v1/help/tickets", json=_new(), headers={"Idempotency-Key": "k-1"}
            )
            mine = (await client.get("/api/v1/help/tickets")).json()
        assert first.status_code == 201 and again.status_code == 200
        assert again.json()["created"] is False
        assert first.json()["ticket"]["id"] == again.json()["ticket"]["id"]
        assert len(mine) == 1
        assert mine[0]["status"] == "open"
        assert mine[0]["subject"] == "My call task failed and I do not know why."

    async def test_only_the_requester_in_that_workspace_sees_it(self, desk, help_on):
        async with client_as(person(desk.customer, desk.org_a)) as client:
            ticket_id = (await client.post("/api/v1/help/tickets", json=_new())).json()[
                "ticket"
            ]["id"]
        async with client_as(person(desk.colleague, desk.org_a)) as client:
            assert (
                await client.get(f"/api/v1/help/tickets/{ticket_id}")
            ).status_code == 404
            assert (await client.get("/api/v1/help/tickets")).json() == []
            assert (
                await client.post(
                    f"/api/v1/help/tickets/{ticket_id}/messages", json={"body": "hi"}
                )
            ).status_code == 404
        # The same person, switched to their other workspace.
        async with client_as(person(desk.customer, desk.org_b)) as client:
            assert (
                await client.get(f"/api/v1/help/tickets/{ticket_id}")
            ).status_code == 404
            assert (await client.get("/api/v1/help/tickets")).json() == []

    async def test_a_retried_reply_is_one_message(self, desk, help_on):
        async with client_as(person(desk.customer, desk.org_a)) as client:
            ticket_id = (await client.post("/api/v1/help/tickets", json=_new())).json()[
                "ticket"
            ]["id"]
            for _ in range(2):
                sent = await client.post(
                    f"/api/v1/help/tickets/{ticket_id}/messages",
                    json={"body": "Here is more detail.", "client_key": "reply-1"},
                )
                assert sent.status_code == 201
            thread = (await client.get(f"/api/v1/help/tickets/{ticket_id}")).json()[
                "messages"
            ]
        assert [m["body"] for m in thread].count("Here is more detail.") == 1

    async def test_internal_notes_never_reach_the_customer(self, desk, help_on):
        async with client_as(person(desk.customer, desk.org_a)) as client:
            ticket_id = (await client.post("/api/v1/help/tickets", json=_new())).json()[
                "ticket"
            ]["id"]
        await tickets.add_note(
            ticket_id=ticket_id,
            staff_id=desk.agent,
            body="SECRET: customer seems confused",
            client_key=None,
        )
        await tickets.reply_as_staff(
            ticket_id=ticket_id,
            staff_id=desk.agent,
            body="We are looking into it.",
            client_key="s1",
        )
        async with client_as(person(desk.customer, desk.org_a)) as client:
            detail = (await client.get(f"/api/v1/help/tickets/{ticket_id}")).json()
            listing = (await client.get("/api/v1/help/tickets")).json()
        assert "SECRET" not in str(detail) and "notes" not in detail
        # What must appear: the staff reply, and the "support replied" marker.
        assert [m["body"] for m in detail["messages"]][-1] == "We are looking into it."
        assert detail["status"] == "waiting_on_customer"
        assert listing[0]["support_replied"] is True

    async def test_resolve_then_reopen_keeps_the_history(self, desk, help_on):
        async with client_as(person(desk.customer, desk.org_a)) as client:
            ticket_id = (await client.post("/api/v1/help/tickets", json=_new())).json()[
                "ticket"
            ]["id"]
            resolved = (
                await client.post(f"/api/v1/help/tickets/{ticket_id}/resolve")
            ).json()
            assert resolved["status"] == "resolved" and resolved["resolved_at"]
            refused = await client.post(
                f"/api/v1/help/tickets/{ticket_id}/messages", json={"body": "one more"}
            )
            assert refused.status_code == 409
            reopened = (
                await client.post(
                    f"/api/v1/help/tickets/{ticket_id}/reopen",
                    json={"body": "It failed again."},
                )
            ).json()
        assert reopened["status"] == "open" and reopened["reopened_count"] == 1
        bodies = [m["body"] for m in reopened["messages"]]
        assert bodies == [
            "My call task failed and I do not know why.",
            "Marked resolved by you.",
            "It failed again.",
        ]
        assert reopened["shared"]["sections"]

    async def test_events_say_what_happened_and_carry_no_words(
        self, desk, help_on, monkeypatch
    ):
        monkeypatch.setattr(constants, "EVENT_CATALOGUE_ENABLED", True)
        monkeypatch.setattr(constants, "ANALYTICS_PSEUDONYM_KEY", "test-key")
        async with client_as(person(desk.customer, desk.org_a)) as client:
            ticket_id = (await client.post("/api/v1/help/tickets", json=_new())).json()[
                "ticket"
            ]["id"]
            await client.post(f"/api/v1/help/tickets/{ticket_id}/resolve")
            await client.post(f"/api/v1/help/tickets/{ticket_id}/reopen", json={})
        async with db_client.async_session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT name, envelope FROM analytics_outbox "
                        "WHERE name IN ('ticket_created','ticket_resolved','ticket_reopened') "
                        "ORDER BY occurred_at DESC LIMIT 3"
                    )
                )
            ).all()
            await session.execute(
                text("DELETE FROM analytics_outbox WHERE name LIKE 'ticket_%'")
            )
            await session.commit()
        names = sorted(r[0] for r in rows)
        assert names == ["ticket_created", "ticket_reopened", "ticket_resolved"]
        assert "do not know why" not in str([r[1] for r in rows])


class _FakeStorage:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    async def acreate_file_from_bytes(self, key, data):
        self.files[key] = data
        return True

    async def aget_signed_url(self, key, expiration=3600, **_):
        return f"https://storage.test/{key}?ttl={expiration}"


@pytest.mark.asyncio
class TestAttachments:
    async def test_storage_not_set_up_is_said_and_the_ticket_is_kept(
        self, desk, help_on
    ):
        async with client_as(person(desk.customer, desk.org_a)) as client:
            ticket_id = (await client.post("/api/v1/help/tickets", json=_new())).json()[
                "ticket"
            ]["id"]
            response = await client.post(
                f"/api/v1/help/tickets/{ticket_id}/attachments",
                files={"file": ("shot.png", b"\x89PNG....", "image/png")},
            )
            assert response.status_code == 503
            assert "kept" in response.json()["detail"]
            detail = (await client.get(f"/api/v1/help/tickets/{ticket_id}")).json()
        assert detail["attachments"] == [] and len(detail["messages"]) == 1

    async def test_a_file_is_stored_listed_and_opened_by_staff(self, desk, help_on):
        fake = _FakeStorage()
        with patch("api.services.support.attachments._storage", return_value=fake):
            async with client_as(person(desk.customer, desk.org_a)) as client:
                ticket_id = (
                    await client.post("/api/v1/help/tickets", json=_new())
                ).json()["ticket"]["id"]
                stored = await client.post(
                    f"/api/v1/help/tickets/{ticket_id}/attachments",
                    files={"file": ("../shot?.png", b"\x89PNGdata", "image/png")},
                )
                assert stored.status_code == 201
                assert stored.json()["file_name"] == "shot_.png"
                wrong = await client.post(
                    f"/api/v1/help/tickets/{ticket_id}/attachments",
                    files={"file": ("run.sh", b"rm -rf /", "application/x-sh")},
                )
                assert wrong.status_code == 422
                detail = (await client.get(f"/api/v1/help/tickets/{ticket_id}")).json()
            async with client_as(person(desk.colleague, desk.org_a)) as client:
                theirs = await client.post(
                    f"/api/v1/help/tickets/{ticket_id}/attachments",
                    files={"file": ("x.png", b"x", "image/png")},
                )
                assert theirs.status_code == 404
            from api.services.support import attachments

            url = await attachments.staff_link(
                attachment_id=detail["attachments"][0]["id"], staff_id=desk.agent
            )
        assert len(fake.files) == 1 and next(iter(fake.files)).startswith(
            f"support/{desk.org_a}/{ticket_id}/"
        )
        assert url.startswith("https://storage.test/support/")
        async with db_client.async_session() as session:
            audited = await session.scalar(
                text(
                    "SELECT count(*) FROM admin_action_log WHERE actor_user_id = :a "
                    "AND action = 'support_attachment_opened'"
                ),
                {"a": desk.agent},
            )
        assert audited == 1


@pytest.mark.asyncio
async def test_staff_view_of_a_customer_ticket_needs_staff(desk, monkeypatch):
    switch_on(monkeypatch)
    async with client_as(person(desk.customer, desk.org_a)) as client:
        ticket_id = (await client.post("/api/v1/help/tickets", json=_new())).json()[
            "ticket"
        ]["id"]
        assert (
            await client.get(f"/api/v1/admin/support/tickets/{ticket_id}")
        ).status_code == 403
    async with client_as(staff(desk.agent)) as client:
        assert (
            await client.get(f"/api/v1/admin/support/tickets/{ticket_id}")
        ).status_code == 200
