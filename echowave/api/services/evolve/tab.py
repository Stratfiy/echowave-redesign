"""The Skills tab on Agents: every skill this workspace keeps, as a full card.

One card per installed skill, plus the viewer's own remembered drafts. Each
card answers the five questions (``explain``), and adds what the shelf never
had: its version history with who made each one, the evidence a learned
version was offered on and how it did against the version before it, and
which agents use it.
"""

from __future__ import annotations

from typing import Any

from api.db import db_client
from api.services import evolve
from api.services.evolve import explain, versions


def _improvement(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest published learned version's evaluation, in short."""
    for row in reversed(history):
        if row["origin"] != evolve.ORIGIN_LEARNED or row["status"] != evolve.PUBLISHED:
            continue
        evaluation = row.get("evaluation") or {}
        related = evaluation.get("related") or {}
        unrelated = evaluation.get("unrelated") or {}
        return {
            "version": row["version"],
            "related": {
                "n": related.get("n", 0),
                "baseline_passed": related.get("baseline_passed", 0),
                "candidate_passed": related.get("candidate_passed", 0),
            },
            "unrelated": {
                "n": unrelated.get("n", 0),
                "regressions": len(unrelated.get("regressions") or []),
            },
            "cost": row.get("cost") or {},
            "evidence": len(row.get("evidence") or []),
        }
    return None


async def _names(organization_id: int) -> dict[int, str]:
    from api.services.skills import shelf

    return await shelf._bot_names(organization_id)


async def cards(organization_id: int, viewer_user_id: int) -> dict[str, Any]:
    from api.services.skills import catalogue, shelf
    from api.services.workflow.skill_context import imported_skills

    have = await shelf.installed(organization_id)
    names = await _names(organization_id)
    own = await imported_skills(organization_id)
    out: list[dict[str, Any]] = []
    all_history = await versions.history(organization_id, None, viewer_user_id)
    by_slug: dict[str, list[dict[str, Any]]] = {}
    for row in all_history:
        by_slug.setdefault(row["slug"], []).append(row)

    slugs = list(have)
    # A remembered draft not yet saved is on the tab too, for its author.
    for slug, rows in by_slug.items():
        waiting = any(r["status"] in (evolve.DRAFT, evolve.OFFERED) for r in rows)
        if slug not in have and waiting:
            slugs.append(slug)

    for slug in slugs:
        entry = catalogue.get(slug) or own.get(slug)
        history = by_slug.get(slug, [])
        active = next(
            (r for r in reversed(history) if r["status"] == evolve.PUBLISHED), None
        )
        latest = history[-1] if history else None
        content = dict((active or latest or {}).get("content") or {})
        title = (
            content.get("title") or (entry.title if entry is not None else None) or slug
        )
        description = (
            entry.description if entry is not None else content.get("description", "")
        )
        body = entry.skill.body if entry is not None else versions.render(content)
        on = [
            {"id": wid, "name": names.get(wid, "an agent")}
            for wid in (have[slug].on_bots if slug in have else ())
        ]
        out.append(
            {
                "slug": slug,
                "title": title,
                "emoji": entry.emoji if entry is not None else "",
                "division": entry.division if entry is not None else "Yours",
                "own": entry is None or slug.startswith(shelf.OWN_PREFIX),
                "installed": slug in have,
                "explain": explain.explain(
                    title=title,
                    description=description or "",
                    body=body or "",
                    content=content if versions.is_whole(content) else None,
                    on_agents=[b["name"] for b in on],
                    installed=slug in have,
                ),
                "on_agents": on,
                "active_version": active["version"] if active else None,
                "versions": history,
                "improvement": _improvement(history),
                "experience": await db_client.count_experience(
                    organization_id=organization_id, task_family=slug
                ),
            }
        )
    return {"skills": out, "max_per_bot": shelf.MAX_PER_BOT}


__all__ = ["cards"]
