"""Decibyl's own WhatsApp sender.

One Meta WhatsApp Business account, registered by Decibyl, sends on behalf of
every customer's agents — the way a managed phone number does for calls. A
clinic that wants "your appointment is confirmed" on WhatsApp gets it without
a Meta business verification of its own, and Decibyl bills the message as one
line. Configured by environment; absent means the WhatsApp channel falls back
to the account's own Twilio sender, which is what it did before this existed.
"""

from __future__ import annotations

from typing import Any

from api import constants

PROVIDER = "meta_whatsapp"


def is_configured() -> bool:
    return bool(
        constants.WHATSAPP_ACCESS_TOKEN.strip()
        and constants.WHATSAPP_PHONE_NUMBER_ID.strip()
    )


def credentials() -> dict[str, Any]:
    return {
        "access_token": constants.WHATSAPP_ACCESS_TOKEN.strip(),
        "phone_number_id": constants.WHATSAPP_PHONE_NUMBER_ID.strip(),
        "graph_version": constants.WHATSAPP_GRAPH_VERSION.strip() or "v21.0",
    }
