"""Reading a picture: what it shows, and every word in it.

A photo of a price board, a screenshot of a WhatsApp order, a scanned
receipt saved as JPEG. None of these has a text layer, and before this every
one of them was refused as an unsupported type -- including the images that
arrive on WhatsApp and are filed as documents.

The picture is read by the account's own text model, through the same door
every other model call outside a pipeline uses
(``agent_builder.client.complete``, chosen by
``agent_builder.settings.resolve_for_organization``) and the same way Studio
hands that client a screenshot: as pictures on a tool result
(``IMAGES_KEY``), which each vendor adapter already sends the way its vendor
accepts images. No new provider, no new key.

When no model can look at it -- none configured, the vendor refuses, the
reply is empty -- tesseract reads the words if it is installed. When neither
can, the file fails with a sentence saying so: never a document listed as
ready that no agent can answer from.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from api.services.knowledge_base.errors import (
    DocumentExtractionError,
    EmptyDocumentError,
)
from api.services.knowledge_base.extraction import (
    IMAGE_EXTENSIONS,
    ExtractedDocument,
    TextBlock,
)

__all__ = ["IMAGE_EXTENSIONS", "read_image"]

#: The largest picture sent as is. Vendors cap an inline image at about 5 MB
#: of base64, which is ~3.75 MB of bytes; past this it is shrunk first.
MAX_INLINE_BYTES = 3_500_000
#: The longest side a shrunk picture keeps. Enough for small print on a
#: phone photo of a price board, which is the case that matters.
MAX_SIDE_PIXELS = 2000

DESCRIPTION_HEADING = "What the picture shows"
TEXT_HEADING = "Text in the picture"

TOOL_NAME = "look_at_file"

SYSTEM = (
    "You read pictures that a business has saved to its files, so that its "
    "assistants can answer questions from them later. Be literal and "
    "complete; never guess at a word you cannot read."
)

ASK = (
    "Look at the picture in the file below and reply in exactly this form:\n"
    "DESCRIPTION: two or three sentences on what the picture is and shows "
    "(a price list, a receipt, a shop sign, a chart...), naming any "
    "business, product, person or date in it.\n"
    "TEXT:\n"
    "every piece of text in the picture, transcribed exactly, line by line, "
    "in its own language. Keep table rows on one line with their column "
    "headers, as 'Header: value; Header: value'. Write NONE if there is no "
    "text."
)


def media_type(data: bytes, filename: str) -> str:
    """The picture's real type, from its first bytes (the name can lie)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise DocumentExtractionError(
        f"{filename} is not a PNG, JPEG or WebP picture",
        user_message=(
            "This file is named like a picture but is not a PNG, JPEG or WebP "
            "image we can open. Save it again as one of those and upload it."
        ),
    )


def fit_for_model(data: bytes, kind: str) -> tuple[bytes, str]:
    """The picture, shrunk to JPEG if it is too large to send inline."""
    if len(data) <= MAX_INLINE_BYTES:
        return data, kind
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            image.thumbnail((MAX_SIDE_PIXELS, MAX_SIDE_PIXELS))
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=85)
            return out.getvalue(), "image/jpeg"
    except Exception as exc:  # noqa: BLE001 - try it as it is
        logger.warning("Could not shrink a large picture: {}", exc)
        return data, kind


def parse_reading(reply: str) -> tuple[str, list[str]]:
    """The description, and the lines of text, from the model's reply."""
    text = (reply or "").strip()
    description = ""
    lines: list[str] = []
    match = re.search(
        r"DESCRIPTION:\s*(.*?)(?:\n\s*TEXT:|\Z)", text, re.DOTALL | re.IGNORECASE
    )
    if match:
        description = " ".join(match.group(1).split())
    body = re.search(r"\n?\s*TEXT:\s*\n?(.*)\Z", text, re.DOTALL | re.IGNORECASE)
    if body:
        for line in body.group(1).splitlines():
            line = " ".join(line.split())
            if line and line.upper() != "NONE":
                lines.append(line)
    if not match and not body:
        # A model that ignored the form still said something worth keeping.
        description = " ".join(text.split())
    return description, lines


