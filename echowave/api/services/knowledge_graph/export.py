"""What the business has taught its workers, as a picture, a zip, and a
delete (B7, B8).

Three things a business must be able to do with its own memory, or the
memory is not theirs:

**See it.** :func:`graph_view` is the memory as nodes and edges for a
screen: the graph's entities and the facts between them, with each fact
marked ``confirmed`` when the account's own record holds it and
``inferred`` when only a conversation said so. A screen draws inferred
faint. :func:`node_detail` is one node opened: its connections and the
conversations they came from.

**Take it away.** :func:`obsidian_zip` is the whole memory as a folder of
linked Markdown in the shape Obsidian reads: one file per node, wiki links
for edges, front matter for dates and status, one file per source
conversation, and an index. Nothing here is a format we invented -- a
person who leaves us opens it in a free tool and every link works.

**Delete it.** :func:`forget_everything` empties the organisation's
partition of the graph, its remembered facts and gaps, and the day-slot
marks. It is a delete, not a status, and it is not reversible; the card
that proposes it says so and a person confirms it.

Everything reads the organisation's own partition and nothing else; the
scoping module refuses to name any other.
"""

from __future__ import annotations

import asyncio
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.services.knowledge_graph.client import (
    SEARCH_TIMEOUT_SECONDS,
    Fact,
    _overlay_confirmed,
    get_graph,
)
from api.services.knowledge_graph.scoping import (
    group_id_for_organization,
    group_ids_for_search,
)

CONFIRMED = "confirmed"
INFERRED = "inferred"

#: How much of a source conversation a node's detail shows.
EXCERPT_CHARS = 400
#: How many of anything one read pulls. A small business by a wide margin;
#: a large one gets its newest.
LIMIT = 2_000


@dataclass(frozen=True)
class Entity:
    uuid: str
    name: str
    summary: str
    created_at: datetime | None
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Relation:
    uuid: str
    source: str
    target: str
    name: str
    fact: str
    status: str
    valid_at: datetime | None
    invalid_at: datetime | None
    created_at: datetime | None
    episodes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Episode:
    uuid: str
    name: str
    source: str
    content: str
    valid_at: datetime | None
    created_at: datetime | None
    run_id: int | None = None


@dataclass(frozen=True)
class Record:
    """A remembered fact or gap from the account's own table."""

    id: int
    kind: str
    key: str
    value: str
    status: str
    times_seen: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    workflow_id: int | None = None


@dataclass
class Snapshot:
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    episodes: list[Episode] = field(default_factory=list)
    records: list[Record] = field(default_factory=list)
    graph_available: bool = True

    @property
    def is_empty(self) -> bool:
        return not (self.entities or self.relations or self.episodes or self.records)


# ---------------------------------------------------------------------------
# Reading


def _run_id_of(episode: Any) -> int | None:
    meta = getattr(episode, "episode_metadata", None) or {}
    for key in ("workflow_run_id", "run_id"):
        value = meta.get(key) if isinstance(meta, dict) else None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


async def _graph_parts(
    organization_id: int,
) -> tuple[list[Any], list[Any], list[Any]] | None:
    graph = await get_graph()
    if graph is None:
        return None
    try:
        from graphiti_core.edges import EntityEdge
        from graphiti_core.nodes import EntityNode, EpisodicNode

        group_ids = group_ids_for_search(organization_id)
        nodes, edges, episodes = await asyncio.wait_for(
            asyncio.gather(
                EntityNode.get_by_group_ids(graph.driver, group_ids, limit=LIMIT),
                EntityEdge.get_by_group_ids(graph.driver, group_ids, limit=LIMIT * 2),
                EpisodicNode.get_by_group_ids(graph.driver, group_ids, limit=LIMIT),
            ),
            timeout=SEARCH_TIMEOUT_SECONDS * 3,
        )
    except Exception as exception:  # noqa: BLE001 - no graph is an answer
        logger.warning(f"Graph read failed for org {organization_id}: {exception}")
        return None
    return list(nodes or []), list(edges or []), list(episodes or [])


