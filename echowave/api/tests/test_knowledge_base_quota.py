"""The ceiling on what an organization may keep in its knowledge base.

Every document is embedded at ingestion, and on a managed account that runs on
*our* model key. Embeddings have no cost component, no rate card row, no usage
item and no ledger debit anywhere — so this is the only thing standing between
us and an account that re-uploads a corpus nightly for free.

A cap rather than a meter, deliberately, because that is what the category
does: ElevenLabs sells RAG as a per-tier allowance and Vapi folds it into the
plan silently. Nobody bills per embedded token.

The per-file limit that already existed bounds one upload and says nothing
about how many uploads there are, which is the hole these tests close.
"""

from __future__ import annotations

import pytest

from api.db.models import KnowledgeBaseDocumentModel, OrganizationModel, UserModel

MB = 1024 * 1024


async def _org(session, slug: str) -> tuple[int, int]:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    session.add_all([org, user])
    await session.flush()
    return org.id, user.id


async def _document(
    session, organization_id: int, user_id: int, *, size: int, active: bool = True
):
    row = KnowledgeBaseDocumentModel(
        organization_id=organization_id,
        created_by=user_id,
        filename=f"doc-{size}.pdf",
        file_size_bytes=size,
        processing_status="completed",
        is_active=active,
    )
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio
class TestCountingWhatIsHeld:
    async def test_an_empty_account_holds_nothing(self, db_session, async_session):
        from api.db import db_client

        organization_id, _user = await _org(async_session, "empty")

        assert await db_client.get_knowledge_base_bytes_used(organization_id) == 0

    async def test_documents_add_up(self, db_session, async_session):
        from api.db import db_client

        organization_id, user_id = await _org(async_session, "sum")
        await _document(async_session, organization_id, user_id, size=3 * MB)
        await _document(async_session, organization_id, user_id, size=2 * MB)

        assert await db_client.get_knowledge_base_bytes_used(organization_id) == 5 * MB

    async def test_deleting_gives_the_allowance_back(self, db_session, async_session):
        """The cap is about the ongoing cost of holding and re-embedding a
        corpus, not a lifetime upload tally. A deleted document is not being
        re-embedded, so it should not count."""
        from api.db import db_client

        organization_id, user_id = await _org(async_session, "deleted")
        await _document(async_session, organization_id, user_id, size=4 * MB)
        await _document(
            async_session, organization_id, user_id, size=4 * MB, active=False
        )

        assert await db_client.get_knowledge_base_bytes_used(organization_id) == 4 * MB

    async def test_a_size_not_yet_written_counts_as_zero(
        self, db_session, async_session
    ):
        """``file_size_bytes`` is filled in by the ingestion worker after the
        fact, so a document uploaded seconds ago is still NULL. Erring toward
        letting the upload through beats refusing somebody because our own
        worker had not caught up."""
        from api.db import db_client

        organization_id, user_id = await _org(async_session, "pending")
        await _document(async_session, organization_id, user_id, size=2 * MB)
        row = await _document(async_session, organization_id, user_id, size=1)
        row.file_size_bytes = None
        await async_session.flush()

        assert await db_client.get_knowledge_base_bytes_used(organization_id) == 2 * MB

    async def test_another_organization_is_never_counted(
        self, db_session, async_session
    ):
        """The cap is per account. Counting across tenants would let one
        customer's uploads lock another customer out."""
        from api.db import db_client

        mine, mine_user = await _org(async_session, "mine")
        theirs, theirs_user = await _org(async_session, "theirs")
        await _document(async_session, mine, mine_user, size=1 * MB)
        await _document(async_session, theirs, theirs_user, size=200 * MB)

        assert await db_client.get_knowledge_base_bytes_used(mine) == 1 * MB


class TestTheDefault:
    def test_the_aggregate_cap_is_far_above_the_per_file_cap(self):
        """They bound different things. A total equal to the per-file limit
        would mean one document fills the account, which is not a knowledge
        base."""
        from api.constants import (
            KNOWLEDGE_BASE_MAX_FILE_SIZE_BYTES,
            KNOWLEDGE_BASE_MAX_TOTAL_BYTES,
        )

        assert KNOWLEDGE_BASE_MAX_TOTAL_BYTES > KNOWLEDGE_BASE_MAX_FILE_SIZE_BYTES * 10

    def test_the_default_is_generous(self):
        """Set high on purpose. A cap that trips existing accounts on the day it
        ships is an outage, not a control — the number can come down once there
        is data about what real accounts hold."""
        from api.constants import KNOWLEDGE_BASE_MAX_TOTAL_BYTES

        assert KNOWLEDGE_BASE_MAX_TOTAL_BYTES >= 100 * MB
