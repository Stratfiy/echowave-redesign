"""People: synced contacts with context (PEOPLE.md).

Done when: a person's contacts arrive from Google and Microsoft (the fake
provider, a real port) and re-sync incrementally; a vCard, a CSV and the
phone's picker import into the same list; a call, a mail, a send and a
meeting through their real code paths add or update the contact and its
last interactions; the brief is rewritten after the debounce window from
what it needs and nothing more, and the person can edit it; duplicates are
suggested and merged only when the person says so; Decibyl can look someone
up by name for the person asking and nobody else; and all of it is off,
404 and silent, while the flag is.
"""

from __future__ import annotations

from datetime import timedelta
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select, update

from api import constants
from api.db import db_client
from api.db.people_models import PeopleSyncModel, PersonModel
from api.services import acting
from api.services.people import briefs, interactions, store
from api.services.people import sync as people_sync
from api.services.people import tools as people_tools
from api.services.people.normalise import Incoming
from api.tests.support import people_fakes
from api.tests.support.people_fixtures import (  # noqa: F401
    client_as,
    fake_provider,
    people,
    people_on,
    worker,
)

VCARD = b"""BEGIN:VCARD
VERSION:3.0
FN:Ravi Kumar
TEL;TYPE=CELL:+91 98765 43210
EMAIL:ravi@example.in
ORG:Kumar Traders
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Priya Sharma
TEL:09812345678
END:VCARD
"""


def google(uid: int) -> str:
    return f"fake-google-{uid}"


async def _ours(people, user) -> list[PersonModel]:
    rows, _ = await store.listing(people.org, user.id, limit=200)
    return rows


# --- off ----------------------------------------------------------------------


@pytest.mark.asyncio
class TestOff:
    async def test_every_route_is_a_404(self, people, monkeypatch):
        monkeypatch.setattr(constants, "PEOPLE_ENABLED", False)
        async with client_as(people.as_user(people.a)) as c:
            for path in (
                "/api/v1/people",
                "/api/v1/people/status",
                "/api/v1/people/merges",
            ):
                assert (await c.get(path)).status_code == 404
            assert (
                await c.post("/api/v1/people", json={"name": "Ravi"})
            ).status_code == 404

    async def test_nothing_is_recorded_and_decibyl_is_not_told(
        self, people, monkeypatch
    ):
        monkeypatch.setattr(constants, "PEOPLE_ENABLED", False)
        got = await interactions.record(
            people.org,
            people.a.id,
            channel="call",
            phone="9876543210",
            line="x",
            ref="run:1",
        )
        assert got is None
        assert await store.count(people.org, people.a.id) == 0
        from api.services.workflow import decibyl

        assert people_tools.TOOL_NAME not in decibyl.system_prompt(people.org)
        assert people_tools.TOOL_NAME not in {
            t["name"] for t in decibyl.office_tools(people.org)
        }


# --- sources ------------------------------------------------------------------


