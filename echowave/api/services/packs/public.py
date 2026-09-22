"""The shelf as a stranger sees it: every listed role, readable with no account.

The in-app shelf (``routes/packs.py``) answers a signed-in user. The public
one answers somebody deciding whether to sign up at all, which is the point
Grok Bot's marketplace made in August 2026: browsing is free and open, using
is what an account buys. So everything a person needs to judge a role before
hiring it is here -- what it does, what it will ask you, which apps it needs,
what it will never do, how its work is laid out and how often it runs.

**What is deliberately not here.** The prompt text itself: publishing it is
the founder's call, not a default (22 Sept 2026), and a page once public is
cached and indexed whatever we later decide. Anything about an account --
connected apps, spend, agents -- which would be a disclosure to a stranger.
A price in rupees, until how credits charge for models is decided.

Reading only. Nothing here can hire, and nothing depends on who is asking.
"""

from __future__ import annotations

from typing import Any

from api.services.packs._base import AgentPack
from api.services.packs.derive import card, flow, hire_steps

#: A step's place in the work, as data. The page chooses the words, and a
#: role that does not ring a phone is never described as a call.
_KIND = {
    "startCall": "start",
    "agentNode": "step",
    "agent": "step",
    "endCall": "finish",
}


def outline(pack: AgentPack) -> list[dict[str, str]]:
    """The role's work in order: each step's own name and its place."""
    template = pack.template
    if template is None:
        return []
    return [
        {"name": node.name, "kind": _KIND[node.type]}
        for node in template.nodes
        if node.type in _KIND
    ]


def runs(pack: AgentPack) -> str | None:
    """How often a scheduled role works, in the words its template uses."""
    template = pack.template
    shape = getattr(template, "schedule_shape", None) if template else None
    return getattr(shape, "runs", None) or None


def detail(pack: AgentPack) -> dict[str, Any]:
    """One listed role in full, as the public page shows it."""
    template = pack.template
    return {
        "card": card(pack),
        # What the hire flow is keyed on (``/start?template=``). Not secret:
        # it names a catalogue entry, not anything of an account's.
        "template_id": pack.template_id,
        "flow": flow(pack),
        "steps": hire_steps(pack),
        "guardrails": list(template.guardrails) if template else [],
        "compliance_notes": list(template.compliance_notes) if template else [],
        "outline": outline(pack),
        "speaks": bool(template.speaks) if template else False,
        "runs": runs(pack),
    }


__all__ = ["detail", "outline", "runs"]
