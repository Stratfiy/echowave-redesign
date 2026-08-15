"""Reading a PDF that is pictures of words.

A scan has no text layer. pypdf reads it and gets nothing, and until now the
answer was a message asking the customer to go and OCR it themselves — which
is a correct instruction and a bad experience, because the file they have is
the file their office scanner produced, and "run it through OCR" is not a step
most people have a tool for. Scanned policies, signed contracts and government
circulars are a large share of what an Indian SMB has on hand to give an agent.

So this tries. It is a **fallback**, reached only when the ordinary readers
found no text, because OCR is slow (seconds per page against milliseconds),
approximate, and worse than a real text layer whenever one exists.

Two binaries have to be present — tesseract to recognise, poppler to turn PDF
pages into images — and neither is imported at module load or assumed at call
time. On an image without them the customer gets exactly the message they got
before, which is why this is safe to ship on a deployment that has not rebuilt.
"""

from __future__ import annotations

import os
import shutil
import tempfile

from loguru import logger

from api.constants import (
    KNOWLEDGE_BASE_OCR_DPI,
    KNOWLEDGE_BASE_OCR_ENABLED,
    KNOWLEDGE_BASE_OCR_LANGUAGES,
    KNOWLEDGE_BASE_OCR_MAX_PAGES,
)

#: Characters on a page below which OCR is treated as having found nothing.
#: Tesseract returns stray marks — a speck read as a full stop — for a blank
#: scan, and a document of those is not a document.
MIN_CHARS_PER_PAGE = 12


def _binaries_present() -> bool:
    """Whether both halves of the toolchain are installed.

    Checked by looking for the executables rather than by a setting: a setting
    claiming OCR is available on an image that lacks tesseract fails at the
    worst moment, mid-ingestion, on a customer's file.
    """
    return bool(shutil.which("tesseract")) and bool(shutil.which("pdftoppm"))


def available() -> bool:
    """Whether OCR can be attempted on this deployment at all."""
    if not KNOWLEDGE_BASE_OCR_ENABLED:
        return False
    if not _binaries_present():
        return False
    try:
        import pdf2image  # noqa: F401
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return True


def pages_from_pdf(file_path: str) -> list[str]:
    """Text recognised from each page of a scanned PDF.

    Returns one string per page that produced something, in page order, and an
    empty list when OCR is unavailable, fails, or recognises nothing worth
    keeping. It never raises: this runs after the ordinary readers have already
    failed, and an exception here would replace a clear "this PDF is a scan"
    with a stack trace.

    Pages are converted and recognised one at a time. A hundred-page scan
    rendered to images all at once is hundreds of megabytes of bitmaps held in
    a worker with a fixed disk allowance — the thing that turns one awkward
    upload into a worker that cannot process anything.
    """
    if not available():
        return []

    import pdf2image
    import pytesseract

    try:
        page_count = len(pdf2image.pdfinfo_from_path(file_path).get("Pages", []) or [])
    except Exception:  # noqa: BLE001 — fall back to reading until pages run out
        page_count = 0

    limit = KNOWLEDGE_BASE_OCR_MAX_PAGES
    if page_count and page_count > limit:
        logger.warning(
            "OCR is reading the first {} of {} pages; the rest are skipped.",
            limit,
            page_count,
        )

    pages: list[str] = []
    with tempfile.TemporaryDirectory(prefix="decibyl-ocr-") as workspace:
        for page_number in range(1, limit + 1):
            if page_count and page_number > page_count:
                break
            try:
                images = pdf2image.convert_from_path(
                    file_path,
                    dpi=KNOWLEDGE_BASE_OCR_DPI,
                    first_page=page_number,
                    last_page=page_number,
                    output_folder=workspace,
                )
            except Exception as exc:  # noqa: BLE001 — see the docstring
                logger.warning("OCR could not render page {}: {}", page_number, exc)
                break

            if not images:
                break

            for image in images:
                try:
                    text = pytesseract.image_to_string(
                        image, lang=KNOWLEDGE_BASE_OCR_LANGUAGES
                    )
                except Exception as exc:  # noqa: BLE001 — see the docstring
                    logger.warning("OCR could not read page {}: {}", page_number, exc)
                    text = ""
                finally:
                    image.close()

                if len(text.strip()) >= MIN_CHARS_PER_PAGE:
                    pages.append(text.strip())

    if pages:
        logger.info(
            "OCR recovered text from {} page(s) of {}",
            len(pages),
            os.path.basename(file_path),
        )
    return pages
