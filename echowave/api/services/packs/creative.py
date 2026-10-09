"""Shelf entries for the poster designer and the ad creative maker.

Both wait on ``image_generation`` (``requires_feature``): the image tool they
work with exists only while it is on, and a designer listed without it
could be hired and would have nothing to draw with.

No calling channel and no app to connect: the image provider is chosen on a
card in the agent's own thread the first time an image is asked for, so
nothing is asked at hire beyond the business's name.
"""

from __future__ import annotations

from api.services.packs._base import AgentPack, Channel, RequiredFact

FEATURE = "image_generation"
_LANGUAGES = ["en", "hi", "ta", "te", "kn", "mr"]

_BUSINESS_NAME = RequiredFact(
    key="business_name",
    question="What is your business called?",
    example="Narayani Sweets",
    used_for="The name it designs for, and how it introduces itself.",
)


def packs(publisher) -> tuple[AgentPack, ...]:
    def pack(**kwargs) -> AgentPack:
        return AgentPack(
            publisher=publisher,
            template_id=kwargs["slug"],
            languages=_LANGUAGES,
            channels=[Channel.WEB, Channel.WHATSAPP],
            required_facts=[_BUSINESS_NAME],
            listed=True,
            requires_feature=FEATURE,
            **kwargs,
        )

    return (
        pack(
            slug="poster_designer",
            name="Poster designer",
            job="Graphic designer",
            summary=(
                "Designs posters, WhatsApp status images and Instagram posts "
                "in your language, with options to pick from and edits on "
                "request -- never a price or an offer you did not give."
            ),
        ),
        pack(
            slug="ad_creative_maker",
            name="Ad creative maker",
            job="Ad designer",
            summary=(
                "Makes Instagram, Facebook and WhatsApp ad creatives in the "
                "sizes each needs, with your logo or product photo, and edits "
                "on request -- every claim and price is one you gave."
            ),
        ),
    )
