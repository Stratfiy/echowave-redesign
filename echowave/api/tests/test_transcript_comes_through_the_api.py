"""Serving the transcript instead of signing a URL for it.

Run 336's transcript failed for a day with ``SignatureDoesNotMatch`` -- an
error that names the signature and says nothing about which of the four things
a presigned URL depends on had gone wrong: the signature, the expiry, the
bucket's CORS policy, or the credentials held at the moment of signing.

None of the four are needed to hand somebody a few kilobytes of text. These
tests are about the two properties that make the replacement safe rather than
merely working: it is authorized exactly as the signed-URL route is, and it
never returns a partial file while claiming to be whole.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes.s3_signed_url import (
    MAX_TEXT_BYTES,
    get_text_artifact,
    stream_artifact,
)

KEY = "transcripts/336.txt"


def _user(org=42, staff=None):
    return SimpleNamespace(selected_organization_id=org, staff_role=staff)


def _storage(read):
    return patch(
        "api.routes.s3_signed_url.storage_fs",
        SimpleNamespace(aread_bytes=read),
    )


def _authorized(run=SimpleNamespace(id=336, storage_backend=None)):
    return patch(
        "api.routes.s3_signed_url._authorize_and_get_workflow_run",
        AsyncMock(return_value=run),
    )


def _audited():
    return patch("api.routes.s3_signed_url._record_signed_url_access", AsyncMock())


class TestItIsAuthorizedLikeTheUrlItReplaces:
    @pytest.mark.asyncio
    async def test_another_accounts_run_never_reaches_storage(self):
        """The tenancy check is the whole reason this is not just a proxy."""
        reads = AsyncMock()
        denied = AsyncMock(side_effect=HTTPException(status_code=403, detail="no"))
        with (
            patch("api.routes.s3_signed_url._authorize_and_get_workflow_run", denied),
            _storage(reads),
        ):
            with pytest.raises(HTTPException) as raised:
                await get_text_artifact(key=KEY, user=_user())
        assert raised.value.status_code == 403
        reads.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_key_in_no_recognised_shape_is_refused(self):
        reads = AsyncMock()
        with _storage(reads):
            with pytest.raises(HTTPException) as raised:
                await get_text_artifact(key="../../etc/passwd", user=_user())
        assert raised.value.status_code == 400
        reads.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reading_it_is_written_to_the_audit_trail(self):
        """Being handed the content is the moment access happened, exactly as
        issuing a URL was."""
        with (
            _authorized(),
            _storage(AsyncMock(return_value=b"hello")),
            _audited() as audit,
        ):
            await get_text_artifact(key=KEY, user=_user())
        assert audit.await_args.kwargs["key"] == KEY


class TestItNeverLiesAboutWhatItReturned:
    @pytest.mark.asyncio
    async def test_a_missing_transcript_is_404_not_empty_text(self):
        """An empty string reads as "this call said nothing", which is a
        different claim from "there is no transcript"."""
        with _authorized(), _storage(AsyncMock(return_value=None)), _audited():
            with pytest.raises(HTTPException) as raised:
                await get_text_artifact(key=KEY, user=_user())
        assert raised.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_storage_failure_is_not_reported_as_a_missing_file(self):
        """ "No transcript" and "we could not read the transcript" need
        different words on screen and different actions from whoever reads
        them."""
        boom = AsyncMock(side_effect=RuntimeError("bucket on fire"))
        with _authorized(), _storage(boom), _audited():
            with pytest.raises(HTTPException) as raised:
                await get_text_artifact(key=KEY, user=_user())
        assert raised.value.status_code == 502

    @pytest.mark.asyncio
    async def test_an_oversized_transcript_says_it_was_cut(self):
        oversized = b"x" * (MAX_TEXT_BYTES + 1)
        with _authorized(), _storage(AsyncMock(return_value=oversized)), _audited():
            response = await get_text_artifact(key=KEY, user=_user())
        assert response["truncated"] is True
        assert len(response["text"]) == MAX_TEXT_BYTES

    @pytest.mark.asyncio
    async def test_a_transcript_that_exactly_fits_is_not_called_truncated(self):
        exact = b"y" * MAX_TEXT_BYTES
        with _authorized(), _storage(AsyncMock(return_value=exact)), _audited():
            response = await get_text_artifact(key=KEY, user=_user())
        assert response["truncated"] is False

    @pytest.mark.asyncio
    async def test_one_bad_byte_does_not_cost_the_whole_transcript(self):
        """The content is the point, not its encoding."""
        with (
            _authorized(),
            _storage(AsyncMock(return_value=b"Namaste \xff Smile Dental")),
            _audited(),
        ):
            response = await get_text_artifact(key=KEY, user=_user())
        assert "Namaste" in response["text"]
        assert "Smile Dental" in response["text"]

    @pytest.mark.asyncio
    async def test_the_read_is_capped_at_the_endpoints_limit(self):
        """One byte over, deliberately: it is how a file that exactly fits is
        told apart from one that was cut off."""
        read = AsyncMock(return_value=b"short")
        with _authorized(), _storage(read), _audited():
            await get_text_artifact(key=KEY, user=_user())
        assert read.await_args.args[1] == MAX_TEXT_BYTES


class TestTheRecordingStreams:
    """Run 336 settled which failure was live.

    The transcript came back fine through the API while the recording, on a
    presigned URL from the same process against the same bucket with the same
    credentials, still would not play. So the credentials read S3 correctly and
    it is the presigning that produces a signature S3 rejects.
    """

    @staticmethod
    async def _stream():
        yield b"RIFF"

    def _opened(self, total=1024, start=None, ctype="audio/wav"):
        return patch(
            "api.routes.s3_signed_url.storage_fs",
            SimpleNamespace(
                aopen_range=AsyncMock(
                    return_value=(self._stream(), total, start, ctype)
                )
            ),
        )

    @pytest.mark.asyncio
    async def test_a_whole_recording_advertises_that_it_can_be_seeked(self):
        """Without Accept-Ranges the browser offers no seek bar, and a
        recording you can only play from the start is most of the way to a
        broken one."""
        with _authorized(), self._opened(), _audited():
            response = await stream_artifact(
                request=SimpleNamespace(),
                key="recordings/336.wav",
                user=_user(),
            )
        assert response.status_code == 200
        assert response.headers["accept-ranges"] == "bytes"
        assert response.headers["content-length"] == "1024"

    @pytest.mark.asyncio
    async def test_a_range_reports_the_objects_real_length_not_the_slices(self):
        """Content-Range built from the slice would tell the browser the file
        is as long as the piece it just received -- which is how a seek bar
        claims a two-minute call lasted four seconds."""
        with _authorized(), self._opened(total=1024, start=100), _audited():
            response = await stream_artifact(
                request=SimpleNamespace(),
                key="recordings/336.wav",
                range_header="bytes=100-199",
                user=_user(),
            )
        assert response.status_code == 206
        assert response.headers["content-range"] == "bytes 100-199/1024"
        assert response.headers["content-length"] == "100"

    @pytest.mark.asyncio
    async def test_an_open_ended_range_runs_to_the_end_of_the_object(self):
        with _authorized(), self._opened(total=1024, start=512), _audited():
            response = await stream_artifact(
                request=SimpleNamespace(),
                key="recordings/336.wav",
                range_header="bytes=512-",
                user=_user(),
            )
        assert response.headers["content-range"] == "bytes 512-1023/1024"

    @pytest.mark.asyncio
    async def test_another_accounts_recording_never_reaches_storage(self):
        opens = AsyncMock()
        denied = AsyncMock(side_effect=HTTPException(status_code=403, detail="no"))
        with (
            patch("api.routes.s3_signed_url._authorize_and_get_workflow_run", denied),
            patch(
                "api.routes.s3_signed_url.storage_fs",
                SimpleNamespace(aopen_range=opens),
            ),
        ):
            with pytest.raises(HTTPException) as raised:
                await stream_artifact(
                    request=SimpleNamespace(),
                    key="recordings/336.wav",
                    user=_user(),
                )
        assert raised.value.status_code == 403
        opens.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_missing_recording_is_404_not_a_silent_empty_stream(self):
        with (
            _authorized(),
            patch(
                "api.routes.s3_signed_url.storage_fs",
                SimpleNamespace(aopen_range=AsyncMock(return_value=None)),
            ),
            _audited(),
        ):
            with pytest.raises(HTTPException) as raised:
                await stream_artifact(
                    request=SimpleNamespace(),
                    key="recordings/336.wav",
                    user=_user(),
                )
        assert raised.value.status_code == 404
