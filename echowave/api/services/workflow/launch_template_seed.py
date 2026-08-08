"""Install the launch templates so a fresh deployment is not an empty canvas.

Runs at startup, next to the platform key seeding, for the same reason: a box
that comes up should be usable without somebody logging in and building
something by hand first.

Matched by name. A template whose definition has changed is updated in place;
one that is already identical is left alone, so a restart does not churn the
table. Templates an operator added themselves are never touched — this only
knows about its own four.

Never raises. A template that will not validate is skipped with a loud log
rather than stopping the API from starting: a missing template is a worse
first-run experience, a process that will not boot is an outage.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from api.db import db_client
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.launch_templates import build_all
from api.services.workflow.workflow_graph import WorkflowGraph


@dataclass(frozen=True)
class SeedResult:
    created: list[str]
    updated: list[str]
    unchanged: list[str]
    skipped: list[str]

    @property
    def total(self) -> int:
        return (
            len(self.created)
            + len(self.updated)
            + len(self.unchanged)
            + len(self.skipped)
        )


def _is_valid(definition: dict) -> bool:
    """Whether this definition would open in the editor.

    Checked here as well as in the tests because a template reaching a customer
    broken is the failure that matters, and the tests only run where somebody
    remembers to run them.
    """
    try:
        WorkflowGraph(ReactFlowDTO.model_validate(definition))
    except Exception as e:
        logger.error("Launch template failed validation: {}", e)
        return False
    return True


async def seed_launch_templates() -> SeedResult:
    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    skipped: list[str] = []

    for name, description, definition in build_all():
        if not _is_valid(definition):
            skipped.append(name)
            continue

        try:
            existing = await db_client.get_workflow_template_by_name(name)
        except Exception as e:
            logger.error("Could not read workflow template {!r}: {}", name, e)
            skipped.append(name)
            continue

        try:
            if existing is None:
                await db_client.create_workflow_template(
                    template_name=name,
                    template_description=description,
                    template_json=definition,
                )
                created.append(name)
            elif (
                existing.template_json != definition
                or existing.template_description != description
            ):
                await db_client.update_workflow_template(
                    template_id=existing.id,
                    template_description=description,
                    template_json=definition,
                )
                updated.append(name)
            else:
                unchanged.append(name)
        except Exception as e:
            logger.error("Could not install workflow template {!r}: {}", name, e)
            skipped.append(name)

    if created or updated:
        logger.info(
            "Launch templates installed: {} created, {} updated, {} unchanged.",
            len(created),
            len(updated),
            len(unchanged),
        )
    if skipped:
        logger.warning("Launch templates skipped: {}", ", ".join(skipped))

    return SeedResult(
        created=created, updated=updated, unchanged=unchanged, skipped=skipped
    )
