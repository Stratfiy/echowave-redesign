"""Decibyl searching the workspace's Files, and citing them.

Decibyl reads Files twice: once before every answer, with the question as
asked (``decibyl._knowledge``), and on demand through ``search_files`` when
it needs to look again with better words or after a file has just landed.
Both go through :func:`search`, so they see the same files and cite them the
same way: the file's name and folder as they are now, and the page or the
sheet and rows.

**What Decibyl may read.** It is the workspace's own assistant, so it reads
the workspace's files -- organisation-wide ones and the library -- but not a
file given to one channel or one agent: those are read where they were
given (``KnowledgeScope``). Written as the scopes it may *not* read, so a
scope added later is readable until someone decides otherwise, rather than
silently invisible.

**Every passage, not whole files.** A full-document file is searched by its
passages like any other, so one question does not pull fifty whole files
into the prompt.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.db import db_client

TOOL_NAME = "search_files"

#: Scopes Decibyl does not read: given to one channel or one agent.
NOT_FOR_DECIBYL = ("channel", "bot")

#: Passages handed over for one question.
LIMIT = 4


async def _embeddings(organization_id: int) -> dict[str, Any]:
    """The account's embeddings settings, or {} to search by words alone."""
    try:
        from api.services.configuration.ai_model_configuration import (
            apply_managed_embeddings_base_url,
            get_effective_ai_model_configuration_for_organization,
        )

        config = await get_effective_ai_model_configuration_for_organization(
            organization_id
        )
    except Exception as exc:  # noqa: BLE001 - words still work
        logger.warning("Could not resolve embeddings for Files search: {}", exc)
        return {}
    embeddings = getattr(config, "embeddings", None)
    if not embeddings:
        return {}
    provider = getattr(embeddings, "provider", None)
    return {
        "embeddings_api_key": embeddings.api_key,
        "embeddings_model": embeddings.model,
        "embeddings_base_url": apply_managed_embeddings_base_url(
            provider=provider, base_url=getattr(embeddings, "base_url", None)
        ),
        "embeddings_provider": provider,
        "embeddings_endpoint": getattr(embeddings, "endpoint", None),
        "embeddings_api_version": getattr(embeddings, "api_version", None),
    }


async def search(
    organization_id: int, query: str, *, limit: int = LIMIT
) -> dict[str, Any]:
    """The passages of the workspace's Files that answer ``query``, cited.

    The same payload the agents' tool returns: a status, the passages, and
    an instruction when there is nothing to answer from.
    """
    from api.services.workflow.tools.knowledge_base import (
        retrieve_from_knowledge_base,
    )

    readable = await db_client.readable_document_uuids(
        organization_id, exclude_scopes=NOT_FOR_DECIBYL
    )
    if not readable:
        return {
            "status": "no_match",
            "chunks": [],
            "query": query,
            "total_results": 0,
            "instruction": "There are no files in this workspace to answer from yet.",
        }
    return await retrieve_from_knowledge_base(
        query=query,
        organization_id=organization_id,
        document_uuids=readable,
        limit=limit,
        full_documents=False,
        **(await _embeddings(organization_id)),
    )


def citations_of(result: dict[str, Any]) -> list[str]:
    """Each file a result read from, once, as its citation, in order."""
    out: list[str] = []
    for chunk in result.get("chunks") or []:
        if not isinstance(chunk, dict):
            continue
        label = (
            chunk.get("citation") or chunk.get("document_name") or chunk.get("filename")
        )
        if label and label not in out:
            out.append(str(label))
    return out


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Search the workspace's Files (documents, spreadsheets, PDFs, "
            "pictures) for passages that answer a question. Returns each "
            "passage with its citation: the file, its folder, and the page "
            "or sheet and rows. Runs now."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to look for, in the words the file would use: "
                        "'refund window for plan B', 'price of SKU-114'."
                    ),
                }
            },
            "required": ["query"],
        },
    }


async def for_thread(organization_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
    """The tool: search, say on the thread which files were read, return."""
    from api.services.workflow import agent_timeline

    query = str(arguments.get("query") or "").strip()
    if not query:
        return {"status": "error", "error": "Say what to look for."}
    result = await search(organization_id, query)
    cited = citations_of(result)
    if cited:
        await agent_timeline.record_activity(
            organization_id=organization_id,
            summary=agent_timeline.read_passages_line(result)
            or f"Read {', '.join(cited)}",
            payload={
                "tool": TOOL_NAME,
                "sources": [
                    {
                        "kind": "knowledge",
                        "label": "Files",
                        "status": "read",
                        "detail": f"{len(result.get('chunks') or [])} passage"
                        + ("s" if len(result.get("chunks") or []) != 1 else ""),
                        "documents": cited,
                    }
                ],
            },
            in_channel=False,
        )
    status = result.get("status")
    return {
        # Read by the dispatcher: "success" lets the model use it this turn.
        "status": "success" if status in ("ok", "no_match", "weak_match") else "error",
        "found": status == "ok",
        "passages": [
            {"text": str(c.get("text") or "")[:1200], "citation": c.get("citation")}
            for c in result.get("chunks") or []
            if isinstance(c, dict)
        ],
        **({"instruction": result["instruction"]} if result.get("instruction") else {}),
    }
