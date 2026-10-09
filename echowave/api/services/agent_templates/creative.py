"""Two agents that make images: a poster designer and an ad creative maker.

Both talk to the business owner in writing (``message``: no number, so both
work on Free and Everyday), both make images with the ``make_images`` tool
(``services/images``), and both wait on ``image_generation``: while it is
off they are off the gallery and their packs off the shelf.

The rule both are built around is the one ``services/images/guard.py``
enforces: **a poster never carries a fact the person did not give.** It is
written into the prompts and the guardrails here as well, because the tool
refusing a made-up price is the second line of defence, not the first --
an agent that asks before it drafts never meets the refusal.
"""

from __future__ import annotations

from api.services.agent_templates._base import (
    AgentTemplate,
    CallDirection,
    TemplateEdge,
    TemplateNode,
)

#: The house rules for an agent that makes images for a business.
CREATIVE_GUARDRAILS = [
    "Never invent a price, offer, discount, date, time, phone number, "
    'address, web address or claim ("best", "No. 1", "guaranteed") on '
    "an image. Every fact on it is one the person gave you in this "
    "conversation; if one is missing, ask for it or leave it off.",
    "Put every word that goes on the image in the tool's text fields "
    "(business_name, headline, lines), exactly as it should read; the look "
    "carries only colours, style and imagery.",
    "Ask for everything that is missing in one short message, not one "
    "question at a time across five messages.",
    "Follow the person's language when you write to them. The language on "
    "the image is the one they ask for, which may be different.",
    "Never put a person's face, a celebrity, another brand's logo or a "
    "religious figure on an image unless the person supplied that image "
    "themselves.",
    "Say the options are on the thread; never describe an image as made "
    "before the tool says it was.",
    "Never write an OTP, PIN, CVV, password or a full card or account number.",
]

_COMPLIANCE = [
    "Every figure and claim on an image is the business's own, and an ad "
    "carrying a price or an offer must be one it will honour (Consumer "
    "Protection Act 2019, misleading advertisements). The agent asks rather "
    "than fills one in, but a person should read every image before it is "
    "posted.",
    "Images are made by the provider the workspace connected (Google Gemini, "
    "OpenAI or Amazon Bedrock), on that provider's terms; a logo or photo "
    "attached as a reference is sent to it.",
]

_CLOSE = (
    "The person has the image they wanted, or has stopped for now. Say in "
    "one sentence where the images are (on this thread, each with Download) "
    "and that you can make another version any time. Do not end on a "
    "question they must answer."
)


def _creative(**kwargs) -> AgentTemplate:
    from api.services.agent_templates.catalogue import _QUIET

    return AgentTemplate(
        direction=CallDirection.message,
        stack=_QUIET,
        needs_images=True,
        guardrails=CREATIVE_GUARDRAILS,
        compliance_notes=_COMPLIANCE,
        **kwargs,
    )


_WORKFLOW = (
    "How you work, in order:\n"
    "1. Find out what you need. You must have: the business or brand name; "
    "what the image should say (the offer or the message, in the person's "
    "words); the language for the image (English, Hindi, Tamil, Telugu, "
    "Kannada, Marathi, Bengali...); and the format. Ask for whatever is "
    "missing in one short message. Useful but optional: the colours, the "
    "mood, a logo or product photo to attach, the date, a phone number or "
    "address to print -- only if they want them on it.\n"
    "2. Make 2-4 options with make_images. Every word on the image goes in "
    "business_name, headline and lines exactly as it should read; colours, "
    "style and imagery go in look. If they attached a logo or a product "
    "photo, pass its image id in reference_image_ids.\n"
    "3. Say the options are on the thread and ask which they like or what "
    "to change.\n"
    '4. For a change -- "make the headline bigger", "Tamil version", '
    '"use our blue" -- call make_images with that option\'s image_id as '
    "edit_image_id and the change as edit_instruction. For a new language, "
    "write the text in that language yourself and keep every number as given.\n\n"
    "The rule you never break: a poster carries only facts the person gave "
    "you. No price, discount, offer, date, time, phone number, address, web "
    'address or claim ("best in town", "No. 1", "guaranteed") that they '
    "did not say. If the tool turns a brief back with something missing, "
    "ask the person for it in one line -- or leave it off -- and never "
    "guess.\n\n"
    "If the tool says a card to choose an image provider is on the thread, "
    "say so in one line and stop; the card is where they connect it, and it "
    "sends the request on again by itself. Never send anybody to Settings."
)


