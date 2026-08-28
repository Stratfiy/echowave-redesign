"""Object storage access is by signature, not by publishing the bucket.

This bucket holds recordings and transcripts of real conversations. It used to
carry a ``Principal: {"AWS": "*"}`` policy granting GetObject, PutObject *and*
DeleteObject, applied on every initialisation — so anyone who could reach the
endpoint could read any recording whose URL they guessed, and overwrite or
destroy it.

These tests assert the two things that keep it shut: no policy is applied unless
someone deliberately asks for one, and the URLs handed out carry a signature
rather than being bare bucket paths that only work because the bucket is open.
"""

from unittest.mock import MagicMock, patch

import pytest

from api.services.filesystem import minio as minio_module
from api.services.filesystem.minio import MinioFileSystem


@pytest.fixture
def minio_clients():
    """Stand in for both SDK clients without touching a real server."""
    with patch.object(minio_module, "Minio") as factory:
        clients = []

        def _make(*args, **kwargs):
            client = MagicMock()
            client.bucket_exists.return_value = True
            client.presigned_get_object.return_value = (
                "https://storage.example.com/voice-audio/rec.wav?X-Amz-Signature=abc"
            )
            client.presigned_put_object.return_value = (
                "https://storage.example.com/voice-audio/rec.wav?X-Amz-Signature=def"
            )
            client._init_args = (args, kwargs)
            clients.append(client)
            return client

        factory.side_effect = _make
        yield factory, clients


def _fs(**kwargs) -> MinioFileSystem:
    defaults = {
        "endpoint": "minio:9000",
        "access_key": "key",
        "secret_key": "secret",
        "bucket_name": "voice-audio",
        "secure": False,
        "public_endpoint": "https://storage.example.com",
    }
    defaults.update(kwargs)
    return MinioFileSystem(**defaults)


class TestTheBucketIsNotPublished:
    def test_no_bucket_policy_is_applied_by_default(self, minio_clients, monkeypatch):
        """The default must be a private bucket.

        A default that publishes recordings is one every deployment inherits
        without ever choosing it.
        """
        monkeypatch.setattr(minio_module, "MINIO_PUBLIC_BUCKET", False)
        _factory, clients = minio_clients

        _fs()

        for client in clients:
            client.set_bucket_policy.assert_not_called()

    def test_the_anonymous_policy_is_opt_in(self, minio_clients, monkeypatch):
        """Still available for a local stack, but only when asked for."""
        monkeypatch.setattr(minio_module, "MINIO_PUBLIC_BUCKET", True)
        _factory, clients = minio_clients

        _fs()

        applied = [c for c in clients if c.set_bucket_policy.called]
        assert len(applied) == 1
        policy = applied[0].set_bucket_policy.call_args[0][1]
        assert "s3:GetObject" in policy

    def test_the_bucket_is_still_created(self, monkeypatch):
        """Turning off the policy must not turn off provisioning."""
        monkeypatch.setattr(minio_module, "MINIO_PUBLIC_BUCKET", False)

        with patch.object(minio_module, "Minio") as factory:
            client = MagicMock()
            client.bucket_exists.return_value = False
            factory.return_value = client
            _fs()

        client.make_bucket.assert_called_once_with("voice-audio")
        client.set_bucket_policy.assert_not_called()


