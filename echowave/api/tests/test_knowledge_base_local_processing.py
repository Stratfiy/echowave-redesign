"""Ingestion works on a deployment with nothing else stood up.

That sentence is the whole point of these tests. Conversion and chunking were
the one stage of the knowledge base path that left the process, delegated to a
service with no local fallback, so a deployment that could not reach that host
had an advertised feature that returned a transport error. Everything here runs
with no network at all.

The second thing under test is the failure that looks like a success: a scanned
PDF, an empty file, a document that chunks to nothing. Those used to land as
``completed`` with zero chunks — the screen said ready, the agent knew nothing.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from api.services.knowledge_base import (
    EmptyDocumentError,
    UnsupportedDocumentTypeError,
    chunking,
    errors,
    extract_document,
    process_document_locally,
    resolve_backend,
)
from api.services.knowledge_base.chunking import chunk_blocks, count_tokens
from api.services.knowledge_base.extraction import TextBlock

# ---------------------------------------------------------------------------
# Fixtures that build real files, not mocks. A chunker tested only against
# strings is a chunker untested against the formats customers actually upload.
# ---------------------------------------------------------------------------

POLICY_TEXT = """Refund policy

Customers may request a refund within fourteen days of purchase. Refunds are
issued to the original payment method and take five to seven working days to
appear.

International orders

