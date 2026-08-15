"""Reading a PDF that is pictures of words.

The PDFs an Indian SMB actually has to hand — a scanned policy, a signed
contract, a government circular — have no text layer, and until now they were
refused with a message asking the customer to go and OCR the file themselves.
That is correct advice and a bad experience, since the file they have is what
their office scanner produced.

The scans here are made rather than committed: text is drawn onto a bitmap and
the bitmap is embedded in a PDF, which is what a scanner does. That means the
tests run against real tesseract output rather than a fixture recorded once
from a machine nobody can check, and it means a change that quietly stops
calling tesseract fails them.

The two tests that matter most are the ones about *absence*: a deployment
without the binaries has to keep working, with the message it always gave.
Those run everywhere. The rest skip when OCR is not installed.
"""

from __future__ import annotations

import io

import pytest

from api.services.knowledge_base import extraction, ocr
from api.services.knowledge_base.errors import EmptyDocumentError

REFUND_LINES = [
    "REFUND POLICY",
    "",
    "Customers may request a refund within",
    "fourteen days of purchase.",
]

DELIVERY_LINES = [
    "DELIVERY TIMES",
    "",
    "Standard delivery inside India takes",
    "three to five working days.",
]


def _page_bitmap(lines: list[str]):
    """A page of text, as an image. What a scanner hands you."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("L", (1240, 1754), 255)
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 44)
    except OSError:  # pragma: no cover — depends on the image's fonts
        pytest.skip("no scalable font available to render a test scan")

    y = 180
    for line in lines:
        draw.text((120, y), line, fill=0, font=font)
        y += 78
    return image


def _scanned_pdf(path, pages: list[list[str]]) -> str:
    """A PDF whose every page is one image and no text at all."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    for lines in pages:
        pdf.drawImage(
            ImageReader(_page_bitmap(lines)), 0, 0, width=width, height=height
        )
        pdf.showPage()
    pdf.save()

    path.write_bytes(buffer.getvalue())
    return str(path)


needs_ocr = pytest.mark.skipif(
    not ocr.available(),
    reason="tesseract and poppler are not installed in this environment",
)


@needs_ocr
class TestReadingAScan:
    def test_a_scanned_pdf_is_read_instead_of_refused(self, tmp_path):
        """The whole point: this file used to raise EmptyDocumentError."""
        path = _scanned_pdf(tmp_path / "scan.pdf", [REFUND_LINES])

        document = extraction.extract_document(path, "scan.pdf", "application/pdf")

        text = " ".join(block.text for block in document.blocks).lower()
        assert "refund" in text
        assert "fourteen days" in text

    def test_the_provenance_says_the_text_was_recognised(self, tmp_path):
        """OCR is approximate. A quote the agent reads out on a call should be
        traceable to text a machine guessed at, not to a real text layer."""
        path = _scanned_pdf(tmp_path / "scan.pdf", [REFUND_LINES])

        document = extraction.extract_document(path, "scan.pdf", "application/pdf")

        assert document.metadata["extractor"] == "tesseract"
        assert document.metadata["ocr_pages"] == 1

    def test_each_page_keeps_its_number(self, tmp_path):
        """Page numbers survive into the chunk metadata, which is what lets a
        customer check a quoted line against the document."""
        path = _scanned_pdf(tmp_path / "scan.pdf", [REFUND_LINES, DELIVERY_LINES])

        document = extraction.extract_document(path, "scan.pdf", "application/pdf")

        assert [block.page_number for block in document.blocks] == [1, 2]
        assert "refund" in document.blocks[0].text.lower()
        assert "delivery" in document.blocks[1].text.lower()

    def test_a_blank_scan_is_still_refused(self, tmp_path):
        """Tesseract returns stray marks for an empty page — a speck read as a
        full stop. A document of those is not a document, and accepting it
        would recreate the failure this whole area exists to prevent: a file
        listed as ready that the agent cannot answer anything from."""
        path = _scanned_pdf(tmp_path / "blank.pdf", [[""]])

        with pytest.raises(EmptyDocumentError):
            extraction.extract_document(path, "blank.pdf", "application/pdf")

    def test_only_the_first_pages_of_a_long_scan_are_read(self, tmp_path, monkeypatch):
        """Seconds per page means a scanned book stops the queue for everyone
        else's documents. Verified by lowering the ceiling rather than by
        rendering fifty pages."""
        monkeypatch.setattr(ocr, "KNOWLEDGE_BASE_OCR_MAX_PAGES", 1)

        path = _scanned_pdf(tmp_path / "long.pdf", [REFUND_LINES, DELIVERY_LINES])

        assert len(ocr.pages_from_pdf(path)) == 1

    def test_a_pdf_with_real_text_is_not_sent_through_ocr(self, tmp_path):
        """OCR is slower and worse than a text layer wherever one exists, and
        this fallback must not quietly become the normal path."""
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        path = tmp_path / "typed.pdf"
        pdf = canvas.Canvas(str(path), pagesize=A4)
        pdf.drawString(
            80,
            700,
            "Customers may request a refund within fourteen days of purchase.",
        )
        pdf.save()

        document = extraction.extract_document(
            str(path), "typed.pdf", "application/pdf"
        )

        assert document.metadata["extractor"] != "tesseract"
        assert "ocr_pages" not in document.metadata


class TestADeploymentWithoutTheBinaries:
    """These run everywhere, because they are about what happens on an image
    that was built before this change."""

    @pytest.fixture
    def no_binaries(self, monkeypatch):
        monkeypatch.setattr(ocr.shutil, "which", lambda name: None)

    def test_ocr_reports_itself_unavailable(self, no_binaries):
        assert ocr.available() is False

    def test_no_attempt_is_made_to_read_a_file(self, no_binaries, tmp_path):
        """Not merely "returns nothing" — it must not reach for a binary that
        is not there and raise from inside the fallback."""
        path = tmp_path / "whatever.pdf"
        path.write_bytes(b"%PDF-1.4 not really a pdf")

        assert ocr.pages_from_pdf(str(path)) == []

    def test_a_scan_gets_the_message_it_always_got(self, no_binaries, tmp_path):
        """The graceful degradation, in the customer's words. A PDF with no
        text layer and no OCR available is refused with the sentence naming
        what to do about it — not with a stack trace, and not silently."""
        path = tmp_path / "scan.pdf"
        # A one-page PDF with no text and no image: pypdf reads it fine and
        # finds nothing, which is the shape of a scan as far as this code is
        # concerned.
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        pdf = canvas.Canvas(str(path), pagesize=A4)
        pdf.showPage()
        pdf.save()

        with pytest.raises(EmptyDocumentError) as raised:
            extraction.extract_document(str(path), "scan.pdf", "application/pdf")

        assert "OCR" in raised.value.user_message
        assert "scan" in raised.value.user_message.lower()


class TestTheSwitch:
    def test_ocr_can_be_turned_off(self, monkeypatch):
        """A deployment that would rather refuse scans than spend worker time
        on them says so once, and nothing else changes."""
        monkeypatch.setattr(ocr, "KNOWLEDGE_BASE_OCR_ENABLED", False)

        assert ocr.available() is False
