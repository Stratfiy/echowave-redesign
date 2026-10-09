"""Every request to a model fits what the model can take (context_v2).

The plan's allowance (``chat_memory``) decides how much of a thread is kept
and deliberately keeps the newest message whole, however big; nothing
counted the rest of the request -- the context, the tool schemas, tool
results, the reply's room -- or counted Indian scripts at anything like
their real cost. A pasted contract, a mailbox returned by a connected app,
or a long Tamil message made a request the vendor refused.

These hold the assembled request to a hard ceiling and check that what the
person asked for is still in it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api.services import features
from api.services.agent_builder import client
from api.services.agent_builder import request_budget as rb
from api.services.billing import model_usage
from api.services.workflow import chat_memory

#: Small enough that a test message can exceed it without megabytes.
CEILING = 24_000
BUDGET = rb.input_budget(CEILING)


@pytest.fixture
def v2_on():
    features.set_snapshot({(rb.FLAG, None): features.Override(enabled=True)})
    yield
    features.clear_snapshot()


def _filler(n: int, word: str = "routine") -> str:
    return "\n\n".join(
        f"Paragraph {i}: the {word} terms of supply, delivery schedules and "
        "the usual boilerplate that every agreement carries, repeated here "
        "at length so the document is long." * 3
        for i in range(n)
    )


def _sent_tokens(prepared: rb.Prepared, system: str = "", tools=None) -> int:
    return (
        rb.estimate(system)
        + rb.tools_tokens(tools)
        + sum(rb.message_tokens(m) for m in prepared.conversation.messages)
    )


def _prepare(conversation, *, system="You are helpful.", tools=None):
    return rb.prepare(
        provider="openai",
        system=system,
        conversation=conversation,
        tools=tools,
        organization_id=1,
        ceiling=CEILING,
    )


class TestTheEstimate:
    def test_indian_scripts_count_at_least_a_token_a_character(self):
        tamil = "இந்த மாதம் கட்டணம் செலுத்த வேண்டிய தேதி பதினைந்து" * 20
        hindi = "कृपया अगले मंगलवार को डिलीवरी का समय बताइए" * 20
        for text in (tamil, hindi):
            letters = sum(1 for ch in text if not ch.isspace())
            assert rb.estimate(text) >= letters
            # The plan meter's estimate is a gauge; this one is a bound.
            assert rb.estimate(text) > chat_memory.tokens_of(text)

    def test_latin_is_counted_more_pessimistically_than_the_plan_meter(self):
        assert rb.estimate("x" * 40_000) > chat_memory.tokens_of("x" * 40_000)

    def test_a_picture_is_a_picture_not_its_base64(self):
        content = {
            "summary": "screenshot of the page",
            "_images": [{"media_type": "image/png", "data": "A" * 500_000}],
        }
        assert rb.content_tokens(content) < rb.IMAGE_TOKENS + 100

    def test_the_ceiling_is_the_smallest_vendor_a_turn_can_fall_back_to(self):
        assert rb.request_ceiling("google") == min(rb.PROVIDER_CEILINGS.values())
        assert rb.input_budget(100_000) == int(100_000 * rb.SAFETY) - rb.OUTPUT_RESERVE


class TestOffMeansUntouched:
    def test_flag_off_sends_the_conversation_as_built(self):
        conversation = client.Conversation()
        conversation.add_user("x" * 400_000)
        prepared = _prepare(conversation)
        assert prepared.conversation is conversation
        assert prepared.estimated is None


class TestAnOversizedSingleMessage:
    def test_a_pasted_document_keeps_the_question_and_the_passage_it_asks_about(
        self, v2_on
    ):
        document = (
            "Supply agreement between Kaveri Traders and Meera Textiles.\n\n"
            + _filler(80)
            + "\n\nPenalty clause: a late delivery costs Rs 4,500 per day, "
            "capped at Rs 90,000.\n\n"
            + _filler(80, "standard")
            + "\n\nWhat is the penalty for a late delivery in this agreement?"
        )
        conversation = client.Conversation()
        conversation.add_user(document)
        assert rb.estimate(document) > BUDGET

        prepared = _prepare(conversation)
        sent = prepared.conversation.messages[-1]["content"]

        assert _sent_tokens(prepared, "You are helpful.") <= BUDGET
        assert "Rs 4,500 per day" in sent
        assert "What is the penalty for a late delivery" in sent
        assert "Supply agreement between Kaveri Traders" in sent
        # Said, not silently cut.
        assert "Shortened to fit" in sent
        assert "too long to read whole" in sent
        # The caller's transcript is the record; it is not rewritten.
        assert conversation.messages[-1]["content"] == document

    def test_decibyls_context_goes_before_the_persons_words(self, v2_on):
        question = "Book Meera for Tuesday at 4 and tell her the address."
        request = "## The team\n" + _filler(150) + rb.QUESTION_MARKER + question
        conversation = client.Conversation()
        conversation.add_user(request)

        prepared = _prepare(conversation)
        sent = prepared.conversation.messages[-1]["content"]

        assert _sent_tokens(prepared, "You are helpful.") <= BUDGET
        assert sent.endswith(rb.QUESTION_MARKER + question)
        assert prepared.actions == ["shortened the context in front of the request"]

    def test_history_goes_first_and_the_cut_is_said(self, v2_on):
        conversation = client.Conversation()
        for i in range(30):
            conversation.add_user(f"Old question {i}: " + _filler(3))
            conversation.messages.append(
                {"role": "assistant", "content": f"Old answer {i}: " + _filler(3)}
            )
        conversation.add_user("And what did we decide about Thursday?")

        prepared = _prepare(conversation)
        messages = prepared.conversation.messages

        assert _sent_tokens(prepared, "You are helpful.") <= BUDGET
        assert messages[-1]["content"] == "And what did we decide about Thursday?"
        assert messages[0]["role"] == "user"
        assert (
            "earlier messages of this conversation were left out"
            in (messages[0]["content"])
        )
        # The turn just before the request survives the cut.
        assert "Old answer 29" in messages[-2]["content"]


class TestALongToolResult:
    def _turn(self) -> client.Conversation:
        conversation = client.Conversation()
        for i in range(6):
            conversation.add_user(f"Earlier {i}: " + _filler(2))
            conversation.messages.append({"role": "assistant", "content": f"Reply {i}"})
        conversation.add_user("Find Meera's email about the refund for order 5512.")
        call = client.ToolCall(id="call_1", name="app_gmail_search", arguments={})
        conversation.add_assistant(client.ModelReply(text="", tool_calls=(call,)))
        emails = [
            {
                "from": f"customer{i}@example.com",
                "subject": f"Delivery update {i}",
                "body": "Thanks for the delivery schedule, all received in good "
                "order and nothing further is needed from your side.",
            }
            for i in range(1500)
        ]
        emails.insert(
            900,
            {
                "from": "meera@textiles.example",
                "subject": "Refund for order 5512",
                "body": "Please refund Rs 12,400 to account ending 8841.",
            },
        )
        conversation.add_tool_result(call, {"messages": emails})
        return conversation

    def test_the_result_is_cut_to_what_the_request_asked_for(self, v2_on):
        conversation = self._turn()
        prepared = _prepare(conversation)
        messages = prepared.conversation.messages

        assert _sent_tokens(prepared, "You are helpful.") <= BUDGET
        tool = messages[-1]
        assert tool["role"] == "tool"
        assert tool["tool_call_id"] == "call_1"
        assert "Rs 12,400" in tool["content"]
        assert "narrower read" in tool["content"]
        # The request and the call it started are intact.
        assert messages[-3]["content"] == (
            "Find Meera's email about the refund for order 5512."
        )
        assert messages[-2]["tool_calls"][0]["id"] == "call_1"

    def test_a_mid_turn_notice_is_not_taken_for_the_request(self, v2_on):
        conversation = self._turn()
        conversation.add_user("That was your last tool call for this turn.")
        prepared = _prepare(conversation)
        texts = [m.get("content") for m in prepared.conversation.messages]

        assert _sent_tokens(prepared, "You are helpful.") <= BUDGET
        assert "Find Meera's email about the refund for order 5512." in texts
        assert any(m.get("role") == "tool" for m in prepared.conversation.messages)


class TestIndianScriptText:
    def test_a_long_tamil_message_fits_and_keeps_what_it_asks(self, v2_on):
        filler = "\n\n".join(
            f"பகுதி {i}: இது வழக்கமான விநியோக விதிமுறைகள் மற்றும் பொதுவான "
            "நிபந்தனைகள் பற்றிய நீண்ட விளக்கம் ஆகும்." * 4
            for i in range(40)
        )
        key = "அபராதம்: தாமதமான ஒவ்வொரு நாளுக்கும் ரூ 4500 அபராதம் விதிக்கப்படும்."
        question = "இந்த ஒப்பந்தத்தில் தாமதமான விநியோகத்துக்கு அபராதம் எவ்வளவு?"
        text = f"{filler}\n\n{key}\n\n{filler}\n\n{question}"
        # The plan meter thinks this fits; a vendor would not.
        assert chat_memory.tokens_of(text) < CEILING < rb.estimate(text)

        conversation = client.Conversation()
        conversation.add_user(text)
        prepared = _prepare(conversation)
        sent = prepared.conversation.messages[-1]["content"]

        assert _sent_tokens(prepared, "You are helpful.") <= BUDGET
        assert key in sent
        assert question in sent


class TestReconcile:
    def test_the_ratio_is_against_the_vendors_whole_input(self):
        prepared = rb.Prepared(conversation=None, estimated=1200)
        reply = client.ModelReply(
            text="ok",
            usage={
                "prompt_tokens": 200,
                "completion_tokens": 10,
                "cache_read_input_tokens": 800,
            },
        )
        assert rb.reconcile(prepared, reply, "anthropic") == pytest.approx(1.2)
        assert rb.reconcile(prepared, reply, "openai") == pytest.approx(6.0)

    def test_nothing_to_reconcile_without_both_numbers(self):
        reply = client.ModelReply(text="ok", usage=None)
        assert rb.reconcile(rb.Prepared(None, estimated=10), reply, "openai") is None
        assert rb.reconcile(rb.Prepared(None), reply, "openai") is None


class TestTheVendorSeesTheFittedRequest:
    @pytest.mark.asyncio
    async def test_complete_sends_the_fitted_conversation(self, v2_on):
        conversation = client.Conversation()
        conversation.add_user("x " * 400_000 + "\n\nSummarise the above.")
        sent = {}

        async def fake(**kwargs):
            sent["conversation"] = kwargs["conversation"]
            return client.ModelReply(text="done", usage={"prompt_tokens": 50_000})

        with (
            patch.object(client, "_complete", AsyncMock(side_effect=fake)),
            model_usage.scope(organization_id=7, feature="test"),
        ):
            reply = await client.complete(
                provider="openai",
                model="m",
                api_key="k",
                system="sys",
                conversation=conversation,
                tools=[],
            )

        assert reply.text == "done"
        fitted = sent["conversation"]
        assert fitted is not conversation
        total = rb.estimate("sys") + sum(rb.message_tokens(m) for m in fitted.messages)
        assert total <= rb.input_budget(rb.request_ceiling("openai"))
        assert "Summarise the above." in fitted.messages[-1]["content"]

    @pytest.mark.asyncio
    async def test_stream_sends_the_fitted_conversation(self, v2_on):
        conversation = client.Conversation()
        conversation.add_user("y " * 400_000 + "\n\nWhat is this about?")
        sent = {}

        async def fake(**kwargs):
            sent["conversation"] = kwargs["conversation"]
            return client.ModelReply(text="done")

        with patch.object(client, "_stream", AsyncMock(side_effect=fake)):
            await client.stream(
                provider="anthropic",
                model="m",
                api_key="k",
                system="sys",
                conversation=conversation,
                on_text=AsyncMock(),
            )

        assert sent["conversation"] is not conversation
        assert "What is this about?" in sent["conversation"].messages[-1]["content"]