@pytest.mark.asyncio
class TestGoogleSync:
    async def test_first_sync_reads_every_page(self, people, people_on, worker):
        acct = google(people.a.id)
        people_fakes.put(
            acct,
            "c1",
            name="Ravi Kumar",
            phones=["+91 98765 43210"],
            company="Kumar Traders",
        )
        people_fakes.put(acct, "c2", name="Priya Sharma", emails=["Priya@Example.in"])
        people_fakes.put(acct, "c3", name="Anil Mehta", phones=["99887 76655"])
        async with client_as(people.as_user(people.a)) as c:
            started = await c.post("/api/v1/people/sync/google")
            assert started.status_code == 200, started.text
            status = (await c.get("/api/v1/people/status")).json()
        google_row = next(p for p in status["providers"] if p["provider"] == "google")
        assert google_row["state"] == "ok"
        assert google_row["counts"]["added"] == 3
        names = sorted(p.name for p in await _ours(people, people.a))
        assert names == ["Anil Mehta", "Priya Sharma", "Ravi Kumar"]
        # Pagination was followed (page size 2 in the fake).
        assert len([s for s in people_fakes.SEEN if s[0] == acct]) == 2

    async def test_resync_reads_only_changes_and_deletions(
        self, people, people_on, worker
    ):
        acct = google(people.a.id)
        people_fakes.put(acct, "c1", name="Ravi Kumar", phones=["9876543210"])
        people_fakes.put(acct, "c2", name="Priya Sharma", phones=["9812345678"])
        await people_sync.start(people.org, people.a.id, "google")
        people_fakes.SEEN.clear()
        people_fakes.put(
            acct, "c1", name="Ravi Kumar", phones=["9876543210", "9000000001"]
        )
        people_fakes.remove(acct, "c2")
        await people_sync.start(people.org, people.a.id, "google")
        (query,) = [s[2] for s in people_fakes.SEEN if s[0] == acct]
        assert query["syncToken"].startswith("sync-")
        rows = await _ours(people, people.a)
        assert [p.name for p in rows] == ["Ravi Kumar"]
        assert rows[0].phones == ["+919876543210", "+919000000001"]

    async def test_an_expired_sync_point_reads_everything_again(
        self, people, people_on, worker
    ):
        acct = google(people.a.id)
        people_fakes.put(acct, "c1", name="Ravi Kumar", phones=["9876543210"])
        await people_sync.start(people.org, people.a.id, "google")
        people_fakes.book(acct)["faults"].add("expire")
        people_fakes.put(acct, "c2", name="Priya Sharma", phones=["9812345678"])
        await people_sync.start(people.org, people.a.id, "google")
        assert sorted(p.name for p in await _ours(people, people.a)) == [
            "Priya Sharma",
            "Ravi Kumar",
        ]
        rows = await people_sync.status(people.org, people.a.id)
        assert rows[0]["state"] == "ok"

    async def test_no_contacts_permission_is_an_error_in_words_not_zero(
        self, people, people_on, worker
    ):
        people_fakes.book(google(people.a.id))["faults"].add("forbid")
        await people_sync.start(people.org, people.a.id, "google")
        row = (await people_sync.status(people.org, people.a.id))[0]
        assert row["state"] == "error"
        assert "allow contacts" in row["detail"]

    async def test_not_connected_is_a_state_with_its_chip(
        self, people, people_on, monkeypatch
    ):
        from api.services.people import providers

        monkeypatch.setattr(providers, "account_for", AsyncMock(return_value=None))
        rows = await people_sync.status(people.org, people.a.id)
        assert {r["state"] for r in rows} == {"not_connected"}
        async with client_as(people.as_user(people.a)) as c:
            refused = await c.post("/api/v1/people/sync/google")
        assert refused.status_code == 409
        assert "Connect Google Contacts" in refused.json()["detail"]

    async def test_without_per_person_connections_it_needs_setup(
        self, people, people_on, monkeypatch
    ):
        # Off the fake, a workspace's own connection is never read as a
        # person's address book.
        monkeypatch.setattr(constants, "PEOPLE_FAKE_PROVIDER_URL", None)
        rows = await people_sync.status(people.org, people.a.id)
        assert {r["state"] for r in rows} == {"needs_setup"}

    async def test_a_stale_resync_is_started_by_the_hourly_sweep(
        self, people, people_on, worker
    ):
        people_fakes.put(google(people.a.id), "c1", name="Ravi", phones=["9876543210"])
        await people_sync.start(people.org, people.a.id, "google")
        async with db_client.async_session() as session:
            await session.execute(
                update(PeopleSyncModel)
                .where(PeopleSyncModel.user_id == people.a.id)
                .values(last_synced_at=store.now() - timedelta(hours=7))
            )
            await session.commit()
        assert await people_sync.resync_due() >= 1


@pytest.mark.asyncio
class TestMicrosoftSync:
    async def test_delta_pages_and_removals(self, people, people_on, worker):
        acct = f"fake-microsoft-{people.a.id}"
        people_fakes.put(acct, "m1", name="Sunita Rao", emails=["sunita@example.org"])
        people_fakes.put(acct, "m2", name="Dev", phones=["9811111111"])
        people_fakes.put(
            acct, "m3", name="Asha", phones=["9822222222"], company="Asha Co"
        )
        await people_sync.start(people.org, people.a.id, "microsoft")
        assert len(await _ours(people, people.a)) == 3
        people_fakes.remove(acct, "m2")
        await people_sync.start(people.org, people.a.id, "microsoft")
        assert sorted(p.name for p in await _ours(people, people.a)) == [
            "Asha",
            "Sunita Rao",
        ]
        last = [s for s in people_fakes.SEEN if s[0] == acct][-1][2]
        assert "$deltatoken" in last


@pytest.mark.asyncio
class TestImports:
    async def test_a_vcard_twice_changes_nothing_the_second_time(
        self, people, people_on
    ):
        async with client_as(people.as_user(people.a)) as c:
            first = (
                await c.post(
                    "/api/v1/people/import",
                    files={"file": ("contacts.vcf", VCARD, "text/vcard")},
                )
            ).json()
            again = (
                await c.post(
                    "/api/v1/people/import",
                    files={"file": ("contacts.vcf", VCARD, "text/vcard")},
                )
            ).json()
        assert (first["added"], first["source"]) == (2, "vcard")
        assert (again["added"], again["unchanged"]) == (0, 2)
        assert again["open_merges"] == 0

    async def test_a_csv_and_the_picker(self, people, people_on):
        csv = b"Name,Mobile,Email\nAnil Mehta,99887 76655,anil@example.in\n"
        async with client_as(people.as_user(people.a)) as c:
            got = (
                await c.post(
                    "/api/v1/people/import", files={"file": ("c.csv", csv, "text/csv")}
                )
            ).json()
            picked = (
                await c.post(
                    "/api/v1/people/import/picker",
                    json={"contacts": [{"name": ["Dev"], "tel": ["98111 11111"]}]},
                )
            ).json()
        assert (
            got["added"] == 1 and picked["added"] == 1 and picked["source"] == "picker"
        )

    async def test_a_bad_file_is_refused_in_words(self, people, people_on):
        async with client_as(people.as_user(people.a)) as c:
            refused = await c.post(
                "/api/v1/people/import",
                files={"file": ("x.pdf", b"%PDF", "application/pdf")},
            )
        assert refused.status_code == 422
        assert ".vcf" in refused.json()["detail"]


