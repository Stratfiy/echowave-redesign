"""Launch stream `reach`: outside AI tools and ordering, from Chat.

See ``REACH.md`` at the repository root for the whole picture. Three
switches, each off by default (``services/features.py``):

* ``outside_tools`` -- a person connects an outside AI tool (an MCP server)
  in the thread, with a connect chip, and Decibyl can use it from Chat.
  Reads run as Decibyl answers; anything else is a card the person confirms.
* ``ordering`` -- food and groceries from a list in Chat, through each
  app's official server (Zomato first; Swiggy once Builders Club access is
  granted). Always an order card showing the items, every charge, the
  total, the address and how it is paid, before anything is placed.
* ``price_compare`` -- prices and coupons compared only across the ordering
  apps the person has connected, saying which were compared and when.

Everything is the person's: their connections, their drafts, their cards.
A colleague in the same workspace cannot list, use, confirm or revoke them.
"""

from __future__ import annotations

from api.services import features

OUTSIDE_TOOLS = "outside_tools"
ORDERING = "ordering"
PRICE_COMPARE = "price_compare"


def enabled(flag: str, organization_id: int | None = None) -> bool:
    return features.is_on(flag, organization_id)


__all__ = ["ORDERING", "OUTSIDE_TOOLS", "PRICE_COMPARE", "enabled"]
