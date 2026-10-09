"""After a promotion: are later, unseen tasks doing better or worse?

The gate tested a lesson on the workspace's held-out past. This watches its
future: attempts recorded *after* the version was published, which nobody
had seen when it was approved, against attempts on the version before it.
Two comparisons, the same as the gate's:

* the skill's own tasks, on the new version against the old one;
* the other work of the agents carrying it (negative transfer), before the
  publish against after.

When either is worse by ``WORSE_BY`` or more, on at least ``MIN_TASKS``
tasks with a known outcome on each side, a card goes up offering to roll the
version back. It never rolls back by itself: a person decides, the same as
they decided to publish. One card per version.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.db import db_client
from api.services import evolve
from api.services.evolve import experience, versions

#: Tasks with a known outcome needed on each side before anything is said.
MIN_TASKS = 5
#: How much lower the success rate must be to count as worse.
WORSE_BY = 0.2


def _rate(rows: list[Any]) -> tuple[float, int] | None:
    known = [r for r in rows if r.outcome in (experience.SUCCESS, experience.FAILURE)]
    if len(known) < MIN_TASKS:
        return None
    wins = sum(1 for r in known if r.outcome == experience.SUCCESS)
    return wins / len(known), len(known)


def compare(before: list[Any], after: list[Any]) -> dict[str, Any] | None:
    """The two rates when the later one is worse enough to say so."""
    b, a = _rate(before), _rate(after)
    if b is None or a is None:
        return None
    if a[0] <= b[0] - WORSE_BY:
        return {
            "before": {"rate": round(b[0], 3), "n": b[1]},
            "after": {"rate": round(a[0], 3), "n": a[1]},
        }
    return None


async def check_version(organization_id: int, row: Any) -> int | None:
    """Post the rollback card for one published version if it has earned
    one. Returns the card's id."""
    if row.status != evolve.PUBLISHED or row.published_at is None:
        return None
    monitor = dict((row.evaluation or {}).get("monitor") or {})
    if monitor.get("card_event_id"):
        return None
    attempts = list(
        await db_client.list_experience(
            organization_id=organization_id,
            task_family=row.slug,
            kinds=[evolve.ATTEMPT],
            limit=400,
        )
    )
    after = [
        r
        for r in attempts
        if r.skill_version == row.version and r.occurred_at >= row.published_at
    ]
    before = [r for r in attempts if r.skill_version == row.base_version]
    own = compare(before, after)

    # Negative transfer on the same agents' other work.
    agents = {r.workflow_id for r in after if r.workflow_id}
    others = [
        r
        for r in await db_client.list_experience(
            organization_id=organization_id,
            exclude_family=row.slug,
            kinds=[evolve.ATTEMPT],
            limit=400,
        )
        if r.workflow_id in agents
    ]
    unrelated = compare(
        [r for r in others if r.occurred_at < row.published_at],
        [r for r in others if r.occurred_at >= row.published_at],
    )
    if own is None and unrelated is None:
        return None
    event_id = await versions.post_card(
        organization_id,
        row,
        versions.CARD_DISABLE,
        extra={"related_outcomes": own, "unrelated_outcomes": unrelated},
    )
    monitor["card_event_id"] = event_id
    await db_client.update_skill_version(
        row.id,
        organization_id=organization_id,
        evaluation={**(row.evaluation or {}), "monitor": monitor},
    )
    return event_id


async def check(organization_id: int) -> int:
    """Every learned version in use in this workspace. Returns cards posted."""
    if not evolve.enabled(organization_id):
        return 0
    posted = 0
    rows = await db_client.list_skill_versions(
        organization_id=organization_id, statuses=[evolve.PUBLISHED]
    )
    for row in rows:
        if row.origin != evolve.ORIGIN_LEARNED:
            continue
        active = await db_client.active_skill_version(
            organization_id=organization_id, slug=row.slug
        )
        if active is None or active.id != row.id:
            continue
        try:
            posted += 1 if await check_version(organization_id, row) else 0
        except Exception as exc:  # noqa: BLE001 - one skill is not the rest
            logger.warning("evolve: could not watch {}: {}", row.slug, exc)
    return posted


__all__ = ["MIN_TASKS", "WORSE_BY", "check", "check_version", "compare"]