# --- duplicates -----------------------------------------------------------------


@pytest.mark.asyncio
class TestMerges:
    async def test_same_number_different_name_is_suggested_never_merged(
        self, people, people_on
    ):
        await store.upsert(
            people.org,
            people.a.id,
            Incoming(name="Ravi Kumar", phones=["9876543210"]).clean(),
            source="vcard",
        )
        await store.upsert(
            people.org,
            people.a.id,
            Incoming(name="Ravi K (shop)", phones=["+91 98765 43210"]).clean(),
            source="csv",
        )
        assert len(await _ours(people, people.a)) == 2  # both kept
        async with client_as(people.as_user(people.a)) as c:
            merges = (await c.get("/api/v1/people/merges")).json()["merges"]
            assert len(merges) == 1
            assert merges[0]["reason"] == "phone"
            assert merges[0]["value"] == "+919876543210"
            kept = (
                await c.post(
                    f"/api/v1/people/merges/{merges[0]['id']}", json={"action": "merge"}
                )
            ).json()
        assert kept["name"] == "Ravi Kumar"
        assert sorted(kept["sources"]) == ["csv", "vcard"]
        assert len(await _ours(people, people.a)) == 1

    async def test_keep_both_stays_decided(self, people, people_on):
        for name in ("Ravi Kumar", "Ravi K"):
            await store.upsert(
                people.org,
                people.a.id,
                Incoming(name=name, emails=["ravi@example.in"]).clean(),
                source="manual",
            )
        (row,) = await store.open_merges(people.org, people.a.id)
        await store.decide_merge(
            people.org, people.a.id, row["merge"].uuid, merge=False
        )
        await store.edit(
            people.org, people.a.id, row["other"].uuid, {"emails": ["ravi@example.in"]}
        )
        assert await store.open_merge_count(people.org, people.a.id) == 0

    async def test_a_merge_moves_the_history(self, people, people_on):
        a = await store.upsert(
            people.org,
            people.a.id,
            Incoming(name="Ravi", phones=["9876543210"]).clean(),
            source="manual",
        )
        await interactions.record(
            people.org,
            people.a.id,
            channel="call",
            phone="9876543210",
            line="Called about the order",
            ref="run:900",
        )
        b = await store.upsert(
            people.org,
            people.a.id,
            Incoming(
                name="Ravi Kumar", phones=["9876543210"], emails=["r@k.in"]
            ).clean(),
            source="manual",
        )
        assert a.person.id != b.person.id
        (row,) = await store.open_merges(people.org, people.a.id)
        kept = await store.decide_merge(
            people.org, people.a.id, row["merge"].uuid, merge=True
        )
        lines = [i.line for i in await store.interactions(kept)]
        assert lines == ["Called about the order"]
        assert kept.emails == ["r@k.in"]


# --- interactions through the real code paths ----------------------------------------


@pytest.fixture
async def dial_rig(people, monkeypatch):
    from api.services.compliance import dnd
    from api.services.telephony import factory, outbound
    from api.services.voice import appointments, readiness

    monkeypatch.setattr(constants, "CALL_FOR_ME_ENABLED", True)
    monkeypatch.setattr(
        readiness,
        "calls",
        AsyncMock(
            return_value=SimpleNamespace(
                state=readiness.AVAILABLE, reason=None, next_step=None
            )
        ),
    )
    workflow = await db_client.create_workflow(
        name="Call and Appointment",
        workflow_definition={"nodes": [], "edges": []},
        user_id=people.a.id,
        organization_id=people.org,
    )
    monkeypatch.setattr(
        appointments,
        "get_policy",
        AsyncMock(return_value={"call_workflow_id": workflow.id}),
    )

    async def may_call(_org, number, **_):
        return number

    monkeypatch.setattr(dnd, "assert_may_call", may_call)
    monkeypatch.setattr(
        db_client,
        "get_default_telephony_configuration",
        AsyncMock(return_value=SimpleNamespace(id=7)),
    )
    monkeypatch.setattr(
        factory, "get_default_telephony_provider", AsyncMock(return_value=MagicMock())
    )
    dial = AsyncMock(return_value=4242)
    monkeypatch.setattr(outbound, "dial_workflow", dial)
    return dial


def _call_card(people) -> dict:
    return {
        "action": "place_call",
        "args": {
            "to": "+919876543210",
            "callee": "Ravi Kumar",
            "purpose": "Confirm Friday's delivery",
            "details": "",
            "principal_user_id": people.a.id,
        },
        "label": "Call Ravi Kumar for you",
        "version": "v1",
    }


