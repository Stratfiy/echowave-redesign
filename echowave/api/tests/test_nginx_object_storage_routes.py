"""Both nginx templates must route the bare bucket path to MinIO.

A MinIO/S3 client with no configured region resolves the bucket's region before
it will sign anything, with ``GET /voice-audio?location=``. Without an exact
match for that path nginx 301s it to add a trailing slash; the client follows
the redirect, so the path it signed over changes, and because SigV4 signs the
path MinIO answers SignatureDoesNotMatch.

That was fixed once, in ``nginx.remote.conf.template`` alone. The subdomain
template never got it, so every split-hostname install kept failing with
"Failed to generate signed URL: S3Error from MinioFileSystem" on every
recording and transcript while the fix sat in the repo looking applied.

The API now sets MINIO_REGION and makes no such request, so this is belt to
that braces — and a guard against the two templates disagreeing again about a
path that took an outage to get right.
"""

from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "deploy" / "templates"

NGINX_TEMPLATES = [
    "nginx.remote.conf.template",
    "nginx.subdomains.conf.template",
]


@pytest.mark.parametrize("name", NGINX_TEMPLATES)
def test_the_bare_bucket_path_is_matched_exactly(name):
    """`location = /voice-audio`, not just the trailing-slash prefix."""
    body = (TEMPLATES / name).read_text()

    assert "location = /voice-audio {" in body, (
        f"{name} has no exact match for the bare bucket path. "
        "GetBucketLocation will be redirected and every presigned URL "
        "will be rejected with SignatureDoesNotMatch."
    )


@pytest.mark.parametrize("name", NGINX_TEMPLATES)
def test_the_bare_bucket_path_reaches_minio(name):
    """Matching it is not enough; it has to be proxied to MinIO."""
    body = (TEMPLATES / name).read_text()

    block = body.split("location = /voice-audio {", 1)[1].split("}", 1)[0]
    assert "proxy_pass http://minio:9000/voice-audio;" in block, (
        f"{name} matches the bare bucket path but does not proxy it to MinIO."
    )


@pytest.mark.parametrize("name", NGINX_TEMPLATES)
def test_objects_themselves_are_still_served(name):
    """The prefix location is what actually serves recordings to the browser."""
    body = (TEMPLATES / name).read_text()

    assert "location /voice-audio/ {" in body
    assert "proxy_pass http://minio:9000/voice-audio/;" in body


def test_the_updater_ships_every_nginx_template():
    """A template no updater downloads can never be corrected in place.

    The subdomain template was absent from this list, which is why a host
    running it kept its original config through every update.
    """
    setup_common = (
        Path(__file__).resolve().parents[2] / "scripts" / "lib" / "setup_common.sh"
    ).read_text()

    for name in NGINX_TEMPLATES:
        assert f"deploy/templates/{name}" in setup_common, (
            f"{name} is never downloaded, so fixes to it cannot reach a server."
        )
