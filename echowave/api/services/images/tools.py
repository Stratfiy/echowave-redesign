"""``make_images``: the tool Decibyl and the image agents call.

One tool, two callers. Decibyl offers it in the thread while
``image_generation`` is on ("make me a Diwali sale poster"); an agent hired
from the Poster designer or Ad creative maker template is offered it on its
text and channel runs (``wants_images``). Both go through
``service.generate`` with the person's own words as the only ground for a
fact on the image.

The tool takes a brief, not a prompt -- see ``guard`` for why.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from api.services import images
from api.services.images import formats, service, store

TOOL_NAME = "make_images"
NAMES = frozenset({TOOL_NAME})
#: The workflow configuration key an image agent carries (set on hire).
CONFIG_KEY = "image_generation"

DESCRIPTION = (
    "Make a poster or an ad creative: 2-4 options shown on the thread as a "
    "grid, each with Download and Edit this one. Give a brief, not a prompt: "
    "the words that go on the image in business_name, headline and lines, "
    "exactly as they should read, and the visual direction in look. Every "
    "price, offer, date, time, phone number, address, web address or claim "
    "on the image must be one the person gave; anything else is refused "
    "and nothing is drawn. To change an image, pass its image_id as "
    "edit_image_id and the change as edit_instruction. If no image provider "
    "is connected, a card to choose one is put on the thread instead."
)


def tool_properties() -> dict[str, Any]:
    return {
        "business_name": {
            "type": "string",
            "description": "The business or brand name, as the person gave it.",
        },
        "headline": {
            "type": "string",
            "description": (
                "The largest text: the offer or the message, in the poster's "
                "language. Only facts the person gave."
            ),
        },
        "lines": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Other text on the image, one item per line, exactly as it "
                "should read: the offer's terms, date, phone number, address, "
                "call to action. Only what the person gave; at most 6."
            ),
        },
        "language": {
            "type": "string",
            "description": "English, Hindi, Tamil, Telugu, Kannada, Marathi, Bengali...",
        },
        "format": {
            "type": "string",
            "enum": list(formats.FORMATS),
            "description": "The shape: " + formats.describe(),
        },
        "look": {
            "type": "string",
            "description": (
                'Visual direction only: colours ("our blue", a hex code), '
                "style, mood, imagery (diyas, fresh vegetables). No words, "
                "numbers or quotes -- those go in headline and lines."
            ),
        },
        "options": {
            "type": "integer",
            "minimum": 1,
            "maximum": 4,
            "description": "How many options: 2-4 for a new design, 1 for an edit.",
        },
        "reference_image_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Image ids (img_...) of a logo or product photo the person "
                "attached, from the attachment line."
            ),
        },
        "edit_image_id": {
            "type": "string",
            "description": "The image_id of the option to change, for an edit.",
        },
        "edit_instruction": {
            "type": "string",
            "description": (
                "The change, in the person's words: 'make the headline "
                "bigger', 'Tamil version', 'use our blue'."
            ),
        },
    }


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": tool_properties(),
            "required": [],
        },
    }


RULES = (
    "\nImages (posters and ad creatives):\n"
    f"- {TOOL_NAME}: a poster, a WhatsApp status, an Instagram post or story, "
    "a Facebook ad. First have the business or brand name, what it should "
    "say (the offer or message), the language and the format; ask for "
    "whatever is missing in one short message. Put every word that goes on "
    "the image in business_name, headline and lines, and only colours, "
    "style and imagery in look. NEVER invent a price, offer, discount, date, "
    "time, phone number, address, web address or claim: one the person did "
    "not give is refused by the tool -- ask for it, or leave it off. Make 2-4 "
    "options. For a change ('make the headline bigger', 'Tamil version', "
    "'use our blue'), call it again with edit_image_id and edit_instruction. "
    "An attached logo or product photo has an image id on its attachment "
    "line: pass it in reference_image_ids. With no image provider connected "
    "the tool puts a card on the thread to choose one; never send anybody "
    "to Settings.\n"
)


def rules(organization_id: int | None) -> str:
    return RULES if images.enabled(organization_id) else ""


def schemas(organization_id: int | None) -> list[dict[str, Any]]:
    return [tool_schema()] if images.enabled(organization_id) else []


def keeps_tools(result: Any) -> bool:
    """Whether the turn may go on using its tools after this result: yes
    when nothing reached the thread (a brief turned back, an unknown image,
    a failure), no once a card or a grid is up -- the "show it, say so,
    end" rule every card follows."""
    return isinstance(result, dict) and result.get("status") in (
        "needs_facts",
        "not_found",
        "error",
        "unavailable",
    )


def wants_images(configurations: Mapping[str, Any] | None) -> bool:
    """Whether an agent was hired to make images. Only a plain ``true``."""
    if not isinstance(configurations, Mapping):
        return False
    return configurations.get(CONFIG_KEY) is True


def said_from(lines: Iterable[str]) -> str:
    """The person's own words, joined, for the guard."""
    return "\n".join(str(line) for line in lines if line)


def user_words(messages: Iterable[Any]) -> list[str]:
    """The text of every user message in an LLM context."""
    out: list[str] = []
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            out.extend(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, Mapping) and part.get("type") == "text"
            )
    return out


def attachment_note(attachments: Iterable[Mapping[str, Any]] | None) -> str:
    """The line an agent's run is told about images on the person's message,
    so it can pass their ids on: "(attached image logo.png, image id img_…)"."""
    notes = [
        f"(attached image {a.get('filename') or 'image'}, image id {a['image_uuid']})"
        for a in attachments or ()
        if isinstance(a, Mapping) and store.is_image_id(a.get("image_uuid"))
    ]
    return (" " + " ".join(notes)) if notes else ""


async def run(
    organization_id: int,
    *,
    arguments: dict[str, Any],
    said: str,
    user_id: int | None = None,
    workflow_id: int | None = None,
    workflow_run_id: int | None = None,
) -> dict[str, Any]:
    return await service.generate(
        organization_id=organization_id,
        arguments=arguments if isinstance(arguments, dict) else {},
        said=said,
        user_id=user_id,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
    )