@pytest.mark.asyncio
class TestInteractions:
    async def test_a_call_for_me_adds_the_contact_and_its_line(
        self, people, people_on, dial_rig
    ):
        from api.services.voice import call_for_me

        await call_for_me.execute(
            organization_id=people.org, payload=_call_card(people), event_id=9
        )
        (ravi,) = await _ours(people, people.a)
        assert ravi.name == "Ravi Kumar" and ravi.sources == ["decibyl"]
        (line,) = await store.interactions(ravi)
        assert (line.channel, line.line) == (
            "call",
            "Decibyl called for you: Confirm Friday's delivery",
        )
        assert ravi.brief_due_at is not None  # debounced, not written now
        assert ravi.brief is None
        context = dial_rig.await_args.kwargs["extra_context"]
        assert context["principal_user_id"] == people.a.id
        assert context["callee_brief"] == ""  # not allowed yet

    async def test_the_voice_agent_reads_the_brief_only_when_allowed(
        self, people, people_on, dial_rig
    ):
        from api.services.voice import call_for_me

        made = await store.upsert(
            people.org,
            people.a.id,
            Incoming(name="Ravi Kumar", phones=["9876543210"]).clean(),
            source="manual",
        )
        await store.edit(
            people.org,
            people.a.id,
            made.person.uuid,
            {"brief": "Supplier; owes us a revised quote."},
        )
        await call_for_me.execute(
            organization_id=people.org, payload=_call_card(people), event_id=10
        )
        assert dial_rig.await_args.kwargs["extra_context"]["callee_brief"] == ""
        await store.set_agents_may_read(people.org, people.a.id, True)
        await call_for_me.execute(
            organization_id=people.org, payload=_call_card(people), event_id=11
        )
        assert (
            dial_rig.await_args.kwargs["extra_context"]["callee_brief"]
            == "Supplier; owes us a revised quote."
        )
        # B allowing it does not let anything read A's contacts.
        assert (
            await people_tools.brief_for_agent(
                people.org, people.b.id, phone="9876543210"
            )
            is None
        )

    async def test_mail_arriving_at_the_decibyl_address(
        self, people, people_on, monkeypatch
    ):
        from api.services.identity import email_identity

        monkeypatch.setattr(constants, "IDENTITY_EMAIL_ENABLED", True)
        monkeypatch.setattr(constants, "EMAIL_IDENTITY_WEBHOOK_SECRET", "s")
        monkeypatch.setattr(constants, "EMAIL_IDENTITY_DOMAIN", "decibyl.test")
        sent = AsyncMock(return_value=SimpleNamespace(ok=True, error=None))
        monkeypatch.setattr("api.services.messaging.email.send_email", sent)
        monkeypatch.setattr(
            "api.services.messaging.email.email_is_configured", lambda: True
        )
        monkeypatch.setattr("api.services.identity.notifications.notify", AsyncMock())
        alias = f"meera{people.a.id}"
        await email_identity.reserve(people.a.id, people.org, alias)
        await email_identity.provision(people.a.id)
        probe = _mail(
            f"{alias}@decibyl.test",
            sent.await_args.kwargs["subject"],
            "<probe@x>",
            "Decibyl <noreply@decibyl.test>",
        )
        assert (
            await email_identity.receive(f"{alias}@decibyl.test", probe) == "activated"
        )
        assert (
            await store.count(people.org, people.a.id) == 0
        )  # the check is not a contact
        mail = _mail(
            f"{alias}@decibyl.test",
            "Invoice for September",
            "<m1@example.in>",
            "Priya Sharma <Priya@Example.in>",
        )
        assert (
            await email_identity.receive(f"{alias}@decibyl.test", mail) == "delivered"
        )
        assert (
            await email_identity.receive(f"{alias}@decibyl.test", mail) == "duplicate"
        )
        (priya,) = await _ours(people, people.a)
        assert priya.emails == ["priya@example.in"] and priya.name == "Priya Sharma"
        (line,) = await store.interactions(priya)
        assert (line.channel, line.direction) == ("email", "in")
        assert line.line == "Mail to your Decibyl address: Invoice for September"
        assert await store.count(people.org, people.b.id) == 0

    async def test_an_approved_send_and_a_document_on_whatsapp(
        self, people, people_on, monkeypatch
    ):
        from api.services.workflow import actions, connected_tools, documents

        tool = SimpleNamespace(tool_uuid="t1")
        monkeypatch.setattr(db_client, "get_tool_by_uuid", AsyncMock(return_value=tool))
        monkeypatch.setattr(connected_tools, "is_connected", lambda _t: True)
        monkeypatch.setattr(connected_tools, "toolkit_of", lambda _t: "gmail")
        monkeypatch.setattr(
            connected_tools,
            "execute",
            AsyncMock(return_value={"status": "success", "data": {}}),
        )
        monkeypatch.setattr(
            "api.services.workflow.send_approval.note_sent", AsyncMock()
        )
        payload = {
            "action": actions.RUN_TOOL,
            "args": {
                "tool_uuid": "t1",
                "tool_name": "Send email",
                "arguments": {
                    "recipient_email": "anil@example.in",
                    "subject": "Quote attached",
                },
            },
            "confirmed": {"by": people.a.id, "at": "2026-10-09T10:00:00Z"},
        }
        await actions._execute(people.org, payload, event_id=501)
        monkeypatch.setattr(documents, "deliver", AsyncMock(return_value="Sent."))
        doc = {
            "action": actions.SEND_DOCUMENT,
            "args": {
                "file_id": "f1",
                "name": "quote.pdf",
                "channel": "whatsapp",
                "to": "+919988776655",
            },
            "confirmed": {"by": people.a.id, "at": "2026-10-09T10:05:00Z"},
        }
        await actions._execute(people.org, doc, event_id=502)
        rows = {p.name: p for p in await _ours(people, people.a)}
        anil = rows["anil@example.in"]
        assert [i.line for i in await store.interactions(anil)] == [
            "Sent: Quote attached"
        ]
        whatsapp = rows["+919988776655"]
        (line,) = await store.interactions(whatsapp)
        assert (line.channel, line.line) == ("whatsapp", "Sent quote.pdf")
        # The colleague who did not confirm has nothing.
        assert await store.count(people.org, people.b.id) == 0

    async def test_a_meeting_updates_the_named_contact(self, people, people_on):
        from api.services.meetings import processing

        made = await store.upsert(
            people.org,
            people.a.id,
            Incoming(name="Anil Mehta", phones=["9988776655"]).clean(),
            source="manual",
        )
        meeting = SimpleNamespace(
            id=77,
            owner_user_id=people.a.id,
            title="Quarterly review",
            participants=["Anil Mehta", "Sunita Rao"],
            summary={"summary": ["Agreed new rates."]},
            created_at=store.now(),
        )
        from unittest.mock import patch

        with patch.object(
            processing.db_client, "get_meeting_by_id", AsyncMock(return_value=meeting)
        ):
            await processing._people_note(meeting, people.org)
        anil = await store.get(people.org, people.a.id, made.person.uuid)
        (line,) = await store.interactions(anil)
        assert line.line == "Meeting: Quarterly review -- Agreed new rates."
        assert sorted(p.name for p in await _ours(people, people.a)) == [
            "Anil Mehta",
            "Sunita Rao",
        ]

    async def test_a_retried_job_records_once(self, people, people_on):
        for _ in range(3):
            await interactions.record(
                people.org,
                people.a.id,
                channel="call",
                phone="9876543210",
                line="Called",
                ref="run:5",
            )
        (p,) = await _ours(people, people.a)
        assert len(await store.interactions(p)) == 1


