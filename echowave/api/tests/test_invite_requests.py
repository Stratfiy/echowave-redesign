"""Asking for an invite, and an approver saying yes or no from their inbox.

The founder's whole ask: "When someone asks for an invite, I need to receive
a mail with Approve or Reject. On approval they must get the approval code
along with a mail to onboard." What follows is mostly what must *not* happen:
a second mail for a second click, a scanner approving by fetching a link, a
link working twice or after it expired, a name that writes HTML into the
approver's inbox, a request nobody hears about.

Mail goes through ``send_email``, replaced here with a recorder: nothing is
sent anywhere.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from loguru import logger
from sqlalchemy import select

from api import constants
from api.db import db_client
from api.db.models import UserModel
from api.db.shell_models import WaitlistRequestModel
from api.db.signup_invite_models import SignupInviteModel
from api.enums import StaffRole
from api.services.auth import invite_requests, signup_invites
from api.services.shell import early_access

APPROVER = "approver@example.test"


class Outbox:
    """Stands in for ``send_email``: records every message, sends none."""

    def __init__(self, ok: bool = True):
        self.sent: list[dict] = []
        self.ok = ok

    async def __call__(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(ok=self.ok, error=None if self.ok else "smtp down")

    def to(self, address: str) -> list[dict]:
        return [m for m in self.sent if m["to"] == address]


@pytest.fixture
def outbox(monkeypatch):
    box = Outbox()
    monkeypatch.setattr(invite_requests, "send_email", box)
    return box


@pytest.fixture
def approvers(monkeypatch):
    monkeypatch.setattr(constants, "INVITE_APPROVER_EMAILS", [APPROVER])
    monkeypatch.setattr(constants, "INVITE_REJECT_NOTIFY", False)
    monkeypatch.setattr(constants, "UI_APP_URL", "https://app.example.test")


@pytest.fixture
def early_on(monkeypatch):
    monkeypatch.setattr(constants, "EARLY_ACCESS_ENABLED", True)
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "")


def _links(mail: dict) -> dict[str, str]:
    """The Approve and Reject URLs from an approver mail's plain text."""
    return {
        action: re.search(rf"^{label}: (\S+)$", mail["body_text"], re.M).group(1)
        for action, label in (("approve", "Approve"), ("reject", "Reject"))
    }


def _token(url: str) -> str:
    return parse_qs(urlparse(url).query)["token"][0]


async def _request(db_session, outbox, email="asha@clinic.in", name="Asha Rao"):
    await early_access.join(
        email=email, language="en", first_task="Chase unpaid invoices", name=name
    )
    found = await _row(email)
    mail = outbox.to(APPROVER)[-1]
    return found, _links(mail)


async def _row(email: str) -> WaitlistRequestModel:
    async with db_client.async_session() as session:
        return await session.scalar(
            select(WaitlistRequestModel).where(WaitlistRequestModel.email == email)
        )


# ---------------------------------------------------------------------------
# The request and the approver's mail
# ---------------------------------------------------------------------------


async def test_a_request_mails_the_approver_once_with_two_signed_links(
    db_session, outbox, approvers
):
    row, links = await _request(db_session, outbox)

    assert row.status == invite_requests.PENDING
    assert row.name == "Asha Rao"
    assert len(outbox.sent) == 1
    mail = outbox.sent[0]
    assert mail["to"] == APPROVER
    assert "Asha Rao" in mail["subject"]
    assert "asha@clinic.in" in mail["body_text"]
    assert "Chase unpaid invoices" in mail["body_text"]
    assert ">Approve</a>" in mail["body_html"] and ">Reject</a>" in mail["body_html"]

    for action, url in links.items():
        assert url.startswith("https://app.example.test/invite-requests/decide?token=")
        claims = jwt.decode(
            _token(url),
            constants.OSS_JWT_SECRET,
            algorithms=["HS256"],
            audience="decibyl:invite-request-decision",
        )
        assert claims["act"] == action
        assert claims["rid"] == row.id
        assert claims["by"] == APPROVER
        # Expires, in about the configured fortnight.
        assert (
            claims["exp"] - claims["iat"] == constants.INVITE_DECISION_TTL_DAYS * 86400
        )
        # The person's address is not in the URL.
        assert "asha" not in url


