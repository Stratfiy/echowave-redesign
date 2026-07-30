import asyncio
import io
import json
from datetime import timedelta
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from loguru import logger
from minio import Minio
from minio.error import S3Error

from api.constants import MINIO_PUBLIC_BUCKET

from .base import AsyncReadable, BaseFileSystem


class MinioFileSystem(BaseFileSystem):
    """MinIO implementation of the filesystem interface.

    Access is by **presigned URL**. This bucket holds call recordings and
    transcripts — recordings of real conversations with real customers — and it
    used to be world-readable, world-writable and world-deletable, because a
    ``Principal: {"AWS": "*"}`` policy granting GetObject, PutObject *and*
    DeleteObject was applied on every initialisation. Anyone who could reach the
    endpoint could read any recording whose URL they guessed, and overwrite or
    destroy it. Presigned URLs carry their own expiring signature, so the bucket
    itself can stay private.

    ``MINIO_PUBLIC_BUCKET=true`` restores the old anonymous policy for a local
    development stack where signature juggling across two hostnames is a
    nuisance. It is off by default, and it should never be on anywhere a
    stranger can reach the endpoint.

    Two endpoints, two different purposes:

    - ``endpoint`` (host:port) + ``secure``: used by the SDK for
      container-to-container calls.
    - ``public_endpoint`` (full URL): the host a browser will fetch from.

    A presigned URL is signed **for a specific host**, so a URL signed against
    ``minio:9000`` fails when the browser fetches it from ``example.com``. Hence
    two clients: one bound to each, each signing for its own audience.
    """

    def __init__(
        self,
        endpoint: str = "localhost:9000",
        access_key: str = "minioadmin",
        secret_key: str = "minioadmin",
        bucket_name: str = "voice-audio",
        secure: bool = False,
        public_endpoint: Optional[str] = None,
    ):
        if not public_endpoint:
            raise ValueError(
                "MinioFileSystem requires public_endpoint (set MINIO_PUBLIC_ENDPOINT). "
                "Expected a full URL with scheme, e.g. 'http://localhost:9000' or 'https://example.com'."
            )
        if not (
            public_endpoint.startswith("http://")
            or public_endpoint.startswith("https://")
        ):
            raise ValueError(
                f"MINIO_PUBLIC_ENDPOINT must include a scheme (http:// or https://), got: {public_endpoint!r}"
            )

        self.bucket_name = bucket_name
        self.endpoint = endpoint
        self.public_endpoint = public_endpoint.rstrip("/")
        self.secure = secure
        self.access_key = access_key
        self.secret_key = secret_key

        # Client for internal operations (uploads, stat, copy) and for signing
        # URLs that the API itself will fetch.
        self.client = Minio(
            endpoint, access_key=access_key, secret_key=secret_key, secure=secure
        )

        # A second client bound to the public hostname, used only to sign URLs a
        # browser will fetch. Signing with the internal client would produce a
        # signature computed over "minio:9000" and the browser would be
        # requesting "example.com" — the host is part of what gets signed, so
        # every such URL would 403.
        parsed = urlparse(self.public_endpoint)
        public_host = parsed.netloc or parsed.path
        public_secure = parsed.scheme == "https"
        if public_host == endpoint and public_secure == secure:
            self.public_client = self.client
        else:
            self.public_client = Minio(
                public_host,
                access_key=access_key,
                secret_key=secret_key,
                secure=public_secure,
            )

        try:
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)

            if MINIO_PUBLIC_BUCKET:
                # Local development only. Anonymous PutObject and DeleteObject
                # mean a stranger can overwrite or destroy any recording, so
                # this is opt-in and loud rather than the default it used to be.
                logger.warning(
                    "MINIO_PUBLIC_BUCKET is on: bucket {!r} is anonymously "
                    "readable, writable and deletable. Never use this where the "
                    "endpoint is reachable from outside your machine.",
                    self.bucket_name,
                )
                policy = {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"AWS": "*"},
                            "Action": [
                                "s3:GetObject",
                                "s3:PutObject",
                                "s3:DeleteObject",
                            ],
                            "Resource": [f"arn:aws:s3:::{self.bucket_name}/*"],
                        },
                        {
                            "Effect": "Allow",
                            "Principal": {"AWS": "*"},
                            "Action": ["s3:ListBucket"],
                            "Resource": [f"arn:aws:s3:::{self.bucket_name}"],
                        },
                    ],
                }
                self.client.set_bucket_policy(self.bucket_name, json.dumps(policy))
        except Exception as e:
            # Bucket might already exist or we might be in a restricted environment
            logger.debug(f"Bucket setup note: {e}")
            pass

    async def acreate_file(self, file_path: str, content: AsyncReadable) -> bool:
        try:
            data = await content.read()

            def _put():
                # The MinIO SDK requires a stream with .read(), not raw bytes.
                self.client.put_object(
                    self.bucket_name,
                    file_path,
                    data=io.BytesIO(data),
                    length=len(data),
                )

            await asyncio.to_thread(_put)
            return True
        except S3Error:
            return False

    async def aupload_file(self, local_path: str, destination_path: str) -> bool:
        try:

            def _fput():
                self.client.fput_object(self.bucket_name, destination_path, local_path)

            await asyncio.to_thread(_fput)
            return True
        except S3Error:
            return False

    async def aget_signed_url(
        self,
        file_path: str,
        expiration: int = 3600,
        force_inline: bool = False,
        use_internal_endpoint: bool = False,
    ) -> Optional[str]:
        """A time-limited URL granting read access to one object.

        Genuinely signed, not a bare bucket URL that happens to work because the
        bucket is public. The signature is what authorises the read, so the
        object stays private to anyone without a current link.
        """
        try:
            client = self.client if use_internal_endpoint else self.public_client
            # Inline vs download is a property of the link, not the object, so
            # the same recording can be previewed in one place and downloaded in
            # another without storing it twice.
            response_headers = (
                {"response-content-disposition": "inline"} if force_inline else None
            )

            def _sign() -> str:
                return client.presigned_get_object(
                    self.bucket_name,
                    file_path,
                    expires=timedelta(seconds=expiration),
                    response_headers=response_headers,
                )

            return await asyncio.to_thread(_sign)
        except Exception as e:
            logger.error(f"Error generating MinIO signed URL: {e}")
            return None

    async def aget_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get MinIO object metadata."""
        try:

            def _stat():
                return self.client.stat_object(self.bucket_name, file_path)

            stat = await asyncio.to_thread(_stat)
            return {
                "size": stat.size,
                "created_at": stat.last_modified,
                "modified_at": stat.last_modified,
                "etag": stat.etag.strip('"') if stat.etag else None,
                "content_type": stat.content_type,
                "storage_class": None,  # MinIO doesn't have storage classes like S3
            }
        except S3Error:
            return None

    async def aget_presigned_put_url(
        self,
        file_path: str,
        expiration: int = 900,
        content_type: str = "text/csv",
        max_size: int = 10_485_760,
    ) -> Optional[str]:
        """A time-limited URL granting write access to one object key.

        Signed against the public host, which is what makes it work without the
        bucket accepting anonymous writes. The previous implementation returned
        a bare URL that only functioned because anyone at all could PUT to the
        bucket — including over the top of an existing recording.
        """
        try:

            def _sign() -> str:
                return self.public_client.presigned_put_object(
                    self.bucket_name,
                    file_path,
                    expires=timedelta(seconds=expiration),
                )

            return await asyncio.to_thread(_sign)
        except Exception as e:
            logger.error(f"Error generating MinIO upload URL: {e}")
            return None

    async def adownload_file(self, source_path: str, local_path: str) -> bool:
        """Download a file from MinIO to local path."""
        try:

            def _fget():
                self.client.fget_object(self.bucket_name, source_path, local_path)

            await asyncio.to_thread(_fget)
            return True
        except S3Error:
            return False

    async def acopy_file(self, source_path: str, destination_path: str) -> bool:
        """Copy a file within MinIO (server-side copy)."""
        try:
            from minio.commonconfig import CopySource

            def _copy():
                self.client.copy_object(
                    self.bucket_name,
                    destination_path,
                    CopySource(self.bucket_name, source_path),
                )

            await asyncio.to_thread(_copy)
            return True
        except S3Error:
            return False

    async def adelete_file(self, file_path: str) -> bool:
        """Delete one object. Absent counts as deleted."""
        try:

            def _remove():
                self.client.remove_object(self.bucket_name, file_path)

            await asyncio.to_thread(_remove)
            return True
        except S3Error as e:
            # Already gone is the outcome the caller wanted.
            if getattr(e, "code", "") in {"NoSuchKey", "NoSuchObject"}:
                return True
            logger.error("Could not delete {} from MinIO: {}", file_path, e)
            return False