def _mail(to: str, subject: str, message_id: str, sender: str) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message.set_content("Hello.")
    return message.as_bytes()


# --- briefs -------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBriefs:
    async def test_debounced_then_written_by_the_sweep(self, people, people_on):
        for n in range(4):
            await interactions.record(
                people.org,
                people.a.id,
                channel="call",
                phone="9876543210",
                name="Ravi",
                line=f"Call {n}",
                ref=f"run:{n}",
            )
        (ravi,) = await _ours(people, people.a)
        due = ravi.brief_due_at
        assert due > store.now() + timedelta(seconds=500)  # one window, not four
        assert await briefs.sweep() == 0  # not due yet
        async with db_client.async_session() as session:
            await session.execute(
                update(PersonModel)
                .where(PersonModel.id == ravi.id)
                .values(brief_due_at=store.now())
            )
            await session.commit()
        assert await briefs.sweep() >= 1
        ravi = await store.get(people.org, people.a.id, ravi.uuid)
        assert ravi.brief and ravi.brief_by == "decibyl" and ravi.brief_due_at is None

    async def test_the_model_is_never_sent_a_number_or_address(
        self, people, people_on, monkeypatch
    ):
        seen: list[str] = []

        async def model(_org, _system, text):
            seen.append(text)
            return "Ravi supplies rice; a revised quote is open."

        monkeypatch.setattr(briefs, "ask_model", model)
        await store.upsert(
            people.org,
            people.a.id,
            Incoming(
                name="Ravi Kumar",
                phones=["9876543210"],
                emails=["ravi@example.in"],
                company="Kumar Traders",
            ).clean(),
            source="manual",
        )
        # A line that itself names the number: masked before the model sees it.
        ravi = await interactions.record(
            people.org,
            people.a.id,
            channel="call",
            phone="9876543210",
            line="Called 9876543210 about the quote",
            ref="run:1",
        )
        written = await briefs.write(ravi)
        assert written.brief == "Ravi supplies rice; a revised quote is open."
        (prompt,) = seen
        assert "Ravi Kumar" in prompt and "Kumar Traders" in prompt
        for secret in ("9876543210", "+919876543210", "ravi@example.in"):
            assert secret not in prompt

    async def test_an_edited_brief_is_the_base_of_the_next(
        self, people, people_on, monkeypatch
    ):
        seen: list[str] = []

        async def model(_org, _system, text):
            seen.append(text)
            return "Old friend from college; asked about the wedding date."

        monkeypatch.setattr(briefs, "ask_model", model)
        made = await store.create_manual(
            people.org, people.a.id, Incoming(name="Dev", phones=["9811111111"]).clean()
        )
        async with client_as(people.as_user(people.a)) as c:
            edited = (
                await c.patch(
                    f"/api/v1/people/{made.person.uuid}",
                    json={"brief": "Old friend from college."},
                )
            ).json()
            assert edited["brief_by"] == "you"
            dev = await interactions.record(
                people.org,
                people.a.id,
                channel="whatsapp",
                phone="9811111111",
                line="Asked about the wedding date",
                ref="w:1",
            )
            rewritten = (await c.post(f"/api/v1/people/{dev.uuid}/brief")).json()
        assert "written by the person themselves): Old friend from college." in seen[0]
        assert rewritten["brief"].startswith("Old friend from college")

    async def test_no_model_is_said_not_faked(self, people, people_on, monkeypatch):
        async def none(*_a):
            raise briefs.BriefUnavailable("no key")

        monkeypatch.setattr(briefs, "ask_model", none)
        made = await store.create_manual(
            people.org, people.a.id, Incoming(name="Dev", phones=["9811111111"]).clean()
        )
        async with client_as(people.as_user(people.a)) as c:
            refused = await c.post(f"/api/v1/people/{made.person.uuid}/brief")
        assert refused.status_code == 503
        assert "No model" in refused.json()["detail"]