def blocks_from_reading(description: str, lines: list[str]) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    if description:
        blocks.append(TextBlock(text=description, heading_path=(DESCRIPTION_HEADING,)))
    for line in lines:
        blocks.append(TextBlock(text=line, heading_path=(TEXT_HEADING,)))
    return blocks


async def ask_model(
    organization_id: int | None, data: bytes, kind: str, filename: str
) -> tuple[str, str]:
    """The picture read by the account's model: (reply text, model label).

    The seam the tests replace. Raises when no model can be reached.
    """
    from api.db import db_client
    from api.services.agent_builder import client, settings
    from api.services.billing import model_usage

    async with db_client.async_session() as session:
        model = await settings.resolve_for_organization(
            session, None, organization_id=organization_id
        )
    conversation = client.Conversation()
    conversation.add_user(ASK)
    call = client.ToolCall(id="look-1", name=TOOL_NAME, arguments={"file": filename})
    conversation.add_assistant(client.ModelReply(text="", tool_calls=(call,)))
    conversation.add_tool_result(
        call,
        {
            "file": filename,
            client.IMAGES_KEY: [
                {
                    "media_type": kind,
                    "data": base64.b64encode(data).decode("ascii"),
                    "label": filename,
                }
            ],
        },
    )
    with model_usage.scope(organization_id=organization_id, feature="file_reading"):
        reply = await client.complete(
            provider=model.provider,
            model=model.model,
            api_key=model.api_key,
            system=SYSTEM,
            conversation=conversation,
            tools=[
                {
                    "name": TOOL_NAME,
                    "description": "Shows a file from the workspace's Files.",
                    "parameters": {
                        "type": "object",
                        "properties": {"file": {"type": "string"}},
                    },
                }
            ],
        )
    return reply.text or "", f"{model.provider}/{model.model}"


def tesseract_lines(path: str) -> list[str]:
    """Words in the picture by tesseract, or [] when it is not installed."""
    if not shutil.which("tesseract"):
        return []
    try:
        import pytesseract
        from PIL import Image

        from api.constants import KNOWLEDGE_BASE_OCR_LANGUAGES

        with Image.open(path) as image:
            text = pytesseract.image_to_string(image, lang=KNOWLEDGE_BASE_OCR_LANGUAGES)
    except Exception as exc:  # noqa: BLE001 - a fallback that fails is just absent
        logger.warning("tesseract could not read a picture: {}", exc)
        return []
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


async def read_image(
    file_path: str, filename: str, *, organization_id: int | None
) -> ExtractedDocument:
    """A picture as blocks of text: what it shows, then the words in it."""
    raw = await asyncio.to_thread(Path(file_path).read_bytes)
    kind = media_type(raw, filename)
    metadata: dict[str, Any] = {
        "backend": "local",
        "source_filename": filename,
        "source_extension": os.path.splitext(filename)[1].lower(),
        "declared_content_type": kind,
        "image": True,
    }

    blocks: list[TextBlock] = []
    why_not = ""
    try:
        data, sent_as = fit_for_model(raw, kind)
        reply, label = await ask_model(organization_id, data, sent_as, filename)
        description, lines = parse_reading(reply)
        blocks = blocks_from_reading(description, lines)
        metadata["extractor"] = f"vision:{label}"
        metadata["text_lines"] = len(lines)
    except Exception as exc:  # noqa: BLE001 - try tesseract, then say so
        why_not = str(exc)
        logger.warning("No model could read picture {}: {}", filename, exc)

    if not blocks:
        lines = tesseract_lines(file_path)
        if lines:
            blocks = blocks_from_reading("", lines)
            metadata["extractor"] = "tesseract"
            metadata["text_lines"] = len(lines)

    if not blocks:
        raise EmptyDocumentError(
            f"Nothing read from picture {filename}: {why_not or 'empty reply'}",
            user_message=(
                "We could not read this picture: no model that can look at "
                "images is available to this workspace right now. Try again "
                "later, or upload the text as a PDF or document."
                if why_not
                else "We looked at this picture and found nothing to read in it."
            ),
        )
    metadata["block_count"] = len(blocks)
    metadata["character_count"] = sum(len(b.text) for b in blocks)
    return ExtractedDocument(blocks=blocks, metadata=metadata)