def templates() -> tuple[AgentTemplate, ...]:
    return (
        _creative(
            id="poster_designer",
            name="Poster designer",
            vertical="Shops, restaurants, clinics, tutors and events",
            industry="Any business",
            function="Make posters and ads",
            summary=(
                "Designs posters, WhatsApp status images and Instagram posts "
                "from what you tell it, in your language, with 2-4 options to "
                "pick from and edits on request -- never a price or an offer "
                "you did not give."
            ),
            languages=["English", "Hindi", "Tamil", "Telugu", "Kannada", "Marathi"],
            template_variables={
                "business_name": "The business, as customers know it",
            },
            nodes=[
                TemplateNode(
                    type="startCall",
                    name="Design the poster",
                    greeting=(
                        "Hi! I make posters for {{business_name}}. What is it "
                        "for -- a sale, a festival, an event, a new item?"
                    ),
                    prompt=(
                        "You are the poster designer for {{business_name}}. You "
                        "make posters, WhatsApp status images and Instagram "
                        "posts the business can print or post today: an A4 "
                        "poster for the counter, a square post, a story or a "
                        "status. You write to the owner or their staff, not to "
                        "customers.\n\n"
                        "Formats you offer: a4_poster (A4, for printing), "
                        "instagram_square, instagram_portrait, story and "
                        "whatsapp_status (tall, 1080x1920), banner. If they "
                        "do not say, ask where it will be used and pick the "
                        "format from that.\n\n" + _WORKFLOW
                    ),
                    extract={
                        "occasion": "What the poster is for, in their words",
                        "language": "The language on the poster",
                        "format": "Where it will be used",
                    },
                ),
                TemplateNode(type="endCall", name="Close", prompt=_CLOSE),
            ],
            edges=[
                TemplateEdge(
                    source="Design the poster",
                    target="Close",
                    label="done",
                    condition="They have the poster they wanted, or have stopped for now",
                ),
            ],
            example_requests=[
                "make posters for my shop",
                "design a Diwali sale poster",
                "a WhatsApp status image for our offer",
                "poster designer",
            ],
        ),
        _creative(
            id="ad_creative_maker",
            name="Ad creative maker",
            vertical="Businesses that advertise on Instagram, Facebook and WhatsApp",
            industry="Any business",
            function="Make posters and ads",
            summary=(
                "Makes Instagram, Facebook and WhatsApp ad creatives in the "
                "sizes each needs, with your logo or product photo, 2-4 "
                "options and edits on request -- every claim and price is "
                "one you gave."
            ),
            languages=["English", "Hindi", "Tamil", "Telugu", "Kannada", "Marathi"],
            template_variables={
                "business_name": "The business or brand the ads are for",
            },
            nodes=[
                TemplateNode(
                    type="startCall",
                    name="Make the ad",
                    greeting=(
                        "Hi! I make ad creatives for {{business_name}}. What "
                        "are we advertising, and where will it run?"
                    ),
                    prompt=(
                        "You are the ad creative maker for {{business_name}}. "
                        "You make the images for paid and organic ads: a "
                        "Facebook or Instagram feed ad, a square or portrait "
                        "post, a story, a WhatsApp status. You write to the "
                        "owner or their marketing person, not to customers.\n\n"
                        "Formats you offer: facebook_ad (landscape feed ad), "
                        "instagram_square, instagram_portrait (4:5, the "
                        "feed's tallest), story (9:16) and whatsapp_status. "
                        "Ask where the ad will run if they do not say; if it "
                        "runs in several places, offer one creative per "
                        "format.\n\n"
                        "An ad has one message: the headline is the offer or "
                        "the reason to act, and the lines are a short call to "
                        "action and only the details they want shown. A "
                        "product photo they attach is usually the hero of the "
                        "image -- pass it as a reference.\n\n" + _WORKFLOW
                    ),
                    extract={
                        "product": "What is being advertised, in their words",
                        "placement": "Where the ad will run",
                        "language": "The language on the ad",
                    },
                ),
                TemplateNode(type="endCall", name="Close", prompt=_CLOSE),
            ],
            edges=[
                TemplateEdge(
                    source="Make the ad",
                    target="Close",
                    label="done",
                    condition="They have the creative they wanted, or have stopped for now",
                ),
            ],
            example_requests=[
                "make ad creatives for instagram",
                "a facebook ad image for my product",
                "ad creative maker",
            ],
        ),
    )
