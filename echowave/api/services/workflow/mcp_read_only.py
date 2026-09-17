"""A newly connected MCP server exposes its reads, and nothing else, until
somebody says otherwise.

Zerodha's hosted server was connected with an empty filter, which the schema
documents as "expose all tools". It exposed 22, six of which place, modify
and cancel real orders. The filter was set by hand afterwards, on one
account. Every other bank, broker, CRM or ERP anybody connects by MCP starts
exactly where that one did: with the whole write surface open to a model
that has been told to be helpful.

So the default is the other way round. On connect, the filter is filled with
the tools that only read, and the operator widens it from there -- the field
is on both the create and edit screens as a plain list of names. A write left
out is a tool the bot cannot call until a person adds it, which is the safe
direction. A read left out costs one edit.

This is a *default*, not a gate. A filter somebody set is never touched, on
create or on update, and the runtime still trusts the filter alone. That is
deliberate: a verb rule on a name is a guess, and #343 and #344 are both
records of a guess about slug shape doing something nobody meant. A guess
that only ever narrows what a new server starts with is one whose mistakes
are all visible and all reversible.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

#: First word of a tool name that means it only looks. Order does not
#: matter; the match is exact on the leading token.
READ_VERBS = frozenset(
    {
        "get",
        "list",
        "search",
        "find",
        "fetch",
        "read",
        "query",
        "lookup",
        "describe",
        "show",
        "view",
        "retrieve",
        "check",
        "count",
        "status",
        "quote",
        "quotes",
        "ltp",
        "ohlc",
        "history",
        "historical",
        "instruments",
        "holdings",
        "positions",
        "margins",
        "profile",
        "orders",
        "trades",
        "balance",
        "balances",
        "info",
        "watch",
    }
)

#: Verbs that change something, matched as a prefix so "places", "placed",
#: "cancelling" and "transferred" are all caught. Four letters or more, so a
#: short stem cannot swallow an unrelated word ("add" inside "address").
WRITE_STEMS = (
    "place",
    "cancel",
    "modify",
    "update",
    "delete",
    "remove",
    "create",
    "send",
    "transfer",
    "execute",
    "write",
    "submit",
    "withdraw",
    "deposit",
    "convert",
    "invest",
    "redeem",
    "upload",
    "import",
    "publish",
    "schedule",
    "square",
)

#: Short verbs, matched exactly. Nouns are deliberately absent: "message",
#: "email", "call", "alert" name what a tool is about, and "get_messages"
#: and "list_calls" are reads. It is the verb that makes a write.
WRITE_EXACT = frozenset(
    {
        "add",
        "set",
        "put",
        "buy",
        "sell",
        "pay",
        "post",
        "exit",
        "book",
        "sip",
        "gtt",
        "login",
        "logout",
        "revoke",
    }
)

#: What a new server's filter says when every tool on it changes something.
#: An empty list means "expose all", so empty is the one thing this must not
#: leave behind. Spaces and punctuation make it impossible to collide with a
#: real tool name, and it reads as a sentence in the field it lands in.
NOTHING_ENABLED = "(nothing enabled yet — every tool on this server changes something; add names here to allow them)"


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if w]


def is_read(name: str, description: str = "") -> bool:
    """Whether this tool only looks.

    Unknown means it does not: a tool named ``order_book`` is a read and is
    excluded here, and that costs the operator one edit. A tool named
    ``book_order`` let through would cost somebody an order.
    """
    name_words = _words(name)
    if not name_words:
        return False
    # The description is the server's own word for what the tool does, and
    # "Places a market order" settles it whatever the tool is called.
    for word in name_words + _words(description):
        if word in WRITE_EXACT or any(word.startswith(stem) for stem in WRITE_STEMS):
            return False
    return name_words[0] in READ_VERBS


def default_filter(discovered: Iterable[dict[str, Any]]) -> list[str]:
    """The allowlist a new server starts with.

    Never empty when there was anything to choose from: empty is the
    schema's word for "everything", which is the outcome this exists to
    prevent. A server with no reads at all gets a sentinel that matches
    nothing and explains itself in the field.
    """
    names = []
    for tool in discovered or []:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if name and is_read(name, str(tool.get("description") or "")):
            names.append(name)
    if names:
        return names
    if any(isinstance(t, dict) and t.get("name") for t in discovered or []):
        return [NOTHING_ENABLED]
    return []