# --- chat -----------------------------------------------------------------------------


@pytest.mark.asyncio
class TestChat:
    async def _ravi(self, people):
        await store.upsert(
            people.org,
            people.a.id,
            Incoming(
                name="Ravi Kumar", phones=["9876543210"], emails=["ravi@example.in"]
            ).clean(),
            source="manual",
        )
        ravi = await interactions.record(
            people.org,
            people.a.id,
            channel="call",
            phone="9876543210",
            line="Discussed the October rice order",
            ref="run:1",
        )
        await store.edit(
            people.org,
            people.a.id,
            ravi.uuid,
            {"brief": "Rice supplier; October order open."},
        )

    async def test_what_did_i_last_discuss_with_ravi(self, people, people_on):
        await self._ravi(people)
        block = await people_tools.context_block(
            people.org, people.a.id, "what did I last discuss with Ravi?"
        )
        assert "Ravi Kumar: Rice supplier; October order open." in block
        assert "Discussed the October rice order" in block
        assert "9876543210" not in block

    async def test_the_lookup_tool_masks_until_asked(self, people, people_on):
        await self._ravi(people)
        got = await people_tools.run(
            people.org, user_id=people.a.id, arguments={"name": "ravi"}
        )
        (ravi,) = got["people"]
        assert ravi["phones"] == ["…3210"] and ravi["brief"].startswith("Rice supplier")
        full = await people_tools.run(
            people.org,
            user_id=people.a.id,
            arguments={"name": "Ravi", "include_contact_details": True},
        )
        assert full["people"][0]["emails"] == ["ravi@example.in"]

    async def test_a_colleague_asking_gets_nothing(self, people, people_on):
        await self._ravi(people)
        assert (
            await people_tools.context_block(
                people.org, people.b.id, "what did I last discuss with Ravi?"
            )
            == ""
        )
        got = await people_tools.run(
            people.org, user_id=people.b.id, arguments={"name": "Ravi"}
        )
        assert got["status"] == "not_found"
        nobody = await people_tools.run(
            people.org, user_id=None, arguments={"name": "Ravi"}
        )
        assert nobody["status"] == "unavailable"

    async def test_decibyl_is_handed_the_tool_and_told_the_rule(
        self, people, people_on
    ):
        from api.services.workflow import decibyl

        assert people_tools.TOOL_NAME in decibyl.system_prompt(people.org)
        assert people_tools.TOOL_NAME in {
            t["name"] for t in decibyl.office_tools(people.org)
        }
        await self._ravi(people)
        call = SimpleNamespace(
            name=people_tools.TOOL_NAME, arguments={"name": "Ravi"}, id="c1"
        )
        result = await decibyl._tool(people.org, call, author_id=people.a.id)
        assert result["status"] == "ok"
        assert decibyl._was_a_read(call, result)
        with acting.acting_as(people.b.id):
            other = await decibyl._tool(people.org, call, author_id=people.b.id)
        assert other["status"] == "not_found"


# --- the screen's routes --------------------------------------------------------------


