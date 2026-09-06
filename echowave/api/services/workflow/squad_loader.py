"""Fetching the agents a squad is made of, before the pure splicer runs.

Kept apart from ``squad`` so the splicing rules stay testable without a
database, and because this is where the one security-relevant decision lives:
which agents a handoff is allowed to name.

**The organization check is here and nowhere else.** ``squad.assemble`` cannot
make it — it has no session and no idea who is calling — so it delegates by
asking for a loader and treating ``None`` as "no". Every lookup below goes
through the org-scoped getter, so a handoff naming another account's agent
resolves to nothing and assembly refuses the call rather than splicing in
somebody else's conversation, prompts and tools.

**The released version, not the draft.** A member is reused by other agents,
and half-finished edits to it must not reach a live call through a squad that
somebody else owns.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.db import db_client
from api.services.workflow.squad import (
    MAX_DEPTH,
    assemble,
    handoff_targets,
    has_handoffs,
)


async def _fetch(reference: str, organization_id: int) -> dict | None:
    """One member's released workflow JSON, or ``None`` if it is not ours."""
    workflow = await db_client.get_workflow_by_uuid(reference, organization_id)
    if workflow is None:
        return None

    definition = workflow.released_definition or workflow.current_definition
    if definition is None:
        return None
    return definition.workflow_json


async def collect_members(
    workflow_json: Any, *, organization_id: int
) -> dict[str, dict]:
    """Every agent this squad reaches, by reference.

    Walked breadth-first with a visited set, so two handoffs to the same
    specialist cost one query and a circular squad terminates here rather than
    in the splicer — which would also terminate, but only after fetching the
    same two agents until it hit the depth limit.
    """
    members: dict[str, dict] = {}
    frontier = handoff_targets(workflow_json)
    seen: set[str] = set()

    for _ in range(MAX_DEPTH):
        # Deduplicated within the level as well as across levels. Two handoffs
        # to the same specialist put its reference in the frontier twice, and
        # the visited set alone does not catch that — both are pending before
        # either has been marked, so it is fetched once per handoff.
        pending = list(dict.fromkeys(ref for ref in frontier if ref not in seen))
        if not pending:
            break
        frontier = []
        for reference in pending:
            seen.add(reference)
            member = await _fetch(reference, organization_id)
            if member is None:
                # Left absent deliberately. The splicer refuses a member it
                # cannot resolve, and it is the one that can say which handoff
                # the missing agent was behind.
                logger.warning(
                    f"Handoff names an agent that is not available to org "
                    f"{organization_id}"
                )
                continue
            members[reference] = member
            frontier.extend(handoff_targets(member))

    return members


async def assemble_for_run(workflow_json: Any, *, organization_id: int) -> Any:
    """This workflow with its handoffs replaced, ready for ``WorkflowGraph``.

    Returns the workflow untouched when it has no handoffs, which is almost
    every call — so a squad costs queries only where one exists.
    """
    # `has_handoffs`, not `handoff_targets`: a handoff with no agent chosen has
    # no target, and skipping assembly for it would leave the node in the graph
    # for the runtime to walk into. The splicer refuses it; this makes sure the
    # splicer is asked.
    if not has_handoffs(workflow_json):
        return workflow_json

    members = await collect_members(workflow_json, organization_id=organization_id)
    return assemble(workflow_json, load_member=members.get)