async def _records(organization_id: int) -> list[Record]:
    from api.db import db_client

    try:
        rows = await db_client.organisation_memory(
            organization_id=organization_id, include_bots=True, limit=LIMIT
        )
    except Exception as exception:  # noqa: BLE001
        logger.warning(
            f"Could not read memory rows for org {organization_id}: {exception}"
        )
        return []
    out: list[Record] = []
    for row in rows:
        if getattr(row, "status", "") == "rejected":
            continue
        out.append(
            Record(
                id=int(row.id),
                kind=str(row.kind),
                key=str(row.key or ""),
                value=str(row.value or ""),
                status=str(row.status or ""),
                times_seen=int(getattr(row, "times_seen", 0) or 0),
                first_seen_at=getattr(row, "first_seen_at", None),
                last_seen_at=getattr(row, "last_seen_at", None),
                workflow_id=getattr(row, "workflow_id", None),
            )
        )
    return out


async def snapshot(organization_id: int) -> Snapshot:
    """Everything the organisation's memory holds, read once."""
    parts = await _graph_parts(organization_id)
    records = await _records(organization_id)
    if parts is None:
        return Snapshot(records=records, graph_available=False)
    nodes, edges, episodes = parts

    facts = [
        Fact(
            uuid=str(e.uuid),
            fact=str(getattr(e, "fact", "") or ""),
            valid_at=getattr(e, "valid_at", None),
            invalid_at=getattr(e, "invalid_at", None),
            created_at=getattr(e, "created_at", None),
            episodes=tuple(str(x) for x in (getattr(e, "episodes", None) or [])),
        )
        for e in edges
    ]
    overlaid = {f.uuid: f for f in await _overlay_confirmed(organization_id, facts)}

    entities = [
        Entity(
            uuid=str(n.uuid),
            name=str(getattr(n, "name", "") or "").strip() or str(n.uuid)[:8],
            summary=str(getattr(n, "summary", "") or "").strip(),
            created_at=getattr(n, "created_at", None),
            labels=tuple(
                str(label)
                for label in (getattr(n, "labels", None) or [])
                if str(label) != "Entity"
            ),
        )
        for n in nodes
    ]
    relations = [
        Relation(
            uuid=str(e.uuid),
            source=str(e.source_node_uuid),
            target=str(e.target_node_uuid),
            name=str(getattr(e, "name", "") or "").strip(),
            fact=str(getattr(e, "fact", "") or "").strip(),
            status=(
                CONFIRMED
                if getattr(overlaid.get(str(e.uuid)), "status", "") == CONFIRMED
                else INFERRED
            ),
            valid_at=getattr(e, "valid_at", None),
            invalid_at=getattr(e, "invalid_at", None),
            created_at=getattr(e, "created_at", None),
            episodes=tuple(str(x) for x in (getattr(e, "episodes", None) or [])),
        )
        for e in edges
    ]
    sources = [
        Episode(
            uuid=str(ep.uuid),
            name=str(getattr(ep, "name", "") or "").strip() or "conversation",
            source=str(
                getattr(getattr(ep, "source", None), "value", None)
                or getattr(ep, "source", "")
                or ""
            ),
            content=str(getattr(ep, "content", "") or ""),
            valid_at=getattr(ep, "valid_at", None),
            created_at=getattr(ep, "created_at", None),
            run_id=_run_id_of(ep),
        )
        for ep in episodes
    ]
    return Snapshot(
        entities=entities, relations=relations, episodes=sources, records=records
    )


# ---------------------------------------------------------------------------
# The picture


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def graph_view(snap: Snapshot) -> dict[str, Any]:
    """Nodes and edges for the screen. A node is confirmed when any fact
    on it is; everything else is inferred, and drawn faint."""
    confirmed_nodes: set[str] = set()
    live = [r for r in snap.relations if r.invalid_at is None]
    for r in live:
        if r.status == CONFIRMED:
            confirmed_nodes.add(r.source)
            confirmed_nodes.add(r.target)
    degree: dict[str, int] = {}
    for r in live:
        degree[r.source] = degree.get(r.source, 0) + 1
        degree[r.target] = degree.get(r.target, 0) + 1
    known = {e.uuid for e in snap.entities}
    nodes = [
        {
            "id": e.uuid,
            "label": e.name,
            "summary": e.summary[:200],
            "status": CONFIRMED if e.uuid in confirmed_nodes else INFERRED,
            "connections": degree.get(e.uuid, 0),
            "labels": list(e.labels),
        }
        for e in snap.entities
    ]
    edges = [
        {
            "id": r.uuid,
            "source": r.source,
            "target": r.target,
            "relation": r.name,
            "fact": r.fact,
            "status": r.status,
            "valid_at": _iso(r.valid_at),
            "sources": len(r.episodes),
        }
        for r in live
        if r.source in known and r.target in known
    ]
    return {
        "nodes": nodes,
        "edges": edges,
        "graph_available": snap.graph_available,
        "records": len(snap.records),
    }


