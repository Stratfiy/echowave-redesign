"""The browser's PUT to the presigned URL, against a real S3 server.

Every other knowledge base test stubs this hop, and says so. It is the one
step nothing in the suite crosses: the API mints a URL, something outside the
API writes bytes to it, and a worker later reads them back by key. All three
sides are ours and none of them were exercised together, so a mismatch — a key
built one way and read another, a signature the server rejects, a download
that lands empty — would have been found by a customer.

``moto`` runs an S3 implementation over real HTTP here, so the PUT below goes
over a socket and the bytes are read back through the same ``S3FileSystem``
the worker uses.

**What this cannot show.** moto does not verify signatures: it accepts any
request, including one whose URL was signed for a different key. So nothing
here proves the URL is *restricted* to what it was signed for — only AWS or
MinIO enforce that. The restriction tests below therefore assert on the URL we
generated rather than on a server's willingness to refuse, and say which is
which.
"""

from __future__ import annotations

import socket
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from api.services.filesystem import S3FileSystem
from api.services.knowledge_base import upload_keys

BUCKET = "decibyl-presign-test"
REGION = "ap-south-1"
ORG = 7
DOC = "6f1a0b32-6bcb-4a0e-9f6e-1b7a2c9d4e50"

POLICY = b"Refunds are issued within fourteen days of purchase.\n"


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def s3_server():
    """A real S3 endpoint, over HTTP, for the length of this module."""
    from moto.server import ThreadedMotoServer

    port = _free_port()
    server = ThreadedMotoServer(port=port, verbose=False)
    server.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.stop()


@pytest.fixture
async def storage(s3_server):
    """The same class the deployment uses, pointed at the test endpoint."""
    filesystem = S3FileSystem(
        bucket_name=BUCKET,
        region_name=REGION,
        endpoint_url=s3_server,
        signature_version="s3v4",
        addressing_style="path",
        access_key_id="testing",
        secret_access_key="testing",
    )

    async with filesystem.session.client(
        "s3", endpoint_url=s3_server, region_name=REGION
    ) as client:
        existing = await client.list_buckets()
        if BUCKET not in {b["Name"] for b in existing.get("Buckets", [])}:
            await client.create_bucket(
                Bucket=BUCKET,
                CreateBucketConfiguration={"LocationConstraint": REGION},
            )

    return filesystem


@pytest.mark.asyncio
class TestTheUploadHop:
    async def test_a_file_put_to_the_url_is_readable_at_the_key(
        self, storage, tmp_path
    ):
        """The hop, end to end: mint, PUT over HTTP, download by key.

        The download is the same call the ingestion worker makes, so this is
        the join between the two halves of the upload — not a check that S3
        works.
        """
        key = upload_keys.build_document_key(ORG, DOC, "refund-policy.txt")

        url = await storage.aget_presigned_put_url(
            file_path=key, expiration=900, content_type="text/plain"
        )
        assert url, "no upload URL was generated"

        async with httpx.AsyncClient() as browser:
            response = await browser.put(
                url, content=POLICY, headers={"Content-Type": "text/plain"}
            )
        assert response.status_code == 200, response.text

        destination = tmp_path / "downloaded.txt"
        assert await storage.adownload_file(key, str(destination))
        assert destination.read_bytes() == POLICY

    async def test_a_name_a_browser_can_produce_survives_the_round_trip(
        self, storage, tmp_path
    ):
        """Spaces and unicode come out of a file picker constantly, and a key
        that is built one way and requested another fails only at upload time,
        for the customer, on their first document."""
        key = upload_keys.build_document_key(ORG, DOC, "Q3 réport (final).pdf")

        url = await storage.aget_presigned_put_url(
            file_path=key, expiration=900, content_type="application/pdf"
        )
        async with httpx.AsyncClient() as browser:
            response = await browser.put(
                url, content=POLICY, headers={"Content-Type": "application/pdf"}
            )
        assert response.status_code == 200, response.text

        destination = tmp_path / "downloaded.pdf"
        assert await storage.adownload_file(key, str(destination))
        assert destination.read_bytes() == POLICY

    async def test_the_object_lands_under_the_organizations_prefix(self, storage):
        """Where the bytes end up, listed from the server rather than assumed
        from the key we passed in."""
        key = upload_keys.build_document_key(ORG, DOC, "placement.txt")

        url = await storage.aget_presigned_put_url(
            file_path=key, expiration=900, content_type="text/plain"
        )
        async with httpx.AsyncClient() as browser:
            await browser.put(
                url, content=POLICY, headers={"Content-Type": "text/plain"}
            )

        keys = await storage.alist_files(f"knowledge_base/{ORG}/")
        assert key in keys
        assert all(k.startswith(f"knowledge_base/{ORG}/") for k in keys)

    async def test_the_size_is_readable_before_the_object_is_downloaded(self, storage):
        """What the worker's pre-download size check depends on. If HEAD did
        not report a size, that check would silently never fire and a large
        upload would go back to filling the worker's disk."""
        key = upload_keys.build_document_key(ORG, DOC, "sized.txt")

        url = await storage.aget_presigned_put_url(
            file_path=key, expiration=900, content_type="text/plain"
        )
        async with httpx.AsyncClient() as browser:
            await browser.put(
                url, content=POLICY, headers={"Content-Type": "text/plain"}
            )

        metadata = await storage.aget_file_metadata(key)
        assert metadata is not None
        assert metadata["size"] == len(POLICY)

    async def test_downloading_a_key_that_was_never_uploaded_fails_cleanly(
        self, storage, tmp_path
    ):
        """The worker's first step when a browser PUT silently failed. It must
        report failure rather than raise, or every abandoned upload becomes a
        task retry loop."""
        missing = upload_keys.build_document_key(ORG, DOC, "never-uploaded.txt")

        assert (
            await storage.adownload_file(missing, str(tmp_path / "nothing.txt"))
            is False
        )


