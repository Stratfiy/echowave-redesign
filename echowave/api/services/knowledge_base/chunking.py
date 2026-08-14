"""Turning blocks of text into the units an agent retrieves.

A chunk is the smallest thing the agent can be handed mid-call, so the size
question is not a tuning detail — it is the difference between "your refund
window is 14 days" and half a sentence about refunds. Three properties are
worth more than any clever segmentation:

* **A chunk does not straddle a sentence.** Splitting on a token budget alone
  cuts mid-clause, and the fragment retrieves for queries it cannot answer.
* **A chunk knows where it came from.** The heading path is prepended to the
  *embedded* text and kept out of the *stored* text, so a chunk under "Refunds
  → International orders" matches a question about international refunds
  without that heading being read aloud to the caller.
* **The same document chunks the same way twice.** Re-ingestion after an edit
  should change the chunks that changed, and nothing else — so no randomness,
  no clock, no dictionary ordering.

Token counting uses ``tiktoken`` when it is installed and a calibrated
character heuristic otherwise. The heuristic runs slightly *conservative*
(it over-counts), because the failure mode of over-counting is a chunk smaller
than it needed to be, and the failure mode of under-counting is an embedding
call rejected for exceeding the model's context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable

from api.services.knowledge_base.extraction import TextBlock

#: Below this, a trailing chunk is merged backwards instead of standing alone.
#: A 6-token chunk carries no retrievable meaning but does occupy a result slot
#: that a useful chunk wanted.
MIN_CHUNK_TOKENS = 16

#: Characters per token for the fallback estimator. English prose sits around
#: 4.0; 3.6 is the deliberate over-count described in the module docstring.
_CHARS_PER_TOKEN = 3.6

#: Abbreviations whose full stop does not end a sentence. Kept short and
#: business-shaped rather than exhaustive: the cost of missing one is a chunk
#: boundary in a slightly wrong place, and the cost of a huge list is a regex
#: nobody can read. "Rs." earns its place because a price is exactly the kind
#: of fact a caller asks about.
_ABBREVIATIONS = ("Mr", "Mrs", "Ms", "Dr", "Rs", "No", "St", "Jr", "Sr", "vs", "Inc")

#: Sentence end: terminal punctuation, optional closing quote or bracket, then
#: whitespace. Each lookbehind includes the full stop, because the split point
#: sits *after* it — a lookbehind for "Rs" at that position reads "s." and
#: never matches, which is how "Pay Rs. 500 to Mr. Rao." became four sentences.
_SENTENCE_END = re.compile(
    "".join(rf"(?<!\b{abbrev}\.)" for abbrev in _ABBREVIATIONS)
    # A single capital before the stop is an initial — "A. Kumar", "J. R. Rao".
    + r"(?<!\b[A-Z]\.)"
    + r"""(?<=[.!?])["')\]]*\s+""",
)


@lru_cache(maxsize=1)
def _encoder() -> Any | None:
    """The tokenizer, once, or ``None`` if tiktoken is not installed."""
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        # get_encoding downloads the vocabulary on first use. An air-gapped
        # worker must fall back to the heuristic, not fail ingestion.
        return None


def count_tokens(text: str) -> int:
    """Roughly how much of an embedding request this text will occupy."""
    if not text:
        return 0
    encoder = _encoder()
    if encoder is not None:
        return len(encoder.encode(text))
    return max(1, int(len(text) / _CHARS_PER_TOKEN) + 1)


@dataclass
class Chunk:
    """One retrievable unit, in the shape the ingestion task stores.

    ``text`` is what a caller would hear read back. ``contextualized_text`` is
    what gets embedded — the same text with its heading path in front. Keeping
    them apart is what lets a chunk be findable by its section without that
    section title leaking into the answer.
    """

    text: str
    contextualized_text: str
    index: int
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_mps_chunk(self) -> dict[str, Any]:
        """The wire shape MPS returned, so the caller cannot tell them apart."""
        return {
            "chunk_text": self.text,
            "contextualized_text": self.contextualized_text,
            "chunk_index": self.index,
            "chunk_metadata": self.metadata,
            "token_count": self.token_count,
        }


def split_sentences(text: str) -> list[str]:
    """Split on sentence boundaries, never losing a character.

    Returns the whole text as one element when it contains no boundary — a
    heading, a table row, a bare clause. The caller handles anything too long
    to fit a chunk; this function's only job is not to cut mid-sentence.
    """
    parts = [part.strip() for part in _SENTENCE_END.split(text)]
    return [part for part in parts if part]


def _split_oversized(sentence: str, max_tokens: int) -> list[str]:
    """Break a sentence that cannot fit, on word boundaries.

    Reached by table rows, minified JSON values and legal prose written without
    a full stop for two hundred words. Splitting on words rather than tokens
    keeps the pieces readable; splitting at all is unavoidable, because the
    alternative is a chunk the embedding provider rejects.
    """
    words = sentence.split()
    if not words:
        return []

    pieces: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if current and count_tokens(candidate) > max_tokens:
            pieces.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def _heading_prefix(heading_path: Iterable[str]) -> str:
    return " > ".join(part for part in heading_path if part)


