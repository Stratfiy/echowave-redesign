"""What each bot achieved, counted.

The taxonomy has existed for months and the classifier has been filling it in
on every completed run. Nothing read it back. An operator could tick "booked"
as an outcome, watch a hundred calls get classified, and have no way to ask
the only question they actually have -- *how many did it book* -- short of
paging the calls list and counting chips by eye.

Per bot, because that is the unit somebody judges. An org-wide outcome number
mixes the clinic's bookings with the collections agent's payment promises and
means nothing; "this bot booked 41 of 212" is a sentence a business acts on.

Read from ``annotations['disposition']['dispositions']``, which is where
``run_integrations`` writes it -- the same field the calls list draws its
chips from, so the total here and the rows there cannot drift.

Counted in Python rather than SQL. ``annotations`` is a ``JSON`` column, not
``JSONB``, so there is no index to exploit and the array-expanding operators
buy nothing; a bot's runs over a month is a small enough set to add up, and
the alternative is a query that silently returns zero the day somebody writes
a non-list into that key.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import select

from api.db import db_client
from api.db.models import WorkflowModel, WorkflowRunModel
from api.services.workflow.disposition import parse_taxonomy

#: The windows the UI offers. Anything else is rejected rather than clamped:
#: a caller asking for 365 days wants a year, and quietly handing them 90
#: would be a number labelled wrong.
WINDOWS: tuple[int, ...] = (7, 30, 90)

#: A bot with more runs than this in the window still gets a count -- the cap
#: is on rows loaded per bot, newest first, and the response says when it bit.
#: Without it, one campaign bot with 200k runs decides the page's latency.
MAX_RUNS = 5_000


@dataclass(frozen=True)
class OutcomeCount:
    """One row of the board: a configured outcome and how often it happened."""

    code: str
    label: str
    count: int


@dataclass(frozen=True)
class BoardEntry:
    """One bot's results over the window."""

    workflow_id: int
    name: str
    #: Every configured outcome, including the ones at zero. A taxonomy shown
    #: only where it fired reads as "this bot does three things"; the zeros
    #: are the finding -- nobody has ever been booked.
    outcomes: list[OutcomeCount] = field(default_factory=list)
    #: Runs in the window, classified or not.
    runs: int = 0
    #: Runs carrying at least one outcome. The denominator that makes the
    #: counts honest: a bot whose classifier has been failing shows a hundred
    #: runs and two classified, rather than looking like it achieved nothing.
    classified: int = 0
    #: True when MAX_RUNS bit, so the UI can say "the most recent 5,000"
    #: instead of presenting a partial count as the whole.
    truncated: bool = False
    #: Whether these outcomes are this bot's own or the default set.
    #:
    #: ``parse_taxonomy`` falls back rather than returning nothing, which is
    #: right for the classifier -- a bot nobody configured still sorts its
    #: runs -- and would be a lie on screen. "Booked: 0" under a heading the
    #: business chose is a finding; the same row under a list they have never
    #: seen is an invitation to go and choose one.
    configured: bool = False


def codes_on(annotations: Any) -> list[str]:
    """The outcome codes on one run, tolerating everything the column holds.

    Written by several versions of the classifier into a plain ``JSON``
    column, so this must survive a dict where a list is expected, a null, and
    a list of numbers, and cost only that run when it finds one.
    """
    if not isinstance(annotations, Mapping):
        return []
    disposition = annotations.get("disposition")
    if not isinstance(disposition, Mapping):
        return []
    raw = disposition.get("dispositions")
    if not isinstance(raw, (list, tuple)):
        return []
    return [entry for entry in raw if isinstance(entry, str) and entry]


def tally(
    taxonomy: Sequence[Mapping[str, str]], annotations: Iterable[Any]
) -> tuple[list[OutcomeCount], int, int]:
    """Counts per configured code, plus runs seen and runs classified.

    A code the classifier produced that is no longer in the taxonomy is
    dropped from the rows but still counts as classified. Somebody who
    renames an outcome has not un-happened the calls it already labelled, and
    a run vanishing from the denominator would make the remaining rates look
    better than they are.
    """
    counter: Counter[str] = Counter()
    runs = 0
    classified = 0
    for entry in annotations:
        runs += 1
        codes = codes_on(entry)
        if codes:
            classified += 1
        counter.update(set(codes))
    rows = [
        OutcomeCount(
            code=item["code"], label=item["label"], count=counter.get(item["code"], 0)
        )
        for item in taxonomy
    ]
    return rows, runs, classified


def is_configured(configurations: Any) -> bool:
    """Whether this bot has outcomes of its own, as opposed to the default set.

    Deliberately not "is the taxonomy non-empty": ``parse_taxonomy`` never
    returns empty. This asks the question the screen actually needs -- has
    anybody here decided what a win is -- and a configuration block holding
    an empty list, or thirty malformed entries, answers no.
    """
    if not isinstance(configurations, Mapping):
        return False
    raw = configurations.get("call_outcomes")
    if not isinstance(raw, (list, tuple)):
        return False
    return any(
        isinstance(entry, Mapping) and entry.get("code") for entry in raw
    ) or any(isinstance(entry, str) and entry for entry in raw)


def _since(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


async def board(
    organization_id: int, *, days: int = 30, workflow_id: int | None = None
) -> list[BoardEntry]:
    """Every bot in the account with its outcome counts, newest bots first.

    One query for the bots and one per bot for the runs, rather than a single
    join: the per-bot cap is what keeps a campaign account fast, and a cap
    cannot be applied per group in one statement without a window function
    over a column with no index behind it.
    """
    async with db_client.async_session() as session:
        query = (
            select(WorkflowModel)
            .where(WorkflowModel.organization_id == organization_id)
            .order_by(WorkflowModel.id.desc())
        )
        if workflow_id is not None:
            query = query.where(WorkflowModel.id == workflow_id)
        workflows = (await session.execute(query)).scalars().all()

        entries: list[BoardEntry] = []
        since = _since(days)
        for workflow in workflows:
            annotations = (
                (
                    await session.execute(
                        select(WorkflowRunModel.annotations)
                        .where(WorkflowRunModel.workflow_id == workflow.id)
                        .where(WorkflowRunModel.created_at >= since)
                        .order_by(WorkflowRunModel.created_at.desc())
                        .limit(MAX_RUNS)
                    )
                )
                .scalars()
                .all()
            )
            configurations = workflow.workflow_configurations
            taxonomy = parse_taxonomy(configurations)
            rows, runs, classified = tally(taxonomy, annotations)
            entries.append(
                BoardEntry(
                    workflow_id=workflow.id,
                    name=workflow.name,
                    outcomes=rows,
                    runs=runs,
                    classified=classified,
                    truncated=runs >= MAX_RUNS,
                    configured=is_configured(configurations),
                )
            )
        return entries
