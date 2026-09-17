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

from api.services.workflow import connected_tools

#: How many tools one brief may attach. A bot handed thirty tools chooses
#: badly among them, and no brief names enough apps to need more.
MAX_TOOLS = 8

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
    """The write tools of every app the brief names.

    Same choosing as the reads, and the same cap over the pair: a brief
    naming one app should not hand a bot sixteen tools because half of them
    change something.
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
    wanted.sort(key=lambda t: str(connected_tools.slug_of(t) or ""))
    return [t.tool_uuid for t in wanted]


def tool_uuids(spec: str, tools: list[Any]) -> list[str]:
    """Everything a brief's named apps offer: the reads, then the writes.

    Reads first so that under the cap a bot keeps the ability to look things
    up, which every brief needs, over the ability to change them, which only
    some do.
    """
    reads = read_tool_uuids(spec, tools)
    room = max(0, MAX_TOOLS - len(reads))
    return reads + write_tool_uuids(spec, tools)[:room]


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
    "attach",
    "named",
    "read_tool_uuids",
    "tool_uuids",
    "write_tool_uuids",
]
