"""The life-stage roles promoted from the drafts (8 Oct 2026).

One so far: Daily Check-in, promoted from ``packs/drafts/daily_wellness_checkin``.
The draft was an outbound call for a care agency, which needed a number before
it could do anything. The role on the shelf checks in by message on a routine
and calls only where a line exists (``CallStep`` on its template), so it
declares no calling channel: no seat, no demo line, and it is listed on Free.

The other life-stage roles are templates on the gallery
(``agent_templates.life_stages``) and are not packs yet.
"""

from __future__ import annotations

from api.services.packs._base import (
    AgentPack,
    Channel,
    FactKind,
    RequiredConnector,
    RequiredFact,
)


def packs(publisher) -> tuple[AgentPack, ...]:
    return (
        AgentPack(
            slug="daily_checkin",
            name="Daily Check-in",
            job="Care companion",
            summary=(
                "Checks in every day with three short questions, and tells the "
                "family member you chose if there is no answer or something "
                "sounds wrong."
            ),
            publisher=publisher,
            channels=[Channel.SCHEDULED, Channel.WHATSAPP],
            template_id="daily_checkin",
            industries=["Personal", "Home care and senior care"],
            languages=["en", "hi", "ta", "te", "kn", "mr", "bn"],
            required_facts=[
                RequiredFact(
                    key="person_name",
                    question="What do they like to be called?",
                    example="Amma",
                    used_for="How every check-in greets them.",
                ),
                RequiredFact(
                    key="checkin_time",
                    question="When should it check in each day?",
                    example="9:30 in the morning",
                    used_for="When the daily check-in arrives.",
                ),
                RequiredFact(
                    key="family_contact",
                    question=(
                        "Who should be told if something is wrong, and on which "
                        "WhatsApp number? Only with their agreement."
                    ),
                    kind=FactKind.LONG_TEXT,
                    example="Priya (daughter), +91 98400 12345",
                    used_for=(
                        "Who hears about a missed check-in or a worrying answer. "
                        "It asks them to agree before the first check-in."
                    ),
                ),
            ],
            required_connectors=[
                RequiredConnector(
                    app="whatsapp",
                    label="WhatsApp",
                    used_for="Sending the check-in, and telling the family member.",
                    required=False,
                ),
            ],
            # Nothing to hear: it answers no phone, so nothing to wait for.
            listed=True,
        ),
    )


__all__ = ["packs"]
