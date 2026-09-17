"""Which connected apps a written brief names, and the tools to give it.

A bot built from a brief got no tools at all. The founder asked for one that
summarised his email every morning; it was built, scheduled, and fired, and
it said "I can't access your Gmail from here" -- with Gmail connected and
twelve Gmail tools sitting on the account. Every generated bot had an empty
``tool_uuids`` on every node; the five that worked had been wired by hand in
the builder. The generator wrote a bot whose whole job was reading Gmail and
never gave it Gmail, and nothing failed or warned on the way.

**Named, not guessed.** An app is attached because the brief says its name,
matched whole against the apps this account has actually connected. No model
call: a second model deciding which of somebody's connected accounts a
sentence "probably means" is a way to attach an app nobody mentioned, and it
would be wrong invisibly. Naming an app that is not connected attaches
nothing, because there is nothing to attach.

**Writes come too, and are gated where it matters.** The first version of
this attached reads only, reasoning that a routine runs unsupervised. That
was true of routines and wrong about everything else: a bot that books an
appointment while the customer is on the phone is supervised by the person
who just asked for it, and a bot that can only read is half of what anybody
builds one for. The line is not which tool but whether a given run has a
person in it, so the writes are attached here and refused at run time on an
unattended run -- see ``unattended``.
"""

from __future__ import annotations

import re
from typing import Any

from api.services.workflow import connected_tools, unattended

#: How many tools one brief may attach. A bot handed thirty tools chooses
#: badly among them, and no brief names enough apps to need more.
MAX_TOOLS = 8

#: How many of ``MAX_TOOLS`` are held for writes.
#:
#: Held, not ranked -- the distinction ``tool_sync.most_useful`` was rewritten
#: to make, and this module got wrong in the same way a day later. Reads first
#: plus a hard cap means writes never, for any app with a generous read
#: surface: Gmail publishes exactly eight reads, so a bot built from "read
#: Gmail and reply to them" came out with eight ways to read and no way to
#: reply. Either side borrows what the other does not use.
WRITE_SLOTS = 3

#: The writes a person actually asks a bot for, best first.
#:
#: Spelled here rather than imported from ``tool_sync``: that list decides
#: what the *account* is given when an app is connected, and this one decides
#: what *one bot* is given by one sentence. Same words today, different
#: questions -- and tying them together would mean a change to one silently
#: reshaped the other, which is the hazard ``connected_tools`` names where it
#: duplicates the read verbs.
#:
#: Ordered at all because alphabetical is not an order: sorted by slug,
#: Gmail's CREATE_EMAIL_DRAFT beats SEND_EMAIL, and a bot told to reply gets
#: three ways to make a draft. That is #344's bug, one module over.
WANTED_WRITE_VERBS: tuple[str, ...] = (
    "SEND",
    "REPLY",
    "CREATE",
    "ADD",
    "UPDATE",
    "MODIFY",
    "MOVE",
    "SCHEDULE",
)

#: Node types that can call a tool. ``startCall`` is included because a
#: one-node bot does its work there, and the routine runner's first turn is
#: that node -- miss it and a simple bot has its tools nowhere it can reach.
CALLING_NODES = frozenset({"startCall", "agentNode"})


def named(spec: str, tools: list[Any]) -> set[str]:
    """The connected apps this brief names, by toolkit slug.

    Matched on whole words, so ``gmail`` is not found inside
    ``notgmailish@example.com``. An app the account has not connected is
    never returned: the set is drawn from the tool rows, not from the text.
    """
    text = (spec or "").lower()
    if not text:
        return set()
    found: set[str] = set()
    for tool in tools:
        app = connected_tools.toolkit_of(tool)
        if not app or app in found:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(app)}(?![a-z0-9])", text):
            found.add(app)
    return found


def _verb_rank(tool: Any) -> int:
    """Where a write sits among the writes. Lower is offered sooner.

    A verb nobody listed sorts after every verb somebody did, rather than
    being dropped: an app whose whole write surface is unusual words should
    still give a bot something.
    """
    slug = str(connected_tools.slug_of(tool) or "").upper()
    parts = [p for p in slug.split("_") if p]
    verb = parts[1] if len(parts) > 1 else ""
    try:
        return WANTED_WRITE_VERBS.index(verb)
    except ValueError:
        return len(WANTED_WRITE_VERBS)


