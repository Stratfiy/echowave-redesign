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
