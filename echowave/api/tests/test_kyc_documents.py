"""KYC document storage rules.

These are identity and incorporation documents. The tests that matter here are
the ones about what we refuse to store and where we put it, not the happy path.
"""

import pytest

from api.services.kyc.documents import (
    ALLOWED_CONTENT_TYPES,
    MAX_DOCUMENT_BYTES,
    KycDocumentError,
    storage_key,
    validate_upload,
)


class TestUploadValidation:
    @pytest.mark.parametrize("content_type", sorted(ALLOWED_CONTENT_TYPES))
    def test_accepts_what_a_carrier_will_take(self, content_type):
        validate_upload(content_type=content_type, size_bytes=1024)

    def test_accepts_a_content_type_carrying_a_charset(self):
        validate_upload(content_type="application/pdf; charset=binary", size_bytes=10)

    @pytest.mark.parametrize(
        "content_type",
        [
            # The dangerous ones: both render in a browser and can carry script,
            # so a reviewer opening the document would be the victim.
            "text/html",
            "image/svg+xml",
            "application/javascript",
            "application/x-msdownload",
            "application/zip",
            None,
            "",
        ],
    )
    def test_rejects_anything_that_could_carry_script_or_is_unknown(self, content_type):
        with pytest.raises(KycDocumentError):
            validate_upload(content_type=content_type, size_bytes=1024)

    def test_rejects_an_empty_file(self):
        with pytest.raises(KycDocumentError, match="empty"):
            validate_upload(content_type="application/pdf", size_bytes=0)

    def test_rejects_an_oversized_file(self):
        with pytest.raises(KycDocumentError, match="larger than"):
            validate_upload(
                content_type="application/pdf", size_bytes=MAX_DOCUMENT_BYTES + 1
            )

    def test_accepts_a_file_exactly_at_the_limit(self):
        validate_upload(content_type="application/pdf", size_bytes=MAX_DOCUMENT_BYTES)


class TestStorageKey:
    def test_is_prefixed_by_organization(self):
        """So a deletion request for one account is a prefix operation, and a
        misconfigured policy still segregates customers."""
        key = storage_key(
            organization_id=42, kind="gst_certificate", filename="gst.pdf"
        )
        assert key.startswith("kyc/42/gst_certificate/")

    def test_keeps_the_extension_but_not_the_name(self):
        key = storage_key(
            organization_id=1, kind="other", filename="MyCompany Certificate.PDF"
        )
        assert key.endswith(".pdf")
        assert "MyCompany" not in key
        assert " " not in key

    @pytest.mark.parametrize(
        "filename",
        [
            "../../../etc/passwd",
            "../../secrets.pdf",
            "a/b/c.pdf",
            "..\\..\\windows\\system32",
            "no-extension",
            "",
        ],
    )
    def test_a_hostile_filename_cannot_escape_the_prefix(self, filename):
        """The filename is untrusted input and never reaches the key intact."""
        key = storage_key(organization_id=7, kind="other", filename=filename)
        assert key.startswith("kyc/7/other/")
        assert ".." not in key
        # Exactly the three separators of the prefix, and none from the name.
        assert key.count("/") == 3

    def test_two_uploads_of_the_same_file_do_not_collide(self):
        a = storage_key(organization_id=1, kind="other", filename="x.pdf")
        b = storage_key(organization_id=1, kind="other", filename="x.pdf")
        assert a != b

    def test_a_suspicious_extension_is_dropped_rather_than_kept(self):
        key = storage_key(organization_id=1, kind="other", filename="doc.p<df")
        assert "<" not in key
