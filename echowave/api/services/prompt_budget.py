"""What a prompt may cost, named per source.

Every block that goes in front of a model grows with the account that owns
it. Confirmed facts accumulate, the timeline accumulates, uploaded files
accumulate. None of that is a bug on its own; the bug is that nothing said
how much of it a prompt may carry, so the ceiling was whichever customer
grew fastest and the way we would have found out is a 400 on their biggest
workspace.

The voice path learned this once already. `MAX_REMEMBERED` caps confirmed
facts at forty, and the comment says why: *"every line here is tokens on
every turn of every call, and an account that has confirmed two hundred
facts would otherwise quietly triple its own bill and bury the node's
actual instruction under a wall of trivia."* The chat path reads the same
table through a different function and had no cap at all -- measured at
28,089 characters for two hundred facts, against 5,842 for the same two
hundred on the voice side.

So the caps live here, together, each with the reason it is that number.
Scattered magic numbers are how one surface ends up with a lesson the
other one never learned.

**Estimated, not tokenized.** ``estimate`` divides by a constant, as n8n's
builder does. A tokenizer in this path would be a dependency and a
per-character cost to answer a question -- "is this too big" -- that a
rough number answers just as well. It is deliberately a slight
over-estimate: being wrong towards "smaller" is the direction that fails.

**Two levels, always.** A cap on the number of items bounds the common
case; a cap on each item's length bounds the one that made it through. An
account with one 60,000-character fact passes any row count.
"""

from __future__ import annotations

#: Characters per token, for an estimate rather than a count.
#:
#: 3.5 is the ratio n8n uses for Anthropic models, and erring low means the
#: estimate errs high, which is the safe direction.
CHARS_PER_TOKEN = 3.5

#: Confirmed facts in the workspace chat's context.
#:
#: The same forty the voice path settled on, for the same reason: the rows
#: come back most-seen first, so the cap keeps what the business says most
#: often and drops the long tail nobody asks about.
MAX_FACTS = 40

#: One fact's value.
#:
#: A confirmed fact is a sentence -- "we open at nine", "returns within
#: fourteen days". Three hundred characters is a generous sentence. A value
#: longer than this is somebody's pasted document, and a document belongs in
#: the knowledge base where it can be searched rather than in every prompt.
MAX_FACT_CHARS = 300

#: Timeline entries in the workspace chat's context.
#:
#: Already bounded at the query (`RECENT_EVENTS`); repeated here because a
#: row count is only half a bound.
MAX_EVENTS = 40

#: One timeline entry's summary.
#:
#: A summary is a line -- "asked about a refund, escalated". Two hundred
#: characters is that line with room to spare, and forty of them is a
#: readable page rather than a transcript.
MAX_EVENT_CHARS = 200


def estimate(text: str) -> int:
    """Roughly how many tokens this is. Rounds up."""
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN) + 1


def clip(text: str, limit: int) -> str:
    """One value, cut to fit, with the cut made visible.

    An ellipsis rather than a silent truncation: a model handed half a
    sentence with no sign of it will finish the sentence itself, and what it
    invents reads exactly like what the business confirmed.
    """
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def lines(
    items: list[str],
    *,
    max_items: int,
    noun: str,
    plural: str | None = None,
) -> list[str]:
    """The first ``max_items``, then a line saying how many were left.

    Said rather than dropped: a model that can see forty of two hundred
    facts and is told so can offer to look further, where one silently given
    forty believes it has them all. The silent-absence rule in AGENTS.md, in
    the one place a blocklist cannot apply.

    ``noun`` is the singular and is used as such when exactly one was left
    out. "1 more facts" is the kind of line that tells a reader nobody ran
    the code.
    """
    if len(items) <= max_items:
        return list(items)
    kept = list(items[:max_items])
    more = len(items) - max_items
    word = noun if more == 1 else (plural or f"{noun}s")
    kept.append(f"- and {more} more {word} not shown here")
    return kept