@pytest.mark.asyncio
class TestWhatTheUrlItselfGrants:
    """Properties of the URL we mint. Asserted against the URL because moto
    does not enforce signatures — see the module docstring."""

    async def test_the_url_names_exactly_the_key_it_was_signed_for(self, storage):
        key = upload_keys.build_document_key(ORG, DOC, "scoped.txt")

        url = await storage.aget_presigned_put_url(
            file_path=key, expiration=900, content_type="text/plain"
        )

        path = urlparse(url).path
        assert path.endswith(f"/{BUCKET}/{key}")

    async def test_the_content_type_is_part_of_the_signature(self, storage):
        """Signed headers are what stop an upload URL for a text file being
        used to store something else under a name that reads as harmless."""
        key = upload_keys.build_document_key(ORG, DOC, "typed.txt")

        url = await storage.aget_presigned_put_url(
            file_path=key, expiration=900, content_type="text/plain"
        )

        signed = parse_qs(urlparse(url).query)["X-Amz-SignedHeaders"][0]
        assert "content-type" in signed

    async def test_the_url_carries_the_expiry_it_was_asked_for(self, storage):
        key = upload_keys.build_document_key(ORG, DOC, "expiring.txt")

        url = await storage.aget_presigned_put_url(
            file_path=key, expiration=1800, content_type="text/plain"
        )

        assert parse_qs(urlparse(url).query)["X-Amz-Expires"] == ["1800"]

    async def test_max_size_is_not_in_the_url_and_cannot_be(self, storage):
        """Stated as a test so nobody re-reads the parameter as a control.

        A presigned PUT signs a URL, not a body. Only a browser POST policy
        carries ``content-length-range``, and this is a PUT — so the ceiling
        the upload endpoint claims is enforced by whoever reads the object,
        which is why the ingestion worker HEADs it before downloading.
        """
        key = upload_keys.build_document_key(ORG, DOC, "unbounded.txt")

        url = await storage.aget_presigned_put_url(
            file_path=key, expiration=900, content_type="text/plain", max_size=1
        )

        query = parse_qs(urlparse(url).query)
        assert not any("length" in name.lower() for name in query)

        # And the server takes far more than the stated ceiling.
        async with httpx.AsyncClient() as browser:
            response = await browser.put(
                url, content=b"x" * 4096, headers={"Content-Type": "text/plain"}
            )
        assert response.status_code == 200

        metadata = await storage.aget_file_metadata(key)
        assert metadata["size"] == 4096
