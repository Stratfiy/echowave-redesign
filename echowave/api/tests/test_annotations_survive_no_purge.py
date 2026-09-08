"""The half of a purge that used to be missed.

Clearing a run has always meant the recording, the transcript, the gathered
context and the logs. ``annotations`` was never in that list, and annotations
is where the post-call pass writes what it *learned* from the conversation —
the QA summary, and ``extracted_data``, every named field the extraction
library was configured to pull out of the caller.

So both cleanup paths left behind a structured record of the person whose call
had just been deleted. On the retention sweep that is a storage-limitation
failure. On ``erase_number`` it is worse: that path answers a subject's own
erasure request, its docstring says "erase every trace of one person from an
account's calls", and a tidy JSON object of somebody's name, number and reason
for calling is more identifying than the audio was.

These tests are written against the rule rather than the current key names, so
that a field added to a QA result next year is dropped by default rather than
silently retained.
"""

from __future__ import annotations

from api.services.privacy.redaction import redact_annotations

PERSONAL = {
    "name": "Anita Sharma",
    "phone": "9876543210",
    "address": "12 MG Road, Bengaluru",
    "reason_for_visit": "chest pain since Tuesday",
}

ANNOTATIONS = {
    "qa_node_1": {
        "node_results": {
            "node-a": {
                "tags": ["pricing", "booked"],
                "summary": "Anita called about chest pain and booked Thursday.",
                "score": 4,
                "overall_sentiment": "positive",
                "extracted_data": dict(PERSONAL),
            }
        }
    },
    "tags": ["pricing", "booked"],
    "disposition": {
        "dispositions": ["booked"],
        "provider": "openai",
        "model": "gpt-4o-mini",
    },
    "follow_up_messages": [
        {"to": "9876543210", "body": "Hi Anita, your Thursday 4pm slot is confirmed."}
    ],
    "integration_results": {"crm": {"lead_id": "L-1", "notes": "chest pain"}},
}


class TestWhatIsRemoved:
    def test_the_extracted_fields_are_gone(self):
        """The library exists to capture exactly these. They must not outlive
        the conversation they came from."""
        cleaned = redact_annotations(ANNOTATIONS)
        blob = repr(cleaned)
        for value in PERSONAL.values():
            assert value not in blob

    def test_the_qa_summary_is_gone(self):
        """Free text the model wrote about the caller, which is as identifying
        as the transcript it was written from."""
        cleaned = redact_annotations(ANNOTATIONS)
        assert "chest pain" not in repr(cleaned)
        node = cleaned["qa_node_1"]["node_results"]["node-a"]
        assert "summary" not in node
        assert "extracted_data" not in node

    def test_follow_up_message_bodies_are_gone(self):
        """The agent's own outgoing messages quote the person back to
        themselves, and carry their number."""
        cleaned = redact_annotations(ANNOTATIONS)
        assert "follow_up_messages" not in cleaned
        assert "9876543210" not in repr(cleaned)

    def test_integration_results_are_gone(self):
        """Whatever a customer's CRM echoed back is theirs and unbounded — it
        cannot be assumed impersonal."""
        cleaned = redact_annotations(ANNOTATIONS)
        assert "integration_results" not in cleaned


class TestWhatSurvives:
    def test_the_outcome_labels_stay(self):
        """Closed-vocabulary codes an operations team filters on. Dropping
        these would silently rewrite historical outcome analytics for every
        purged run, which is a different kind of wrong."""
        cleaned = redact_annotations(ANNOTATIONS)
        assert cleaned["disposition"] == {"dispositions": ["booked"]}
        assert cleaned["tags"] == ["pricing", "booked"]

    def test_the_score_and_sentiment_stay(self):
        cleaned = redact_annotations(ANNOTATIONS)
        node = cleaned["qa_node_1"]["node_results"]["node-a"]
        assert node == {
            "tags": ["pricing", "booked"],
            "score": 4,
            "overall_sentiment": "positive",
        }


class TestTheRuleIsAnAllowlist:
    def test_an_unrecognised_key_is_dropped_not_kept(self):
        """The reason this is an allowlist. Annotations is an open dict written
        by four different post-call steps, and the next field somebody adds
        will land here without them knowing this module exists. Defaulting to
        *retain* would leak it; defaulting to drop costs a reviewer one line."""
        cleaned = redact_annotations({**ANNOTATIONS, "voice_biometrics": {"id": "v1"}})
        assert "voice_biometrics" not in cleaned

    def test_an_unrecognised_field_inside_a_qa_result_is_dropped(self):
        cleaned = redact_annotations(
            {"qa": {"node_results": {"n": {"tags": [], "diagnosis_guess": "angina"}}}}
        )
        assert cleaned["qa"]["node_results"]["n"] == {"tags": []}


class TestItIsSafeToRunTwice:
    def test_redacting_an_already_redacted_dict_changes_nothing(self):
        """The retention sweep retries a run whose storage delete failed, so
        this runs again on rows it already cleaned."""
        once = redact_annotations(ANNOTATIONS)
        assert redact_annotations(once) == once

    def test_empty_and_malformed_input_do_not_raise(self):
        """A run purged before annotations existed, or a row holding a list
        because something wrote one, must not take the sweep down."""
        assert redact_annotations(None) == {}
        assert redact_annotations({}) == {}
        assert redact_annotations([1, 2]) == {}
        assert redact_annotations({"qa": "not a dict"}) == {}

    def test_a_qa_block_whose_nodes_all_empty_is_dropped_entirely(self):
        """Rather than leaving a shell that reads like a QA pass which found
        nothing, which is a different claim from one that was erased."""
        cleaned = redact_annotations(
            {"qa": {"node_results": {"n": {"summary": "gone", "extracted_data": {}}}}}
        )
        assert "qa" not in cleaned
