"""Sending the message the caller was promised.

The agent said "I've sent you the link". Everything here is about that sentence
being true, and about the two ways it goes wrong: the message not arriving, and
the message arriving when it should not have.

**Nothing in this path may take the call's data down with it.** By the time a
follow-up runs, the conversation is over, the transcript is stored and the
receipt is costed. Losing any of that because a carrier returned 429 would be a
terrible trade, so failures are values here, not exceptions.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

from api.services.messaging import follow_up
from api.services.messaging.send import (
    MAX_BODY_LENGTH,
    MessagingError,
    MessagingProviderNotSupported,
    SendResult,
    send_message,
)
from api.services.workflow.dto import SmsNodeData

TWILIO = {"account_sid": "ACtest", "auth_token": "secret"}
PLIVO = {"auth_id": "MAtest", "auth_token": "secret"}


def _response(status: int, payload: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=payload if payload is not None else {},
        request=httpx.Request("POST", "https://carrier.example/send"),
    )


class TestNumbersAsTheyArrive:
    """Numbers reach here from CSV columns, caller ID and trigger payloads, and
    every one of those formats them differently."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("+919876543210", "+919876543210"),
            ("+91 98765 43210", "+919876543210"),
            ("+91-98765-43210", "+919876543210"),
            ("0091 9876543210", "+919876543210"),
        ],
    )
    def test_formatting_is_stripped(self, raw, expected):
        from api.services.messaging.send import _normalise_number

        assert _normalise_number(raw) == expected

    @pytest.mark.asyncio
    async def test_a_number_with_no_country_code_is_refused(self):
        """Not defaulted to +91. A ten-digit number meant for +1 would text a
        stranger in India about an appointment they never made — and the mistake
        would be invisible, because the send succeeds."""
        with pytest.raises(MessagingError) as exc:
            await send_message(
                provider="twilio",
                credentials=TWILIO,
                to="9876543210",
                from_="+911111111111",
                body="hi",
            )
        assert "country code" in str(exc.value)


class TestWhatIsRefusedBeforeTheCarrierSeesIt:
    @pytest.mark.asyncio
    async def test_an_unsupported_provider_says_which_ones_work(self):
        with pytest.raises(MessagingProviderNotSupported) as exc:
            await send_message(
                provider="vonage",
                credentials={},
                to="+919876543210",
                from_="+911111111111",
                body="hi",
            )
        assert "twilio" in str(exc.value)

    @pytest.mark.asyncio
    async def test_an_empty_body_is_refused(self):
        with pytest.raises(MessagingError):
            await send_message(
                provider="twilio",
                credentials=TWILIO,
                to="+919876543210",
                from_="+911111111111",
                body="   ",
            )

    @pytest.mark.asyncio
    async def test_an_enormous_body_is_refused_with_the_reason(self):
        """Carriers segment long messages and bill per segment — 70 characters
        each for Indic scripts. Sending it silently would be a surprising
        invoice rather than an error."""
        with pytest.raises(MessagingError) as exc:
            await send_message(
                provider="twilio",
                credentials=TWILIO,
                to="+919876543210",
                from_="+911111111111",
                body="x" * (MAX_BODY_LENGTH + 1),
            )
        assert "segments" in str(exc.value)

    @pytest.mark.asyncio
    async def test_missing_credentials_are_named(self):
        with pytest.raises(MessagingError) as exc:
            await send_message(
                provider="twilio",
                credentials={},
                to="+919876543210",
                from_="+911111111111",
                body="hi",
            )
        assert "account_sid" in str(exc.value)


@pytest.mark.asyncio
class TestTalkingToCarriers:
    async def test_twilio_gets_the_shape_it_expects(self):
        post = AsyncMock(return_value=_response(201, {"sid": "SM123"}))
        with patch("httpx.AsyncClient.post", post):
            result = await send_message(
                provider="twilio",
                credentials=TWILIO,
                to="+919876543210",
                from_="+911111111111",
                body="Your ref is AB12.",
            )

        assert result.ok and result.message_id == "SM123"
        url = post.await_args.args[0]
        assert "ACtest/Messages.json" in url
        assert post.await_args.kwargs["data"]["To"] == "+919876543210"

    async def test_whatsapp_is_twilio_with_a_prefix(self):
        """Same account, same endpoint, different address scheme — which is why
        it needs no separate credential."""
        post = AsyncMock(return_value=_response(201, {"sid": "SM124"}))
        with patch("httpx.AsyncClient.post", post):
            await send_message(
                provider="whatsapp",
                credentials=TWILIO,
                to="+919876543210",
                from_="+911111111111",
                body="hi",
            )
        assert post.await_args.kwargs["data"]["To"] == "whatsapp:+919876543210"
        assert post.await_args.kwargs["data"]["From"] == "whatsapp:+911111111111"

    async def test_plivo_wants_bare_numbers(self):
        post = AsyncMock(return_value=_response(202, {"message_uuid": ["uuid-1"]}))
        with patch("httpx.AsyncClient.post", post):
            result = await send_message(
                provider="plivo",
                credentials=PLIVO,
                to="+919876543210",
                from_="+911111111111",
                body="hi",
            )
        assert result.ok and result.message_id == "uuid-1"
        assert post.await_args.kwargs["json"]["dst"] == "919876543210"

    async def test_a_refusal_carries_the_carriers_own_words(self):
        """"The 'To' number is not a valid mobile number" tells an operator what
        to fix. "HTTP 400" tells them to open a ticket."""
        post = AsyncMock(
            return_value=_response(
                400, {"message": "The 'To' number is not a valid mobile number"}
            )
        )
        with patch("httpx.AsyncClient.post", post):
            result = await send_message(
                provider="twilio",
                credentials=TWILIO,
                to="+919876543210",
                from_="+911111111111",
                body="hi",
            )
        assert not result.ok
        assert "not a valid mobile number" in result.error
        assert result.status_code == 400

    async def test_a_network_failure_is_a_result_not_an_exception(self):
        """The post-call task must survive this. The transcript and the receipt
        matter more than the SMS."""
        with patch("httpx.AsyncClient.post", AsyncMock(side_effect=httpx.ConnectError("boom"))):
            result = await send_message(
                provider="twilio",
                credentials=TWILIO,
                to="+919876543210",
                from_="+911111111111",
                body="hi",
            )
        assert result == SendResult(
            ok=False, provider="twilio", to="+919876543210", error="boom"
        )


