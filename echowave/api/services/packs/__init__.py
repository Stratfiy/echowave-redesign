"""Hirable agents: the format they are published in, and the shelf of ours.

Public surface is small on purpose. A caller either browses the shelf, reads
one pack, or asks what a pack costs and what hiring it involves -- and the last
two are computed, never stored, so there is nowhere for a card and an invoice
to drift apart.
"""

from api.services.packs._base import (
    CALLING_CHANNELS,
    AgentPack,
    Channel,
    FactKind,
    Publisher,
    RequiredConnector,
    RequiredFact,
)
from api.services.packs.catalogue import (
    all_packs,
    get_pack,
    jobs,
    listed_packs,
    resolve_listed_packs,
    resolve_pack,
    resolve_packs,
)
from api.services.packs.derive import (
    FLOW_STANDARD,
    FLOW_VOICE,
    UNIT_MINUTE,
    UNIT_RUN,
    badges,
    blank_flow,
    card,
    charging,
    flow,
    hire_steps,
    is_calling,
)

__all__ = [
    "AgentPack",
    "CALLING_CHANNELS",
    "FLOW_STANDARD",
    "FLOW_VOICE",
    "Channel",
    "FactKind",
    "Publisher",
    "RequiredConnector",
    "RequiredFact",
    "all_packs",
    "badges",
    "blank_flow",
    "card",
    "flow",
    "get_pack",
    "hire_steps",
    "is_calling",
    "jobs",
    "listed_packs",
    "UNIT_MINUTE",
    "UNIT_RUN",
    "charging",
    "resolve_listed_packs",
    "resolve_pack",
    "resolve_packs",
]
