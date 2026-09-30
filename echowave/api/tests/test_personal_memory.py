"""Personal memory per member (PRD v2 MEM-1, KAN-197).

Done when: A's personal fact never reaches B in recall, search, graph or
summaries. Off (the default) all memory is the workspace's, exactly as before.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from api import constants
from api.db import db_client
from api.services import acting
from api.services.knowledge_graph import feed, personal, scoping


@pytest.fixture
def personal_on(monkeypatch):
    monkeypatch.setattr(constants, "PERSONAL_MEMORY_ENABLED", True)


async def _workspace():
    """An organisation and two members, fresh per test, removed after."""
    run = uuid4().hex[:8]
    a, _ = await db_client.get_or_create_user_by_provider_id(f"mem1-a-{run}")
    b, _ = await db_client.get_or_create_user_by_provider_id(f"mem1-b-{run}")
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"mem1-org-{run}", a.id
    )
    return org.id, a.id, b.id


async def _forget(org: int) -> None:
    # Creating an organisation grants a committed onboarding credit, and other
    # tests assert the ledger is empty across the database.
    async with db_client.async_session() as session:
        for table in ("credit_ledger", "organisation_facts"):
            await session.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :org"),
                {"org": org},
            )
        await session.commit()


@pytest.fixture
async def workspace():
    org, a, b = await _workspace()
    try:
        yield org, a, b
    finally:
        await _forget(org)


def _values(rows) -> set[str]:
    return {r.value for r in rows}


class TestTheScopeHelpers:
    def test_off_nobody_owns_anything(self):
        with acting.acting_as(3):
            assert personal.owner() is None
            assert personal.viewer() is None

    def test_on_the_turn_belongs_to_its_member(self, personal_on):
        assert personal.owner() is None  # no turn, no member
        with acting.acting_as(3):
            assert personal.owner() == 3
            assert personal.viewer() == 3
        assert personal.viewer() is None

    @pytest.mark.parametrize("bad", [None, 0, -1, True, "3"])
    def test_a_non_member_is_nobody(self, personal_on, bad):
        with acting.acting_as(bad):
            assert personal.owner() is None


@pytest.mark.asyncio
class TestTheRecord:
    """Tests 1 to 3 and 7: the column, recall, search, and flag off."""

    async def test_a_personal_fact_carries_its_member_and_a_workspace_fact_none(
        self, workspace
    ):
        org, a, b = workspace
        await db_client.remember_organisation_facts(
            organization_id=org, facts={"tea": "no sugar"}, user_id=a
        )
        await db_client.remember_organisation_facts(
            organization_id=org, facts={"hours": "9 to 6"}
        )
        rows = await db_client.organisation_memory(
            organization_id=org, include_bots=True, user_id=a
        )
        owners = {r.key: r.user_id for r in rows}
        assert owners == {"tea": a, "hours": None}

    async def test_recall_for_b_never_returns_a_personal_fact(self, workspace):
        org, a, b = workspace
        await db_client.remember_organisation_facts(
            organization_id=org, facts={"tea": "no sugar"}, user_id=a
        )
        await db_client.remember_organisation_facts(
            organization_id=org, facts={"coffee": "black"}, user_id=b
        )
        await db_client.remember_organisation_facts(
            organization_id=org, facts={"hours": "9 to 6"}
        )

        for_a = await db_client.organisation_memory(organization_id=org, user_id=a)
        for_b = await db_client.organisation_memory(organization_id=org, user_id=b)
        nobody = await db_client.organisation_memory(organization_id=org)
        every_bot = await db_client.organisation_memory(
            organization_id=org, include_bots=True
        )

        assert _values(for_a) == {"no sugar", "9 to 6"}
        assert _values(for_b) == {"black", "9 to 6"}
        # A read that names no member returns no member's memory at all,
        # whatever scope it asked for.
        assert _values(nobody) == {"9 to 6"}
        assert _values(every_bot) == {"9 to 6"}

    async def test_facts_about_people_follow_the_same_rule(self, workspace):
        org, a, b = workspace
        await db_client.remember_organisation_facts(
            organization_id=org,
            facts={"relationship": "college friend"},
            subject_type="person",
            subject_key="arun",
            user_id=a,
        )
        for_a = await db_client.subject_facts(
            organization_id=org, subject_key="arun", user_id=a
        )
        for_b = await db_client.subject_facts(
            organization_id=org, subject_key="arun", user_id=b
        )
        nobody = await db_client.subject_facts(organization_id=org, subject_key="arun")
        assert _values(for_a) == {"college friend"}
        assert for_b == []
        assert nobody == []

    async def test_a_member_and_the_workspace_can_each_hold_the_same_point(
        self, workspace
    ):
        org, a, _ = workspace
        await db_client.remember_organisation_facts(
            organization_id=org, facts={"supplier": "Sharma"}
        )
        await db_client.remember_organisation_facts(
            organization_id=org, facts={"supplier": "Patel"}, user_id=a
        )
        # A second write at each scope updates its own row, never the other's.
        await db_client.remember_organisation_facts(
            organization_id=org, facts={"supplier": "Sharma & Sons"}
        )
        await db_client.remember_organisation_facts(
            organization_id=org, facts={"supplier": "Patel Bros"}, user_id=a
        )
        rows = await db_client.organisation_memory(organization_id=org, user_id=a)
        assert sorted(_values(rows)) == ["Patel Bros", "Sharma & Sons"]

    async def test_workspace_facts_still_deduplicate_with_the_flag_off(self, workspace):
        """Test 7: the rebuilt workspace index still holds one row per point."""
        org, _, _ = workspace
        for value in ("9 to 6", "10 to 7"):
            await db_client.remember_organisation_facts(
                organization_id=org, facts={"hours": value}
            )
        rows = await db_client.organisation_memory(organization_id=org)
        assert [(r.key, r.value, r.user_id) for r in rows] == [
            ("hours", "10 to 7", None)
        ]

    async def test_a_colleague_cannot_change_your_fact(self, workspace):
        org, a, b = workspace
        await db_client.remember_organisation_facts(
            organization_id=org, facts={"tea": "no sugar"}, user_id=a
        )
        (row,) = await db_client.organisation_memory(organization_id=org, user_id=a)
        assert not await db_client.set_organisation_fact_status(
            organization_id=org, fact_id=row.id, status="rejected", user_id=b
        )
        assert not await db_client.set_organisation_fact_status(
            organization_id=org, fact_id=row.id, status="rejected"
        )
        assert await db_client.set_organisation_fact_status(
            organization_id=org, fact_id=row.id, status="rejected", user_id=a
        )

    async def test_forgetting_your_fact_takes_it_out_of_recall(self, workspace):
        """Test 6: forgetting is a status, and recall reads confirmed only."""
        org, a, _ = workspace
        await db_client.remember_organisation_facts(
            organization_id=org, facts={"tea": "no sugar"}, user_id=a
        )
        (row,) = await db_client.organisation_memory(organization_id=org, user_id=a)
        await db_client.set_organisation_fact_status(
            organization_id=org, fact_id=row.id, status="rejected", user_id=a
        )
        confirmed = await db_client.organisation_memory(
            organization_id=org, user_id=a, status="confirmed"
        )
        assert confirmed == []

    async def test_sharing_moves_a_fact_to_the_workspace(self, workspace):
        org, a, b = workspace
        await db_client.remember_organisation_facts(
            organization_id=org, facts={"supplier": "Sharma"}
        )
        await db_client.remember_organisation_facts(
            organization_id=org, facts={"supplier": "Patel"}, user_id=a
        )
        (mine,) = [
            r
            for r in await db_client.organisation_memory(organization_id=org, user_id=a)
            if r.user_id == a
        ]
        assert (
            await db_client.share_member_fact(
                organization_id=org, user_id=b, fact_id=mine.id
            )
            is None
        )
        shared = await db_client.share_member_fact(
            organization_id=org, user_id=a, fact_id=mine.id
        )
        assert shared.user_id is None and shared.value == "Patel"
        for_b = await db_client.organisation_memory(organization_id=org, user_id=b)
        assert [(r.key, r.value) for r in for_b] == [("supplier", "Patel")]


class TestTheGraphPartitions:
    """Test 4."""

    def test_a_member_partition_sits_inside_the_organisation(self):
        assert scoping.group_id_for_member(7, 3) == "org:7:user:3"
        assert scoping.group_id_for_member(7, 3) != scoping.group_id_for_organization(7)
        assert scoping.group_id_for_member(7, 3) != scoping.group_id_for_member(7, 4)
        assert scoping.organization_of("org:7:user:3") == 7
        assert scoping.organization_of("org:7") == 7

    @pytest.mark.parametrize("bad", [None, 0, True, "3"])
    def test_no_member_partition_without_a_real_member(self, bad):
        with pytest.raises(scoping.GraphScopeError):
            scoping.group_id_for_member(7, bad)

    def test_a_search_reads_the_workspace_and_only_the_askers_own(self):
        assert scoping.group_ids_for_search(7) == ["org:7"]
        assert scoping.group_ids_for_search(7, 3) == ["org:7", "org:7:user:3"]

    def test_a_line_said_by_a_member_lands_in_their_partition(self, personal_on):
        with acting.acting_as(3):
            assert feed._partition(7) == "org:7:user:3"
        assert feed._partition(7) == "org:7"

    def test_off_every_line_lands_in_the_workspace(self):
        with acting.acting_as(3):
            assert feed._partition(7) == "org:7"

    @pytest.mark.asyncio
    async def test_recall_searches_the_askers_partition_not_a_colleagues(
        self, personal_on
    ):
        from api.services.knowledge_graph import client as graph_client

        graph = SimpleNamespace(search=AsyncMock(return_value=[]))
        with (
            patch.object(graph_client, "get_graph", AsyncMock(return_value=graph)),
            acting.acting_as(3),
        ):
            await graph_client.search_facts(7, "tea")
        assert graph.search.await_args.kwargs["group_ids"] == ["org:7", "org:7:user:3"]

    @pytest.mark.asyncio
    async def test_a_summary_built_without_a_member_reads_the_workspace_only(
        self, personal_on
    ):
        """Test 5: the Sunday review runs with no member, so it can never
        carry anybody's personal memory; built for A, it carries A's."""
        from datetime import UTC, datetime

        from api.services.knowledge_graph import client as graph_client

        seen: list[list[str]] = []

        async def get_by_group_ids(driver, group_ids, limit):
            seen.append(list(group_ids))
            return []

        graph = SimpleNamespace(driver=object())
        with (
            patch.object(graph_client, "get_graph", AsyncMock(return_value=graph)),
            patch("graphiti_core.edges.EntityEdge.get_by_group_ids", get_by_group_ids),
        ):
            await graph_client.recent_facts(7, since=datetime.now(UTC))
            with acting.acting_as(3):
                await graph_client.recent_facts(7, since=datetime.now(UTC))
        assert seen == [["org:7"], ["org:7", "org:7:user:3"]]


@pytest.mark.asyncio
class TestDecibylWrites:
    async def test_a_correction_in_a_members_turn_is_theirs(self, personal_on):
        from api.services.knowledge_graph import teach

        remember = AsyncMock()
        with (
            patch.object(teach.db_client, "remember_organisation_facts", remember),
            patch.object(teach.feed, "remember_correction", AsyncMock()),
            patch.object(teach.agent_timeline, "record", AsyncMock()),
            acting.acting_as(3),
        ):
            await teach.correct(
                7,
                {"subject": "Arun", "key": "relationship", "value": "college"},
                ref_id="r",
            )
        assert remember.await_args.kwargs["user_id"] == 3

    async def test_off_a_correction_is_the_workspaces(self):
        from api.services.knowledge_graph import teach

        remember = AsyncMock()
        with (
            patch.object(teach.db_client, "remember_organisation_facts", remember),
            patch.object(teach.feed, "remember_correction", AsyncMock()),
            patch.object(teach.agent_timeline, "record", AsyncMock()),
            acting.acting_as(3),
        ):
            await teach.correct(
                7,
                {"subject": "Arun", "key": "relationship", "value": "college"},
                ref_id="r",
            )
        assert remember.await_args.kwargs["user_id"] is None


class TestTheScreen:
    """Test 6: Mine and Shared, and sharing."""

    def _app(self, user_id):
        from api.routes.organisation_memory import router
        from api.services.auth.depends import get_user

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_user] = lambda: SimpleNamespace(
            id=user_id, selected_organization_id=7
        )
        return TestClient(app)

    def _row(self, **kw):
        base = dict(
            id=1,
            kind="fact",
            subject_key="self",
            key="tea",
            value="no sugar",
            status="confirmed",
            times_seen=1,
            first_seen_at=None,
            last_seen_at=None,
            source_run_id=None,
            workflow_id=None,
            user_id=None,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_the_screen_marks_your_own(self, personal_on):
        read = AsyncMock(
            return_value=[self._row(id=1, user_id=3), self._row(id=2, key="hours")]
        )
        with patch(
            "api.routes.organisation_memory.db_client.organisation_memory", read
        ):
            body = self._app(3).get("/api/v1/organisation/memory").json()
        assert read.await_args.kwargs["user_id"] == 3
        assert [(f["id"], f["mine"]) for f in body["facts"]] == [(1, True), (2, False)]

    def test_off_the_screen_asks_for_nobodys_own(self):
        read = AsyncMock(return_value=[])
        with patch(
            "api.routes.organisation_memory.db_client.organisation_memory", read
        ):
            self._app(3).get("/api/v1/organisation/memory")
        assert read.await_args.kwargs["user_id"] is None

    def test_share_is_behind_the_flag(self):
        assert (
            self._app(3).post("/api/v1/organisation/memory/1/share").status_code == 404
        )

    def test_sharing_someone_elses_fact_is_not_found(self, personal_on):
        with patch(
            "api.routes.organisation_memory.db_client.share_member_fact",
            AsyncMock(return_value=None),
        ):
            response = self._app(4).post("/api/v1/organisation/memory/1/share")
        assert response.status_code == 404

    def test_sharing_your_own_returns_the_workspace_row(self, personal_on):
        share = AsyncMock(return_value=self._row(id=9, user_id=None))
        with patch("api.routes.organisation_memory.db_client.share_member_fact", share):
            response = self._app(3).post("/api/v1/organisation/memory/1/share")
        assert response.status_code == 200
        assert response.json()["mine"] is False
        assert share.await_args.kwargs == {
            "organization_id": 7,
            "user_id": 3,
            "fact_id": 1,
        }


def test_the_flag_is_registered_and_off_by_default():
    from api.services import features

    assert features.FLAGS["personal_memory"] == "PERSONAL_MEMORY_ENABLED"
    assert constants.PERSONAL_MEMORY_ENABLED is False
