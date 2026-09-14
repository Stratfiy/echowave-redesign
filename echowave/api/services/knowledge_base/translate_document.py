"""A document in another language, made from one already in the library.

Offered after an upload: a price list in Tamil becomes a price list in
English (or Hindi, or any Sarvam language) as a document of its own, in the
same scope, so the bots that read the original read the translation too.

The route makes the row first -- pending, named for the target, pointing at
its source -- and hands the work to a job: a document is thousands of
characters and Sarvam takes a thousand a request. The job gathers the text
(the stored full text, or the chunks in order), translates it, writes the
result to storage as a text file at the key an upload would have used, and
then queues the ordinary ingestion, so the translation is chunked and
embedded exactly like something somebody uploaded. A failure is written on
the row, where the list already shows failures.
"""

from __future__ import annotations

import uuid as uuidlib
from typing import Any

from loguru import logger

from api.db import db_client
from api.services import translation
from api.services.knowledge_base import upload_keys
from api.services.storage import storage_fs
from api.tasks.function_names import FunctionNames

#: What the copy is called: the original's stem, the language, as text.
LANGUAGE_NAMES = {
    "en-IN": "English",
    "hi-IN": "Hindi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "pa-IN": "Punjabi",
    "od-IN": "Odia",
}


def translated_filename(filename: str, target: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return f"{stem} ({LANGUAGE_NAMES.get(target, target)}).txt"


def text_of(document: Any, chunks: list[Any]) -> str:
    """The document's words: the stored full text, else the chunks in order."""
    full = getattr(document, "full_text", None)
    if full and str(full).strip():
        return str(full).strip()
    ordered = sorted(chunks, key=lambda c: getattr(c, "chunk_index", 0))
    return "\n\n".join(
        str(getattr(c, "chunk_text", "") or "").strip() for c in ordered
    ).strip()


class NotTranslatable(ValueError):
    """The source cannot be translated; the message says why, for the screen."""


async def start(
    *,
    source_uuid: str,
    organization_id: int,
    user_id: int,
    provider_id: str,
    target: str,
) -> Any:
    """Make the pending row and queue the work. Returns the new document."""
    if target not in translation.LANGUAGES:
        raise NotTranslatable(f"Unknown language {target!r}")
    source = await db_client.get_document_by_uuid(
        source_uuid, organization_id=organization_id
    )
    if source is None:
        raise NotTranslatable("Document not found")
    if source.processing_status != "completed":
        raise NotTranslatable("Wait until the document has been read.")

    # Imported here, not at the top: the worker imports this module to run
    # the job, and the queue module imports the worker's functions.
    from api.tasks.arq import enqueue_job

    new_uuid = str(uuidlib.uuid4())
    filename = translated_filename(source.filename, target)
    document = await db_client.create_document(
        organization_id=organization_id,
        created_by=user_id,
        filename=filename,
        file_size_bytes=0,
        file_hash="",
        mime_type="text/plain",
        custom_metadata={
            "s3_key": upload_keys.build_document_key(
                organization_id, new_uuid, filename
            ),
            "translated_from": source.document_uuid,
            "language": target,
        },
        document_uuid=new_uuid,
        retrieval_mode=source.retrieval_mode or "chunked",
        scope=getattr(source, "scope", "library"),
        folder_id=getattr(source, "folder_id", None),
        workflow_id=getattr(source, "workflow_id", None),
    )
    await enqueue_job(
        FunctionNames.TRANSLATE_KNOWLEDGE_BASE_DOCUMENT,
        document.id,
        source.id,
        organization_id,
        provider_id,
        target,
    )
    return document


async def run(
    *,
    document_id: int,
    source_id: int,
    organization_id: int,
    provider_id: str,
    target: str,
) -> None:
    """Translate the source into the pending row's file, then queue ingestion."""
    from api.tasks.arq import enqueue_job

    document = await db_client.get_document_by_id(document_id)
    source = await db_client.get_document_by_id(source_id)
    if document is None or source is None:
        logger.warning("Translation {}: document or source vanished", document_id)
        return
    try:
        chunks = await db_client.get_chunks_for_document(source.id, organization_id)
        text = text_of(source, chunks)
        if not text:
            raise NotTranslatable("The document has no text to translate.")
        translated, _ = await translation.translate(text, target=target)
        # A translated document is billed like any translation: one credit
        # per 100 characters, keyed on the new document so a retried job
        # charges it once (KAN-104).
        from api.services.billing import events as billing_events

        await billing_events.charge_in_own_session(
            organization_id=organization_id,
            event=billing_events.TRANSLATION,
            ref_id=f"doc:{document.id}",
            quantity=billing_events.translation_quantity(text),
            note=f"document {document.filename[:60]} to {target}",
        )
        key = (document.custom_metadata or {}).get(
            "s3_key"
        ) or upload_keys.build_document_key(
            organization_id, document.document_uuid, document.filename
        )
        if not await storage_fs.acreate_file_from_bytes(
            key, translated.encode("utf-8")
        ):
            raise RuntimeError("Could not write the translation to storage.")
        await enqueue_job(
            FunctionNames.PROCESS_KNOWLEDGE_BASE_DOCUMENT,
            document.id,
            key,
            organization_id,
            provider_id,
            128,
            document.retrieval_mode or "chunked",
        )
        logger.info(
            "Translated document {} into {} as {}", source.id, target, document.id
        )
    except Exception as exc:  # noqa: BLE001 - the row says what happened
        logger.error("Translation of document {} failed: {}", source.id, exc)
        await db_client.update_document_status(
            document.id, "failed", error_message=str(exc)[:500]
        )