def _overlap_tail(text: str, overlap_tokens: int) -> str:
    """The last ``overlap_tokens`` worth of ``text``, on a word boundary.

    Overlap exists so an answer split across a chunk edge is still retrievable
    from at least one side of it. Taken from the tail rather than the head
    because the sentence most likely to continue is the last one.
    """
    if overlap_tokens <= 0 or not text:
        return ""
    words = text.split()
    tail: list[str] = []
    for word in reversed(words):
        tail.insert(0, word)
        if count_tokens(" ".join(tail)) >= overlap_tokens:
            break
    return " ".join(tail)


def chunk_blocks(
    blocks: list[TextBlock],
    *,
    max_tokens: int = 128,
    overlap_tokens: int = 0,
    merge_peers: bool = True,
    source_name: str | None = None,
) -> list[Chunk]:
    """Pack ``blocks`` into chunks of at most ``max_tokens``.

    Blocks are packed in order and never merged across a heading change: two
    paragraphs under different headings are about different things, and a
    chunk spanning both answers questions about neither. ``merge_peers``
    controls only merging *within* a heading, which is where it is safe.
    """
    if max_tokens < 1:
        raise ValueError("max_tokens must be at least 1")

    max_tokens = max(max_tokens, 8)
    overlap_tokens = max(0, min(overlap_tokens, max_tokens // 2))

    # A heading block is context for what follows, not content in its own
    # right: emitting "Refunds" as a standalone chunk spends a result slot on a
    # single word. The path is already carried by the blocks beneath it.
    content_blocks = [b for b in blocks if not b.is_heading and b.text.strip()]
    if not content_blocks:
        # ...unless headings are all there is. A document that is nothing but
        # section titles is still worth something, and returning zero chunks
        # here would report an empty success.
        content_blocks = [b for b in blocks if b.text.strip()]

    chunks: list[Chunk] = []
    pending_texts: list[str] = []
    pending_heading: tuple[str, ...] = ()
    pending_pages: list[int] = []

    def flush() -> None:
        nonlocal pending_texts, pending_pages
        if not pending_texts:
            return
        text = " ".join(pending_texts).strip()
        pending_texts = []
        pages = sorted(set(pending_pages))
        pending_pages = []
        if not text:
            return

        previous = chunks[-1] if chunks else None
        if (
            merge_peers
            and previous is not None
            and count_tokens(text) < MIN_CHUNK_TOKENS
            and previous.metadata.get("headings") == list(pending_heading)
            and previous.token_count + count_tokens(text) <= max_tokens
        ):
            # A runt under the same heading joins its neighbour rather than
            # standing as a chunk nobody can use.
            merged_text = f"{previous.text} {text}".strip()
            previous.text = merged_text
            previous.contextualized_text = _contextualize(
                merged_text, pending_heading, source_name
            )
            previous.token_count = count_tokens(previous.contextualized_text)
            previous.metadata["pages"] = sorted(
                set(previous.metadata.get("pages", []) + pages)
            )
            return

        prefix = ""
        if overlap_tokens and previous is not None:
            if previous.metadata.get("headings") == list(pending_heading):
                prefix = _overlap_tail(previous.text, overlap_tokens)

        body = f"{prefix} {text}".strip() if prefix else text
        contextualized = _contextualize(body, pending_heading, source_name)
        chunks.append(
            Chunk(
                text=body,
                contextualized_text=contextualized,
                index=len(chunks),
                token_count=count_tokens(contextualized),
                metadata={
                    "headings": list(pending_heading),
                    "pages": pages,
                    "source": source_name,
                    "overlap_prefix_tokens": count_tokens(prefix) if prefix else 0,
                },
            )
        )

    for block in content_blocks:
        if block.heading_path != pending_heading:
            flush()
            pending_heading = block.heading_path

        for sentence in split_sentences(block.text):
            pieces = (
                [sentence]
                if count_tokens(sentence) <= max_tokens
                else _split_oversized(sentence, max_tokens)
            )
            for piece in pieces:
                candidate = " ".join(pending_texts + [piece])
                if pending_texts and count_tokens(candidate) > max_tokens:
                    flush()
                    pending_heading = block.heading_path
                pending_texts.append(piece)
                if block.page_number is not None:
                    pending_pages.append(block.page_number)

    flush()

    # Indices are assigned as chunks are appended, but merge_peers can absorb a
    # chunk after the fact. Renumber so chunk_index is a contiguous 0..n-1, the
    # invariant the retrieval path and the UI both assume.
    for position, chunk in enumerate(chunks):
        chunk.index = position

    return chunks


def _contextualize(
    text: str, heading_path: Iterable[str], source_name: str | None
) -> str:
    """What actually gets embedded.

    Heading path first, then the text. The source filename is deliberately
    *not* included: a document named ``policy-v3-FINAL-2.pdf`` contributes
    nothing but noise to the vector, and every chunk in the document would
    carry the same noise, which flattens the distances between them.
    """
    prefix = _heading_prefix(heading_path)
    return f"{prefix}\n\n{text}" if prefix else text


def chunk_text(
    text: str,
    *,
    max_tokens: int = 128,
    overlap_tokens: int = 0,
    merge_peers: bool = True,
    source_name: str | None = None,
) -> list[Chunk]:
    """Chunk a bare string. Convenience for callers with no block structure."""
    blocks = [
        TextBlock(text=paragraph.strip())
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    ]
    return chunk_blocks(
        blocks,
        max_tokens=max_tokens,
        overlap_tokens=overlap_tokens,
        merge_peers=merge_peers,
        source_name=source_name,
    )
