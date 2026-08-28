"""Who gets through to a published inbound number.

The caller here is a member of the public. They cannot read our settings, they
cannot tell the difference between "refused" and "broken", and if we get it
wrong the person who notices is the one who could not reach their supplier. So
the tests are mostly about what the guard does *not* refuse.

Redis-gated where it counts calls. The known-caller and allow-list paths need
no Redis and run everywhere.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from api.services.telephony import inbound_guard

requires_redis = pytest.mark.skipif(
    "REDIS_URL" not in os.environ,
    reason="Requires Redis (set REDIS_URL via .env.test)",
)


@dataclass
class _Number:
    """The columns the guard reads off a phone number row."""

    id: int = field(default_factory=lambda: uuid.uuid4().int % 10_000_000)
    address_normalized: str = "+911111111111"
    inbound_contact_list_id: int | None = None
    inbound_require_known_caller: bool = False
    inbound_max_calls_per_caller: int | None = None
    inbound_call_window_hours: int = 24
    inbound_allow_list: list = field(default_factory=list)


@dataclass
class _Contact:
    id: int = 7
    name: str | None = "Asha Rao"
    attributes: dict[str, Any] = field(default_factory=dict)


def _lookup(contact: _Contact | None):
    async def _fn(_list_id, _phone):
        return contact

    return _fn


@pytest.mark.asyncio
class TestAnUnconfiguredNumberAnswersEveryone:
    """The default has to be the behaviour from before this feature existed."""

    async def test_a_bare_number_lets_the_call_through(self):
        decision = await inbound_guard.evaluate(
            caller="+919876543210", phone_number=_Number()
        )

        assert decision.allowed
        assert decision.contact_context == {}

    async def test_a_withheld_caller_id_is_not_a_refusal(self):
        """Anonymous callers exist. Refusing them is a product decision nobody
        made, and a limit keyed on an empty string would count every one of
        them as the same person."""
        decision = await inbound_guard.evaluate(
            caller=None, phone_number=_Number(inbound_max_calls_per_caller=1)
        )

        assert decision.allowed


@pytest.mark.asyncio
class TestKnowingWhoIsCalling:
    async def test_a_known_caller_arrives_with_their_attributes(self):
        contact = _Contact(attributes={"policy_number": "POL-1", "due": "5 March"})

        decision = await inbound_guard.evaluate(
            caller="+919876543210",
            phone_number=_Number(inbound_contact_list_id=3),
            lookup_contact=_lookup(contact),
        )

        assert decision.allowed
        assert decision.contact_context["policy_number"] == "POL-1"
        assert decision.contact_context["contact_name"] == "Asha Rao"
        assert decision.contact_context["contact_is_known"] is True

    async def test_a_csv_column_cannot_overwrite_the_resolved_identity(self):
        """``contact_name`` is a plausible header. Letting a spreadsheet
        column win would put one row's name on another caller's call."""
        contact = _Contact(
            name="Asha Rao", attributes={"contact_name": "somebody else"}
        )

        decision = await inbound_guard.evaluate(
            caller="+919876543210",
            phone_number=_Number(inbound_contact_list_id=3),
            lookup_contact=_lookup(contact),
        )

        assert decision.contact_context["contact_name"] == "Asha Rao"

    async def test_an_unknown_caller_is_allowed_unless_the_switch_is_on(self):
        decision = await inbound_guard.evaluate(
            caller="+919876543210",
            phone_number=_Number(inbound_contact_list_id=3),
            lookup_contact=_lookup(None),
        )

        assert decision.allowed
        assert decision.contact_context == {}

    async def test_the_switch_refuses_an_unknown_caller(self):
        decision = await inbound_guard.evaluate(
            caller="+919876543210",
            phone_number=_Number(
                inbound_contact_list_id=3, inbound_require_known_caller=True
            ),
            lookup_contact=_lookup(None),
        )

        assert not decision.allowed
        assert decision.reason == "caller_not_in_contact_list"

    async def test_the_switch_without_a_list_does_not_take_the_number_off_air(self):
        """There is nothing to be unknown to. Refusing every caller because a
        switch was left on would silently disconnect a published number."""
        decision = await inbound_guard.evaluate(
            caller="+919876543210",
            phone_number=_Number(inbound_require_known_caller=True),
        )

        assert decision.allowed

    async def test_a_failing_lookup_lets_the_call_through(self):
        """Fails open, unlike the DND gate. Wrongly refusing an inbound call is
        a customer who could not reach their supplier and does not know why."""

        async def _boom(_list_id, _phone):
            raise RuntimeError("database is having a moment")

        decision = await inbound_guard.evaluate(
            caller="+919876543210",
            phone_number=_Number(inbound_contact_list_id=3),
            lookup_contact=_boom,
        )

        assert decision.allowed


class TestTheAllowList:
    """Normalized on both sides, because neither side is typed by a machine."""

    def test_a_number_typed_with_spaces_matches_what_the_carrier_sends(self):
        assert inbound_guard.is_allow_listed("+919876543210", ["+91 98765 43210"])

    def test_a_local_form_matches_its_e164_form(self):
        assert inbound_guard.is_allow_listed("+919876543210", ["+91 (98765) 43210"])

    def test_an_unrelated_number_does_not_match(self):
        assert not inbound_guard.is_allow_listed("+919876543210", ["+919999999999"])

    def test_an_empty_list_matches_nothing(self):
        assert not inbound_guard.is_allow_listed("+919876543210", [])
        assert not inbound_guard.is_allow_listed("+919876543210", None)


@pytest.mark.asyncio
class TestTheCallLimit:
    async def test_zero_and_negative_mean_unlimited(self):
        """A form that can only hold a number says "off" with -1, and Bolna
        spells it exactly that way. Storing it literally must not be the same
        as banning every caller."""
        for limit in (0, -1):
            decision = await inbound_guard.evaluate(
                caller="+919876543210",
                phone_number=_Number(inbound_max_calls_per_caller=limit),
            )
            assert decision.allowed, limit

    @requires_redis
    async def test_a_caller_over_the_limit_is_refused(self):
        number = _Number(inbound_max_calls_per_caller=2)

        first = await inbound_guard.evaluate(
            caller="+919876543210", phone_number=number
        )
        second = await inbound_guard.evaluate(
            caller="+919876543210", phone_number=number
        )
        third = await inbound_guard.evaluate(
            caller="+919876543210", phone_number=number
        )

        assert first.allowed and second.allowed
        assert not third.allowed
        assert third.reason == "caller_call_limit_exceeded"

    @requires_redis
    async def test_the_limit_is_per_caller_not_per_number(self):
        number = _Number(inbound_max_calls_per_caller=1)

        await inbound_guard.evaluate(caller="+919876543210", phone_number=number)
        other = await inbound_guard.evaluate(
            caller="+919999999999", phone_number=number
        )

        assert other.allowed

    @requires_redis
    async def test_the_allow_list_bypasses_the_limit(self):
        number = _Number(
            inbound_max_calls_per_caller=1, inbound_allow_list=["+91 98765 43210"]
        )

        for _ in range(4):
            decision = await inbound_guard.evaluate(
                caller="+919876543210", phone_number=number
            )
            assert decision.allowed

    @requires_redis
    async def test_a_refused_caller_still_gets_their_attributes_resolved(self):
        """``contact_context`` must not mean "who is calling, unless we said
        no". A field a reader has to check ``allowed`` before trusting is a
        field somebody eventually reads without checking."""
        number = _Number(inbound_max_calls_per_caller=1, inbound_contact_list_id=3)
        contact = _Contact(attributes={"policy_number": "POL-9"})

        await inbound_guard.evaluate(
            caller="+919876543210",
            phone_number=number,
            lookup_contact=_lookup(contact),
        )
        second = await inbound_guard.evaluate(
            caller="+919876543210",
            phone_number=number,
            lookup_contact=_lookup(contact),
        )

        assert not second.allowed
        assert second.contact_context["policy_number"] == "POL-9"
