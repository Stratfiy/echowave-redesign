"""A preview that drops identifiers makes every later write impossible.

A spilled row was truncated as raw JSON: the first N characters of the
serialised row, then an ellipsis. Whatever came after those characters was
gone -- and what comes after depends on nothing but key order.

For Gmail that is the sender. GMAIL_CREATE_EMAIL_DRAFT needs
``recipient_email``; the fetch spills; the preview keeps the body and the
labels and loses the address. GMAIL_REPLY_TO_THREAD needs only ``thread_id``,
which survives. So the model reached for the reply-send -- not carelessly,
but because it was the only tool whose arguments it could actually fill.

Three PRs were spent on that behaviour before anyone asked why the model
could not fill the draft tool's arguments. The answer was that we had thrown
the argument away.

So the preview truncates prose and keeps identifiers. A body is what a
preview is for cutting; an id, an address or a subject line is the part that
makes the rest of the turn possible.
"""

from __future__ import annotations

import json

from api.services.sandbox import spill


#: A Gmail row shaped the way the real one is: the body dwarfs everything,
#: and the fields that matter for a reply sit after it.
def message(n: int) -> dict:
    return {
        "body": (f"Message {n}. " + "Please confirm the reconciliation figures. " * 40),
        "labels": ["INBOX", "UNREAD"],
        "messageId": f"msg-{n}",
        "threadId": f"thread-{n}",
        "sender": f"person{n}@example.com",
        "subject": f"Invoice {n}",
    }


BIG = {"messages": [message(n) for n in range(40)]}


class TestTheFieldsAWriteNeedsSurvive:
    def test_the_sender_is_still_there(self):
        out = spill.preview(BIG, stored_as="k")
        assert "person0@example.com" in json.dumps(out["first"])

    def test_the_thread_and_message_ids_are_still_there(self):
        out = spill.preview(BIG, stored_as="k")
        shown = json.dumps(out["first"])
        assert "thread-0" in shown
        assert "msg-0" in shown

    def test_the_subject_is_still_there(self):
        out = spill.preview(BIG, stored_as="k")
        assert "Invoice 0" in json.dumps(out["first"])

    def test_every_previewed_row_keeps_them(self):
        """Not just the first one that happened to fit under the budget."""
        out = spill.preview(BIG, stored_as="k")
        for row in out["first"]:
            assert isinstance(row, dict)
            assert row["sender"]
            assert row["threadId"]


class TestTheProseIsStillCut:
    def test_a_long_body_is_shortened(self):
        out = spill.preview(BIG, stored_as="k")
        assert len(out["first"][0]["body"]) < len(BIG["messages"][0]["body"])

    def test_the_preview_stays_small(self):
        """The whole point of spilling. Identifiers are short; keeping them
        must not put the response back in the prompt."""
        out = spill.preview(BIG, stored_as="k")
        assert len(json.dumps(out["first"])) < spill.PREVIEW_CHARS * 3

    def test_the_count_is_still_reported(self):
        assert spill.preview(BIG, stored_as="k")["rows"] == 40


class TestShapesThatAreNotRowsOfDicts:
    def test_a_list_of_strings_still_previews(self):
        out = spill.preview({"ids": ["x" * 500 for _ in range(40)]}, stored_as="k")
        assert out["rows"] == 40
        assert out["first"]

    def test_a_response_with_no_list_still_previews(self):
        out = spill.preview({"body": "y" * 9_000}, stored_as="k")
        assert out["head"]

    def test_a_nested_value_does_not_break_it(self):
        rows = [
            {"id": n, "payload": {"deep": {"deeper": "z" * 400}}} for n in range(40)
        ]
        out = spill.preview({"rows": rows}, stored_as="k")
        assert out["first"][0]["id"] == 0


#: One Gmail message fetched by id, shaped the way the real one is: a long
#: body, and a headers list that is the biggest list in the record.
ONE = {
    "messageText": "Your card payment is due on Friday. " * 600,
    "payload": {"mimeType": "multipart/alternative"},
    "headers": [{"name": f"X-Header-{n}", "value": "v" * 50} for n in range(30)],
    "threadId": "thread-one",
    "sender": "billing@bank.example",
    "subject": "Payment due",
}


class TestARecordAroundAListKeepsItsOwnFields:
    """Seen live: one message, spilled, previewed as five header rows and
    nothing else. The thread id and sender beside the list were gone, and
    the bot said it could not reply for want of them."""

    def _preview(self) -> dict:
        return spill.preview(ONE, stored_as="k")

    def test_the_thread_id_and_sender_ride_along(self):
        fields = self._preview()["fields"]
        assert fields["threadId"] == "thread-one"
        assert fields["sender"] == "billing@bank.example"
        assert fields["subject"] == "Payment due"

    def test_the_body_is_shortened_and_the_list_summarised(self):
        fields = self._preview()["fields"]
        assert fields["messageText"].endswith(" …")
        assert fields["headers"] == "30 items"

    def test_the_rows_are_still_there(self):
        out = self._preview()
        assert out["rows"] == 30 and len(out["first"]) == spill.PREVIEW_ITEMS

    def test_it_stays_small(self):
        assert len(json.dumps(self._preview())) < 3_000