def node_detail(snap: Snapshot, uuid: str) -> dict[str, Any] | None:
    """One node opened: what it is connected to, and the conversations
    those connections came from."""
    by_uuid = {e.uuid: e for e in snap.entities}
    node = by_uuid.get(uuid)
    if node is None:
        return None
    episodes = {ep.uuid: ep for ep in snap.episodes}
    connections = []
    seen_sources: dict[str, Episode] = {}
    for r in snap.relations:
        if uuid not in (r.source, r.target):
            continue
        other_uuid = r.target if r.source == uuid else r.source
        other = by_uuid.get(other_uuid)
        connections.append(
            {
                "id": r.uuid,
                "other_id": other_uuid,
                "other": other.name if other else other_uuid[:8],
                "relation": r.name,
                "fact": r.fact,
                "status": r.status,
                "valid_at": _iso(r.valid_at),
                "invalid_at": _iso(r.invalid_at),
                "current": r.invalid_at is None,
            }
        )
        for ep_uuid in r.episodes:
            ep = episodes.get(ep_uuid)
            if ep is not None:
                seen_sources.setdefault(ep.uuid, ep)
    connections.sort(
        key=lambda c: (not c["current"], c["status"] != CONFIRMED, c["other"])
    )
    sources = sorted(
        seen_sources.values(),
        key=lambda ep: (ep.valid_at or ep.created_at or datetime.min).replace(
            tzinfo=None
        ),
        reverse=True,
    )
    return {
        "id": node.uuid,
        "label": node.name,
        "summary": node.summary,
        "labels": list(node.labels),
        "status": CONFIRMED
        if any(c["status"] == CONFIRMED and c["current"] for c in connections)
        else INFERRED,
        "connections": connections,
        "sources": [
            {
                "id": ep.uuid,
                "name": ep.name,
                "source": ep.source,
                "when": _iso(ep.valid_at or ep.created_at),
                "excerpt": ep.content[:EXCERPT_CHARS],
                "run_id": ep.run_id,
            }
            for ep in sources
        ],
    }


# ---------------------------------------------------------------------------
# The zip


def _file_stem(name: str) -> str:
    """A name Obsidian will link to: no path or wiki characters, trimmed."""
    stem = re.sub(r"[\\/:*?\"<>|#^\[\]]+", " ", name)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    return stem[:80] or "untitled"


def _front_matter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {_yaml_scalar(v)}" for v in value)
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return _iso(value) or ""
    text = str(value).replace("\n", " ").strip()
    if re.search(r"[:#\[\]{}&*!|>'\"%@`,]", text) or text != text.strip() or not text:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


