"""The Studio conversation: one message in, agents and a website out.

The same loop as the builder (``services/agent_builder/session.py``) -- ask
the model, run the tools it asked for, feed the results back, until it
answers in prose or reaches the per-turn ceiling -- with a different brief,
a larger catalogue and a larger ceiling, because writing a site is a loop of
write, build, read the error, fix, build again.

Stateless on the server, like the builder: the transcript comes in with each
request and goes back with each reply. What would make that expensive is the
source code, so :func:`compact` takes file contents out of the transcript
once a turn is over. Every file is saved on the site, and the model can read
any of them back with ``read_site_file``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.services.agent_builder.client import IMAGES_KEY, Conversation, complete
from api.services.agent_builder.settings import BuilderModel
from api.services.aws_gateway import fallback
from api.services.billing import model_usage
from api.services.studio import tools as studio_tools

SYSTEM_PROMPT = """\
You are Decibyl Studio. From one conversation you build two things for a \
business: the agents that talk to its customers, connected to the apps the \
business already uses, and a website for them that looks as good as the best \
sites on the web.

## Agents

**Offer proven roles first.** Call `suggest_roles` with what the business \
does; a listed role has a measured outcome rate. Build from a template \
(`list_agent_templates`, `get_agent_template`, `create_agent`) when no role \
fits. A business often needs several agents -- a receptionist, a sales \
agent, a support agent -- and you can make each one.

**Ask only what you cannot infer, one question at a time.** Never invent a \
fact a caller will hear: hours, prices, names, addresses come from the user \
or are left for later.

**Connect them to everything.** Call `list_connected_apps` early. An agent \
that books should have the calendar; one that confirms should have WhatsApp \
or email; one that logs leads should have the sheet or the CRM. If an app is \
not connected, `connect_app` gives a link -- the user sees it as a button \
right here in the chat, never "go to another screen". Once it is connected, \
`list_app_actions` (prefer a written skill), `list_app_accounts` when there \
are several, then `attach_app_tool` to the agent that needs it. Say plainly \
that attached actions are on the draft and run for real once published.

**Numbers.** `list_phone_numbers` shows what the account has. You cannot buy \
one: a number is bought in Telephony.

## The website

`create_site` gives a designed starting point: React + Vite + Tailwind CSS \
v4, a theme, and finished sections (Navbar, Hero, Features, Stats, Steps, \
Testimonials, Pricing, FAQ, CTA, Contact, Footer) rendered from \
`src/site.js`. Make it this business's site, not a template:

1. `list_design_themes` and pick the theme that fits; pass it to \
`create_site` (or `apply_design_theme` later).
2. Write `src/site.js` with real copy: a headline that says what the business \
does for whom in under ten words, a lead that says why them, services named \
as the business names them. No placeholder text may survive.
3. `find_images` for a hero photo and section photos; set `hero.image` \
({src, alt}) and add each credit to `site.credits` when `needs_credit`.
4. Add what this business specifically needs as new sections in \
`src/components/sections/` -- a menu, a doctor roster, a gallery, a course \
timetable, a property grid -- and compose them in `src/App.jsx`. Remove \
sections that do not apply.
5. `build_site`; fix errors and rebuild until it succeeds.
6. `review_site_design`, judge the screenshots against its checklist, fix \
what is off, rebuild, review again -- up to three rounds. Only then give \
the user the preview link.

**Design rules.** Use the theme tokens, never raw hex: bg-brand, \
text-brand-ink (text on brand), text-ink, text-muted, bg-surface, bg-canvas, \
ring-line, font-display, font-body, rounded-card. One typeface pair, one \
button style, one card style. A clear type scale (text-4xl to 6xl \
headlines, text-lg leads, text-sm labels). Generous spacing (py-16 to py-24 \
per section, gap-6 grids). Mobile first: single column on a phone, \
grid on sm/md/lg; nothing wider than the screen; tap targets at least 44px. \
Icons from lucide-react, imported by name. Subtle motion only, through the \
Reveal component. Accessible: alt text on every image, labels on every \
field, real headings in order, visible focus.

