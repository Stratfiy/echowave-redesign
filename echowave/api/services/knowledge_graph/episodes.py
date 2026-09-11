"""Turning the things that happened into episodes the graph can read.

Graphiti's unit of ingestion is an episode: a piece of text, a description of
where it came from, and the time it happened. Everything else -- entities,
relationships, when a fact stopped being true -- is derived from those. So this
module is where we decide what counts as a thing that happened, and it is
deliberately the only place that decides it.

These are pure functions returning :class:`Episode` values. Nothing here talks
to Graphiti, to a database, or to the network, which is why the decisions that
matter can be tested without standing up a graph:

* **``reference_time`` is when it happened, never when we ingested it.** This
  is the entire reason for using a temporal graph. A call from the 14th
  backfilled today is a fact about the 14th, and a graph told otherwise will
  answer "what did we know before the appointment" with facts from after it.
* **A call with nothing said produces no episode.** An empty or near-empty
  transcript ingested anyway costs an LLM call and adds a node that means
  nothing, and the graph gets worse the more of them it holds.
* **What the agent recorded is separated from what was said.** A value the
  workflow captured into a variable was confirmed by a tool or read back;
  a line in a transcript is what the speech recogniser thought it heard in a
  noisy shop. Marking which is which lets the extraction weight them, and
  keeps a mis-heard phone number from being stored with the same confidence as
  one the caller confirmed digit by digit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: Graphiti's source kinds, as it names them. A call is a dialogue between two
#: speakers; a document is prose.
SOURCE_MESSAGE = "message"
SOURCE_TEXT = "text"

#: Below this many characters a transcript is a hang-up, a wrong number, or a
#: greeting nobody answered. Ingesting it costs an LLM call and teaches the
#: graph nothing. Roughly one exchanged sentence.
MINIMUM_TRANSCRIPT_CHARS = 80

#: The most of one call we hand to the extractor at once. A long call is not
#: more informative per token -- the facts cluster near where things were
#: agreed -- and an unbounded body turns one pathological call into a
#: pathological bill.
MAX_EPISODE_CHARS = 12_000


@dataclass(frozen=True)
class Episode:
    """One thing that happened, ready to be written to a partition.

    Field names mirror Graphiti's ``add_episode`` arguments so the adapter that
    finally calls it is a mapping and nothing more. Keeping that layer dumb is
    what lets the decisions above be tested here.
    """

    name: str
    body: str
    source_description: str
    reference_time: datetime
    group_id: str
    source: str = SOURCE_MESSAGE


def _truncate(text: str) -> str:
    """Cut to the cap at a line boundary, so a fact is not halved mid-sentence."""
    if len(text) <= MAX_EPISODE_CHARS:
        return text
    cut = text[:MAX_EPISODE_CHARS]
    last_break = cut.rfind("\n")
    return cut[:last_break] if last_break > MAX_EPISODE_CHARS // 2 else cut


def _recorded_lines(gathered_context: dict[str, Any] | None) -> list[str]:
    """The variables the workflow captured, as plain statements.

    Only scalars. A nested structure in gathered context is workflow plumbing
    rather than something a person said, and flattening it produces sentences
    that read like facts without being any.
    """
    if not gathered_context:
        return []
    lines = []
    for key, value in sorted(gathered_context.items()):
        if value is None or isinstance(value, (dict, list)):
            continue
        if isinstance(value, str) and not value.strip():
            continue
        readable = key.replace("_", " ").strip()
        lines.append(f"- {readable}: {value}")
    return lines


def episode_from_call(
    *,
    organization_id: int,
    group_id: str,
    workflow_run_id: int,
    agent_name: str,
    transcript: str | None,
    happened_at: datetime,
    gathered_context: dict[str, Any] | None = None,
) -> Episode | None:
    """One completed call, or None when there is nothing worth remembering.

    ``group_id`` is passed in rather than derived here so that no function in
    this module can write to a partition without a caller having gone through
    :mod:`api.services.knowledge_graph.scoping` first.
    """
    text = (transcript or "").strip()
    recorded = _recorded_lines(gathered_context)

    if len(text) < MINIMUM_TRANSCRIPT_CHARS and not recorded:
        return None

    parts = []
    if recorded:
        parts.append(
            "What the agent recorded during this call (confirmed values):\n"
            + "\n".join(recorded)
        )
    if text:
        parts.append("Transcript of the call as heard:\n" + text)

    return Episode(
        name=f"call:{workflow_run_id}",
        body=_truncate("\n\n".join(parts)),
        source_description=(
            f"Phone call handled by the agent {agent_name} "
            f"for organization {organization_id}"
        ),
        reference_time=happened_at,
        group_id=group_id,
        source=SOURCE_MESSAGE,
    )


def episodes_from_document(
    *,
    group_id: str,
    document_uuid: str,
    filename: str,
    chunks: list[str],
    published_at: datetime,
) -> list[Episode]:
    """A knowledge base document, one episode per chunk.

    Per chunk rather than whole-document because the chunks already exist --
    the knowledge base splits and embeds every upload today -- and because a
    price list handed over whole is one enormous extraction that mostly
    rediscovers the same few entities. Reusing the existing chunking also means
    the graph and the vector search are reading the same text, so an answer
    from one is never contradicted by the other.

    ``published_at`` is when the document was given to us, which is the honest
    event time for what it asserts: a fee schedule uploaded in March states
    March's fees, and a later upload should supersede it rather than sit beside
    it as an equally current truth.
    """
    episodes = []
    for index, chunk in enumerate(chunks):
        text = (chunk or "").strip()
        if not text:
            continue
        episodes.append(
            Episode(
                name=f"document:{document_uuid}:{index}",
                body=_truncate(text),
                source_description=f"Section {index + 1} of the document {filename}",
                reference_time=published_at,
                group_id=group_id,
                source=SOURCE_TEXT,
            )
        )
    return episodes