async def test_asking_twice_is_one_request_and_one_mail(db_session, outbox, approvers):
    first = await early_access.join(email="Twice@Clinic.in", language="en")
    again = await early_access.join(email=" twice@clinic.in ", language="en")
    assert first.created and not again.created
    assert len(outbox.to(APPROVER)) == 1


async def test_an_existing_account_is_sent_to_sign_in_and_nobody_is_mailed(
    db_session, outbox, approvers
):
    with patch.object(
        early_access.db_client,
        "get_user_by_email",
        AsyncMock(return_value=SimpleNamespace(id=1)),
    ):
        result = await early_access.join(email="owner@clinic.in", language="en")
    assert result.state == early_access.ALREADY_REGISTERED
    assert outbox.sent == []


async def test_html_in_a_name_or_note_is_escaped(db_session, outbox, approvers):
    await early_access.join(
        email="x@clinic.in",
        language="en",
        name='<img src=x onerror="alert(1)">Mallory',
        first_task="<script>steal()</script>",
    )
    mail = outbox.to(APPROVER)[0]
    assert "<img" not in mail["body_html"]
    assert "<script>" not in mail["body_html"]
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;Mallory" in mail["body_html"]
    assert "&lt;script&gt;steal()&lt;/script&gt;" in mail["body_html"]
    assert "\n" not in mail["subject"]


async def test_with_no_approvers_configured_the_superadmins_are_mailed(
    async_session, db_session, outbox, monkeypatch
):
    monkeypatch.setattr(constants, "INVITE_APPROVER_EMAILS", [])
    admin = UserModel(
        provider_id="invite-fallback-admin",
        email="founder-fallback@example.test",
        staff_role=StaffRole.SUPERADMIN.value,
    )
    async_session.add(admin)
    await async_session.flush()

    warnings: list[str] = []
    sink = logger.add(lambda m: warnings.append(str(m)), level="WARNING")
    try:
        await early_access.join(email="fallback@clinic.in", language="en")
    finally:
        logger.remove(sink)

    assert len(outbox.to("founder-fallback@example.test")) == 1
    assert any("INVITE_APPROVER_EMAILS is not set" in w for w in warnings)


# ---------------------------------------------------------------------------
# The links
# ---------------------------------------------------------------------------


async def test_opening_a_link_changes_nothing(
    test_client_factory, db_session, outbox, approvers
):
    row, links = await _request(db_session, outbox)
    anon, _ = await db_session.get_or_create_user_by_provider_id("anon-invite-get")
    async with test_client_factory(anon) as client:
        for _ in range(3):  # a scanner, a preview, the approver
            seen = await client.get(
                "/api/v1/public/invite-requests/decide",
                params={"token": _token(links["approve"])},
            )
            assert seen.status_code == 200
    body = seen.json()
    assert body["action"] == "approve"
    assert body["request"]["state"] == "pending"
    assert body["request"]["email"] == "asha@clinic.in"
    assert (await invite_requests._get(row.id)).status == invite_requests.PENDING
    assert outbox.to("asha@clinic.in") == []


