"""Care's cards are the older person's alone (phase 3, care).

Found in the integrated build with two people in one workspace: a care card
(a medicine reminder, a family invitation) and the line under it once it ran
(which carries the invitation code) sat on Decibyl's shared thread, so with
private threads off every colleague could read them, and with them on an
admin could; the workspace audit log carried the medicine and family names.

Done when: the card and its lines are readable by the person only, on every
timeline read, whatever the private-threads switch says; and the audit row
records the press without the medicine or the family member's name.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from api.db import db_client
from api.services.care import circle, medicines
from api.tests import care_support as cs

MARK_MED = "QZCAREMED7"
MARK_FAMILY = "QZCAREFAM7"


@pytest.fixture
async def office(test_engine, monkeypatch):
    cs.all_on(monkeypatch)
    from api import constants

    monkeypatch.setattr(constants, "CARE_CALLS_FAKE", "")
    a = await cs.person("priv-a")
    b = await cs.person("priv-b")
    org = await cs.workspace(a.id)
    yield a, b, org
    await cs.cleanup(org)


async def _make_private_things(a, org):
    invite = await circle.propose_member(
        org,
        a.id,
        name=f"Priya {MARK_FAMILY}",
        email="qzcare-family@example.com",
        shares=["medicine_alerts"],
    )
    ran = await cs.press(org, invite["event_id"], a.id)
    code = ran["result"]["invite_code"]
    reminder = await medicines.propose(
        org, a.id, label=f"{MARK_MED} tablet", times=["08:00"], channel="app"
    )
    await cs.press(org, reminder["event_id"], a.id)
    return code


@pytest.mark.asyncio
@pytest.mark.parametrize("private_threads", [False, True])
async def test_a_colleague_never_reads_my_care_cards(
    office, monkeypatch, private_threads
):
    a, b, org = office
    from api import constants

    monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", private_threads)
    code = await _make_private_things(a, org)
    reads = [
        "/api/v1/timeline",
        "/api/v1/timeline?assistant=true",
        "/api/v1/timeline/recents",
        "/api/v1/timeline/threads",
    ]
    async with cs.client(a.id, org) as c:
        mine = " ".join(
            [
                (await c.get(p)).text
                for p in ("/api/v1/care/medicines", "/api/v1/care/circle")
            ]
        )
        if not private_threads:
            mine += (await c.get("/api/v1/timeline?assistant=true")).text
            # The person still reads their own cards on Decibyl's thread.
            assert MARK_MED in (await c.get("/api/v1/timeline")).text
    # The control: the person reads their own.
    assert MARK_MED in mine and MARK_FAMILY in mine
    async with cs.client(b.id, org) as c:
        theirs = " ".join([(await c.get(path)).text for path in reads])
    for secret in (MARK_MED, MARK_FAMILY, code):
        assert secret not in theirs


@pytest.mark.asyncio
async def test_the_audit_log_keeps_the_press_not_the_names(office):
    a, _, org = office
    await _make_private_things(a, org)
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT action, subject FROM audit_entries WHERE organization_id = :o"
                ),
                {"o": org},
            )
        ).all()
    body = str(rows)
    assert "card_confirmed" in body
    assert MARK_MED not in body and MARK_FAMILY not in body
    assert "Care:" in body
