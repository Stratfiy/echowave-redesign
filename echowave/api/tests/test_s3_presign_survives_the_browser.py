"""A presigned URL must be usable by the browser it is handed to.

A presigned URL signs the host it is addressed to. If S3 answers with a
redirect, the browser follows it to a different host and the signature it is
carrying no longer matches — surfacing as ``SignatureDoesNotMatch``: a 403
that reads like a permissions problem and is not one.

That is what happened on the first day recordings lived in S3. The objects
were in the bucket, the IAM policy was correct and listable, and every single
playback failed with a 403. The fix is two signing defaults, and these tests
exist so nobody removes them believing them to be cosmetic.
"""

from unittest.mock import patch

from botocore.config import Config

from api.services.filesystem.s3 import S3FileSystem


def _config(fs: S3FileSystem) -> Config:
    assert fs._config is not None, "AWS deployments must pin a signing config"
    return fs._config


class TestRealAws:
    """No endpoint_url means AWS, and AWS gets both defaults."""

    def test_signs_with_sigv4(self):
        # ap-south-1 opened in 2016. Every region opened after January 2014
        # accepts only SigV4, so a SigV2 presigned URL is rejected outright —
        # not redirected, not degraded: rejected.
        assert _config(S3FileSystem(bucket_name="b")).signature_version == "s3v4"

    def test_addresses_the_bucket_by_hostname(self):
        # Virtual addressing puts the bucket in the host, so the host that was
        # signed is already the final host and there is no path-style redirect
        # for a browser to follow.
        assert (
            _config(S3FileSystem(bucket_name="b")).s3["addressing_style"] == "virtual"
        )

    def test_a_deployment_that_sets_nothing_still_gets_both(self):
        # The bug was not that these were wrong. It was that they were absent,
        # and an absent default is whatever botocore happens to do that year.
        fs = S3FileSystem(bucket_name="b", region_name="ap-south-1")
        assert _config(fs).signature_version == "s3v4"
        assert _config(fs).s3["addressing_style"] == "virtual"


class TestS3CompatibleStores:
    """An explicit endpoint is the signal that this is not AWS."""

    def test_assumes_nothing_for_a_custom_endpoint(self):
        # MinIO and Ceph generally need path addressing; forcing virtual on
        # them would break the deployments this class exists to support.
        fs = S3FileSystem(bucket_name="b", endpoint_url="https://minio.internal:9000")
        assert fs._config is None

    def test_an_operator_override_wins_on_aws_too(self):
        fs = S3FileSystem(
            bucket_name="b",
            signature_version="s3",
            addressing_style="path",
        )
        assert _config(fs).signature_version == "s3"
        assert _config(fs).s3["addressing_style"] == "path"

    def test_one_override_does_not_drop_the_other_default(self):
        # Setting only the addressing style must not silently take SigV4 away
        # — the failure that would cause is invisible until a browser tries it.
        fs = S3FileSystem(bucket_name="b", addressing_style="path")
        assert _config(fs).signature_version == "s3v4"
        assert _config(fs).s3["addressing_style"] == "path"


class TestThePresignChecksItsOwnOutput:
    """Run 336 spent a night reading as a credentials problem.

    S3's answer was ``SignatureDoesNotMatch``, which names the signature and
    says nothing about *why* it does not match. The signature covers the query
    string, so a URL signed with a ``Response*`` override and delivered without
    it fails exactly that way -- and the only evidence sits in an XML page in
    somebody's browser, hours later.

    So the presign now checks that the URL it returns carries what it signed.
    It cannot repair anything; it can stop the next one being a night.
    """

    @staticmethod
    def _collect(messages):
        """loguru formats lazily, so the template and its arguments arrive
        separately. Rendering here is what lets a test assert on the sentence
        an operator actually reads."""

        def record(template, *args):
            messages.append(template.format(*args))

        return record

    def test_it_says_which_override_went_missing(self):
        from api.services.filesystem.s3 import (
            _warn_if_the_url_lost_what_was_signed as check,
        )

        stripped = (
            "https://b.s3.ap-south-1.amazonaws.com/transcripts/336.txt"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc"
        )
        messages = []
        with patch("api.services.filesystem.s3.logger.error", self._collect(messages)):
            check(
                stripped,
                {
                    "Bucket": "b",
                    "Key": "transcripts/336.txt",
                    "ResponseContentType": "text/plain",
                    "ResponseContentDisposition": "inline",
                },
                "transcripts/336.txt",
            )

        assert messages, "a URL missing a signed parameter said nothing"
        assert "response-content-disposition" in messages[0]
        assert "SignatureDoesNotMatch" in messages[0]

    def test_a_complete_url_is_quiet(self):
        from api.services.filesystem.s3 import (
            _warn_if_the_url_lost_what_was_signed as check,
        )

        intact = (
            "https://b.s3.ap-south-1.amazonaws.com/transcripts/336.txt"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc"
            "&response-content-type=text%2Fplain"
            "&response-content-disposition=inline"
        )
        messages = []
        with patch("api.services.filesystem.s3.logger.error", self._collect(messages)):
            check(
                intact,
                {
                    "ResponseContentType": "text/plain",
                    "ResponseContentDisposition": "inline",
                },
                "transcripts/336.txt",
            )
        assert messages == []

    def test_a_download_with_no_overrides_is_not_flagged(self):
        """A recording asked for without force_inline signs no overrides, so
        there is nothing to lose and nothing to say."""
        from api.services.filesystem.s3 import (
            _warn_if_the_url_lost_what_was_signed as check,
        )

        messages = []
        with patch("api.services.filesystem.s3.logger.error", self._collect(messages)):
            check(
                "https://b.s3.ap-south-1.amazonaws.com/recordings/336.wav"
                "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc",
                {"Bucket": "b", "Key": "recordings/336.wav"},
                "recordings/336.wav",
            )
        assert messages == []

    def test_a_url_with_no_signature_at_all_is_reported(self):
        from api.services.filesystem.s3 import (
            _warn_if_the_url_lost_what_was_signed as check,
        )

        messages = []
        with patch("api.services.filesystem.s3.logger.error", self._collect(messages)):
            check("https://b.s3.ap-south-1.amazonaws.com/a.txt", {}, "a.txt")
        assert any("no signature" in m for m in messages)
