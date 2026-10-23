"""Shelf entry for the inventory alert.

Runs on a routine and writes to one person on the team, so no calling
channel and no demo line. Google Sheets and Gmail are both needed for the
job: without the sheet there is nothing to check, without Gmail nobody
hears about it.
"""

from __future__ import annotations

from api.services.packs._base import (
    AgentPack,
    Channel,
    FactKind,
    RequiredConnector,
    RequiredFact,
)
from api.services.packs.creative import _BUSINESS_NAME


def packs(publisher) -> tuple[AgentPack, ...]:
    return (
        AgentPack(
            publisher=publisher,
            slug="inventory_alert",
            template_id="inventory_alert",
            name="Inventory alert",
            job="Stores assistant",
            summary=(
                "Checks your inventory Google Sheet every morning and emails "
                "the person in charge when an item falls to its reorder "
                "level: what is short, by how much, and who supplies it."
            ),
            languages=["en", "hi"],
            channels=[Channel.SCHEDULED, Channel.EMAIL],
            listed=True,
            required_facts=[
                _BUSINESS_NAME,
                RequiredFact(
                    key="inventory_sheet",
                    question="Which Google Sheet is the stock kept in?",
                    example="Inventory 2026, Stock tab",
                    used_for="The only place it reads stock from. It never changes it.",
                ),
                RequiredFact(
                    key="default_threshold",
                    question="At what stock level is an item running short?",
                    kind=FactKind.NUMBER,
                    example="10",
                    used_for=(
                        "The limit for every item without its own reorder "
                        "level in the sheet."
                    ),
                ),
                RequiredFact(
                    key="alert_to",
                    question="Who should get the alert email?",
                    example="Priya Sharma, priya@yourcompany.com",
                    used_for="The one person it emails what is short.",
                ),
            ],
            required_connectors=[
                RequiredConnector(
                    app="googlesheets",
                    label="Google Sheets",
                    used_for="Reading the stock, every morning.",
                ),
                RequiredConnector(
                    app="gmail",
                    label="Gmail",
                    used_for="Emailing the alert to the person in charge.",
                ),
            ],
        ),
    )


__all__ = ["packs"]
