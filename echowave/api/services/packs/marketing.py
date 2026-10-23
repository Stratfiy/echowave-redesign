"""Shelf entry for the marketing manager.

Not waiting on ``image_generation``: the template only *offers* images
(``offers_images``), so the pack is on the shelf whatever that switch says,
and the posters appear in the plan once it is on.

Runs on a routine and writes to the owner, so no calling channel and no
demo line. Every app is optional: the plan is useful with none connected,
and each one adds a step (the sheet, the email, the mailing list).
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

_LANGUAGES = ["en", "hi", "ta", "te", "kn", "mr", "bn", "gu"]


def packs(publisher) -> tuple[AgentPack, ...]:
    return (
        AgentPack(
            publisher=publisher,
            slug="marketing_manager",
            template_id="marketing_manager",
            name="Marketing manager",
            job="Marketing manager",
            summary=(
                "Every Monday, plans and writes the week's posts with "
                "captions, hashtags, posting times and posters, around the "
                "festivals coming up, plus one promotion message ready to "
                "approve -- never an offer you did not give."
            ),
            languages=_LANGUAGES,
            channels=[Channel.SCHEDULED, Channel.WEB, Channel.EMAIL],
            listed=True,
            required_facts=[
                _BUSINESS_NAME,
                RequiredFact(
                    key="what_you_sell",
                    question="What do you sell, and what are you known for?",
                    kind=FactKind.LONG_TEXT,
                    example="Sweets and namkeen; famous for our kaju katli",
                    used_for="What every post is about.",
                ),
                RequiredFact(
                    key="customers",
                    question="Who are your customers, and where are they?",
                    kind=FactKind.LONG_TEXT,
                    example="Families in Anna Nagar, Chennai; office orders at Diwali",
                    used_for="Who the posts speak to, and the local hashtags.",
                ),
                RequiredFact(
                    key="channels",
                    question="Where do you post?",
                    kind=FactKind.LIST,
                    example="Instagram, Facebook, WhatsApp status, Google Business Profile",
                    used_for="The channels it plans posts for, and only those.",
                ),
                RequiredFact(
                    key="post_language",
                    question="Which language should the posts be in?",
                    example="Tamil and English",
                    used_for="The language of every caption and message.",
                ),
                RequiredFact(
                    key="plan_sheet",
                    question="Which Google Sheet should it write each week's posts to?",
                    example="Marketing plan 2026",
                    required=False,
                    used_for="One row per post, so you can tick them off.",
                ),
            ],
            required_connectors=[
                RequiredConnector(
                    app="googlesheets",
                    label="Google Sheets",
                    used_for="Writing each week's posts to your sheet.",
                    required=False,
                ),
                RequiredConnector(
                    app="gmail",
                    label="Gmail",
                    used_for="Sending the promotion email, after you approve it.",
                    required=False,
                ),
                RequiredConnector(
                    app="mailchimp",
                    label="Mailchimp",
                    used_for="Sending the promotion to your mailing list, after you approve it.",
                    required=False,
                ),
            ],
        ),
    )


__all__ = ["packs"]
