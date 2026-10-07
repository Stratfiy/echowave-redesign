"""The ordering apps, behind one interface.

Each app is reached only through its own official server -- Zomato's hosted
MCP server, and Swiggy's through Builders Club once the founder has access.
No scraping, no unofficial API, no stored card: the apps take payment on
their own side (Zomato's server hands back a UPI payment step), and what we
keep about payment is the method's name as the app labels it, masked.

An app is one of three things to a person, said in these words everywhere:

* **needs setup** -- Decibyl does not have access yet (no server address
  configured, or for Swiggy, no Builders Club access). Nothing can be
  connected and nothing pretends otherwise;
* **available** -- the person can connect it from the chip in the thread;
* **connected** -- this person signed in, and the server offers every
  operation ordering needs. A server missing one is an error that names it.

**Operations.** Ordering needs four things from a server: search, a quoted
cart, placing it, and (optionally) saved addresses, offers and order
status. Servers name their tools differently, so each operation lists the
names it answers to, and the first one the server actually offers is used.
The Zomato names below are taken from Zomato's published server and must be
checked against it once access is granted (REACH.md, "Needs a human"); a
mismatch shows as "needs setup", never as a wrong order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from api import constants

SEARCH = "search"
ADDRESSES = "addresses"
QUOTE = "quote"
PLACE = "place"
OFFERS = "offers"
STATUS = "status"

REQUIRED = (SEARCH, QUOTE, PLACE)

AVAILABLE = "available"
NEEDS_SETUP = "needs_setup"
CONNECTED = "connected"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    #: What can be ordered: "food", "groceries".
    kinds: tuple[str, ...]
    url_setting: str
    client_id_setting: str
    #: Why it needs setup, in a sentence, while it does.
    setup_reason: str
    tool_names: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def url(self) -> str | None:
        return getattr(constants, self.url_setting, None) or None

    def client_id(self) -> str | None:
        return getattr(constants, self.client_id_setting, None) or None

    def state(self) -> tuple[str, str | None]:
        """Whether Decibyl can reach this app at all, before any person."""
        if not self.url():
            return NEEDS_SETUP, self.setup_reason
        return AVAILABLE, None

    def tool_for(self, operation: str, offered: set[str]) -> str | None:
        for name in self.tool_names.get(operation, ()):
            if name in offered:
                return name
        return None

    def missing(self, offered: set[str]) -> list[str]:
        return [op for op in REQUIRED if self.tool_for(op, offered) is None]


_COMMON = {
    SEARCH: ("search_items", "search"),
    ADDRESSES: ("get_saved_addresses", "list_addresses", "get_addresses"),
    QUOTE: ("quote_cart", "create_cart", "update_cart"),
    PLACE: ("place_order", "checkout"),
    OFFERS: ("get_offers", "list_offers", "get_coupons"),
    STATUS: ("get_order_status", "track_order", "get_order"),
}

ZOMATO = Provider(
    key="zomato",
    name="Zomato",
    kinds=("food",),
    url_setting="ZOMATO_MCP_URL",
    client_id_setting="ZOMATO_OAUTH_CLIENT_ID",
    setup_reason=(
        "Ordering on Zomato is still being set up on Decibyl's side, so it "
        "cannot be connected yet."
    ),
    tool_names={
        SEARCH: (
            "get_restaurants_for_keyword",
            "getRestaurantsForKeyword",
            "search_restaurants",
            "searchRestaurants",
            *_COMMON[SEARCH],
        ),
        ADDRESSES: (
            "get_saved_addresses_for_user",
            "getSavedAddresses",
            *_COMMON[ADDRESSES],
        ),
        QUOTE: ("create_cart", "createCart", "addToCart", *_COMMON[QUOTE]),
        PLACE: ("checkout_cart", "checkout", *_COMMON[PLACE]),
        OFFERS: ("get_offers", "getOffers", *_COMMON[OFFERS]),
        STATUS: ("get_order_status", "getOrderStatus", *_COMMON[STATUS]),
    },
)

SWIGGY = Provider(
    key="swiggy",
    name="Swiggy",
    kinds=("food", "groceries"),
    url_setting="SWIGGY_MCP_URL",
    client_id_setting="SWIGGY_OAUTH_CLIENT_ID",
    setup_reason=(
        "Swiggy needs Builders Club access, which Decibyl is waiting for, so "
        "it cannot be connected yet."
    ),
    tool_names=dict(_COMMON),
)

#: Every app, in the order they are offered. Swiggy is listed while it needs
#: setup, so a person asking for it hears why rather than nothing.
PROVIDERS: dict[str, Provider] = {p.key: p for p in (ZOMATO, SWIGGY)}


def get(key: str) -> Provider | None:
    return PROVIDERS.get(str(key or "").strip().lower())


def describe(provider: Provider, connection: Any | None) -> dict[str, Any]:
    """The app's state for one person, as the chip and the model see it."""
    state, reason = provider.state()
    if state == AVAILABLE and connection is not None:
        if connection.status == "connected":
            offered = {t.get("name") for t in connection.tools or []}
            missing = provider.missing(offered)
            if missing:
                state = UNAVAILABLE
                reason = (
                    f"{provider.name}'s server does not offer "
                    f"{', '.join(missing)} yet, so ordering cannot go through it."
                )
            else:
                state = CONNECTED
        elif connection.status == "error":
            reason = connection.last_error or "The last connection attempt failed."
    return {
        "provider": provider.key,
        "name": provider.name,
        "kinds": list(provider.kinds),
        "state": state,
        "reason": reason,
        "connection_id": getattr(connection, "uuid", None),
    }


__all__ = [
    "ADDRESSES",
    "AVAILABLE",
    "CONNECTED",
    "NEEDS_SETUP",
    "OFFERS",
    "PLACE",
    "PROVIDERS",
    "Provider",
    "QUOTE",
    "REQUIRED",
    "SEARCH",
    "STATUS",
    "SWIGGY",
    "UNAVAILABLE",
    "ZOMATO",
    "describe",
    "get",
]