@dataclass
class Vault:
    """The files of the export, by path, so a test can open any link."""

    files: dict[str, str] = field(default_factory=dict)

    def add(self, path: str, text: str) -> None:
        self.files[path] = text

    def zip_bytes(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(self.files):
                archive.writestr(path, self.files[path])
        return buffer.getvalue()

    def links(self) -> set[str]:
        found: set[str] = set()
        for text in self.files.values():
            for match in re.finditer(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", text):
                found.add(match.group(1).strip())
        return found

    def stems(self) -> set[str]:
        return {
            path.rsplit("/", 1)[-1][: -len(".md")]
            for path in self.files
            if path.endswith(".md")
        }


def build_vault(
    snap: Snapshot, *, business_name: str, exported_at: datetime | None = None
) -> Vault:
    """The memory as an Obsidian vault: ``<Business>/`` with ``People and
    things/``, ``Conversations/``, ``Remembered/`` and an index. Every
    wiki link names a file that is in the zip."""
    exported_at = exported_at or datetime.now(UTC)
    root = _file_stem(business_name) or "Memory"
    vault = Vault()

    # Unique stems: two entities called "Meera" become "Meera" and "Meera (2)".
    stems: dict[str, str] = {}
    taken: set[str] = set()

    def stem_for(key: str, name: str) -> str:
        if key in stems:
            return stems[key]
        base = _file_stem(name)
        candidate, n = base, 2
        while candidate.lower() in taken:
            candidate = f"{base} ({n})"
            n += 1
        taken.add(candidate.lower())
        stems[key] = candidate
        return candidate

    entity_by_uuid = {e.uuid: e for e in snap.entities}
    episode_by_uuid = {ep.uuid: ep for ep in snap.episodes}

    for e in snap.entities:
        stem_for(f"entity:{e.uuid}", e.name)
    for ep in snap.episodes:
        when = (
            (ep.valid_at or ep.created_at or exported_at)
            .astimezone(UTC)
            .strftime("%Y-%m-%d")
        )
        stem_for(f"episode:{ep.uuid}", f"{when} {ep.name}")
    for r in snap.records:
        stem_for(f"record:{r.id}", r.key or r.value[:40])

    # People and things: one file per entity.
    for e in snap.entities:
        mine = [r for r in snap.relations if e.uuid in (r.source, r.target)]
        confirmed = any(r.status == CONFIRMED and r.invalid_at is None for r in mine)
        lines = [
            _front_matter(
                {
                    "uuid": e.uuid,
                    "kind": list(e.labels) or "entity",
                    "status": CONFIRMED if confirmed else INFERRED,
                    "created": e.created_at,
                    "exported": exported_at,
                }
            ),
            f"# {e.name}",
            "",
        ]
        if e.summary:
            lines += [e.summary, ""]
        current = [r for r in mine if r.invalid_at is None]
        past = [r for r in mine if r.invalid_at is not None]
        if current:
            lines += ["## Connections", ""]
            for r in current:
                other = entity_by_uuid.get(r.target if r.source == e.uuid else r.source)
                target = stems.get(f"entity:{other.uuid}") if other else None
                link = f"[[{target}]]" if target else "(unknown)"
                since = f", since {r.valid_at.date().isoformat()}" if r.valid_at else ""
                lines.append(f"- {link} — {r.fact} ({r.status}{since})")
            lines.append("")
        if past:
            lines += ["## No longer true", ""]
            for r in past:
                other = entity_by_uuid.get(r.target if r.source == e.uuid else r.source)
                target = stems.get(f"entity:{other.uuid}") if other else None
                link = f"[[{target}]]" if target else "(unknown)"
                until = (
                    f", until {r.invalid_at.date().isoformat()}" if r.invalid_at else ""
                )
                lines.append(f"- {link} — {r.fact} ({r.status}{until})")
            lines.append("")
        sources = {
            ep_uuid
            for r in mine
            for ep_uuid in r.episodes
            if ep_uuid in episode_by_uuid
        }
        if sources:
            lines += ["## Sources", ""]
            for ep_uuid in sorted(sources, key=lambda u: stems[f"episode:{u}"]):
                lines.append(f"- [[{stems[f'episode:{ep_uuid}']}]]")
            lines.append("")
        vault.add(
            f"{root}/People and things/{stems[f'entity:{e.uuid}']}.md", "\n".join(lines)
        )

    # Conversations: one file per source episode.
    for ep in snap.episodes:
        mentioned = sorted(
            {
                stems[f"entity:{u}"]
                for r in snap.relations
                if ep.uuid in r.episodes
                for u in (r.source, r.target)
                if u in entity_by_uuid
            }
        )
        lines = [
            _front_matter(
                {
                    "uuid": ep.uuid,
                    "source": ep.source,
                    "when": ep.valid_at or ep.created_at,
                    "run_id": ep.run_id,
                    "exported": exported_at,
                }
            ),
            f"# {ep.name}",
            "",
            ep.content.strip() or "(no text)",
            "",
        ]
        if mentioned:
            lines += ["## Mentions", ""] + [f"- [[{m}]]" for m in mentioned] + [""]
        vault.add(
            f"{root}/Conversations/{stems[f'episode:{ep.uuid}']}.md", "\n".join(lines)
        )

    # Remembered: the account's own facts and gaps.
    for r in snap.records:
        lines = [
            _front_matter(
                {
                    "id": r.id,
                    "kind": r.kind,
                    "key": r.key,
                    "status": r.status,
                    "times_seen": r.times_seen,
                    "first_seen": r.first_seen_at,
                    "last_seen": r.last_seen_at,
                    "bot_id": r.workflow_id,
                    "exported": exported_at,
                }
            ),
            f"# {r.key or r.kind}",
            "",
            r.value,
            "",
            f"[[{root}]]",
            "",
        ]
        vault.add(f"{root}/Remembered/{stems[f'record:{r.id}']}.md", "\n".join(lines))

    # The index.
    lines = [
        _front_matter(
            {"business": business_name, "exported": exported_at, "kind": "index"}
        ),
        f"# {business_name}",
        "",
        "What this business has taught its workers, exported from Decibyl. "
        "Faint in the app means inferred here: a fact a conversation "
        "suggested that nobody confirmed.",
        "",
    ]
    if snap.records:
        lines += ["## Remembered", ""]
        for r in sorted(snap.records, key=lambda x: (-x.times_seen, x.key)):
            lines.append(
                f"- [[{stems[f'record:{r.id}']}]] — {r.value} ({r.status}, seen {r.times_seen}×)"
            )
        lines.append("")
    if snap.entities:
        lines += ["## People and things", ""]
        for e in sorted(snap.entities, key=lambda x: x.name.lower()):
            lines.append(f"- [[{stems[f'entity:{e.uuid}']}]]")
        lines.append("")
    if snap.episodes:
        lines += ["## Conversations", ""]
        for ep in sorted(
            snap.episodes, key=lambda x: stems[f"episode:{x.uuid}"], reverse=True
        ):
            lines.append(f"- [[{stems[f'episode:{ep.uuid}']}]]")
        lines.append("")
    if not snap.graph_available:
        lines += [
            "",
            "_The knowledge graph was not reachable at export time; only the remembered facts are here._",
            "",
        ]
    vault.add(f"{root}/{root}.md", "\n".join(lines))
    return vault


def obsidian_zip(snap: Snapshot, *, business_name: str) -> bytes:
    return build_vault(snap, business_name=business_name).zip_bytes()


# ---------------------------------------------------------------------------
# The delete


async def forget_everything(organization_id: int) -> dict[str, int]:
    """Empty the organisation's memory: its graph partition, its remembered
    facts and gaps, and its day-slot marks. Returns what went. Raises only
    when the graph is there and will not delete -- a half-empty memory is
    worse than an honest refusal."""
    from api.db import db_client

    counts = {"entities": 0, "episodes": 0, "records": 0}
    graph = await get_graph()
    if graph is not None:
        from graphiti_core.nodes import EntityNode, EpisodicNode

        group_id = group_id_for_organization(organization_id)
        before = await _graph_parts(organization_id)
        if before is not None:
            counts["entities"] = len(before[0])
            counts["episodes"] = len(before[2])
        # Entities take their edges with them (detach delete); episodes
        # are their own nodes and go separately.
        await EntityNode.delete_by_group_id(graph.driver, group_id)
        await EpisodicNode.delete_by_group_id(graph.driver, group_id)
        # Communities, when the graph has built any, hang off the same group.
        try:
            from graphiti_core.nodes import CommunityNode

            await CommunityNode.delete_by_group_id(graph.driver, group_id)
        except Exception:  # noqa: BLE001 - not every build has them
            pass

    counts["records"] = int(await db_client.delete_organisation_facts(organization_id))

    try:
        import redis.asyncio as aioredis

        from api import constants

        client = await aioredis.from_url(constants.REDIS_URL, decode_responses=True)
        try:
            keys = [
                k
                async for k in client.scan_iter(
                    match=f"memory:said:{organization_id}:*"
                )
            ]
            if keys:
                await client.delete(*keys)
        finally:
            await client.aclose()
    except Exception as exception:  # noqa: BLE001 - a mark is not memory
        logger.debug(
            f"Could not clear day-slot marks for org {organization_id}: {exception}"
        )
    return counts


__all__ = [
    "CONFIRMED",
    "INFERRED",
    "Entity",
    "Episode",
    "Record",
    "Relation",
    "Snapshot",
    "Vault",
    "build_vault",
    "forget_everything",
    "graph_view",
    "node_detail",
    "obsidian_zip",
    "snapshot",
]
