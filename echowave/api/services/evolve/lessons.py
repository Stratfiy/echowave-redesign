"""Lessons: small, itemised changes to a skill's playbook, learned offline.

ACE's evolving playbook (generation, reflection, curation, incremental
deltas), held to two constraints from the research plan:

* **Only from external feedback.** "LLMs cannot self-correct reasoning yet":
  a model's view of its own work is not a truth signal. A cluster is built
  only from records whose outcome came from persisted evidence -- a failed
  app reply, a discarded or edited card, a person's own correction. Records
  that merely finished, or that only the model could vouch for, are never
  in one.
* **Small, versioned edits, never a rewrite.** Each delta adds or removes
  one lesson line, cites the records it came from, and lands in a *new*
  version; the version it would replace is untouched.

The three steps:

1. **Generation** -- one model call per cluster proposes deltas, each citing
   record ids from that cluster.
2. **Reflection** -- deterministic checks, not a second opinion from the
   same model: every delta must cite records it was shown, be a usable
   length, not repeat a current lesson or one a person already turned down,
   and pass the guard. A delta that touches a control rejects the whole
   candidate, visibly.
3. **Curation** -- the surviving deltas are merged into the current
   playbook, capped, and written as a draft version with its evidence and
   cost; then the gate (``evaluate``) decides whether it is offered.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from loguru import logger

from api.db import db_client
from api.services import evolve
from api.services.evolve import evaluate, experience, guard, model, versions

#: Records a cluster of failures needs before it may teach anything. One
#: failed call is an incident; two of the same kind are a pattern. A
#: person's own correction is enough on its own.
MIN_CLUSTER = 2
MAX_DELTAS = 3
MIN_LESSON_CHARS = 12
MAX_RECORDS_SHOWN = 12

GENERATION_SYSTEM = (
    "You improve one skill's playbook from evidence. You are shown what went "
    "wrong on real tasks -- tools that failed, cards a person turned down or "
    "changed, and corrections a person gave -- each with an id. Propose at "
    "most three small lessons: one short imperative line each, about how to "
    "do the work. Each lesson must cite the ids it comes from. You may also "
    "remove a current lesson the evidence contradicts. Never write about who "
    "may be contacted, spending, calling hours, permissions or other "
    "workspaces: those are not part of a skill. Reply as JSON only: "
    '{"deltas": [{"op": "add", "text": "...", "evidence": [ids]}, '
    '{"op": "remove", "text": "exact current lesson"}]}'
)


def _signal(row: Any) -> str | None:
    """What a record is evidence *of*, or None when it is evidence of
    nothing worth learning from. The cluster key."""
    if row.outcome != experience.FAILURE:
        return None
    correction = row.correction or {}
    if correction.get("category"):
        return f"correction:{correction['category']}"
    failed = sorted(
        {t.get("name") for t in (row.tool_calls or []) if t.get("status") == "error"}
    )
    if failed:
        return f"tool:{failed[0]}"
    return None


def clusters(rows: list[Any]) -> dict[str, list[Any]]:
    """Group failures by what they are about; keep the groups big enough."""
    groups: dict[str, list[Any]] = {}
    for row in rows:
        key = _signal(row)
        if key is not None:
            groups.setdefault(key, []).append(row)
    return {
        key: members
        for key, members in groups.items()
        if len(members) >= MIN_CLUSTER
        or any(m.kind == evolve.CORRECTION for m in members)
    }


def reflect(
    deltas: Any,
    *,
    shown_ids: set[int],
    current: list[str],
    turned_down: set[str],
) -> tuple[list[dict[str, Any]], list[guard.Violation]]:
    """Keep the deltas the evidence supports. Returns them and any guard
    violations found (which reject the candidate)."""
    kept: list[dict[str, Any]] = []
    violations: list[guard.Violation] = []
    current_lower = {c.strip().lower() for c in current}
    for delta in deltas if isinstance(deltas, list) else []:
        if not isinstance(delta, dict):
            continue
        op = str(delta.get("op") or "add").lower()
        text = experience.clean_instruction(delta.get("text"))
        if op == "remove":
            if text.lower() in current_lower:
                kept.append({"op": "remove", "text": text})
            continue
        if op != "add" or len(text) < MIN_LESSON_CHARS:
            continue
        cited = {
            int(e)
            for e in (delta.get("evidence") or [])
            if isinstance(e, int) or str(e).isdigit()
        }
        if not cited or not cited <= shown_ids:
            # A lesson that cites nothing it was shown is the model's own
            # idea, which is exactly what may not teach.
            continue
        if text.lower() in current_lower or text.lower() in turned_down:
            continue
        hits = guard.scan_text("lessons", text)
        if hits:
            violations.extend(hits)
            continue
        kept.append({"op": "add", "text": text, "evidence": sorted(cited)})
        if len([k for k in kept if k["op"] == "add"]) >= MAX_DELTAS:
            break
    return kept, violations


def curate(
    content: dict[str, Any], deltas: list[dict[str, Any]], version: int
) -> dict[str, Any]:
    """Apply the deltas to a copy of the current content."""
    lessons = [dict(lesson) for lesson in content.get("lessons") or []]
    removed = {d["text"].lower() for d in deltas if d["op"] == "remove"}
    lessons = [
        lesson for lesson in lessons if str(lesson.get("text")).lower() not in removed
    ]
    for n, delta in enumerate([d for d in deltas if d["op"] == "add"], 1):
        lessons.append(
            {
                "id": f"v{version}-{n}",
                "text": delta["text"],
                "evidence": delta["evidence"],
                "added_in": version,
            }
        )
    out = {k: v for k, v in content.items() if k != "lessons"}
    out["lessons"] = lessons[-guard.MAX_LESSONS :]
    return out


async def base_body(organization_id: int, slug: str) -> str | None:
    """The procedure as shipped or as the workspace wrote it."""
    from api.services.skills import catalogue

    entry = catalogue.get(slug)
    if entry is not None:
        return entry.skill.body
    doc = await db_client.get_skill_document(organization_id=organization_id, slug=slug)
    return doc.body if doc is not None else None


async def _first_author(rows: list[Any]) -> int | None:
    first = rows[0] if rows else None
    if first is not None and first.origin == evolve.ORIGIN_REMEMBERED:
        return first.author_user_id
    return None


async def propose_for_skill(
    organization_id: int,
    slug: str,
    *,
    runner: evaluate.Runner | None = None,
) -> list[int]:
    """Every candidate this skill's experience supports, gated. Returns the
    version ids written (offered or not)."""
    procedure = await base_body(organization_id, slug)
    if procedure is None:
        return []
    history = list(
        await db_client.list_skill_versions(organization_id=organization_id, slug=slug)
    )
    author = await _first_author(history)
    active = next((r for r in reversed(history) if r.status == evolve.PUBLISHED), None)
    consumed: set[int] = set()
    turned_down: set[str] = set()
    for row in history:
        waiting = (row.evaluation or {}).get("waiting_for_holdout")
        if not waiting:
            consumed |= {int(e.get("id")) for e in row.evidence or [] if e.get("id")}
        if row.status in (evolve.DISCARDED, evolve.ROLLED_BACK, evolve.REJECTED):
            turned_down |= {
                str(lesson.get("text") or "").lower()
                for lesson in (row.content or {}).get("lessons") or []
            }
    rows = await db_client.list_experience(
        organization_id=organization_id,
        task_family=slug,
        split=experience.TRAIN,
        viewer_user_id=author,
        include_personal=author is not None,
    )
    fresh = [r for r in rows if r.id not in consumed]
    written: list[int] = []
    current_content = dict(active.content or {}) if active is not None else {}
    current_lessons = [
        str(lesson.get("text") or "") for lesson in current_content.get("lessons") or []
    ]
    for key, members in clusters(fresh).items():
        members = members[:MAX_RECORDS_SHOWN]
        if await _still_waiting(organization_id, slug, history, members):
            continue
        spend = model.Spend()
        shown = {m.id for m in members}
        evidence_lines = "\n".join(f"[{m.id}] {experience.summary(m)}" for m in members)
        try:
            proposed = await model.ask_json(
                organization_id,
                system=GENERATION_SYSTEM,
                user=(
                    f"Skill: {await versions._title(organization_id, slug, current_content)}\n"
                    f"Current lessons:\n"
                    + ("\n".join(f"- {t}" for t in current_lessons) or "(none)")
                    + f"\n\nWhat happened ({key}):\n{evidence_lines}"
                ),
                spend=spend,
            )
        except model.ModelUnavailable:
            return written
        deltas, violations = reflect(
            proposed.get("deltas"),
            shown_ids=shown,
            current=current_lessons,
            turned_down=turned_down,
        )
        evidence = [
            {
                "id": m.id,
                "kind": m.kind,
                "outcome": m.outcome,
                "summary": experience.summary(m),
            }
            for m in members
        ]
        personal = any(m.scope == experience.SCOPE_PERSONAL for m in members)
        where = Counter(m.workflow_id for m in members if m.workflow_id)
        common = {
            "organization_id": organization_id,
            "slug": slug,
            "origin": evolve.ORIGIN_LEARNED,
            "base_version": active.version if active is not None else None,
            "evidence": evidence,
            "workflow_id": where.most_common(1)[0][0] if where else None,
            "owner_user_id": author if personal else None,
        }
        if violations:
            row = await versions.create(
                **common,
                content={"lessons": [{"text": v.quote} for v in violations]},
                cost=spend.as_dict(),
            )
            written.append(row.id)
            logger.info("evolve: candidate for {} rejected by the guard", slug)
            continue
        if not [d for d in deltas if d["op"] == "add"]:
            continue
        number_hint = (history[-1].version if history else 0) + 1
        content = curate(current_content, deltas, number_hint)
        row = await versions.create(**common, content=content, cost=spend.as_dict())
        written.append(row.id)
        await _gate_and_offer(
            organization_id,
            row,
            procedure=procedure,
            active=active,
            lesson_ids=shown,
            personal_user_id=author if personal else None,
            spend=spend,
            runner=runner,
        )
    return written


async def _still_waiting(
    organization_id: int, slug: str, history: list[Any], members: list[Any]
) -> bool:
    """A candidate parked for want of held-out cases is not proposed again
    until there are more of them: otherwise every tick spends a model call
    to learn there still are not."""
    ids = {m.id for m in members}
    for row in reversed(history):
        waiting = (row.evaluation or {}).get("waiting_for_holdout")
        if not waiting:
            continue
        if {int(e.get("id")) for e in row.evidence or [] if e.get("id")} != ids:
            continue
        have = await db_client.list_experience(
            organization_id=organization_id,
            task_family=slug,
            split=experience.HOLDOUT,
            limit=evaluate.MAX_RELATED * 4,
        )
        return len(have) <= int((row.evaluation or {}).get("related", {}).get("n") or 0)
    return False


async def _gate_and_offer(
    organization_id: int,
    row: Any,
    *,
    procedure: str,
    active: Any,
    lesson_ids: set[int],
    personal_user_id: int | None,
    spend: model.Spend,
    runner: evaluate.Runner | None,
) -> None:
    baseline = (
        versions.body_for(dict(active.content or {}), procedure, active.version)
        if active is not None
        else procedure
    )
    candidate = versions.body_for(dict(row.content or {}), procedure, row.version)
    try:
        result = await evaluate.gate(
            organization_id=organization_id,
            slug=row.slug,
            baseline=baseline,
            candidate=candidate,
            lesson_record_ids=lesson_ids,
            personal_user_id=personal_user_id,
            runner=runner,
            spend=spend,
        )
    except model.ModelUnavailable:
        await db_client.move_skill_version(
            row.id,
            organization_id=organization_id,
            from_statuses=[evolve.DRAFT],
            to_status=evolve.REJECTED,
            reason="Could not be tested: no model answered.",
            evaluation={"passed": False, "waiting_for_holdout": True},
            cost=spend.as_dict(),
        )
        return
    evaluation = result.as_dict()
    if result.passed:
        await db_client.update_skill_version(
            row.id,
            organization_id=organization_id,
            evaluation=evaluation,
            cost=result.cost,
        )
        await versions.offer(organization_id, row.id)
        return
    if result.related.n < evaluate.MIN_RELATED:
        evaluation["waiting_for_holdout"] = True
    await db_client.move_skill_version(
        row.id,
        organization_id=organization_id,
        from_statuses=[evolve.DRAFT],
        to_status=evolve.REJECTED,
        evaluation=evaluation,
        cost=result.cost,
        reason=" ".join(result.reasons)[:500],
    )


async def propose(
    organization_id: int, *, runner: evaluate.Runner | None = None
) -> list[int]:
    """Every installed skill in the workspace, one after another."""
    if not evolve.enabled(organization_id):
        return []
    from api.services.skills import shelf

    written: list[int] = []
    for slug in sorted(await shelf.installed(organization_id)):
        try:
            written += await propose_for_skill(organization_id, slug, runner=runner)
        except Exception as exc:  # noqa: BLE001 - one skill is not the rest
            logger.warning("evolve: lessons for {} failed: {}", slug, exc)
    return written


__all__ = [
    "GENERATION_SYSTEM",
    "MIN_CLUSTER",
    "base_body",
    "clusters",
    "curate",
    "propose",
    "propose_for_skill",
    "reflect",
]
