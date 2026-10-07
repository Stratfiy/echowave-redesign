"""Scam check (launch stream care): a plain answer, and why.

Done when: the common Indian scam shapes read as likely scams with the
reason that makes them so; a genuine OTP message is not called a scam but
the person is told never to read the code out; ordinary messages are not
flagged; every answer says Decibyl never asks for an OTP or password and
that a list can miss a new trick; the words are never stored; and checks
are the person's own.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from api.db import db_client
from api.services.care import CareError, scam
from api.tests import care_support as cs

LIKELY = [
    (
        "Hi, I am calling from HDFC bank, please tell me the OTP you received",
        "asks_for_otp",
    ),
    (
        "Please enter your UPI PIN to receive the refund of Rs 2000",
        "asks_for_password_or_pin",
    ),
    (
        "Dear customer your SBI account will be blocked today. Update KYC at http://bit.ly/sbi-kyc",
        "kyc_update",
    ),
    (
        "Congratulations you have won KBC lottery Rs 25 lakh. Pay processing fee to claim",
        "pay_to_receive",
    ),
    (
        "Police officer said I am under digital arrest and must stay on video call",
        "digital_arrest",
    ),
    ("Install AnyDesk so I can help you with the refund", "remote_access_app"),
    (
        "Your electricity connection will be cut tonight at 9.30pm. Call officer now",
        "bill_cutoff",
    ),
    (
        "Mom my phone broke this is my new number, I am stuck, please send money urgently",
        "family_emergency_new_number",
    ),
    ("आपका खाता बंद हो जाएगा, ओटीपी बताएं", "asks_for_otp"),
    ("Earn 5000 daily part time job, like videos on youtube and earn", "paid_tasks"),
]

ORDINARY = [
    "Hi beta, dinner at 8? Love, Dad",
    "Ship to 12 MG Road, pin code 560001",
    "Your appointment with Dr. Rao is on Monday at 10.",
    "Never share your PIN or password with anyone. Team HDFC",
]


@pytest.mark.parametrize("message, sign", LIKELY)
def test_common_scams_are_called_scams_with_the_reason(message, sign):
    answer = scam.assess(message)
    assert answer["verdict"] == scam.LIKELY, answer
    assert sign in [r["code"] for r in answer["reasons"]]
    assert answer["headline"] == "This looks like a scam."
    assert any("1930" in step for step in answer["what_to_do"])


@pytest.mark.parametrize("message", ORDINARY)
def test_ordinary_messages_are_not_flagged(message):
    assert scam.assess(message)["verdict"] == scam.NO_SIGNS


def test_a_genuine_otp_message_is_not_a_scam_but_the_code_stays_secret():
    answer = scam.assess(
        "Your OTP for login is 482913. Do not share it with anyone. -SBI"
    )
    assert answer["verdict"] != scam.LIKELY
    reasons = {r["code"]: r["why"] for r in answer["reasons"]}
    assert "asks_for_otp" not in reasons
    assert "Never tell this code to anyone" in reasons["contains_code"]


@pytest.mark.parametrize("message", [m for m, _ in LIKELY] + ORDINARY)
def test_every_answer_is_honest_about_itself_and_never_asks(message):
    answer = scam.assess(message)
    assert (
        answer["never_asks"]
        == "Decibyl will never ask you for an OTP, PIN or password."
    )
    assert "can get past it" in answer["limits"]
    said = " ".join(
        [
            answer["headline"],
            *answer["what_to_do"],
            *(r["why"] for r in answer["reasons"]),
        ]
    ).lower()
    for ask in (
        "send us your otp",
        "tell us your otp",
        "enter your password",
        "share your pin",
    ):
        assert ask not in said
    if answer["verdict"] == scam.NO_SIGNS:
        assert "safe" not in answer["headline"].lower()


def test_empty_is_refused():
    with pytest.raises(CareError):
        scam.assess("   ")


@pytest.fixture
async def two(test_engine, monkeypatch):
    cs.all_on(monkeypatch)
    a = await cs.person("scam-a")
    b = await cs.person("scam-b")
    org = await cs.workspace(a.id)
    yield a, b, org
    await cs.cleanup(org)


@pytest.mark.asyncio
async def test_the_words_are_not_kept(two):
    a, _, org = two
    secret = "Share OTP 482913 now or your account ending 4455 is blocked"
    answer = await scam.check(org, a.id, text=secret)
    assert answer["verdict"] == scam.LIKELY
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                text("SELECT * FROM care_scam_checks WHERE organization_id = :o"),
                {"o": org},
            )
        ).all()
    assert len(rows) == 1
    assert "482913" not in str(rows) and "4455" not in str(rows)


@pytest.mark.asyncio
async def test_over_http_and_history_is_the_persons_own(two):
    a, b, org = two
    async with cs.client(a.id, org) as c:
        r = await c.post(
            "/api/v1/care/scam-check",
            json={"text": "Install AnyDesk for your refund", "kind": "call"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"] == "likely_scam" and body["kind"] == "call"
        assert body["reasons"][0]["code"] == "remote_access_app"
        assert (
            await c.post("/api/v1/care/scam-check", json={"text": ""})
        ).status_code == 422
        mine = (await c.get("/api/v1/care/scam-check/recent")).json()["checks"]
        assert [m["verdict"] for m in mine] == ["likely_scam"]
    async with cs.client(b.id, org) as c:
        assert (await c.get("/api/v1/care/scam-check/recent")).json()["checks"] == []
