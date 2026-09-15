"""Memory you can see, take away and delete (B7, B8).

Step 19's checks: the export opens as an Obsidian vault in which every
wiki link names a file in the zip; delete-all leaves nothing. Around them:
the screen's graph tells confirmed from inferred, a node opened shows its
connections and the conversations behind them, the routes gate the delete
on a typed phrase, and the card can propose it.

Eval scenarios: memory_export_opens_in_obsidian_with_working_links,
memory_delete_all_leaves_nothing.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.db import db_client
from api.services.knowledge_graph import export
from api.services.workflow import actions

ORG = 42
T1 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)


def _snapshot() -> export.Snapshot:
    """Three people and things, one of them named twice, three facts (one
    closed), two conversations, two remembered rows."""
    meera = export.Entity(
        "e-meera", "Meera Iyer", "A patient of the clinic.", T1, ("Person",)
    )
    meera2 = export.Entity("e-meera-2", "Meera Iyer", "Another Meera.", T1, ("Person",))
    tuesday = export.Entity("e-tue", "Tuesday 5 pm slot", "", T1)
    dr = export.Entity("e-dr", "Dr Anitha", "", T1, ("Person",))
    relations = [
        export.Relation(
            "r1",
            "e-meera",
            "e-tue",
            "BOOKED",
            "Meera Iyer booked the Tuesday 5 pm slot",
            export.CONFIRMED,
            T1,
            None,
            T1,
            ("ep-1",),
        ),
        export.Relation(
            "r2",
            "e-meera",
            "e-dr",
            "SEES",
            "Meera Iyer sees Dr Anitha",
            export.INFERRED,
            T1,
            None,
            T1,
            ("ep-1", "ep-2"),
        ),
        export.Relation(
            "r3",
            "e-meera-2",
            "e-dr",
            "SEES",
            "The other Meera saw Dr Anitha",
            export.INFERRED,
            T1,
            T2,
            T1,
            ("ep-2",),
        ),
    ]
    episodes = [
        export.Episode(
            "ep-1",
            "Call from +91…3210",
            "message",
            "Caller: I want Tuesday 5 pm with Dr Anitha. Agent: Booked.",
            T1,
            T1,
            501,
        ),
        export.Episode(
            "ep-2",
            "WhatsApp thread",
            "message",
            "Meera: is Dr Anitha in on Thursday?",
            T2,
            T2,
            None,
        ),
    ]
    records = [
        export.Record(
            9, "fact", "opening_hours", "Mon-Sat 9:30-8", "confirmed", 4, T1, T2
        ),
        export.Record(
            11,
            "gap",
            "do_you_open_on_saturday",
            "Do you open on Saturday?",
            "learned",
            12,
            T1,
            T2,
        ),
    ]
    return export.Snapshot(
        entities=[meera, meera2, tuesday, dr],
        relations=relations,
        episodes=episodes,
        records=records,
    )


class TestThePicture:
    def test_a_node_is_confirmed_when_any_fact_on_it_is_and_faint_otherwise(self):
        view = export.graph_view(_snapshot())
        status = {n["id"]: n["status"] for n in view["nodes"]}
        assert status["e-meera"] == export.CONFIRMED
        assert status["e-tue"] == export.CONFIRMED
        assert status["e-dr"] == export.INFERRED
        assert status["e-meera-2"] == export.INFERRED

    def test_a_closed_fact_is_not_drawn(self):
        view = export.graph_view(_snapshot())
        assert {e["id"] for e in view["edges"]} == {"r1", "r2"}
        assert view["records"] == 2 and view["graph_available"] is True

    def test_a_node_opened_shows_its_connections_and_sources(self):
        detail = export.node_detail(_snapshot(), "e-dr")
        assert detail["label"] == "Dr Anitha"
        assert [c["other"] for c in detail["connections"]] == [
            "Meera Iyer",
            "Meera Iyer",
        ]
        assert [c["current"] for c in detail["connections"]] == [True, False]
        assert [s["id"] for s in detail["sources"]] == ["ep-2", "ep-1"]
        assert detail["sources"][1]["run_id"] == 501
        assert "Tuesday 5 pm" in detail["sources"][1]["excerpt"]

    def test_an_unknown_node_is_none(self):
        assert export.node_detail(_snapshot(), "nope") is None


class TestTheVault:
    """The export opens in Obsidian with working links: every ``[[link]]``
    in every file names a ``.md`` in the zip, and every file has front
    matter with its dates and status."""

    def test_memory_export_opens_in_obsidian_with_working_links(self):
        data = export.obsidian_zip(_snapshot(), business_name="Narayani Dental")
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = archive.namelist()
        assert all(n.startswith("Narayani Dental/") for n in names)
        stems = {n.rsplit("/", 1)[-1][:-3] for n in names if n.endswith(".md")}
        # Two Meeras, two files.
        assert "Meera Iyer" in stems and "Meera Iyer (2)" in stems
        # Every link resolves.
        links: set[str] = set()
        for n in names:
            text = archive.read(n).decode("utf-8")
            assert text.startswith("---\n"), n
            links |= {m.group(1) for m in re.finditer(r"\[\[([^\]|#]+)", text)}
        assert links, "the vault has links"
        assert links <= stems, links - stems

    def test_front_matter_carries_dates_and_status(self):
        vault = export.build_vault(
            _snapshot(), business_name="Narayani Dental", exported_at=T2
        )
        meera = vault.files["Narayani Dental/People and things/Meera Iyer.md"]
        head = meera.split("---")[1]
        assert (
            "status: confirmed" in head and "created: 2026-09-01T10:00:00+00:00" in head
        )
        assert "exported: 2026-09-08T10:00:00+00:00" in head
        dr = vault.files["Narayani Dental/People and things/Dr Anitha.md"]
        assert "status: inferred" in dr.split("---")[1]
        assert "## No longer true" in dr and "until 2026-09-08" in dr
        remembered = vault.files["Narayani Dental/Remembered/opening_hours.md"]
        assert "times_seen: 4" in remembered and "status: confirmed" in remembered

    def test_the_index_lists_everything_and_a_conversation_links_back(self):
        vault = export.build_vault(
            _snapshot(), business_name="Narayani Dental", exported_at=T2
        )
        index = vault.files["Narayani Dental/Narayani Dental.md"]
        for stem in (
            "opening_hours",
            "Meera Iyer",
            "Dr Anitha",
            "2026-09-01 Call from +91…3210",
        ):
            assert f"[[{stem}]]" in index
        conversation = vault.files[
            "Narayani Dental/Conversations/2026-09-01 Call from +91…3210.md"
        ]
        assert "run_id: 501" in conversation
        assert (
            "[[Meera Iyer]]" in conversation and "[[Tuesday 5 pm slot]]" in conversation
        )

    def test_no_graph_is_said_in_the_index(self):
        snap = export.Snapshot(records=_snapshot().records, graph_available=False)
        vault = export.build_vault(snap, business_name="Acme")
        assert "not reachable" in vault.files["Acme/Acme.md"]
        assert len(vault.files) == 3


@pytest.mark.asyncio
class TestReading:
    async def test_no_graph_means_records_only_and_says_so(self):
        rows = [
            SimpleNamespace(
                id=1,
                kind="fact",
                key="k",
                value="v",
                status="confirmed",
                times_seen=1,
                first_seen_at=None,
                last_seen_at=None,
                workflow_id=None,
            )
        ]
        with (
            patch.object(export, "get_graph", AsyncMock(return_value=None)),
            patch.object(
                db_client, "organisation_memory", AsyncMock(return_value=rows)
            ),
        ):
            snap = await export.snapshot(ORG)
        assert snap.graph_available is False
        assert [r.key for r in snap.records] == ["k"]

    async def test_a_rejected_row_is_not_exported(self):
        rows = [
            SimpleNamespace(
                id=1,
                kind="fact",
                key="k",
                value="v",
                status="rejected",
                times_seen=1,
                first_seen_at=None,
                last_seen_at=None,
                workflow_id=None,
            ),
        ]
        with (
            patch.object(export, "get_graph", AsyncMock(return_value=None)),
            patch.object(
                db_client, "organisation_memory", AsyncMock(return_value=rows)
            ),
        ):
            snap = await export.snapshot(ORG)
        assert snap.records == []


@pytest.mark.asyncio
class TestDeleteAll:
    async def test_memory_delete_all_leaves_nothing(self):
        """The graph partition, the rows and the day-slot marks all go, and
        a second read finds nothing."""
        driver = object()
        graph = SimpleNamespace(driver=driver)
        entity_delete = AsyncMock()
        episode_delete = AsyncMock()
        community_delete = AsyncMock()
        delete_rows = AsyncMock(return_value=7)
        redis = AsyncMock()

        async def scan(match):
            for key in (
                f"memory:said:{ORG}:2026-09-14",
                f"memory:said:{ORG}:2026-09-15",
            ):
                yield key

        redis.scan_iter = scan
        # Before: three entities and two episodes; after: nothing.
        parts = AsyncMock(side_effect=[([1, 2, 3], [1, 2], [1, 2]), ([], [], [])])
        with (
            patch.object(export, "get_graph", AsyncMock(return_value=graph)),
            patch.object(export, "_graph_parts", parts),
            patch("graphiti_core.nodes.EntityNode.delete_by_group_id", entity_delete),
            patch(
                "graphiti_core.nodes.EpisodicNode.delete_by_group_id", episode_delete
            ),
            patch(
                "graphiti_core.nodes.CommunityNode.delete_by_group_id", community_delete
            ),
            patch.object(db_client, "delete_organisation_facts", delete_rows),
            patch("redis.asyncio.from_url", AsyncMock(return_value=redis)),
            patch.object(db_client, "organisation_memory", AsyncMock(return_value=[])),
        ):
            counts = await export.forget_everything(ORG)
            after = await export.snapshot(ORG)

        assert counts == {"entities": 3, "episodes": 2, "records": 7}
        entity_delete.assert_awaited_once_with(driver, f"org:{ORG}")
        episode_delete.assert_awaited_once_with(driver, f"org:{ORG}")
        delete_rows.assert_awaited_once_with(ORG)
        redis.delete.assert_awaited_once_with(
            f"memory:said:{ORG}:2026-09-14", f"memory:said:{ORG}:2026-09-15"
        )
        assert after.is_empty

    async def test_the_route_needs_the_phrase(self):
        from api.routes.organisation_memory import (
            ForgetEverythingRequest,
            forget_everything,
        )

        user = SimpleNamespace(id=1, selected_organization_id=ORG)
        with pytest.raises(HTTPException) as refused:
            await forget_everything(ForgetEverythingRequest(confirm="yes"), user=user)
        assert refused.value.status_code == 422
        with (
            patch.object(
                export,
                "forget_everything",
                AsyncMock(return_value={"entities": 1, "episodes": 2, "records": 3}),
            ),
            patch("api.services.workflow.agent_timeline.record", AsyncMock()) as record,
        ):
            result = await forget_everything(
                ForgetEverythingRequest(confirm="Delete Everything "), user=user
            )
        assert (result.entities, result.episodes, result.records) == (1, 2, 3)
        assert record.await_args.kwargs["payload"]["by"] == 1

    async def test_the_card_proposes_it_and_running_it_deletes(self):
        payload = await actions.resolve(
            organization_id=ORG,
            workflow_id=None,
            arguments={"action": actions.FORGET_EVERYTHING, "why": "they asked"},
        )
        assert payload["reversible"] is False
        assert "everything" in payload["label"].lower()
        with patch.object(
            export,
            "forget_everything",
            AsyncMock(return_value={"entities": 1, "episodes": 0, "records": 2}),
        ) as run:
            line = await actions._execute(ORG, payload)
        run.assert_awaited_once_with(ORG)
        assert line.startswith("Forgotten everything: 2 remembered")
        with pytest.raises(actions.ActionError):
            await actions._reverse(ORG, payload)

    def test_the_model_is_told_when_not_to_use_it(self):
        description = actions.tool_properties()["action"]["description"]
        assert (
            "forget_everything" in description and "never for one fact" in description
        )


@pytest.mark.asyncio
class TestTheEmail:
    async def test_the_vault_is_emailed_to_the_person_who_asked(self):
        from api.tasks import memory_export

        user = SimpleNamespace(
            id=5, email="owner@clinic.in", selected_organization_id=ORG
        )
        send = AsyncMock(return_value=SimpleNamespace(ok=True, error=None))
        with (
            patch.object(db_client, "get_user_by_id", AsyncMock(return_value=user)),
            patch.object(
                db_client,
                "get_organization_by_id",
                AsyncMock(return_value=SimpleNamespace(name="Narayani Dental")),
            ),
            patch.object(export, "snapshot", AsyncMock(return_value=_snapshot())),
            patch("api.services.messaging.email.send_email", send),
            patch("api.services.workflow.agent_timeline.record", AsyncMock()) as record,
        ):
            ok = await memory_export.export_memory({}, ORG, 5)
        assert ok is True
        sent = send.await_args.kwargs
        assert sent["to"] == "owner@clinic.in"
        assert sent["attachment_filename"].startswith("Narayani Dental memory ")
        assert zipfile.ZipFile(io.BytesIO(sent["attachment_bytes"])).namelist()
        assert "exported to owner@clinic.in" in record.await_args.kwargs["summary"]

    async def test_somebody_elses_organisation_gets_nothing(self):
        from api.tasks import memory_export

        user = SimpleNamespace(id=5, email="x@y.in", selected_organization_id=ORG + 1)
        with (
            patch.object(db_client, "get_user_by_id", AsyncMock(return_value=user)),
            patch("api.services.messaging.email.send_email", AsyncMock()) as send,
        ):
            assert await memory_export.export_memory({}, ORG, 5) is False
        send.assert_not_awaited()