@pytest.mark.asyncio
class TestUrlsAreSigned:
    async def test_a_read_url_carries_a_signature(self, minio_clients, monkeypatch):
        """The signature is what authorises the read.

        A bare bucket path would only work while the bucket is open to the
        world, which is the thing being fixed.
        """
        monkeypatch.setattr(minio_module, "MINIO_PUBLIC_BUCKET", False)
        fs = _fs()

        url = await fs.aget_signed_url("rec.wav")

        assert url is not None
        assert "X-Amz-Signature" in url
        fs.public_client.presigned_get_object.assert_called_once()

    async def test_an_upload_url_carries_a_signature(self, minio_clients, monkeypatch):
        """Anonymous PutObject let a stranger overwrite an existing recording."""
        monkeypatch.setattr(minio_module, "MINIO_PUBLIC_BUCKET", False)
        fs = _fs()

        url = await fs.aget_presigned_put_url("rec.wav")

        assert url is not None
        assert "X-Amz-Signature" in url
        fs.public_client.presigned_put_object.assert_called_once()

    async def test_read_urls_expire(self, minio_clients, monkeypatch):
        from datetime import timedelta

        monkeypatch.setattr(minio_module, "MINIO_PUBLIC_BUCKET", False)
        fs = _fs()

        await fs.aget_signed_url("rec.wav", expiration=120)

        kwargs = fs.public_client.presigned_get_object.call_args.kwargs
        assert kwargs["expires"] == timedelta(seconds=120)

    async def test_inline_display_is_a_property_of_the_link(
        self, minio_clients, monkeypatch
    ):
        """So one recording can be previewed in one place and downloaded in
        another without storing it twice."""
        monkeypatch.setattr(minio_module, "MINIO_PUBLIC_BUCKET", False)
        fs = _fs()

        await fs.aget_signed_url("rec.wav", force_inline=True)

        kwargs = fs.public_client.presigned_get_object.call_args.kwargs
        assert kwargs["response_headers"] == {"response-content-disposition": "inline"}


class TestSigningHost:
    def test_two_clients_when_the_hosts_differ(self, minio_clients, monkeypatch):
        """A URL signed against minio:9000 fails when a browser fetches it from
        example.com — the host is part of what gets signed."""
        monkeypatch.setattr(minio_module, "MINIO_PUBLIC_BUCKET", False)
        fs = _fs(endpoint="minio:9000", public_endpoint="https://storage.example.com")

        assert fs.public_client is not fs.client

    def test_one_client_when_the_host_is_the_same(self, minio_clients, monkeypatch):
        """No point signing twice against the same audience."""
        monkeypatch.setattr(minio_module, "MINIO_PUBLIC_BUCKET", False)
        fs = _fs(
            endpoint="localhost:9000",
            secure=False,
            public_endpoint="http://localhost:9000",
        )

        assert fs.public_client is fs.client


class TestSigningNeedsNoRoundTrip:
    """Both clients must be given a region.

    Without one the MinIO SDK will not sign locally. It resolves the bucket's
    region first, over the network, with ``GET <endpoint>/<bucket>?location=``.
    For the public client that request goes to the browser-facing host on the
    bare bucket path — and on a split-hostname install nginx answers it with a
    301 adding a trailing slash. The SDK follows the redirect, the path it
    signed over changes, and SigV4 signs the path, so MinIO rejects it with
    SignatureDoesNotMatch. Every recording and transcript then failed with
    "Failed to generate signed URL: S3Error from MinioFileSystem" — a message
    that reads like bad credentials and is not.
    """

    def test_the_internal_client_is_given_a_region(self, minio_clients, monkeypatch):
        monkeypatch.setattr(minio_module, "MINIO_PUBLIC_BUCKET", False)
        _factory, clients = minio_clients

        _fs(region="ap-south-1")

        _args, kwargs = clients[0]._init_args
        assert kwargs["region"] == "ap-south-1"

    def test_the_public_client_is_given_a_region(self, minio_clients, monkeypatch):
        """The one that actually signs the URLs the browser fetches."""
        monkeypatch.setattr(minio_module, "MINIO_PUBLIC_BUCKET", False)
        _factory, clients = minio_clients

        fs = _fs(region="ap-south-1")

        # Two distinct hosts, so two distinct clients.
        assert fs.public_client is not fs.client
        _args, kwargs = clients[1]._init_args
        assert kwargs["region"] == "ap-south-1"

    def test_a_region_is_set_even_when_nobody_configured_one(
        self, minio_clients, monkeypatch
    ):
        """A default of "no region" is the bug. Any region avoids the lookup."""
        monkeypatch.setattr(minio_module, "MINIO_PUBLIC_BUCKET", False)
        _factory, clients = minio_clients

        _fs()

        for client in clients:
            _args, kwargs = client._init_args
            assert kwargs.get("region")
