"""Email a business its memory as an Obsidian vault. See
services/knowledge_graph/export.py."""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger


async def export_memory(_ctx, organization_id: int, user_id: int) -> bool:
    from api.db import db_client
    from api.enums import AgentEventActor, AgentEventKind
    from api.services.knowledge_graph import export
    from api.services.messaging.email import send_email
    from api.services.workflow import agent_timeline

    user = await db_client.get_user_by_id(int(user_id))
    email = str(getattr(user, "email", "") or "").strip()
    if not user or not email or user.selected_organization_id != int(organization_id):
        logger.warning(
            "Memory export for org {} has nobody to send to", organization_id
        )
        return False
    organization = await db_client.get_organization_by_id(int(organization_id))
    business = str(getattr(organization, "name", "") or "This business")

    snap = await export.snapshot(int(organization_id))
    data = export.obsidian_zip(snap, business_name=business)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    filename = f"{export._file_stem(business)} memory {stamp}.zip"
    result = await send_email(
        to=email,
        subject=f"Your memory export from Decibyl ({stamp})",
        body_text=(
            "Attached is everything this business has taught its workers, as a "
            "folder of linked notes. Unzip it and open the folder in Obsidian "
            "(free) or any Markdown editor; every link works.\n\n"
            f"{len(snap.entities)} people and things, {len(snap.relations)} "
            f"connections, {len(snap.episodes)} conversations, "
            f"{len(snap.records)} remembered facts and gaps."
        ),
        attachment_bytes=data,
        attachment_filename=filename,
        attachment_mime_type="application/zip",
    )
    line = (
        f"Memory exported to {email} ({len(snap.entities)} people and things, "
        f"{len(snap.records)} remembered)"
        if result.ok
        else f"Could not email the memory export: {result.error}"
    )
    await agent_timeline.record(
        organization_id=int(organization_id),
        kind=AgentEventKind.AGENT_ACTED.value
        if result.ok
        else AgentEventKind.COULD_NOT.value,
        actor=AgentEventActor.AGENT.value,
        summary=line,
        payload={
            "from": "Decibyl",
            "export": {
                "files": len(snap.entities) + len(snap.episodes) + len(snap.records) + 1
            },
        },
        in_channel=False,
    )
    return bool(result.ok)