Orders shipped outside India carry a thirty day return window instead of
fourteen. Return shipping is paid by the customer unless the item arrived
damaged.
"""


@pytest.fixture
def text_file(tmp_path):
    path = tmp_path / "refunds.txt"
    path.write_text(POLICY_TEXT, encoding="utf-8")
    return str(path)


@pytest.fixture
def markdown_file(tmp_path):
    path = tmp_path / "handbook.md"
    path.write_text(
        "# Support handbook\n\n"
        "## Refunds\n\n"
        "Refunds are issued within fourteen days of purchase.\n\n"
        "## Escalation\n\n"
        "Escalate to a supervisor when the caller asks twice.\n",
        encoding="utf-8",
    )
    return str(path)


def _minimal_pdf(path, text: str | None) -> str:
    """A real PDF, with or without a text layer.

    Written by hand rather than with a library because the case under test is
    precisely a PDF that has *no* text — the scan — and no PDF writer wants to
    produce one.
    """
    if text is None:
        content = b"q Q"  # a page that draws nothing
    else:
        escaped = text.replace("(", r"\(").replace(")", r"\)")
        content = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length "
        + str(len(content)).encode()
        + b" >>\nstream\n"
        + content
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()

    path.write_bytes(bytes(out))
    return str(path)


@pytest.fixture
def pdf_with_text(tmp_path):
    return _minimal_pdf(
        tmp_path / "policy.pdf",
        "Refunds are issued within fourteen days of purchase.",
    )


@pytest.fixture
def scanned_pdf(tmp_path):
    """A page with no text layer. What a scanner produces."""
    return _minimal_pdf(tmp_path / "scan.pdf", None)


@pytest.fixture
def docx_file(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("Refund policy", level=1)
    document.add_paragraph(
        "Customers may request a refund within fourteen days of purchase."
    )
    document.add_heading("International orders", level=1)
    document.add_paragraph("Orders outside India carry a thirty day window.")
    path = tmp_path / "policy.docx"
    document.save(str(path))
    return str(path)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class TestExtraction:
    def test_plain_text_keeps_its_paragraphs(self, text_file):
        extracted = extract_document(text_file, "refunds.txt")
        assert any("fourteen days" in block.text for block in extracted.blocks)
        assert extracted.metadata["backend"] == "local"

    def test_markdown_headings_become_a_path(self, markdown_file):
        extracted = extract_document(markdown_file, "handbook.md")
        refund_block = next(
            b
            for b in extracted.blocks
            if "fourteen days" in b.text and not b.is_heading
        )
        assert refund_block.heading_path == ("Support handbook", "Refunds")

    def test_pdf_text_and_page_numbers_survive(self, pdf_with_text):
        extracted = extract_document(pdf_with_text, "policy.pdf")
        assert "fourteen days" in extracted.full_text
        assert extracted.metadata["page_count"] == 1
        assert extracted.metadata["has_text_layer"] is True
        assert extracted.blocks[0].page_number == 1

    def test_docx_headings_and_body(self, docx_file):
        extracted = extract_document(docx_file, "policy.docx")
        body = next(b for b in extracted.blocks if "fourteen days" in b.text)
        assert body.heading_path == ("Refund policy",)

    def test_json_is_flattened_to_readable_lines(self, tmp_path):
        path = tmp_path / "faq.json"
        path.write_text(
            json.dumps({"refunds": {"window_days": 14, "method": "original card"}}),
            encoding="utf-8",
        )
        extracted = extract_document(str(path), "faq.json")
        assert "refunds.window_days: 14" in extracted.full_text

    def test_csv_rows_carry_their_header(self, tmp_path):
        path = tmp_path / "plans.csv"
        path.write_text("plan,refund_days\nBasic,14\nPro,30\n", encoding="utf-8")
        extracted = extract_document(str(path), "plans.csv")
        assert "plan: Basic; refund_days: 14" in extracted.full_text

    def test_html_tags_are_stripped_and_scripts_dropped(self, tmp_path):
        path = tmp_path / "page.html"
        path.write_text(
            "<html><head><style>p{color:red}</style></head><body>"
            "<h1>Refunds</h1><p>Fourteen days.</p>"
            "<script>alert('x')</script></body></html>",
            encoding="utf-8",
        )
        extracted = extract_document(str(path), "page.html")
        assert "Fourteen days." in extracted.full_text
        assert "alert" not in extracted.full_text
        assert "color:red" not in extracted.full_text

    def test_a_scanned_pdf_is_a_named_failure_not_an_empty_success(self, scanned_pdf):
        with pytest.raises(EmptyDocumentError) as caught:
            extract_document(scanned_pdf, "scan.pdf")
        # The customer is told what to do, in words that match what they can
        # see on the page.
        assert "OCR" in caught.value.user_message

    def test_an_empty_file_is_refused(self, tmp_path):
        path = tmp_path / "empty.txt"
        path.write_text("", encoding="utf-8")
        with pytest.raises(EmptyDocumentError):
            extract_document(str(path), "empty.txt")

    def test_legacy_doc_gets_a_remedy_rather_than_a_shrug(self, tmp_path):
        path = tmp_path / "old.doc"
        path.write_bytes(b"\xd0\xcf\x11\xe0")
        with pytest.raises(UnsupportedDocumentTypeError) as caught:
            extract_document(str(path), "old.doc")
        assert ".docx" in caught.value.user_message

    def test_an_unreadable_format_names_what_is_supported(self, tmp_path):
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        with pytest.raises(UnsupportedDocumentTypeError) as caught:
            extract_document(str(path), "clip.mp4")
        assert ".pdf" in caught.value.user_message

    def test_a_corrupt_docx_says_so(self, tmp_path):
        path = tmp_path / "broken.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("not-a-word-file.txt", "hello")
        with pytest.raises(errors.DocumentExtractionError):
            extract_document(str(path), "broken.docx")

    def test_a_windows_encoded_file_is_read_rather_than_rejected(self, tmp_path):
        """cp1252 smart quotes are not a reason to refuse a policy document."""
        path = tmp_path / "smart.txt"
        # 0x92 is the cp1252 right single quote — invalid UTF-8, and the most
        # common byte in a .txt saved out of Word on Windows.
        path.write_bytes(b"The customer\x92s refund window is 14 days.")
        extracted = extract_document(str(path), "smart.txt")
        assert "refund window" in extracted.full_text


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


class TestChunking:
    def test_no_chunk_exceeds_the_budget(self):
        blocks = [TextBlock(text=" ".join(f"word{i}" for i in range(2000)))]
        chunks = chunk_blocks(blocks, max_tokens=64)
        assert chunks
        assert all(count_tokens(c.contextualized_text) <= 64 * 1.2 for c in chunks)

    def test_chunks_do_not_cut_mid_sentence(self):
        sentences = [
            f"Sentence number {i} explains a policy detail." for i in range(40)
        ]
        chunks = chunk_blocks([TextBlock(text=" ".join(sentences))], max_tokens=48)
        for chunk in chunks:
            assert chunk.text.endswith(".")

    def test_the_heading_is_embedded_but_not_spoken(self, markdown_file):
        extracted = extract_document(markdown_file, "handbook.md")
        chunks = chunk_blocks(extracted.blocks, max_tokens=128, source_name="handbook")
        refunds = next(c for c in chunks if "fourteen days" in c.text)
        assert "Refunds" in refunds.contextualized_text
        # What a caller would hear back has no navigation furniture in it.
        assert "Support handbook >" not in refunds.text

    def test_content_under_different_headings_is_not_merged(self, markdown_file):
        extracted = extract_document(markdown_file, "handbook.md")
        chunks = chunk_blocks(extracted.blocks, max_tokens=512)
        for chunk in chunks:
            assert not ("fourteen days" in chunk.text and "supervisor" in chunk.text)

    def test_indices_are_contiguous_from_zero(self):
        blocks = [TextBlock(text=f"Paragraph {i}. " * 12) for i in range(10)]
        chunks = chunk_blocks(blocks, max_tokens=40)
        assert [c.index for c in chunks] == list(range(len(chunks)))

    def test_chunking_is_deterministic(self, text_file):
        extracted = extract_document(text_file, "refunds.txt")
        first = chunk_blocks(extracted.blocks, max_tokens=48)
        second = chunk_blocks(extracted.blocks, max_tokens=48)
        assert [c.text for c in first] == [c.text for c in second]

    def test_overlap_carries_the_tail_of_the_previous_chunk(self):
        sentences = [
            f"Clause {i} sets out an obligation of the parties." for i in range(30)
        ]
        chunks = chunk_blocks(
            [TextBlock(text=" ".join(sentences))], max_tokens=48, overlap_tokens=12
        )
        assert len(chunks) > 1
        assert any(c.metadata["overlap_prefix_tokens"] > 0 for c in chunks[1:])

    def test_a_sentence_longer_than_the_budget_is_split_not_dropped(self):
        monster = " ".join(f"token{i}" for i in range(500)) + "."
        chunks = chunk_blocks([TextBlock(text=monster)], max_tokens=32)
        assert len(chunks) > 1
        assert "token499" in " ".join(c.text for c in chunks)

    def test_page_numbers_reach_the_chunk_metadata(self, pdf_with_text):
        extracted = extract_document(pdf_with_text, "policy.pdf")
        chunks = chunk_blocks(extracted.blocks, max_tokens=128)
        assert chunks[0].metadata["pages"] == [1]

    def test_a_headings_only_document_still_produces_something(self):
        blocks = [
            TextBlock(text="Refunds", is_heading=True),
            TextBlock(text="Escalation", is_heading=True),
        ]
        assert chunk_blocks(blocks, max_tokens=64)

    def test_token_counting_never_reports_zero_for_real_text(self):
        assert count_tokens("a") >= 1
        assert count_tokens("") == 0

    def test_abbreviations_do_not_end_a_sentence(self):
        parts = chunking.split_sentences("Pay Rs. 500 to Mr. Rao. Then call us.")
        assert len(parts) == 2


# ---------------------------------------------------------------------------
# The backend seam, and the shape the ingestion task consumes
# ---------------------------------------------------------------------------


class TestProcessing:
    def test_local_processing_returns_the_shape_the_task_expects(self, text_file):
        result = process_document_locally(file_path=text_file, filename="refunds.txt")
        assert result["mode"] == "chunked"
        assert result["chunks"]
        for chunk in result["chunks"]:
            assert set(chunk) >= {
                "chunk_text",
                "contextualized_text",
                "chunk_index",
                "chunk_metadata",
                "token_count",
            }

    def test_full_document_mode_returns_text_and_no_chunks(self, text_file):
        result = process_document_locally(
            file_path=text_file, filename="refunds.txt", retrieval_mode="full_document"
        )
        assert result["mode"] == "full_document"
        assert "fourteen days" in result["full_text"]
        assert result["chunks"] == []

    def test_a_scanned_pdf_never_reports_success(self, scanned_pdf):
        with pytest.raises(EmptyDocumentError):
            process_document_locally(file_path=scanned_pdf, filename="scan.pdf")

    def test_backend_defaults_to_local(self, monkeypatch):
        monkeypatch.setattr(
            "api.services.knowledge_base.processor.KB_DOCUMENT_PROCESSOR", "local"
        )
        assert resolve_backend() == "local"

    def test_auto_falls_back_to_local_without_an_mps_url(self, monkeypatch):
        monkeypatch.setattr("api.services.knowledge_base.processor.MPS_API_URL", "")
        assert resolve_backend("auto") == "local"

    def test_auto_prefers_mps_when_one_is_configured(self, monkeypatch):
        monkeypatch.setattr(
            "api.services.knowledge_base.processor.MPS_API_URL", "https://mps.internal"
        )
        assert resolve_backend("auto") == "mps"

    def test_a_nonsense_setting_falls_back_rather_than_failing_ingestion(self):
        assert resolve_backend("carrier-pigeon") == "local"

    async def test_auto_falls_back_to_local_when_mps_is_unreachable(
        self, text_file, monkeypatch
    ):
        """The outage case. A customer uploading during an MPS incident gets a
        working knowledge base, not a transport error on their files screen."""
        import httpx

        from api.services.knowledge_base import processor

        async def exploding_process_document(**kwargs):
            raise httpx.ConnectError("Name or service not known")

        monkeypatch.setattr(
            "api.services.mps_service_key_client.mps_service_key_client.process_document",
            exploding_process_document,
        )
        monkeypatch.setattr(processor, "MPS_API_URL", "https://mps.invalid")

        result = await processor.process_document(
            file_path=text_file, filename="refunds.txt", backend="auto"
        )
        assert result["chunks"]
        assert result["docling_metadata"]["backend"] == "local"

    async def test_a_pinned_mps_backend_surfaces_its_outage(
        self, text_file, monkeypatch
    ):
        """Pinning to MPS is a choice. Silently working around it would hide an
        outage from the person who made that choice."""
        import httpx

        from api.services.knowledge_base import processor

        async def exploding_process_document(**kwargs):
            raise httpx.ConnectError("Name or service not known")

        monkeypatch.setattr(
            "api.services.mps_service_key_client.mps_service_key_client.process_document",
            exploding_process_document,
        )

        with pytest.raises(httpx.ConnectError):
            await processor.process_document(
                file_path=text_file, filename="refunds.txt", backend="mps"
            )

    async def test_mps_returning_zero_chunks_is_a_failure_too(
        self, text_file, monkeypatch
    ):
        from api.services.knowledge_base import processor

        async def empty_process_document(**kwargs):
            return {"mode": "chunked", "chunks": [], "docling_metadata": {}}

        monkeypatch.setattr(
            "api.services.mps_service_key_client.mps_service_key_client.process_document",
            empty_process_document,
        )

        with pytest.raises(EmptyDocumentError):
            await processor.process_document(
                file_path=text_file, filename="refunds.txt", backend="mps"
            )


# ---------------------------------------------------------------------------
# What the customer is shown
# ---------------------------------------------------------------------------


class TestCustomerFacingErrors:
    def test_a_transport_error_never_reaches_the_customer_verbatim(self):
        import httpx

        message = errors.user_facing_message(
            httpx.ConnectError("[Errno -2] Name or service not known")
        )
        assert "Errno" not in message
        assert "services.decibyl.ai" not in message

    def test_our_own_errors_keep_their_remedy(self):
        exc = EmptyDocumentError("internal detail", user_message="Run it through OCR.")
        assert errors.user_facing_message(exc) == "Run it through OCR."

    def test_an_unexpected_bug_is_owned_rather_than_blamed_on_the_file(self):
        message = errors.user_facing_message(RuntimeError("list index out of range"))
        assert "index" not in message
        assert "our side" in message