async def test_approving_mints_a_pinned_code_and_mails_a_short_welcome(
    async_session, test_client_factory, db_session, outbox, approvers
):
    row, links = await _request(db_session, outbox)
    anon, _ = await db_session.get_or_create_user_by_provider_id("anon-invite-post")
    async with test_client_factory(anon) as client:
        done = await client.post(
            "/api/v1/public/invite-requests/decide",
            json={"token": _token(links["approve"])},
        )
    assert done.status_code == 200
    assert done.json()["outcome"] == "approved"

    invite = await async_session.scalar(
        select(SignupInviteModel).where(SignupInviteModel.email == "asha@clinic.in")
    )
    assert invite is not None
    assert (invite.max_uses, invite.uses) == (1, 0)
    assert invite.note == f"approved from request #{row.id}"

    decided = await invite_requests._get(row.id)
    assert decided.status == invite_requests.APPROVED
    assert decided.invite_id == invite.id
    assert decided.decided_by_email == APPROVER
    assert decided.decided_at is not None

    (welcome,) = outbox.to("asha@clinic.in")
    code = signup_invites.display(invite.code)
    assert welcome["subject"] == "You're in, welcome to Decibyl"
    assert welcome["body_text"].startswith("Hi Asha,")
    assert f"Code: {code}" in welcome["body_text"]
    link = f"https://app.example.test/auth/signup?code={code}&email=asha%40clinic.in"
    assert link in welcome["body_text"]
    assert link.replace("&", "&amp;") in welcome["body_html"]
    assert code in welcome["body_html"]
    assert "Create your account" in welcome["body_html"]
    # Short, and nothing about money.
    assert len(welcome["body_text"]) < 400
    for word in ("price", "plan", "₹", "credit", "trial"):
        assert word not in welcome["body_text"].lower()


async def test_a_second_approve_is_a_no_op_that_says_who_and_when(
    db_session, outbox, approvers
):
    row, links = await _request(db_session, outbox)
    first = await invite_requests.decide(_token(links["approve"]))
    second = await invite_requests.decide(_token(links["approve"]))
    # The other button is spent too.
    third = await invite_requests.decide(_token(links["reject"]))

    assert first["outcome"] == "approved"
    for again in (second, third):
        assert again["outcome"] == "already_decided"
        assert again["message"].startswith(f"Already approved by {APPROVER} on ")
    assert len(outbox.to("asha@clinic.in")) == 1
    assert (await invite_requests._get(row.id)).status == invite_requests.APPROVED


async def test_an_expired_or_tampered_link_is_refused(
    test_client_factory, db_session, outbox, approvers
):
    row, links = await _request(db_session, outbox)
    expired = invite_requests.issue_token(
        request_id=row.id,
        action="approve",
        approver_email=APPROVER,
        request_email=row.email,
        now=datetime.now(UTC) - timedelta(days=30),
    )
    good = _token(links["approve"])
    head, payload, sig = good.split(".")
    tampered = f"{head}.{payload}.{sig[:-4]}AAAA"
    forged = jwt.encode(
        {"aud": "decibyl:invite-request-decision", "rid": row.id, "act": "approve"},
        "not-our-secret",
        algorithm="HS256",
    )
    # A real token for one request does not decide another.
    other = await early_access.join(email="other@clinic.in", language="en")
    assert other.created
    other_row = await _row("other@clinic.in")
    wrong_row = invite_requests.issue_token(
        request_id=other_row.id,
        action="approve",
        approver_email=APPROVER,
        request_email=row.email,
    )

    anon, _ = await db_session.get_or_create_user_by_provider_id("anon-invite-bad")
    async with test_client_factory(anon) as client:
        for bad in (expired, tampered, forged, wrong_row):
            seen = await client.post(
                "/api/v1/public/invite-requests/decide", json={"token": bad}
            )
            assert seen.status_code == 400, bad
        late = await client.get(
            "/api/v1/public/invite-requests/decide", params={"token": expired}
        )
    assert "expired" in late.json()["detail"]
    assert (await invite_requests._get(row.id)).status == invite_requests.PENDING
    assert (await invite_requests._get(other_row.id)).status == invite_requests.PENDING


async def test_rejecting_mails_the_person_nothing_by_default(
    db_session, outbox, approvers
):
    row, links = await _request(db_session, outbox)
    done = await invite_requests.decide(_token(links["reject"]))
    assert done["outcome"] == "rejected"
    assert done["message"] == "Rejected."
    assert outbox.to("asha@clinic.in") == []
    assert (await invite_requests._get(row.id)).status == invite_requests.REJECTED
    again = await invite_requests.decide(_token(links["approve"]))
    assert again["message"].startswith(f"Already rejected by {APPROVER}")