@pytest.mark.asyncio
class TestRoutes:
    async def test_list_search_detail_edit_delete(self, people, people_on):
        async with client_as(people.as_user(people.a)) as c:
            made = (
                await c.post(
                    "/api/v1/people",
                    json={
                        "name": "Ravi Kumar",
                        "phones": ["98765 43210"],
                        "company": "Kumar Traders",
                    },
                )
            ).json()
            await c.post(
                "/api/v1/people", json={"name": "Priya", "emails": ["priya@example.in"]}
            )
            listed = (await c.get("/api/v1/people")).json()
            assert listed["total"] == 2
            assert [
                p["name"]
                for p in (await c.get("/api/v1/people", params={"q": "kumar"})).json()[
                    "people"
                ]
            ] == ["Ravi Kumar"]
            assert [
                p["name"]
                for p in (await c.get("/api/v1/people", params={"q": "43210"})).json()[
                    "people"
                ]
            ] == ["Ravi Kumar"]
            detail = (await c.get(f"/api/v1/people/{made['id']}")).json()
            assert detail["phones"] == ["+919876543210"]
            bad = await c.patch(f"/api/v1/people/{made['id']}", json={"phones": ["12"]})
            assert bad.status_code == 422
            assert (await c.delete(f"/api/v1/people/{made['id']}")).status_code == 200
            assert (await c.get(f"/api/v1/people/{made['id']}")).status_code == 404

    async def test_a_colleague_cannot_open_change_or_delete_it(self, people, people_on):
        async with client_as(people.as_user(people.a)) as c:
            made = (
                await c.post(
                    "/api/v1/people", json={"name": "Ravi", "phones": ["9876543210"]}
                )
            ).json()
        async with client_as(people.as_user(people.b)) as c:
            assert (await c.get(f"/api/v1/people/{made['id']}")).status_code == 404
            assert (
                await c.patch(f"/api/v1/people/{made['id']}", json={"brief": "x"})
            ).status_code == 404
            assert (await c.delete(f"/api/v1/people/{made['id']}")).status_code == 404
            assert (
                await c.post(
                    f"/api/v1/people/{made['id']}/share", json={"user_id": people.b.id}
                )
            ).status_code == 404
            assert (
                await c.get(f"/api/v1/people/shared/{made['id']}")
            ).status_code == 404
        async with client_as(people.as_user(people.stranger, people.other_org)) as c:
            assert (await c.get(f"/api/v1/people/{made['id']}")).status_code == 404

    async def test_sharing_shows_the_card_never_the_brief(self, people, people_on):
        async with client_as(people.as_user(people.a)) as c:
            made = (
                await c.post(
                    "/api/v1/people", json={"name": "Ravi", "phones": ["9876543210"]}
                )
            ).json()
            await c.patch(
                f"/api/v1/people/{made['id']}",
                json={"brief": "PRIVATE-NOTE owes money"},
            )
            assert (
                await c.post(
                    f"/api/v1/people/{made['id']}/share",
                    json={"user_id": people.stranger.id},
                )
            ).status_code == 404
            shared = (
                await c.post(
                    f"/api/v1/people/{made['id']}/share", json={"user_id": people.b.id}
                )
            ).json()
            assert shared["shared_with"] == [people.b.id]
        async with client_as(people.as_user(people.b)) as c:
            listed = (await c.get("/api/v1/people")).json()
            card = (await c.get(f"/api/v1/people/shared/{made['id']}")).json()
        assert listed["people"] == [] and [s["name"] for s in listed["shared"]] == [
            "Ravi"
        ]
        assert card["phones"] == ["+919876543210"]
        assert "PRIVATE-NOTE" not in str(listed) + str(card)
        async with client_as(people.as_user(people.a)) as c:
            await c.delete(f"/api/v1/people/{made['id']}/share/{people.b.id}")
        async with client_as(people.as_user(people.b)) as c:
            assert (await c.get("/api/v1/people")).json()["shared"] == []

    async def test_settings_are_the_persons_own(self, people, people_on):
        async with client_as(people.as_user(people.a)) as c:
            assert (await c.get("/api/v1/people/settings")).json() == {
                "agents_may_read": False
            }
            await c.put("/api/v1/people/settings", json={"agents_may_read": True})
            assert (await c.get("/api/v1/people/status")).json()[
                "agents_may_read"
            ] is True
        async with client_as(people.as_user(people.b)) as c:
            assert (await c.get("/api/v1/people/settings")).json() == {
                "agents_may_read": False
            }


# --- privacy center -------------------------------------------------------------------


@pytest.mark.asyncio
class TestPrivacyCenter:
    async def test_export_has_theirs_and_deletion_removes_only_theirs(
        self, people, people_on
    ):
        from api.services.settings import privacy

        await store.create_manual(
            people.org,
            people.a.id,
            Incoming(name="Ravi", phones=["9876543210"]).clean(),
        )
        await interactions.record(
            people.org,
            people.a.id,
            channel="call",
            phone="9876543210",
            line="Called",
            ref="run:1",
        )
        await store.create_manual(
            people.org,
            people.b.id,
            Incoming(name="B's friend", phones=["9811111111"]).clean(),
        )
        await store.set_agents_may_read(people.org, people.a.id, True)
        gathered = await privacy._gather(people.a.id)
        assert [p["name"] for p in gathered["people"]] == ["Ravi"]
        assert gathered["people"][0]["interactions"][0]["line"] == "Called"
        counts = await privacy._counts(people.a.id)
        assert counts["people"] == 1
        removed = await privacy._delete_store(
            "people", user_id=people.a.id, keep_event_id=None
        )
        assert removed == 1
        assert await store.count(people.org, people.a.id) == 0
        assert await store.agents_may_read(people.org, people.a.id) is False
        assert await store.count(people.org, people.b.id) == 1
        async with db_client.async_session() as session:
            left = (
                await session.execute(
                    select(PersonModel.id).where(
                        PersonModel.owner_user_id == people.a.id
                    )
                )
            ).all()
        assert left == []


# --- the phone app's address book ------------------------------------------------


def _phone(cid: str, name: str, number: str) -> dict:
    return {"id": cid, "name": name, "phones": [number]}