**Packages from npm you may add** (rewrite package.json with them): \
lucide-react, motion, clsx, tailwind-merge, @radix-ui/react-* primitives, \
embla-carousel-react (carousels), react-router-dom (multi-page), recharts \
(charts), @fontsource-variable/* (fonts). Do not add a UI framework that \
fights Tailwind.

**Code rules.** ES modules and JSX in .jsx files; local imports are relative \
with the extension (./components/sections/Menu.jsx); every imported file \
must exist. Keep index.html's script tag and the decibyl-agents markers, \
vite.config.js's base "./", and src/lib/config.js as Studio writes it. \
Read a file before changing it unless you wrote it in this conversation; \
send each changed file whole.

## Agents on the site

`put_agents_on_site` puts a chat-and-voice widget on the site for the \
domains the user names -- ask for the domain, never guess. \
`connect_form_to_agent` sends the contact form to an agent with an \
instruction ("reply on WhatsApp within a minute and offer three slots"); \
make sure that agent has the actions the instruction needs. Build again \
after either.

## What you cannot do

You cannot buy numbers, publish an agent, or put the site on the user's \
domain. Say so once when it matters, and what they do instead: a number is \
bought in Telephony, an agent is published from its screen, and the site \
downloads from Studio as files any static host serves.

## Tone

Short and plain. Say what you did and what is next in a few sentences, with \
two or three suggestions for what to change. No code in replies. Match the \
user's language: if they write in Hindi or Hinglish, reply that way. Never \
claim to have done something a tool did not confirm."""


_PLACEHOLDER_TAIL = " characters, saved on the site -- read_site_file to see it]"


def _placeholder(chars: int) -> str:
    """What replaces a file's content in the transcript once a turn is over."""
    return f"[{chars}{_PLACEHOLDER_TAIL}"


def _is_placeholder(text: str) -> bool:
    return text.startswith("[") and text.endswith(_PLACEHOLDER_TAIL)


def compact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The transcript with file contents replaced by placeholders.

    Only the two places code appears: the files a ``write_site_files`` call
    carried, and the content a ``read_site_file`` result returned. Everything
    else -- what the user said, what the model replied, every other tool
    result -- is kept as it was.
    """
    out = copy.deepcopy(messages)
    for message in out:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if call.get("name") != "write_site_files":
                    continue
                arguments = call.get("arguments")
                if not isinstance(arguments, dict):
                    continue
                for item in arguments.get("files") or []:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("content")
                    if isinstance(text, str) and not _is_placeholder(text):
                        item["content"] = _placeholder(len(text))
        elif (
            message.get("role") == "tool"
            and isinstance(message.get("content"), dict)
            and IMAGES_KEY in message["content"]
        ):
            # Screenshots are for the turn that took them; the next turn
            # takes new ones if it needs to look again.
            shown = len(message["content"].get(IMAGES_KEY) or [])
            del message["content"][IMAGES_KEY]
            message["content"]["screenshots"] = (
                f"[{shown} screenshots, seen at the time]"
            )
        elif message.get("role") == "tool" and message.get("name") == "read_site_file":
            content = message.get("content")
            if isinstance(content, dict) and isinstance(content.get("content"), str):
                text = content["content"]
                if not _is_placeholder(text):
                    content["content"] = _placeholder(len(text))
    return out


#: Design reviews one turn may take: the first look and three rounds of fixes.
MAX_REVIEWS_PER_TURN = 4


@dataclass
class TurnResult:
    reply: str
    conversation: list[dict[str, Any]]
    actions: list[str] = field(default_factory=list)
    #: Agents made this turn, for the screen to link to.
    created_workflow_ids: list[int] = field(default_factory=list)
    #: The site this turn last touched, for the screen to show.
    site_id: int | None = None
    #: App-connection links the turn produced, shown as buttons in the thread.
    connect_links: list[dict[str, str]] = field(default_factory=list)


def _touched_site(name: str, arguments: dict[str, Any], result: dict[str, Any]) -> Any:
    if name == "create_site" and result.get("created"):
        return result.get("id")
    if name in studio_tools.SITE_TOOL_NAMES and "error" not in result:
        return arguments.get("site_id")
    return None


async def run_turn(
    *,
    session: AsyncSession,
    model: BuilderModel,
    organization_id: int,
    user_id: int,
    message: str,
    history: list[dict[str, Any]] | None = None,
) -> TurnResult:
    """Run one exchange. Raises ``BuilderClientError`` when the model cannot
    be reached; tool failures come back to the model as text."""
    # Compacted on the way in too: the transcript is the browser's to send,
    # and nothing it carries should cost a full file or a screenshot again.
    conversation = Conversation(messages=compact(list(history or [])))
    conversation.add_user(message)
    schemas = studio_tools.tool_schemas()
    actions: list[str] = []
    created: list[int] = []
    site_id: int | None = None
    links: list[dict[str, str]] = []
    reviews = 0

    for _ in range(constants.STUDIO_MAX_TOOL_CALLS_PER_TURN):
        with model_usage.scope(organization_id=organization_id, feature="studio"):
            reply = await complete(
                provider=model.provider,
                model=model.model,
                api_key=model.api_key,
                system=SYSTEM_PROMPT,
                conversation=conversation,
                tools=schemas,
            )
        conversation.add_assistant(reply)
        if not reply.wants_tools:
            return TurnResult(
                # Said when a backup model wrote it (aws_gateway/fallback.py).
                reply=fallback.with_note(reply.text)
                if reply.fallback_model
                else reply.text,
                conversation=compact(conversation.messages),
                actions=actions,
                created_workflow_ids=created,
                site_id=site_id,
                connect_links=links,
            )
        for call in reply.tool_calls:
            actions.append(call.name)
            arguments = call.arguments if isinstance(call.arguments, dict) else {}
            if call.name == "review_site_design":
                reviews += 1
            if call.name == "review_site_design" and reviews > MAX_REVIEWS_PER_TURN:
                # Screenshots are the most expensive thing a turn reads; past
                # this the model is polishing, and the user should see it.
                result = {
                    "error": (
                        f"{MAX_REVIEWS_PER_TURN} reviews this turn already. Show "
                        "the user the preview and ask what they would change."
                    )
                }
            else:
                result = await studio_tools.dispatch(
                    call.name,
                    arguments,
                    session=session,
                    organization_id=organization_id,
                    user_id=user_id,
                )
            if call.name == "create_agent" and result.get("created"):
                created.append(int(result["workflow_id"]))
            if call.name == "connect_app" and result.get("connect_url"):
                links.append(
                    {
                        "app": str(
                            result.get("app_name") or result.get("app") or "app"
                        ),
                        "url": str(result["connect_url"]),
                    }
                )
            touched = _touched_site(call.name, arguments, result)
            if touched is not None:
                try:
                    site_id = int(touched)
                except (TypeError, ValueError):
                    pass
            conversation.add_tool_result(call, result)

    logger.warning(
        "Studio hit the tool-call ceiling for organization {}", organization_id
    )
    return TurnResult(
        reply=(
            "That took more steps than one message allows, so I stopped here. "
            'Everything so far is saved. Say "carry on" and I will pick up '
            "where I left off."
        ),
        conversation=compact(conversation.messages),
        actions=actions,
        created_workflow_ids=created,
        site_id=site_id,
        connect_links=links,
    )


__all__ = ["SYSTEM_PROMPT", "TurnResult", "compact", "run_turn"]
