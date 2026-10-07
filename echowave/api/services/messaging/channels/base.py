"""What every platform adapter provides, and the shapes it passes around."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

WHATSAPP = "whatsapp"
TELEGRAM = "telegram"
SLACK = "slack"
TEAMS = "teams"
CHANNELS = (WHATSAPP, TELEGRAM, SLACK, TEAMS)

#: The verbs a card button can carry, as ``actions.settle`` names them.
VERBS = ("confirm", "decline", "undo")


@dataclass(frozen=True)
class Tap:
    """A card button pressed in the app."""

    event_id: int
    verb: str
    #: The payload version the card showed (task ledger approval binding);
    #: None from a button sent before versions existed.
    version: str | None = None


@dataclass(frozen=True)
class Inbound:
    """One message or button press from a platform, normalised."""

    channel: str
    #: Who, in the platform's terms: +E164, Telegram chat id, "T:U", ...
    external_id: str
    message_id: str
    text: str = ""
    tap: Tap | None = None
    display_name: str = ""
    #: What replying needs (Slack channel, Teams serviceUrl + conversation).
    ref: dict[str, Any] = field(default_factory=dict)
    #: Platform extras the adapter may want back (a Telegram callback id to
    #: answer, the Slack response_url).
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Card:
    """An action card as an app shows it."""

    event_id: int
    label: str
    effect: str = ""
    state: str = "proposed"
    reversible: bool = False
    #: The payload version a Confirm on this card approves.
    version: str = ""

    def buttons(self) -> list[tuple[str, str]]:
        """(verb, label) for the state the card is in."""
        if self.state == "proposed":
            return [("confirm", "Confirm"), ("decline", "Decline")]
        if self.state == "armed" or (self.state == "done" and self.reversible):
            return [("undo", "Undo" if self.state == "armed" else "Put it back")]
        return []

    def headline(self) -> str:
        status = {
            "proposed": "Needs your OK",
            "armed": "Confirmed · running in 10 s",
            "done": "Done",
            "declined": "Declined",
            "undone": "Undone",
            "cancelled": "Cancelled",
            "failed": "Failed",
        }.get(self.state, self.state.capitalize())
        return f"{status}: {self.label}"


def button_id(event_id: int, verb: str, version: str = "") -> str:
    """Short enough for Telegram's 64-byte callback_data and WhatsApp's id.

    A Confirm carries the payload version the card showed, so a press in an
    app approves exactly what that app displayed (actions.settle)."""
    if version and verb == "confirm":
        return f"card:{event_id}:{verb}:{version}"
    return f"card:{event_id}:{verb}"


def parse_button_id(value: str | None) -> Tap | None:
    parts = (value or "").split(":")
    if len(parts) not in (3, 4) or parts[0] != "card" or parts[2] not in VERBS:
        return None
    version = parts[3] if len(parts) == 4 else None
    if version is not None and not (0 < len(version) <= 32 and version.isalnum()):
        return None
    try:
        return Tap(event_id=int(parts[1]), verb=parts[2], version=version)
    except ValueError:
        return None


class Adapter(Protocol):
    name: str

    def enabled(self, organization_id: int | None = None) -> bool: ...

    async def send_text(self, ref: dict[str, Any], text: str) -> bool: ...

    async def send_card(self, ref: dict[str, Any], card: Card) -> bool: ...

    async def acknowledge(self, inbound: Inbound, note: str = "") -> None: ...
