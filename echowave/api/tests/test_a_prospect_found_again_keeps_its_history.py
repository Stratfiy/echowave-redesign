"""Against the real database: a prospect saved a second time keeps what the
send recorded on it, and a status that means stop survives anything the
model writes. Before this, a refresh replaced the attributes wholesale, so
a run that found the same clinic again wiped ``status: emailed`` and the
next run would write to them as new.
"""

from __future__ import annotations

from api.db.models import ContactModel, OrganizationModel
from api.services.workflow import prospects, send_approval


async def _attributes(db, org_id: int, email: str) -> dict:
    rows = await db.search_contacts_for_organization(org_id, [email], limit=5)
    return dict(rows[0].attributes)


async def test_found_again_keeps_the_send_and_the_stop(db_session, async_session):
    org = OrganizationModel(provider_id="org-outreach-merge")
    async_session.add(org)
    await async_session.flush()

    first = await prospects.save(
        org.id,
        {
            "prospects": [
                {
                    "email": "priya@sunrise.example",
                    "hook": "New branch",
                    "fit_score": 4,
                },
                {"email": "ravi@moon.example", "note": "fits"},
            ]
        },
    )
    assert first["saved"] == 2
    found_at = (await _attributes(db_session, org.id, "priya@sunrise.example"))[
        "found_at"
    ]

    assert await send_approval.note_sent(
        org.id, {"to": "priya@sunrise.example", "subject": "Your new branch"}
    )
    await prospects.save(
        org.id,
        {"prospects": [{"email": "ravi@moon.example", "status": "unsubscribed"}]},
    )

    # The next run finds both again and writes over them.
    again = await prospects.save(
        org.id,
        {
            "prospects": [
                {
                    "email": "priya@sunrise.example",
                    "note": "still fits",
                    "hook": "Award",
                },
                {"email": "ravi@moon.example", "status": "interested"},
            ]
        },
    )
    assert again["saved"] == 2

    priya = await _attributes(db_session, org.id, "priya@sunrise.example")
    assert priya["status"] == "emailed"
    assert priya["emails_sent"] == 1
    assert priya["last_subject"] == "Your new branch"
    assert priya["hook"] == "Award" and priya["note"] == "still fits"
    assert priya["fit_score"] == 4
    assert priya["found_at"] == found_at

    ravi = await _attributes(db_session, org.id, "ravi@moon.example")
    assert ravi["status"] == "unsubscribed"

    count = await db_session.search_contacts_for_organization(
        org.id, ["example"], limit=10
    )
    assert len(count) == 2 and all(isinstance(r, ContactModel) for r in count)
