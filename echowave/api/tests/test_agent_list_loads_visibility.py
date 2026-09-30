"""The agent list must load every column read after its session closes.

``get_all_workflows_for_listing`` uses ``load_only``; the route then filters
by ``visibility`` and returns it, outside the session. A column missing from
``load_only`` is a DetachedInstanceError on every request for the list, which
took the channel page down on 30 Sep.
"""

import pytest

from api.db import db_client
from api.db.models import OrganizationModel, UserModel, WorkflowModel


@pytest.mark.asyncio
async def test_listing_columns_are_readable_after_the_session_closes(db_session):
    async with db_client.async_session() as session:
        org = OrganizationModel(provider_id="org-listing-vis", quota_decibyl_tokens=0)
        user = UserModel(provider_id="user-listing-vis")
        session.add_all([org, user])
        await session.flush()
        session.add(
            WorkflowModel(
                name="Front desk",
                user_id=user.id,
                organization_id=org.id,
                workflow_definition={},
                template_context_variables={},
                call_disposition_codes={},
                visibility="admins",
            )
        )
        await session.commit()
        org_id = org.id

    workflows = await db_client.get_all_workflows_for_listing(
        organization_id=org_id, status=None
    )

    assert [w.visibility for w in workflows] == ["admins"]
    # Everything the list response reads, touched outside the session.
    for w in workflows:
        (w.id, w.name, w.status, w.is_live, w.created_at, w.folder_id)
        (w.workflow_uuid, w.handle)
