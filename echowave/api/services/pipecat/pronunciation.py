"""Making the agent say a name the way the business says it.

A voice agent that mispronounces the clinic's own name, the doctor's name or
the locality is not a small blemish — it is the first thing a caller notices
and the thing that makes the business sound like it did not set this up. It is
also the one class of error the customer can hear immediately and, until now,
could do nothing about.

**A respelling, not a phoneme alphabet.** The obvious engineering answer is
SSML with IPA, and it is the wrong one here: half our TTS providers do not
support it, the Indic voices that do support it least are exactly the ones that
need it most, and nobody running a dental clinic in Hosur is going to type
``/tʃɪnəswaːmi/``. What they will happily type is *"Chinna-swaamy"*, because
that is how they would write it for a new receptionist.

So a lexicon is a list of pairs: what appears in the text, and what the voice
should be handed instead. It runs on the aggregated sentence just before
synthesis, so it is provider-agnostic and every voice gets it.

**Longest match first.** "Dr Rao" and "Rao" can both be in a lexicon, and if
the short one is applied first the long one can never match. Sorting by length
is the whole of the disambiguation, and it is the sort of thing that silently
half-works if left to dictionary order.
"""

from __future__ import annotations

import re
from typing import Any

# A lexicon much longer than this is a sign somebody is trying to rewrite the
# script rather than fix a name, and every entry costs a regex pass on every
# sentence the agent speaks.
MAX_ENTRIES = 200

# Long enough to matter, short enough that it cannot hold a sentence.
MAX_TERM_LENGTH = 80


def parse_lexicon(raw: Any) -> list[tuple[str, str]]:
    """Read the configured lexicon into ordered (find, say) pairs.

    Tolerant of shape because this comes out of ``workflow_configurations``, a
    free-form JSON blob: anything malformed is skipped rather than allowed to
    raise while a call is being set up. A missing pronunciation is a blemish; a
    pipeline that will not build is a dead call.
    """
    if not isinstance(raw, list):
        return []

    pairs: list[tuple[str, str]] = []
    for entry in raw[:MAX_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        find = entry.get("find") or entry.get("from") or entry.get("word")
        say = entry.get("say") or entry.get("to") or entry.get("pronounce")
        if not isinstance(find, str) or not isinstance(say, str):
            continue
        find = find.strip()[:MAX_TERM_LENGTH]
        say = say.strip()[:MAX_TERM_LENGTH]
        if not find or not say:
            continue
        pairs.append((find, say))

    # Longest first, so "Dr Rao" wins over "Rao".
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    return pairs


def compile_lexicon(pairs: list[tuple[str, str]]) -> list[tuple[re.Pattern, str]]:
    """Pre-compile each entry to a word-bounded, case-insensitive pattern.

    Word-bounded so a lexicon entry for "Rao" does not rewrite "Raoul", and
    ``re.escape`` so a customer typing "Dr. Rao" gets a literal full stop
    rather than a wildcard that matches "Dr!Rao" — or, worse, a term of theirs
    that happens to be valid regex quietly changing what other sentences say.
    """
    compiled: list[tuple[re.Pattern, str]] = []
    for find, say in pairs:
        # \b does not fire next to punctuation, so terms that start or end with
        # a non-word character use a lookaround on whitespace instead.
        prefix = r"\b" if find[:1].isalnum() else r"(?<!\S)"
        suffix = r"\b" if find[-1:].isalnum() else r"(?!\S)"
        compiled.append(
            (re.compile(prefix + re.escape(find) + suffix, re.IGNORECASE), say)
        )
    return compiled


def apply_lexicon(text: str, compiled: list[tuple[re.Pattern, str]]) -> str:
    """Rewrite every configured term in one sentence of about-to-be-spoken text."""
    if not text or not compiled:
        return text
    for pattern, say in compiled:
        # A plain string replacement, so a backslash or \1 in what the customer
        # typed is spoken rather than interpreted as a group reference.
        text = pattern.sub(lambda _match, value=say: value, text)
    return text