async def test_rejecting_sends_one_kind_line_when_switched_on(
    db_session, outbox, approvers, monkeypatch
):
    monkeypatch.setattr(constants, "INVITE_REJECT_NOTIFY", True)
    _row, links = await _request(db_session, outbox)
    await invite_requests.decide(_token(links["reject"]))
    (decline,) = outbox.to("asha@clinic.in")
    assert decline["body_text"].startswith("Hi Asha,")
    assert "code" not in decline["body_text"].lower()


async def test_the_code_never_reaches_the_log(db_session, approvers, monkeypatch):
    failing = Outbox(ok=False)
    monkeypatch.setattr(invite_requests, "send_email", failing)
    await early_access.join(email="logs@clinic.in", language="en")
    row = await _row("logs@clinic.in")
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        result = await invite_requests.approve(row.id, decided_by_email=APPROVER)
    finally:
        logger.remove(sink)
    invite = await invite_requests._get(row.id)
    code = (await signup_invites.list_invites())[0]["code"]
    assert invite.invite_id is not None
    assert result["mail_sent"] is False
    # The approver is handed the link to pass on, since the mail failed.
    assert code in result["signup_link"]
    assert lines, "the failed send should be logged"
    joined = "\n".join(lines)
    assert code not in joined and signup_invites.normalise(code) not in joined


# ---------------------------------------------------------------------------
# The superadmin screen
# ---------------------------------------------------------------------------


async def test_superadmin_lists_and_decides_with_the_same_rules(
    async_session, db_session, outbox, approvers
):
    from api.routes import superuser as route

    admin = UserModel(
        provider_id="invite-screen-admin",
        email="screen-admin@example.test",
        staff_role=StaffRole.SUPERADMIN.value,
    )
    async_session.add(admin)
    await async_session.flush()

    await early_access.join(email="one@clinic.in", language="en", name="One")
    await early_access.join(email="two@clinic.in", language="en", name="Two")
    listed = (await route.list_invite_requests(user=admin))["requests"]
    by_email = {r["email"]: r for r in listed}
    assert by_email["one@clinic.in"]["state"] == "pending"

    one = by_email["one@clinic.in"]["id"]
    two = by_email["two@clinic.in"]["id"]
    approved = await route.approve_invite_request(one, user=admin)
    rejected = await route.reject_invite_request(two, user=admin)
    again = await route.approve_invite_request(one, user=admin)

    assert approved["outcome"] == "approved"
    assert rejected["outcome"] == "rejected"
    assert again["outcome"] == "already_decided"
    assert "screen-admin@example.test" in again["message"]

    invite = await async_session.scalar(
        select(SignupInviteModel).where(SignupInviteModel.email == "one@clinic.in")
    )
    assert invite.created_by_user_id == admin.id
    assert len(outbox.to("one@clinic.in")) == 1
    assert outbox.to("two@clinic.in") == []

    listed = {r["email"]: r for r in await invite_requests.list_requests()}
    assert listed["one@clinic.in"]["state"] == "approved"
    assert listed["two@clinic.in"]["state"] == "rejected"


async def test_a_mailed_approval_credits_the_approver_account_when_there_is_one(
    async_session, db_session, outbox, approvers
):
    approver = UserModel(provider_id="invite-approver-user", email=APPROVER)
    async_session.add(approver)
    await async_session.flush()
    _row, links = await _request(db_session, outbox)
    await invite_requests.decide(_token(links["approve"]))
    invite = await async_session.scalar(
        select(SignupInviteModel).where(SignupInviteModel.email == "asha@clinic.in")
    )
    assert invite.created_by_user_id == approver.id


async def test_the_public_waitlist_route_takes_a_name(
    test_client_factory, db_session, outbox, approvers, early_on
):
    anon, _ = await db_session.get_or_create_user_by_provider_id("anon-invite-name")
    async with test_client_factory(anon) as client:
        joined = await client.post(
            "/api/v1/public/early-access/waitlist",
            json={"email": "named@clinic.in", "language": "en", "name": "Meera K"},
        )
    assert joined.json() == {"state": "waitlisted", "created": True}
    assert "Meera K" in outbox.to(APPROVER)[0]["subject"]
