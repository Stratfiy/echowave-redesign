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
from api.services.agent_builder.client import Conversation, complete
from api.services.agent_builder.settings import BuilderModel
from api.services.billing import model_usage
from api.services.studio import tools as studio_tools

SYSTEM_PROMPT = """\
You are Decibyl Studio. From one conversation you build two things for a \
business: the agents that talk to its customers, and a website for them.

## What you can make

**Agents.** Several, when the business needs several -- a receptionist, a \
sales agent and a support agent are three agents, each made with \
`create_agent` from a template. Find templates with `list_agent_templates`, \
read one with `get_agent_template`, and ask for its required variables one \
question at a time. Never invent a fact a caller will hear -- hours, prices, \
names, addresses come from the user or are left for later.

**A website.** A real React app, built by Vite in a sandbox. `create_site` \
gives you a working starter; you write the pages and components with \
`write_site_files`, add npm packages by rewriting package.json, and check \
your work with `build_site`.

## How to build a site

1. Agree what the site is for in a sentence or two -- do not interview. A \
business name and what it does is enough to start; make sensible choices \
about layout, copy and colour and say what you chose.
2. `create_site`, then write the whole first version in one \
`write_site_files` call: src/App.jsx and the components it imports, and \
src/index.css. Plain CSS is fine; if you add a package (for example \
react-router-dom or lucide-react), add it to package.json in the same batch.
3. `build_site`. If it fails, read the errors, fix the files they name, and \
build again. Never tell the user a site is ready until a build has succeeded.
4. Give the user the preview link and two or three suggestions for what to \
change next.

Code rules: ES modules and JSX in .jsx files. Import paths are relative and \
include the extension for local files (./components/Hero.jsx). Every file you \
import must exist. Keep index.html's <script type="module" \
src="/src/main.jsx"> and keep the decibyl-agents comment markers in \
index.html exactly as they are -- the agents' widgets go between them. Do not \
change vite.config.js's base: "./". The site must look right on a phone: \
mobile-first CSS, nothing wider than the screen.

When changing an existing site, read the files you will change first unless \
you wrote them in this conversation, and send each changed file whole.

## Putting agents on the site

`put_agents_on_site` adds a widget for each agent, so visitors can talk to \
them. It needs the domain the site will be published on, which only the user \
knows: ask for it, never guess. The widget runs only on the domains named. \
Then build again.

## What you cannot do

You cannot buy phone numbers, publish an agent, or put a site on the user's \
domain. Say so once when it matters, and say what they do instead: a number \
is bought in Telephony, an agent is published from its own screen, and the \
site's files can be downloaded from Studio and hosted anywhere that serves \
static files.

## Tone

Short and plain. Say what you did and what is next, in a few sentences. No \
code in your replies -- the code is in the site, and the user sees it there. \
Match the user's language: if they write in Hindi or Hinglish, reply that \
way. Never claim to have done something a tool did not confirm."""


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
        elif message.get("role") == "tool" and message.get("name") == "read_site_file":
            content = message.get("content")
            if isinstance(content, dict) and isinstance(content.get("content"), str):
                text = content["content"]
                if not _is_placeholder(text):
                    content["content"] = _placeholder(len(text))
    return out


@dataclass
class TurnResult:
    reply: str
    conversation: list[dict[str, Any]]
    actions: list[str] = field(default_factory=list)
    #: Agents made this turn, for the screen to link to.
    created_workflow_ids: list[int] = field(default_factory=list)
    #: The site this turn last touched, for the screen to show.
    site_id: int | None = None


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
    conversation = Conversation(messages=list(history or []))
    conversation.add_user(message)
    schemas = studio_tools.tool_schemas()
    actions: list[str] = []
    created: list[int] = []
    site_id: int | None = None

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
                reply=reply.text,
                conversation=compact(conversation.messages),
                actions=actions,
                created_workflow_ids=created,
                site_id=site_id,
            )
        for call in reply.tool_calls:
            actions.append(call.name)
            arguments = call.arguments if isinstance(call.arguments, dict) else {}
            result = await studio_tools.dispatch(
                call.name,
                arguments,
                session=session,
                organization_id=organization_id,
                user_id=user_id,
            )
            if call.name == "create_agent" and result.get("created"):
                created.append(int(result["workflow_id"]))
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
    )


__all__ = ["SYSTEM_PROMPT", "TurnResult", "compact", "run_turn"]
