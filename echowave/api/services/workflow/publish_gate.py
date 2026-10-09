"""The one gate a draft passes to become the version that answers.

A published version is what runtime executes, so publishing is where the
full DTO + graph + trigger-conflict checks must pass, where the instructions
are read against the acceptable use policy, and where the act is written to
the audit log. The editor's Publish button and the Publish button on a card
a bot proposed in a thread both come through ``publish_draft`` -- a second
path that called ``db_client.publish_workflow_draft`` directly skipped all
three, which is how a chat-proposed change could go live unchecked and
unrecorded. A card publishes only its own change, through
``publish_definition``: the same three checks on the graph that goes live,
with the rest of the draft left a draft.

Routes stay thin: they map the exceptions below onto HTTP; the card maps
them onto a line in the thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from pydantic import ValidationError

from api.db import db_client
from api.services.compliance import acceptable_use, ai_disclosure
from api.services.workflow import audit_log, unfilled
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.errors import ItemKind, WorkflowError
from api.services.workflow.squad import SquadError, has_handoffs
from api.services.workflow.squad_loader import (
    assemble_for_run,
    validate_for_organization,
)
from api.services.workflow.trigger_paths import (
    extract_trigger_paths,
    trigger_path_to_node_id,
    validate_trigger_paths,
)
from api.services.workflow.workflow_graph import WorkflowGraph

#: Where a publish came from, written on its audit row.
VIA_EDITOR = "editor"
VIA_EDIT_CARD = "edit_card"


async def validate_definition(
    workflow_definition: dict | None,
    exclude_workflow_id: int | None = None,
    organization_id: int | None = None,
) -> list[WorkflowError]:
    """Run DTO + graph + trigger-conflict checks on a workflow definition.

    Returns the list of errors (empty if the definition is valid). This is
    the single source of truth for "is this workflow valid?" — used by the
    /validate route (read-only audit) and by ``publish_draft`` below, the
    gate every publish goes through.
    """
    errors: list[WorkflowError] = []
    if not workflow_definition:
        return errors

    # ----------- DTO Validation ------------
    dto: ReactFlowDTO | None = None
    try:
        dto = ReactFlowDTO.model_validate(workflow_definition)
    except ValidationError as exc:
        errors.extend(transform_schema_errors(exc, workflow_definition))

    # ----------- Graph Validation if DTO is valid ------------
    try:
        if dto:
            WorkflowGraph(dto)
    except ValueError as e:
        errors.extend(e.args[0])

    # ----------- Squad Assembly Check ------------
    # A handoff is replaced by the agent it names when the call starts, so the
    # graph that actually runs is not the one validated above. Assembling it
    # here turns "the squad is a circle" and "that agent was deleted" into
    # errors at save, rather than a call that fails while somebody is on the
    # line — which is the only time anyone would otherwise find out.
    # Bound before the branch: the assembly check below reads it, and a
    # workflow with no handoffs never enters the block that fills it.
    squad_problems: list = []
    if dto and organization_id is not None and has_handoffs(workflow_definition):
        # Ask what is wrong before asking it to be built. `assemble_for_run`
        # raises on the first problem and has nowhere to attach it, so a squad
        # with three broken handoffs reported one of them as a banner about the
        # workflow and left the reader to find which step it meant. The
        # validator answers the same five questions, in the same words, for
        # every handoff, and names the node each belongs to.
        squad_problems = await validate_for_organization(
            workflow_definition, organization_id=organization_id
        )
        errors.extend(
            WorkflowError(
                kind=ItemKind.node if problem.node_id else ItemKind.workflow,
                id=problem.node_id,
                field=None,
                message=problem.message,
            )
            for problem in squad_problems
        )

    # Assembly is only worth attempting on a squad the validator passed:
    # otherwise it would raise the first of the problems just reported, as a
    # duplicate with less information attached. What it still catches is the
    # case the validator cannot — two agents that are each valid alone and do
    # not fit together once spliced.
    if (
        dto
        and organization_id is not None
        and has_handoffs(workflow_definition)
        and not squad_problems
    ):
        try:
            assembled = await assemble_for_run(
                workflow_definition, organization_id=organization_id
            )
            WorkflowGraph(ReactFlowDTO.model_validate(assembled))
        except SquadError as exc:
            # A safety net now rather than the main path: reaching this means
            # the splicer refused something the validator did not predict.
            errors.append(
                WorkflowError(
                    kind=ItemKind.workflow,
                    id=None,
                    field=None,
                    message=str(exc),
                )
            )
        except ValidationError as exc:
            errors.extend(transform_schema_errors(exc, workflow_definition))
        except ValueError as exc:
            # The assembled graph broke a rule the parts kept on their own —
            # two agents that are each fine and do not fit together.
            errors.extend(
                exc.args[0]
                if exc.args and isinstance(exc.args[0], list)
                else [
                    WorkflowError(
                        kind=ItemKind.workflow,
                        id=None,
                        field=None,
                        message=str(exc),
                    )
                ]
            )

    # ----------- Trigger Path Format Check ------------
    for issue in validate_trigger_paths(workflow_definition):
        errors.append(
            WorkflowError(
                kind=ItemKind.node,
                id=issue.node_id,
                field="data.trigger_path",
                message=issue.message,
            )
        )

    # ----------- AI-identity line (FD-1) ------------
    # May be switched off only with the acknowledgement; a draft that has it
    # off and unacknowledged does not publish.
    errors.extend(ai_disclosure.problems(workflow_definition))

    # ----------- Unanswered placeholders ------------
    # A caller heard "Namaste, ." on 20 Sept because {{clinic_name}} was never
    # answered. The template already called these "values the operator must
    # supply before going live"; nobody was checking.
    errors.extend(unfilled.problems(workflow_definition))

    # ----------- Trigger Path Conflict Check ------------
    trigger_paths = extract_trigger_paths(workflow_definition)
    if trigger_paths:
        conflicts = await db_client.check_trigger_path_conflicts(
            trigger_paths=trigger_paths,
            exclude_workflow_id=exclude_workflow_id,
        )
        if conflicts:
            path_to_node = trigger_path_to_node_id(workflow_definition)
            for conflicting_path in conflicts:
                errors.append(
                    WorkflowError(
                        kind=ItemKind.node,
                        id=path_to_node.get(conflicting_path),
                        field="data.trigger_path",
                        message=(
                            "Trigger path is already in use. Please choose "
                            "another one or leave empty to use a unique one "
                            "generated by the server."
                        ),
                    )
                )

    return errors


def transform_schema_errors(
    exc: ValidationError, workflow_definition: dict
) -> list[WorkflowError]:
    out: list[WorkflowError] = []

    for err in exc.errors():
        loc = err["loc"]
        idx = workflow_definition[loc[0]][loc[1]]["id"]

        kind: ItemKind = ItemKind.node if loc[0] == "nodes" else ItemKind.edge

        out.append(
            WorkflowError(
                kind=kind,
                id=idx,
                field=".".join(str(p) for p in err["loc"][2:]) or None,
                message=err["msg"].capitalize(),
            )
        )
    return out


class PublishError(Exception):
    """A publish that did not happen. ``str()`` is a sentence for a person."""


class WorkflowNotFound(PublishError):
    pass


class NoDraft(PublishError):
    pass


class LiveMoved(PublishError):
    """Another publish landed between reading the live version and writing
    the new one; nothing was published."""


class DraftInvalid(PublishError):
    """The draft fails validation; nothing was published."""

    def __init__(self, errors: list[WorkflowError]):
        self.errors = errors
        super().__init__(
            "This change cannot go live yet: " + "; ".join(reasons_from_errors(errors))
        )


class PolicyFindings(PublishError):
    """The acceptable-use screen named a clause and the caller asked for a
    refusal on findings; nothing was published."""

    def __init__(self, findings: list[acceptable_use.Finding]):
        self.findings = findings
        super().__init__(
            "This change was not published because it may breach the acceptable "
            "use policy: " + "; ".join(reasons_from_findings(findings))
        )


def _kind_name(kind: Any) -> str:
    return str(getattr(kind, "value", kind) or "")


def reasons_from_errors(errors: list[WorkflowError], *, limit: int = 5) -> list[str]:
    reasons = []
    for e in errors:
        message = str(e.get("message") or "").strip()
        if not message:
            continue
        # "Field required" alone tells nobody what to fix.
        where = " ".join(
            str(part)
            for part in (e.get("field"), "on", _kind_name(e.get("kind")), e.get("id"))
            if part
        )
        reasons.append(f"{message} ({where})" if e.get("id") else message)
    if len(reasons) > limit:
        reasons = reasons[:limit] + [f"and {len(reasons) - limit} more"]
    return reasons or ["the draft did not pass validation"]


def reasons_from_findings(findings: list[acceptable_use.Finding]) -> list[str]:
    out = []
    for f in findings:
        line = f.title or f.clause
        if f.quote:
            line += f" (\u201c{f.quote}\u201d)"
        if f.why:
            line += f" \u2014 {f.why}"
        out.append(line)
    return out


@dataclass
class Published:
    definition: Any
    workflow: Any
    findings: list[acceptable_use.Finding] = field(default_factory=list)


async def _check(
    definition: dict | None,
    *,
    workflow_id: int,
    organization_id: int,
    via: str,
    refuse_on_findings: bool,
) -> list[acceptable_use.Finding]:
    """Validation, then the acceptable-use screen, on what is about to go
    live. Raises ``DraftInvalid`` or ``PolicyFindings``; returns the
    findings it only warns about."""
    errors = await validate_definition(
        definition,
        exclude_workflow_id=workflow_id,
        organization_id=organization_id,
    )
    if errors:
        raise DraftInvalid(errors)

    # Here rather than on every save: a draft is work in progress and a
    # warning on each keystroke is a warning nobody reads, while publishing
    # is the moment this becomes the thing that answers the phone. `screen`
    # cannot raise.
    async with db_client.async_session() as session:
        findings = await acceptable_use.screen(
            session,
            instructions=acceptable_use.instructions_in(definition),
        )
    if findings:
        logger.warning(
            "Acceptable-use findings on workflow {} for org {} (via {}): {}",
            workflow_id,
            organization_id,
            via,
            [f.clause for f in findings],
        )
        if refuse_on_findings:
            raise PolicyFindings(findings)
    return findings


async def publish_draft(
    *,
    workflow_id: int,
    organization_id: int,
    user_id: int | None,
    via: str = VIA_EDITOR,
    refuse_on_findings: bool = False,
) -> Published:
    """Validate, screen, publish and record the draft of this agent.

    Org-scoped: the workflow is fetched with ``organization_id``, so an id
    from a request body or a card payload cannot reach another tenant's bot.

    ``refuse_on_findings`` is False for the editor, where a person wrote the
    instructions and the screen warns rather than refuses (see
    ``acceptable_use``). The card passes True: the text there was written by
    the bot on somebody's say-so in a chat, and one click should not put a
    flagged instruction on the phone; the card names the clause instead.
    """
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise WorkflowNotFound(f"Workflow with id {workflow_id} not found")

    draft = await db_client.get_draft_version(workflow_id)
    if draft is None:
        raise NoDraft("No draft to publish")

    findings = await _check(
        draft.workflow_json,
        workflow_id=workflow_id,
        organization_id=organization_id,
        via=via,
        refuse_on_findings=refuse_on_findings,
    )

    try:
        published = await db_client.publish_workflow_draft(workflow_id)
    except ValueError as exc:
        # The draft went between the read above and now: somebody published
        # or discarded it from another tab.
        raise NoDraft(str(exc)) from exc

    await audit_log.record(
        organization_id,
        action=audit_log.AGENT_PUBLISHED,
        subject_kind="agent",
        subject_id=workflow_id,
        subject=getattr(workflow, "name", None),
        actor_user_id=user_id,
        after={"version_number": published.version_number, "via": via},
    )
    return Published(definition=published, workflow=workflow, findings=findings)


async def publish_definition(
    *,
    workflow_id: int,
    organization_id: int,
    user_id: int | None,
    workflow_json: dict,
    based_on_definition_id: int,
    via: str = VIA_EDIT_CARD,
    refuse_on_findings: bool = True,
    workflow_configurations: dict | None = None,
    rewrite_draft_configurations=None,
    check_graph: bool = True,
) -> Published:
    """Validate, screen, publish and record one graph, leaving the draft a draft.

    For a change that is not the whole draft: an edit card publishes its own
    change on top of the live version, and whatever else is waiting in the
    draft stays there (``db_client.publish_workflow_json``). The same three
    checks as ``publish_draft`` -- the graph checked and screened is the one
    that goes live, not the draft. Org-scoped like ``publish_draft``.

    ``based_on_definition_id`` is the live version ``workflow_json`` was
    built from; a publish that got in first raises ``LiveMoved``.
    ``workflow_configurations`` and ``rewrite_draft_configurations`` are for
    a card that changed a configuration rather than the graph (see
    ``db_client.publish_workflow_json``).
    """
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise WorkflowNotFound(f"Workflow with id {workflow_id} not found")

    # A configuration-only change puts the live graph back live unchanged:
    # checking it again would refuse the change for a graph it did not
    # touch (one published before a newer validation rule, say).
    findings = (
        await _check(
            workflow_json,
            workflow_id=workflow_id,
            organization_id=organization_id,
            via=via,
            refuse_on_findings=refuse_on_findings,
        )
        if check_graph
        else []
    )

    try:
        published = await db_client.publish_workflow_json(
            workflow_id,
            workflow_json=workflow_json,
            based_on_definition_id=based_on_definition_id,
            workflow_configurations=workflow_configurations,
            rewrite_draft_configurations=rewrite_draft_configurations,
        )
    except ValueError as exc:
        raise LiveMoved(str(exc)) from exc

    await audit_log.record(
        organization_id,
        action=audit_log.AGENT_PUBLISHED,
        subject_kind="agent",
        subject_id=workflow_id,
        subject=getattr(workflow, "name", None),
        actor_user_id=user_id,
        after={"version_number": published.version_number, "via": via},
    )
    return Published(definition=published, workflow=workflow, findings=findings)
