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


async def sync_missing_tools(
    _ctx=None, *, organization_id: int, apps: list[str], user_id: int
) -> dict[str, int]:
    """Create the missing rows for each named app, as the given user.

    ``user_id`` rather than a stand-in actor because ``ensure_tools`` reads
    the organisation off the user it is handed and refuses a mismatch -- the
    scoping is checked there rather than asserted here.
    """
    created = 0
    failed = 0
    user = await db_client.get_user_by_id(user_id)
    if user is None or user.selected_organization_id != organization_id:
        # Not an error worth raising: the person may have switched
        # organisation or left between the read and this job running. The
        # next read of the catalogue enqueues it again.
        logger.info(
            "Skipping tool sync for org {}: user {} no longer confirms it",
            organization_id,
            user_id,
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

    if created:
        logger.info(
            "Made {} tool rows for org {} across {} app(s)",
            created,
            organization_id,
            len(apps),
        )
    return {"created": created, "failed": failed}