class TestSavingASendMessageNode:
    def test_a_complete_node_validates(self):
        node = SmsNodeData(name="Confirm", body="Your ref is {{ref}}")
        assert node.channel == "sms"

    def test_an_enabled_node_with_no_message_is_refused(self):
        with pytest.raises(ValidationError) as exc:
            SmsNodeData(name="Confirm", body="")
        assert "no message body" in str(exc.value)

    def test_a_disabled_node_may_be_empty(self):
        """Someone switched it off mid-edit. Refusing to save that would trap
        them with a node they cannot get rid of without finishing it."""
        SmsNodeData(name="Confirm", body="", enabled=False)

    def test_a_condition_with_no_variable_is_refused(self):
        """The dangerous half-edit: it reads as a guard that is holding
        something back, and in fact every call gets texted."""
        with pytest.raises(ValidationError) as exc:
            SmsNodeData(name="Confirm", body="hi", send_when_operator="equals")
        assert "no variable" in str(exc.value)

    def test_a_condition_that_could_never_match_is_refused(self):
        with pytest.raises(ValidationError):
            SmsNodeData(
                name="Confirm",
                body="hi",
                send_when_variable="amount",
                send_when_operator="greater_than",
                send_when_value="a lot",
            )


@pytest.mark.asyncio
class TestDecidingWhetherToSend:
    async def _deliver(self, node, variables, post=None):
        post = post or AsyncMock(return_value=_response(201, {"sid": "SM1"}))
        with patch("httpx.AsyncClient.post", post):
            return await follow_up.deliver(
                node,
                variables=variables,
                provider="twilio",
                credentials=TWILIO,
                default_from="+911111111111",
            ), post

    async def test_it_texts_the_number_that_was_on_the_call(self):
        """The behaviour that is right almost every time, and saves every author
        from knowing which key their trigger used for the number."""
        node = SmsNodeData(name="Confirm", body="Done.")
        _, post = await self._deliver(node, {"phone_number": "+919876543210"})
        assert post.await_args.kwargs["data"]["To"] == "+919876543210"

    async def test_an_explicit_recipient_wins(self):
        node = SmsNodeData(name="Confirm", body="Done.", to="{{alt_number}}")
        _, post = await self._deliver(
            node, {"phone_number": "+919000000000", "alt_number": "+919876543210"}
        )
        assert post.await_args.kwargs["data"]["To"] == "+919876543210"

    async def test_the_body_is_rendered_from_the_calls_own_variables(self):
        node = SmsNodeData(name="Confirm", body="Namaste {{first_name}}, ref {{ref}}.")
        _, post = await self._deliver(
            node,
            {"phone_number": "+919876543210", "first_name": "Lakshmi", "ref": "AB12"},
        )
        assert post.await_args.kwargs["data"]["Body"] == "Namaste Lakshmi, ref AB12."

    async def test_a_condition_that_holds_sends(self):
        node = SmsNodeData(
            name="Confirm",
            body="Done.",
            send_when_variable="outcome",
            send_when_operator="equals",
            send_when_value="agreed",
        )
        result, _ = await self._deliver(
            node, {"phone_number": "+919876543210", "outcome": "agreed"}
        )
        assert result.ok

    async def test_a_condition_that_does_not_hold_sends_nothing(self):
        """The failure this exists to prevent: texting somebody a confirmation
        of a call that did not succeed."""
        node = SmsNodeData(
            name="Confirm",
            body="Done.",
            send_when_variable="outcome",
            send_when_operator="equals",
            send_when_value="agreed",
        )
        result, post = await self._deliver(
            node, {"phone_number": "+919876543210", "outcome": "declined"}
        )
        assert result is None
        post.assert_not_awaited()

    async def test_a_missing_outcome_variable_sends_nothing(self):
        """Same rule as a branch: absence is not a match. A call whose outcome
        we failed to capture is not a call that went well."""
        node = SmsNodeData(
            name="Confirm",
            body="Done.",
            send_when_variable="outcome",
            send_when_operator="equals",
            send_when_value="agreed",
        )
        result, post = await self._deliver(node, {"phone_number": "+919876543210"})
        assert result is None
        post.assert_not_awaited()

    async def test_a_disabled_node_sends_nothing(self):
        node = SmsNodeData(name="Confirm", body="Done.", enabled=False)
        result, post = await self._deliver(node, {"phone_number": "+919876543210"})
        assert result is None
        post.assert_not_awaited()

    async def test_no_number_anywhere_is_a_recorded_failure_not_a_crash(self):
        node = SmsNodeData(name="Confirm", body="Done.")
        result, post = await self._deliver(node, {})
        assert result is not None and not result.ok
        post.assert_not_awaited()