def read_tool_uuids(spec: str, tools: list[Any]) -> list[str]:
    """The uuids to attach: the read tools of every app the brief names.

    Ordered by slug so the same brief and the same account always produce
    the same list -- a regenerated bot that silently got a different tool
    set would be a bug nobody could see.
    """
    apps = named(spec, tools)
    if not apps:
        return []
    wanted = [
        tool
        for tool in tools
        if connected_tools.toolkit_of(tool) in apps and connected_tools.is_read(tool)
    ]
    wanted.sort(key=lambda t: str(connected_tools.slug_of(t) or ""))
    return [t.tool_uuid for t in wanted][:MAX_TOOLS]


def write_tool_uuids(spec: str, tools: list[Any]) -> list[str]:
    """The write tools of every app the brief names, best first.

    Ordered by what people ask for -- send, reply, create -- and only then
    by slug. Sorting by slug alone put Gmail's CREATE_EMAIL_DRAFT ahead of
    SEND_EMAIL, so a bot told to reply to people got three ways to write a
    draft and no way to send one.
    """
    apps = named(spec, tools)
    if not apps:
        return []
    wanted = [
        tool
        for tool in tools
        if connected_tools.toolkit_of(tool) in apps
        and not connected_tools.is_read(tool)
    ]
    wanted.sort(key=lambda t: (_verb_rank(t), str(connected_tools.slug_of(t) or "")))
    return [t.tool_uuid for t in wanted]


def _with_a_draft(kept: list[str], writes: list[str], tools: list[Any]) -> list[str]:
    """Guarantee one staged write survives the cap, without reordering.

    Gmail's writes rank SEND_EMAIL, SEND_DRAFT, REPLY_TO_THREAD,
    CREATE_EMAIL_DRAFT, and ``WRITE_SLOTS`` cuts exactly above the draft. So
    every bot built from a brief got three ways to send mail and no way to
    draft one -- and on a schedule, where sending is gated and drafting is
    the only write allowed, that is a bot with no usable write at all.

    Held, not ranked -- the distinction this module already had to learn for
    writes as a class. Reordering instead would put CREATE above SEND and
    undo #344, which is why the ranking above is left exactly as it was: the
    swap happens here, at the cap, and costs the lowest-ranked write that
    made it rather than the highest.

    One is enough. An app publishes one way to draft, and a second staged
    write would cost a second send.
    """
    if not kept or any(uuid in kept for uuid in _staged_uuids(tools)):
        return kept
    staged = [uuid for uuid in writes if uuid in set(_staged_uuids(tools))]
    if not staged:
        return kept
    return kept[:-1] + [staged[0]]


def _staged_uuids(tools: list[Any]) -> list[str]:
    return [t.tool_uuid for t in tools if unattended.is_staged(t)]


def tool_uuids(spec: str, tools: list[Any]) -> list[str]:
    """Everything a brief's named apps offer, reads first and writes kept.

    ``WRITE_SLOTS`` are held rather than merely ranked below the reads. The
    two sound equivalent and are not: Gmail publishes exactly ``MAX_TOOLS``
    reads, so ordering alone gave a bot eight ways to read mail and no way
    to send one. Either side borrows what the other leaves, so an app with
    one write still fills the cap with reads, and an app with two reads
    still gets five writes.

    Reads lead the list because every brief needs to look something up and
    only some need to change anything.
    """
    reads = read_tool_uuids(spec, tools)
    writes = write_tool_uuids(spec, tools)

    keep_writes = min(len(writes), WRITE_SLOTS)
    keep_reads = min(len(reads), MAX_TOOLS - keep_writes)
    # Whatever one side left unused, the other may take.
    keep_writes = min(len(writes), MAX_TOOLS - keep_reads)

    return reads[:keep_reads] + _with_a_draft(writes[:keep_writes], writes, tools)


def attach(definition: dict[str, Any], tool_uuids: list[str]) -> dict[str, Any]:
    """Put the tools on the nodes that can call them.

    Appends rather than replaces, and never twice: a uuid a node already
    carries stays where it is. Returns the definition it was given when
    there is nothing to attach, so a brief naming no app builds exactly the
    bot it built before this existed.
    """
    if not tool_uuids:
        return definition
    for node in (definition or {}).get("nodes") or []:
        if node.get("type") not in CALLING_NODES:
            continue
        data = node.setdefault("data", {})
        existing = list(data.get("tool_uuids") or [])
        for uuid in tool_uuids:
            if uuid not in existing:
                existing.append(uuid)
        data["tool_uuids"] = existing
    return definition


__all__ = [
    "CALLING_NODES",
    "MAX_TOOLS",
    "WRITE_SLOTS",
    "attach",
    "named",
    "read_tool_uuids",
    "tool_uuids",
    "write_tool_uuids",
]
