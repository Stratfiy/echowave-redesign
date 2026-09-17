"""A spilled response must not tell the bot to use a tool it does not have.

A connected app can answer with more than the prompt should carry, so the
response is stored and the model gets a preview. The preview also carries
advice, and the advice was one sentence: run a script and read the rest with
``tools.spilled(stored_as)``.

Running a script is ``code_mode``, and ``code_mode.ALLOWED_PLANS`` does not
include Free. So on Free the preview named the one escape hatch the bot is
never offered. That is the same defect as the context block that told a model
its tools were name-only behind a loader it did not have (#353): the system
describing a capability that is not there.

It bit a real bot. "Draft My Replies" fetched Gmail on a Free account, the
response spilled, and the run ended: "Could not create any draft reply
because the full message body and thread_id are not available (tool response
was spilled/partial). No drafts were created." It was holding
GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID and the message_id at the time.

So the preview says what is true for this caller, and names the way out that
every caller has: ask for less, or fetch one item by its id.
"""

from __future__ import annotations

from api.services.sandbox import spill

#: Varied prose, not a repeated character. A run of 400 x's is ~34,000
#: characters and almost no tokens -- BPE collapses it -- so a fixture built
#: that way passes ``is_large`` under the character fallback and fails it
#: under the real tokenizer, which is the one that runs in CI.
BIG = {
    "messages": [
        {
            "id": f"m{n}",
            "subject": f"Invoice {n} for the Hosur clinic reconciliation",
            "body": (
                f"Message {n}. Please confirm whether the payment against "
                f"invoice {n} cleared on the {n % 28 + 1}th, and if it did "
                "not, say which account it was drawn on so we can trace it "
                "with the bank before the quarter closes."
            ),
        }
        for n in range(120)
    ]
}
#: How a connected app actually answers: the payload under "data", beside the
#: call's own status. ``is_large`` reads that key and nothing else, so a dict
#: without it never spills at all.
ENVELOPE = {"successful": True, "data": BIG}


class TestTheNoteMatchesWhatTheCallerHas:
    def test_scripts_available_still_names_the_script(self):
        """The paid path is unchanged: it is the best answer when it exists,
        because it reads the whole response rather than a slice of it."""
        note = spill.preview(BIG, stored_as="k", can_run_scripts=True)["note"]
        assert "tools.spilled" in note

    def test_scripts_unavailable_never_names_the_script(self):
        note = spill.preview(BIG, stored_as="k", can_run_scripts=False)["note"]
        assert "tools.spilled" not in note
        assert "script" not in note.lower()

    def test_the_default_is_the_safe_one(self):
        """A caller that has not been taught to pass the flag must not be the
        caller that advertises a tool it cannot reach. Unknown means no --
        the direction ``is_read`` and ``writes_allowed`` both take."""
        note = spill.preview(BIG, stored_as="k")["note"]
        assert "tools.spilled" not in note


class TestEveryCallerIsToldWhatItCanDo:
    """The bug was not only the false advice. Nothing named the path the bot
    already had: it knew the message_id and never fetched that one message."""

    def test_fetching_one_item_by_id_is_named(self):
        note = spill.preview(BIG, stored_as="k", can_run_scripts=False)["note"]
        assert "id" in note.lower()

    def test_asking_for_less_is_named(self):
        note = spill.preview(BIG, stored_as="k", can_run_scripts=False)["note"]
        assert "fewer" in note.lower() or "narrow" in note.lower()

    def test_the_paid_note_names_them_too(self):
        """A script is not always the cheapest way out of a spill. One
        message by id beats spinning up a box to read eighty."""
        note = spill.preview(BIG, stored_as="k", can_run_scripts=True)["note"]
        assert "id" in note.lower()


class TestWhatThePreviewStillDoes:
    def test_the_data_still_comes_through(self):
        out = spill.preview(BIG, stored_as="k", can_run_scripts=False)
        assert out["spilled"] is True
        assert out["stored_as"] == "k"
        assert out["rows"] == 120
        assert out["first"]

    def test_a_response_that_is_not_a_list_still_previews(self):
        out = spill.preview({"body": "y" * 9_000}, stored_as="k", can_run_scripts=False)
        assert out["head"]
        assert "tools.spilled" not in out["note"]


class TestTheCallSitesAskTheRealQuestion:
    """A default of False fixes the lie and would leave the paid plans worse
    off than before. Both callers must ask code_mode whether this account can
    actually run a script, so Everyday and up keep the better advice."""

    @staticmethod
    def _a_store_that_works():
        from unittest.mock import AsyncMock, MagicMock, patch

        store = MagicMock()
        store.acreate_file_from_bytes = AsyncMock(return_value=True)
        return patch("api.services.storage.get_storage", return_value=store)

    async def test_the_bot_engine_passes_what_code_mode_says(self):
        from unittest.mock import AsyncMock, patch

        from api.services.sandbox import code_mode

        with (
            self._a_store_that_works(),
            patch.object(code_mode, "allowed", AsyncMock(return_value=True)) as asked,
        ):
            out = await spill.spill_if_large(
                ENVELOPE,
                organization_id=7,
                run_id=1,
                name="GMAIL_FETCH_EMAILS",
                call_id="c1",
                can_run_scripts=await code_mode.allowed(7),
            )
        asked.assert_awaited()
        assert "tools.spilled" in out["data"]["note"]

    async def test_a_free_account_gets_the_reachable_advice(self):
        from unittest.mock import AsyncMock, patch

        from api.services.sandbox import code_mode

        with (
            self._a_store_that_works(),
            patch.object(code_mode, "allowed", AsyncMock(return_value=False)),
        ):
            out = await spill.spill_if_large(
                ENVELOPE,
                organization_id=7,
                run_id=1,
                name="GMAIL_FETCH_EMAILS",
                call_id="c1",
                can_run_scripts=await code_mode.allowed(7),
            )
        note = out["data"]["note"]
        assert "tools.spilled" not in note
        assert "id" in note.lower()


class TestAStoreThatFailedStillNamesAWayOut:
    """No stored copy is the worst case, not a reason to say less. The old
    note told the model only that something had gone wrong."""

    def test_the_failure_note_still_says_what_to_do(self):
        note = spill.preview(BIG, stored_as="", can_run_scripts=False)["note"]
        assert "could not be stored" in note
        assert "id" in note.lower()

    def test_it_does_not_name_a_stored_copy_to_go_back_for(self):
        note = spill.preview(BIG, stored_as="", can_run_scripts=True)["note"]
        assert "tools.spilled" not in note
