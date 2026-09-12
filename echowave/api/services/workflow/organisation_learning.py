"""What the organisation learns from what its agents actually did.

The existing memory module remembers things about *people* -- a caller's name,
their address -- so the next call does not ask again. This one remembers things
about the *business*, and it learns them from actions rather than from a form.

Two kinds of thing come out of a finished call.

**Gaps.** A caller wanted something and no agent could answer or do it. The
call ended before the agent understood, or it escalated to a person, or a tool
it reached for failed. Each of those is the business finding out something
about itself: there is a question it cannot answer, or a system it cannot
reach. Counted rather than listed, because the same gap appearing forty times
is the thing worth acting on and forty separate rows is noise.

**Confirmations.** A call that reached an outside system and succeeded is
evidence that the setup works. Not interesting alone; interesting as the
denominator that makes a gap meaningful.

One rule governs all of it, and it is the reason this is safe to run on every
call: **nothing learned here ever reaches an agent's prompt.** Everything
arrives as ``learned`` and stays out of the agent's mouth until a person
confirms it. An agent that starts confidently telling callers something it
merely overheard is the failure that costs an account, and forty corroborations
do not substitute for somebody saying yes.

Deliberately not an LLM pass over the transcript. Every signal here is already
recorded as structure -- the intent the call reached, the actions it took and
whether they worked -- so reading it costs nothing per call, cannot hallucinate
a gap that never happened, and works retrospectively on calls already made.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional

from loguru import logger

from api.db import db_client

#: The subject everything here hangs off. Facts about the business itself
#: rather than about one of its customers.
SUBJECT_ORGANISATION = "organisation"
#: One organisation has one of these, so the key is a constant rather than an
#: id -- ``subject_key`` exists to separate customers, and there is only one
#: business.
SUBJECT_KEY_SELF = "self"

KIND_FACT = "fact"
KIND_GAP = "gap"

STATUS_LEARNED = "learned"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"

#: What the reports module calls a call that never got past the greeting.
#: Mirrors ``api/services/reports/call_intent.NO_INTENT``; imported rather than
#: re-spelled would be cleaner, and is why this constant carries the note.
NO_INTENT = "Didn't get that far"

#: Gap keys. Named constants because the screen groups by them and a typo
#: would create a second, invisible pile of the same problem.
GAP_NOT_UNDERSTOOD = "not_understood"
GAP_ESCALATED = "escalated"
GAP_APP_FAILED = "app_failed"

#: A gap's value is shown to a person, so it is trimmed to something readable.
#: Long enough for a real question, short enough that the screen stays a list.
MAX_GAP_CHARS = 160

#: Variables whose value is a question the caller asked rather than an answer
#: they gave. These are what a gap is actually *about*, and without one a gap
#: says only "somebody wanted something".
_QUESTION_SUFFIXES: tuple[str, ...] = ("_question", "_query", "_asked", "_request")


def _clean(text: Any) -> str:
    """One line, trimmed, safe to show."""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:MAX_GAP_CHARS]


def caller_question(gathered_context: Optional[Mapping[str, Any]]) -> str:
    """What the caller was asking about, if the agent captured it.

    Returns "" rather than a guess. A gap with no question is still worth
    counting -- it says the agent lost a caller -- and inventing a subject for
    it would put words in somebody's mouth on a screen an operator acts on.
    """
    variables = (gathered_context or {}).get("extracted_variables") or {}
    if not isinstance(variables, Mapping):
        return ""
    for key, value in variables.items():
        if any(str(key).lower().endswith(suffix) for suffix in _QUESTION_SUFFIXES):
            cleaned = _clean(value)
            if cleaned:
                return cleaned
    return ""


def observations_from_run(
    *,
    intent: Optional[str] = None,
    escalated: bool = False,
    gathered_context: Optional[Mapping[str, Any]] = None,
    interactions: Optional[Iterable[Any]] = None,
) -> list[dict[str, str]]:
    """What one finished call taught the business about itself.

    Returns observations rather than writing them, so the rules are testable
    without a database and so the same function can be run over calls already
    made.
    """
    observations: list[dict[str, str]] = []
    question = caller_question(gathered_context)

    # The agent never worked out what the caller wanted. The single most
    # valuable signal in the product: a rising count here is the agent failing
    # to understand people, which no outcome rate reveals on its own.
    if intent and intent.strip() == NO_INTENT:
        observations.append(
            {
                "kind": KIND_GAP,
                "key": GAP_NOT_UNDERSTOOD,
                "value": question or "A caller wanted something it did not understand",
            }
        )

    # It understood and handed over. Not a failure -- handing a real problem to
    # a person is correct behaviour -- but forty of the same handover is a job
    # the business is still doing by hand.
    if escalated:
        observations.append(
            {
                "kind": KIND_GAP,
                "key": GAP_ESCALATED,
                "value": question or "A caller had to be passed to a person",
            }
        )

    # A tool it reached for did not work. The business learning that one of its
    # own systems is unreachable, from the only place that finds out.
    for interaction in interactions or []:
        if getattr(interaction, "status", None) != "error":
            continue
        app = getattr(interaction, "app", None) or getattr(interaction, "name", None)
        if not app:
            continue
        observations.append(
            {
                "kind": KIND_GAP,
                "key": GAP_APP_FAILED,
                "value": _clean(app),
            }
        )

    # Same gap twice in one call is one gap. A caller who asked the same
    # unanswerable question twice has not doubled the problem.
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for observation in observations:
        signature = (observation["key"], observation["value"].lower())
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(observation)
    return unique


async def learn_from_run(
    *,
    organization_id: int,
    workflow_run_id: Optional[int],
    intent: Optional[str] = None,
    escalated: bool = False,
    gathered_context: Optional[Mapping[str, Any]] = None,
    interactions: Optional[Iterable[Any]] = None,
) -> int:
    """Record what this call taught the business. Never raises.

    Called after a call has already ended and the caller has already been
    answered, so there is nothing a failure here could usefully interrupt --
    and an exception escaping into post-call processing would lose the run's
    other bookkeeping alongside it.
    """
    observations = observations_from_run(
        intent=intent,
        escalated=escalated,
        gathered_context=gathered_context,
        interactions=interactions,
    )
    if not observations:
        return 0

    try:
        await db_client.remember_organisation_observations(
            organization_id=organization_id,
            source_run_id=workflow_run_id,
            observations=observations,
        )
    except Exception as error:  # noqa: BLE001 - see the docstring
        logger.warning(
            "Could not record what run {} taught the organisation: {}",
            workflow_run_id,
            error,
        )
        return 0
    return len(observations)
