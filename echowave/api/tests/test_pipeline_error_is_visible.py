"""The recorded cause of a failed call has to leave the backend.

``_record_pipeline_error`` writes the provider's own error onto
``workflow_run.extra`` when a call dies on a fatal pipeline error, with the
stated intent that the UI could show it. Nothing served it: ``extra`` is not on
``WorkflowRunResponseSchema``, so for every failed call the answer to "why did
this end the moment I picked up" was in an API log, behind shell access, until
the logs rotated.

These cover the reader that closes that, and the two properties that make it
safe to put on a response: it never publishes the rest of ``extra``, and it
never turns a failed call into a failed page.
"""

from __future__ import annotations

from api.schemas.workflow import WorkflowRunResponseSchema
from api.utils.run_diagnostics import get_pipeline_error


class TestWhatItReads:
    def test_it_returns_the_recorded_failure(self):
        error = get_pipeline_error(
            {
                "pipeline_error": {
                    "detail": "Error connecting to Sarvam TTS Websocket: 400",
                    "frame_type": "ErrorFrame",
                    "at": "2026-08-28T16:56:00+00:00",
                }
            }
        )

        assert error == {
            "detail": "Error connecting to Sarvam TTS Websocket: 400",
            "frame_type": "ErrorFrame",
            "at": "2026-08-28T16:56:00+00:00",
        }

    def test_it_publishes_nothing_else_from_extra(self):
        """``extra`` is an open bag — recording storage keys today, whatever
        the next feature adds tomorrow. Exposing it wholesale would make every
        future key an accidental part of the public API, so the reader takes
        the one entry and the three fields it knows."""
        error = get_pipeline_error(
            {
                "recordings": {"user": {"storage_key": "s3://bucket/secret-key"}},
                "internal_note": "not for customers",
                "pipeline_error": {
                    "detail": "boom",
                    "frame_type": "ErrorFrame",
                    "at": "2026-08-28T16:56:00+00:00",
                    "stack": "a stack trace nobody asked to publish",
                },
            }
        )

        assert set(error) == {"detail", "frame_type", "at"}


class TestWhatItRefuses:
    def test_a_run_that_did_not_fail_this_way_has_none(self):
        assert get_pipeline_error(None) is None
        assert get_pipeline_error({}) is None
        assert get_pipeline_error({"recordings": {}}) is None

    def test_a_malformed_record_is_none_rather_than_a_crash(self):
        """A run that failed is never worth a 500 on the screen someone opened
        to find out why it failed."""
        assert get_pipeline_error({"pipeline_error": "just a string"}) is None
        assert get_pipeline_error({"pipeline_error": []}) is None
        assert get_pipeline_error({"pipeline_error": {}}) is None
        assert get_pipeline_error({"pipeline_error": {"detail": ""}}) is None
        assert get_pipeline_error({"pipeline_error": {"detail": 42}}) is None

    def test_partial_records_keep_what_they_have(self):
        """The detail is the whole point; the other two are decoration."""
        error = get_pipeline_error({"pipeline_error": {"detail": "boom"}})

        assert error == {"detail": "boom"}


class TestTheResponseCarriesIt:
    def test_the_schema_has_the_field_and_defaults_it_to_none(self):
        """Both serializers set it explicitly, but the default is what keeps
        every other construction site (and every test fixture) valid."""
        run = WorkflowRunResponseSchema.model_validate(
            {
                "id": 1,
                "workflow_id": 2,
                "name": "run",
                "mode": "plivo",
                "created_at": "2026-08-28T16:56:00+00:00",
                "is_completed": True,
                "transcript_url": None,
                "recording_url": None,
                "cost_info": None,
                "definition_id": None,
                "call_type": "outbound",
            }
        )

        assert run.pipeline_error is None

    def test_it_round_trips_onto_the_response(self):
        run = WorkflowRunResponseSchema.model_validate(
            {
                "id": 1,
                "workflow_id": 2,
                "name": "run",
                "mode": "plivo",
                "created_at": "2026-08-28T16:56:00+00:00",
                "is_completed": True,
                "transcript_url": None,
                "recording_url": None,
                "cost_info": None,
                "definition_id": None,
                "call_type": "outbound",
                "pipeline_error": get_pipeline_error(
                    {"pipeline_error": {"detail": "boom"}}
                ),
            }
        )

        assert run.pipeline_error == {"detail": "boom"}
