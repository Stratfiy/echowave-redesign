"""An app that is connected brings its tools with it.

Connecting Gmail used to give a business nothing it could point a bot at.
The authorization was real, the app showed as connected, and the Tools
screen was still empty: somebody had to know that a *tool* is a separate
row, open the builder, and attach one action at a time by slug. Most
people connected an app, saw no change anywhere, and concluded it had not
worked.

So the tools are made here, from the app's own catalogue, the first time
we see it connected. One row per action, org-scoped, named and described
from what the vendor says the action does -- which is also what the
Integrations screen now lists under each app, one line each.

**Bounded, and chosen.** ``MAX_PER_APP`` rows at most: some toolkits
expose hundreds of actions and a business wants four hundred tools no
more than it wants none.

This used to say the catalogue returned them "in the vendor's own order,
most-used first", and take the first dozen on that basis. It does not.
They come back **alphabetically**, and the first twelve of Gmail's sixty
were:

    ADD_LABEL, BATCH_DELETE, BATCH_MODIFY, CREATE_DRAFT, CREATE_FILTER,
    CREATE_LABEL, DELETE_DRAFT, DELETE_FILTER, DELETE_LABEL,
    DELETE_MESSAGE, DELETE_THREAD, FETCH_EMAILS

Seven ways to delete mail, one way to read it -- and that one only
because ``FETCH`` sorts twelfth. No ``SEND_EMAIL``, no reply, no search.
A founder who connected Gmail and asked his bot to check his email was
one alphabetical position away from it not working at all, and could not
have sent one however he asked.

So the dozen is ranked here rather than taken on faith: reads first,
because they are what people ask for and they are safe; then the ordinary
writes; destructive actions last, because a bot that can delete a mailbox
twelve ways and not send a message has the tools exactly backwards. The
builder can still attach anything else by slug.

**Never twice.** Existing rows are read first and matched on the action
slug, so a second sync after a reconnect adds only what is missing. A row
somebody renamed or deleted on purpose is left alone: this creates, it
does not reconcile downward.

**Never raises.** Syncing is something that happens on the way to
somewhere else -- a screen loading, a card being pressed. A vendor that
will not answer must not take the screen down with it, so a failure is
logged and reported in the return, and the caller carries on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import ToolCategory, ToolStatus
from api.schemas.tool import CreateToolRequest
from api.services.integrations.composio.client import toolkit_actions
from api.services.tool_management import create_tool_for_user

#: How many of an app's actions become tools on their own.
#:
#: Twelve because that is what a person will read down the Integrations
#: card without it becoming a directory, and because the actions past the
#: first dozen of any toolkit are the administrative ones nobody asks a
#: bot for. Anything else is still attachable by slug in the builder.
MAX_PER_APP = 12

#: How many of the vendor's actions to rank before taking ``MAX_PER_APP``.
#:
#: The cap used to be passed to the vendor as the page size, so only twelve
#: were ever fetched and the choice was whatever the first page held. A
#: ranking cannot improve on a sample it never sees.
ACTIONS_CONSIDERED = 200

#: Second words that mean the action only reads. Duplicated deliberately
#: rather than imported from ``services/workflow/connected_tools``: that
#: module decides whether a tool needs a confirmation card at run time, and
#: this one decides what to offer at all. Tying them together would mean a
#: change to the safety rule silently reshaped everybody's tool list.
_READ_VERBS = frozenset(
    {"GET", "FETCH", "LIST", "SEARCH", "FIND", "READ", "LOOKUP", "COUNT"}
)

#: Second words that destroy something. Offered last, never first.
_DESTRUCTIVE_VERBS = frozenset(
    {"DELETE", "REMOVE", "TRASH", "PURGE", "DESTROY", "ARCHIVE", "CLEAR"}
)

#: The ordinary writes a person actually asks a bot for, best first. Ranked
#: above other writes so "send an email" survives a cap of twelve.
_WANTED_VERBS: tuple[str, ...] = (
    "SEND",
    "REPLY",
    "CREATE",
    "ADD",
    "UPDATE",
    "MODIFY",
    "MOVE",
    "SCHEDULE",
)


def _verb(slug: str) -> str:
    """The action word of a Composio slug: GMAIL_SEND_EMAIL -> SEND."""
    parts = [p for p in slug.upper().split("_") if p]
    return parts[1] if len(parts) > 1 else ""


def rank(action: dict[str, Any]) -> tuple[int, int]:
    """Where an action sits in the dozen. Lower is offered sooner.

    The second element keeps the vendor's order stable inside a band, so
    the choice is deterministic and a re-sync does not churn the list.
    """
    verb = _verb(str(action.get("slug") or ""))
    if verb in _DESTRUCTIVE_VERBS:
        return (3, 0)
    if verb in _READ_VERBS:
        return (0, 0)
    if verb in _WANTED_VERBS:
        return (1, _WANTED_VERBS.index(verb))
    return (2, 0)


def most_useful(actions: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """The ``limit`` actions worth offering, best first."""
    ordered = sorted(enumerate(actions), key=lambda p: (rank(p[1]), p[0]))
    return [action for _, action in ordered[:limit]]


#: How long a generated description may be. The vendor's own sentence,
#: clipped -- it is read by a person on a card and by a model choosing a
#: tool, and both are better served by one line than by three.
MAX_DESCRIPTION = 200


@dataclass(frozen=True)
class Synced:
    """What one sync did, in the words the caller reports."""

    app: str
    created: int
    existing: int
    #: Set when the app's action list could not be read. The distinction is
    #: the one ``toolkit_actions`` draws and it matters here too: no tools
    #: because the app exposes none is a fact, no tools because the vendor
    #: timed out is a retry.
    error: str | None = None

    @property
    def total(self) -> int:
        return self.created + self.existing


def action_words(slug: str) -> str:
    """One action's slug as a person reads it.

    ``GMAIL_SEND_EMAIL`` is how the vendor names it and not how anybody
    says it. The app prefix is dropped -- the app's name is already above
    the list -- and what is left is title-cased: "Send Email". Falls back
    to the slug itself rather than to an empty string: a row with no name
    is the silent kind of missing.
    """
    raw = str(slug or "").strip()
    tail = raw.split("_", 1)[1] if "_" in raw else raw
    words = " ".join(part.capitalize() for part in tail.split("_") if part)
    return words or raw


def _name(app_name: str, action: dict[str, Any]) -> str:
    """A tool's display name: the app, then the action in words."""
    words = action_words(str(action.get("slug") or ""))
    return f"{app_name} — {words}"[:255]