@pytest.mark.asyncio
class TestDeviceSync:
    async def _post(self, c, **body):
        response = await c.post(
            "/api/v1/people/device/sync", json={"device_id": "pixel-1", **body}
        )
        assert response.status_code == 200, response.text
        return response.json()

    async def test_full_in_pages_then_only_changes(self, people, people_on):
        async with client_as(people.as_user(people.a)) as c:
            first = await self._post(
                c,
                full=True,
                final=False,
                contacts=[
                    _phone("1", "Ravi", "98765 43210"),
                    _phone("2", "Priya", "9812345678"),
                ],
            )
            assert first["added"] == 2 and first["cursor"]
            last = await self._post(
                c,
                cursor=first["cursor"],
                full=True,
                final=True,
                contacts=[_phone("3", "Anil", "9988776655")],
            )
            assert (last["added"], last["removed"]) == (1, 0)
            changes = await self._post(
                c,
                cursor=last["cursor"],
                contacts=[_phone("1", "Ravi Kumar", "98765 43210")],
                removed=["2"],
            )
        assert (changes["updated"], changes["removed"]) == (1, 1)
        rows = {p.name: p for p in await _ours(people, people.a)}
        assert sorted(rows) == ["Anil", "Ravi Kumar"]  # the phone's rename followed
        assert rows["Ravi Kumar"].sources == ["device"]

    async def test_a_cursor_that_does_not_match_changes_nothing(
        self, people, people_on
    ):
        async with client_as(people.as_user(people.a)) as c:
            done = await self._post(
                c, full=True, contacts=[_phone("1", "Ravi", "9876543210")]
            )
            stale = await self._post(c, cursor="not-the-cursor", removed=["1"])
            # The old cursor is spent too: each answer issues a new one.
            await self._post(c, cursor=done["cursor"], contacts=[])
            again = await self._post(c, cursor=done["cursor"], removed=["1"])
        assert stale == {**stale, "full_required": True, "cursor": None, "removed": 0}
        assert again["full_required"] is True
        assert [p.name for p in await _ours(people, people.a)] == ["Ravi"]

    async def test_a_full_resync_removes_what_the_phone_no_longer_has(
        self, people, people_on
    ):
        async with client_as(people.as_user(people.a)) as c:
            await self._post(
                c,
                full=True,
                contacts=[
                    _phone("1", "Ravi", "9876543210"),
                    _phone("2", "Priya", "9812345678"),
                    _phone("3", "Dev", "9811111111"),
                ],
            )
            # Decibyl called Priya: her history keeps her when the phone drops her.
            await interactions.record(
                people.org,
                people.a.id,
                channel="call",
                phone="9812345678",
                line="Called",
                ref="run:1",
            )
            again = await self._post(
                c, full=True, contacts=[_phone("1", "Ravi", "9876543210")]
            )
        assert again["removed"] == 2
        rows = {p.name: p for p in await _ours(people, people.a)}
        assert sorted(rows) == ["Priya", "Ravi"]
        assert rows["Priya"].sources == ["decibyl"]

    async def test_another_persons_phone_never_meets_mine(self, people, people_on):
        async with client_as(people.as_user(people.a)) as c:
            mine = await self._post(
                c, full=True, contacts=[_phone("1", "Ravi PRIVATE", "9876543210")]
            )
        async with client_as(people.as_user(people.b)) as c:
            # Same device id, A's cursor: B gets no access to A's sync.
            theirs = await self._post(c, cursor=mine["cursor"], removed=["1"])
            assert theirs["full_required"] is True
            await self._post(
                c, full=True, contacts=[_phone("1", "B's friend", "9000000001")]
            )
            listed = (await c.get("/api/v1/people")).text
        assert "PRIVATE" not in listed
        assert [p.name for p in await _ours(people, people.a)] == ["Ravi PRIVATE"]
        assert [p.name for p in await _ours(people, people.b)] == ["B's friend"]

    async def test_limits_and_the_flag(self, people, people_on, monkeypatch):
        async with client_as(people.as_user(people.a)) as c:
            too_many = await c.post(
                "/api/v1/people/device/sync",
                json={
                    "device_id": "p",
                    "full": True,
                    "contacts": [
                        _phone(str(i), "X", "9876543210") for i in range(2001)
                    ],
                },
            )
            assert too_many.status_code == 422
            monkeypatch.setattr(constants, "PEOPLE_ENABLED", False)
            off = await c.post(
                "/api/v1/people/device/sync", json={"device_id": "p", "full": True}
            )
            assert off.status_code == 404

    async def test_the_six_hourly_resync_leaves_phones_alone(self, people, people_on):
        async with client_as(people.as_user(people.a)) as c:
            await self._post(c, full=True, contacts=[_phone("1", "Ravi", "9876543210")])
        async with db_client.async_session() as session:
            await session.execute(
                update(PeopleSyncModel)
                .where(PeopleSyncModel.user_id == people.a.id)
                .values(last_synced_at=store.now() - timedelta(hours=7))
            )
            await session.commit()
        assert await people_sync.resync_due() == 0
        async with db_client.async_session() as session:
            (row,) = (
                (
                    await session.execute(
                        select(PeopleSyncModel).where(
                            PeopleSyncModel.user_id == people.a.id
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert row.status == "ok"
