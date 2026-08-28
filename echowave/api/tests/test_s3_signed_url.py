from api.routes import s3_signed_url
from api.routes.s3_signed_url import (
    _extract_legacy_workflow_run_id,
    _extract_org_id_from_key,
)


def test_split_recording_keys_are_workflow_run_artifacts_not_org_keys():
    assert _extract_legacy_workflow_run_id("recordings/1855/user.wav") == 1855
    assert _extract_legacy_workflow_run_id("recordings/1855/bot.wav") == 1855

    assert _extract_org_id_from_key("recordings/1855/user.wav") is None
    assert _extract_org_id_from_key("recordings/1855/bot.wav") is None


def test_legacy_recording_keys_do_not_fall_through_to_org_scoped_auth():
    assert _extract_legacy_workflow_run_id("recordings/1855.wav") == 1855
    assert _extract_legacy_workflow_run_id("recordings/1855/other.wav") is None

    assert _extract_org_id_from_key("recordings/1855.wav") is None
    assert _extract_org_id_from_key("recordings/1855/other.wav") is None


def test_known_org_scoped_keys_extract_org_id():
    assert _extract_org_id_from_key("campaigns/42/source.csv") == 42
    assert _extract_org_id_from_key("knowledge_base/42/document/file.pdf") == 42
    assert _extract_legacy_workflow_run_id("campaigns/42/source.csv") is None


def test_unknown_numeric_prefix_is_not_treated_as_org_scoped():
    assert _extract_org_id_from_key("unknown/42/file.wav") is None


class TestStorageBackendFallback:
    """A run naming a backend this deployment no longer configures.

    The production symptom was a Run Preview showing "Recording: Failed to
    generate signed URL Transcript: Failed to generate signed URL" — both
    artifacts failing identically, which is the tell that the failure is
    per-deployment rather than per-file. ``get_storage_for_backend`` raises
    ``ValueError`` for a missing ``MINIO_PUBLIC_ENDPOINT``/``S3_BUCKET``
    *before* anything is signed, so neither artifact ever reached storage.
    """

    def test_a_configured_backend_is_used_as_is(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(
            s3_signed_url, "get_storage_for_backend", lambda backend: sentinel
        )

        assert (
            s3_signed_url._storage_for_recorded_backend("s3", "recordings/64.wav")
            is sentinel
        )

    def test_an_unconfigured_backend_falls_back_to_the_current_one(self, monkeypatch):
        def _unconfigured(backend):
            raise ValueError("MINIO_PUBLIC_ENDPOINT is required for MinIO storage.")

        current = object()
        monkeypatch.setattr(s3_signed_url, "get_storage_for_backend", _unconfigured)
        monkeypatch.setattr(s3_signed_url, "storage_fs", current)

        assert (
            s3_signed_url._storage_for_recorded_backend("minio", "recordings/64.wav")
            is current
        )

    def test_the_fallback_is_logged_as_an_error_not_swallowed(self, monkeypatch):
        """The other reason a run names an unconfigured backend is that the
        storage config is simply wrong, and that must stay visible."""

        def _unconfigured(backend):
            raise ValueError("S3_BUCKET environment variable is required")

        monkeypatch.setattr(s3_signed_url, "get_storage_for_backend", _unconfigured)
        monkeypatch.setattr(s3_signed_url, "storage_fs", object())

        seen: list[str] = []
        monkeypatch.setattr(
            s3_signed_url.logger, "error", lambda message: seen.append(message)
        )

        s3_signed_url._storage_for_recorded_backend("s3", "transcripts/64.txt")

        assert len(seen) == 1
        assert "transcripts/64.txt" in seen[0]
        assert "S3_BUCKET" in seen[0]