async def existing_slugs(organization_id: int, app: str) -> set[str]:
    """The action slugs this organisation already has a tool row for."""
    from api.services.workflow import connected_tools

    rows = await db_client.get_tools_for_organization(
        organization_id, status=ToolStatus.ACTIVE.value
    )
    return {
        slug
        for row in rows
        if connected_tools.toolkit_of(row) == app.strip().lower()
        and (slug := connected_tools.slug_of(row)) is not None
    }


async def ensure_tools(
    *, organization_id: int, app: str, app_name: str, actor: Any
) -> Synced:
    """Make the missing tool rows for one connected app.

    ``actor`` is the real user the rows are created as, because
    ``create_tool_for_user`` reads the organisation off it: passing a
    stand-in carrying an id we chose would be us asserting the scoping
    rather than it being checked.
    """
    slug = app.strip().lower()
    if not slug:
        return Synced(app=app, created=0, existing=0, error="No app named.")
    if getattr(actor, "selected_organization_id", None) != organization_id:
        # The one case worth refusing rather than logging: rows created as
        # somebody else's organisation would be invisible here and wrong
        # there.
        return Synced(
            app=slug,
            created=0,
            existing=0,
            error="That account could not be confirmed.",
        )

    try:
        actions = await toolkit_actions(slug, limit=ACTIONS_CONSIDERED)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not list {} actions to sync tools: {}", slug, exc)
        actions = None
    if actions is None:
        return Synced(
            app=slug,
            created=0,
            existing=0,
            error=f"Could not read what {app_name} can do just now.",
        )

    try:
        have = await existing_slugs(organization_id, slug)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read existing {} tools: {}", slug, exc)
        return Synced(
            app=slug, created=0, existing=0, error="Could not read the tools you have."
        )

    created = 0
    for action in most_useful(actions, MAX_PER_APP):
        action_slug = str(action.get("slug") or "").strip()
        if not action_slug or action_slug in have:
            continue
        try:
            await create_tool_for_user(
                CreateToolRequest(
                    name=_name(app_name, action),
                    description=(action.get("does") or "")[:MAX_DESCRIPTION] or None,
                    category=ToolCategory.COMPOSIO.value,
                    definition={
                        "type": "composio",
                        "config": {"toolkit": slug, "tool_slug": action_slug},
                    },
                ),
                actor,
                source="connector_sync",
            )
        except Exception as exc:  # noqa: BLE001
            # One action failing is one tool missing, not a failed sync: the
            # other eleven are worth having and the screen must still load.
            logger.warning("Could not create a tool for {}: {}", action_slug, exc)
            continue
        created += 1
        have.add(action_slug)
    return Synced(app=slug, created=created, existing=len(have) - created)


async def toolkits_with_rows(organization_id: int) -> set[str]:
    """The apps this organisation already has at least one tool row for.

    The counterpart to :func:`existing_slugs`, which answers the same
    question for one app. Used to spot a connected app whose rows were never
    made -- the app works, the bot cannot see it, and nothing on any screen
    says so.
    """
    from api.services.workflow import connected_tools

    rows = await db_client.get_tools_for_organization(
        organization_id, status=ToolStatus.ACTIVE.value
    )
    return {
        toolkit
        for row in rows
        if (toolkit := connected_tools.toolkit_of(row)) is not None
    }
