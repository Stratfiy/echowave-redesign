"""Make the tool rows for an app that is connected but has none.

Connecting Gmail worked: the authorization completed, the Integrations
screen said Connected, and the catalogue said "9 tools". The bot still could
not use it, because nothing had created the ``tools`` rows the engine and
Decibyl both read -- those were made only when somebody expanded the "9
tools" disclosure on the Integrations screen. Nobody has to know that, and
the founder did not: he connected Gmail and reported that the agent could
not fetch the tools.

The route that makes the rows has always said it is "called when a sign-in
finishes, so connecting an app is the whole job". This is the part that
makes that sentence true, from whichever screen the app was connected on and
whether or not anybody came back to the tab.

Safe to run repeatedly: ``ensure_tools`` creates only what is missing.
"""

from __future__ import annotations

from loguru import logger

from api.db import db_client
from api.services.integrations.composio import tool_sync
from api.services.integrations.composio.client import toolkit_name


async def _actor_for(organization_id: int, user_id: int | None):
    """A real user of this organisation, to create the rows as.

    ``ensure_tools`` reads the organisation off the user it is handed and
    refuses a mismatch, so the scoping is checked there rather than asserted
    here -- which means a stand-in carrying an id we chose will not do.

    The caller's own user is preferred and confirmed. When there is no
    caller -- the chat noticing the gap has a workspace, not a person at a
    keyboard -- any member of the organisation whose selected organisation
    is this one will do: the rows belong to the account, not to whoever
    happened to trigger the sync.
    """
    if user_id:
        user = await db_client.get_user_by_id(user_id)
        if user is not None and user.selected_organization_id == organization_id:
            return user
        # Not an error: the person may have switched organisation or left
        # between the read and this job running. Fall through to a member.
        logger.info(
            "Tool sync for org {}: user {} no longer confirms it, using a member",
            organization_id,
            user_id,
        )
    try:
        members = await db_client.get_organization_users(organization_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read members of org {}: {}", organization_id, exc)
        return None
    for member in members:
        if member.selected_organization_id == organization_id:
            return member
    return None


async def sync_missing_tools(
    _ctx=None, *, organization_id: int, apps: list[str], user_id: int | None = None
) -> dict[str, int]:
    """Create the missing rows for each named app.

    ``user_id`` is the person who triggered it, when there was one. The
    chat path has no person -- it notices the gap while answering -- so it
    passes none and a member of the organisation is used instead.
    """
    created = 0
    failed = 0
    user = await _actor_for(organization_id, user_id)
    if user is None:
        logger.warning(
            "Skipping tool sync for org {}: no member confirms it", organization_id
        )
        return {"created": 0, "failed": 0}

    for app in apps:
        try:
            name = await toolkit_name(app) or app
            result = await tool_sync.ensure_tools(
                organization_id=organization_id,
                app=app,
                app_name=name,
                actor=user,
            )
        except Exception as exc:  # noqa: BLE001 - one app failing is not all
            logger.warning(
                "Could not sync {} tools for org {}: {}", app, organization_id, exc
            )
            failed += 1
            continue
        if result.error:
            logger.warning(
                "Could not sync {} tools for org {}: {}",
                app,
                organization_id,
                result.error,
            )
            failed += 1
            continue
        created += result.created
        # A bot built before this app was connected has been waiting for it
        # (bot_from_brief.attach_waiting): now it gets its tools.
        try:
            from api.services.workflow import bot_from_brief

            await bot_from_brief.attach_waiting(
                organization_id=organization_id, app=app
            )
        except Exception as exc:  # noqa: BLE001 - the rows are made; that is the job
            logger.warning("Could not hand {} to the bots waiting on it: {}", app, exc)

    if created:
        logger.info(
            "Made {} tool rows for org {} across {} app(s)",
            created,
            organization_id,
            len(apps),
        )
    return {"created": created, "failed": failed}
