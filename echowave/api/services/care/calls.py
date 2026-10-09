"""Reminder calls: placed on time, once, through the existing voice pipeline,
and the family told when a dose is missed or a call goes unanswered.

The scheduler (``tick``, every minute from the ARQ worker) writes one
``care_dose_calls`` row per due dose -- unique per (medicine, due time), so
two ticks or two workers make one row -- and only the tick that inserted the
row places the call. The call is the platform's own outbound path
(``telephony.outbound.dial_workflow``: concurrency slot, run, quota, dial),
ringing a reminder agent made for the workspace from
``services/care/reminder_call``: it says it is Decibyl, names the medicine
as the person wrote it, asks whether it has been taken, and gives no advice.

What happened comes back in three ways:

* the call finished (``record_run_outcome``, from post-call processing):
  taken, not taken, not answered, or unclear;
* nothing came back within ``CARE_CALL_ANSWER_MINUTES`` (``sweep``): the
  call is reconciled against its run (``reconcile``) -- the carrier's
  no-answer is "not answered"; silence is ``unknown``, not "not answered";
  a dose whose run was never recorded was never dialled, and the family is
  told Decibyl could not call;
* the person tapped "I took it" in the app (``medicines.mark_taken``).

Anything but taken tells the family members named on the reminder, if they
still share medicine alerts -- at most once per dose. Late evidence repairs
an ``unknown`` or a "not answered" (``outcome_history`` keeps both); the
only second alert is a correction when the family was told "did not answer"
and the person in fact answered and took it.

The run is recorded on the dose before the provider is asked
(``dial_workflow``'s ``on_run_created``), so a dial that raises afterwards
is ``unknown`` (it may have rung), and one that raises before is a
verified "could not call".

Where ``reminder_calls`` is on for the workspace, these calls take a slot
of the person's daily call allowance (``call_when_done.allowance``), the one
cap of 5 shared with call-when-done and reminder calls on the person's local
day (decision D3 in docs/plans/reminder-calls.md, default chosen pending the
founder: ``reminder_calls.policy.CARE_SHARES_THE_CAP``). The slot is
reserved right before the dial and given back only on a verified
non-dispatch; past the cap the dose is ``failed`` (``daily_cap``) and the
family told Decibyl could not call. With ``reminder_calls`` off, nothing
changes: these calls are uncapped, at the times the person confirmed.
The calling-window exemption below is unchanged either way (D2).

``CARE_CALLS_FAKE`` replaces the dial with a simulated outcome for local
runs and tests; it is ignored in every other environment, and where it
applies the status says "test mode", so a simulated call is never mistaken
for a real one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from api import constants
from api.db import db_client
from api.db.care_models import CareDoseCallModel, CareMedicineModel
from api.services import features
from api.services.care import MEDICINE_CALLS, CareError
from api.services.care import medicines as medicines_service

CALLING = "calling"
#: An app reminder (no phone call) shown and sent; waits for "I took it".
REMINDED = "reminded"
#: What a dose waits in before it has an outcome.
WAITING = (CALLING, REMINDED)
#: Said wherever a phone call cannot be placed: the way past is a reminder
#: in Decibyl, which needs no number.
APP_INSTEAD = (
    "Until then, Decibyl can remind you in Decibyl instead; that needs no phone number."
)
TAKEN = "taken"
NOT_TAKEN = "not_taken"
NOT_ANSWERED = "not_answered"
UNCLEAR = "unclear"
FAILED = "failed"
#: Claimed by a tick, then paused or removed before the dial: nothing rang,
#: and nobody is told about a dose the person stopped.
CANCELLED = "cancelled"
#: The call may have rung and nothing has proved what happened (a timeout
#: after the provider accepted it; no report by the answer window). Never
#: re-dialled; reconciled against the run; the family is not told "did not
#: answer" on silence (``reconcile``).
UNKNOWN = "unknown"
#: The outcomes the family is told about.
TELL_FAMILY = (NOT_TAKEN, NOT_ANSWERED, UNCLEAR, FAILED)

#: A dose this late is still called; older than this, a tick that was down
#: (a deploy, a restart) does not ring somebody about a dose long past.
LATE_MINUTES = 10

FAKE_OUTCOMES = {"taken": TAKEN, "not_taken": NOT_TAKEN, "no_answer": NOT_ANSWERED}
FAKE_ENVIRONMENTS = frozenset({"local", "dev", "development", "test"})


class CallRefused(CareError):
    """The call was not placed; ``reason`` is a code, the message a sentence."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _now() -> datetime:
    return datetime.now(UTC)


def fake_mode() -> str | None:
    """The simulated outcome, or None. Never in production."""
    value = getattr(constants, "CARE_CALLS_FAKE", "") or ""
    if not value or value not in FAKE_OUTCOMES:
        return None
    # Only where nobody could be misled: a local run or the tests. Any other
    # environment (production, staging, anything new) dials for real.
    if str(getattr(constants, "ENVIRONMENT", "")).lower() not in FAKE_ENVIRONMENTS:
        return None
    return value


async def readiness(organization_id: int) -> dict[str, str]:
    """Whether reminder calls can be placed here, and if not, why.

    ``ready``, ``test_mode`` or ``needs_setup`` -- never a "ready" that is
    not: no outbound line means no call, said before anything is saved."""
    if fake_mode():
        return {
            "state": "test_mode",
            "reason": "Test mode: calls are simulated and nobody is rung.",
        }
    try:
        row = await db_client.get_default_telephony_configuration(organization_id)
    except Exception as exc:  # noqa: BLE001 - a status read
        logger.warning("Could not read telephony for care calls: {}", exc)
        row = None
    if not row:
        return {
            "state": "needs_setup",
            "reason": (
                "Reminder calls need a phone line for calling out. This "
                "workspace does not have one yet."
            ),
        }
    return {
        "state": "ready",
        "reason": "Calls go out from this workspace's phone line.",
    }


# --- the scheduler ---------------------------------------------------------


async def tick(now: datetime | None = None) -> int:
    """Write and place every dose that has come due. Returns calls placed."""
    if not features.on_anywhere(MEDICINE_CALLS):
        return 0
    now = now or _now()
    async with db_client.async_session() as session:
        meds = (
            await session.scalars(
                select(CareMedicineModel)
                .where(CareMedicineModel.state == medicines_service.ACTIVE)
                .order_by(CareMedicineModel.id)
                .limit(2000)
            )
        ).all()
    placed = 0
    for med in meds:
        if not features.is_on(MEDICINE_CALLS, med.organization_id):
            continue
        for due in _due_now(med, now):
            dose_id = await _claim(med, due)
            if dose_id is None:
                continue
            await place(dose_id)
            placed += 1
    return placed


def _due_now(med: Any, now: datetime) -> list[datetime]:
    from zoneinfo import ZoneInfo

    local = now.astimezone(ZoneInfo(med.timezone))
    days = {local.date(), (local - timedelta(minutes=LATE_MINUTES)).date()}
    out = []
    for day in days:
        for due in medicines_service.due_times(med, day):
            if due <= now < due + timedelta(minutes=LATE_MINUTES):
                out.append(due)
    return out


async def _claim(med: Any, due: datetime) -> int | None:
    """The dose's row, if this caller made it; None if it already existed
    (another tick placed it, or the person already marked it taken)."""
    async with db_client.async_session() as session:
        inserted = (
            await session.execute(
                insert(CareDoseCallModel)
                .values(
                    organization_id=med.organization_id,
                    medicine_id=med.id,
                    due_at=due,
                    state=REMINDED if med.channel == "app" else CALLING,
                    created_at=_now(),
                )
                .on_conflict_do_nothing(constraint="uq_care_dose_due")
                .returning(CareDoseCallModel.id)
            )
        ).first()
        await session.commit()
    return inserted[0] if inserted else None


async def place(dose_id: int) -> None:
    """Ring the person for one dose. Never raises: a refusal is the dose's
    state, and the family is told."""
    async with db_client.async_session() as session:
        dose = await session.get(CareDoseCallModel, dose_id)
        med = await session.get(CareMedicineModel, dose.medicine_id) if dose else None
    if dose is None or med is None or med.organization_id != dose.organization_id:
        return
    # Re-read right before ringing: the tick read the medicine list earlier,
    # and the person may have tapped "I took it" (the dose is settled) or
    # paused or removed the reminder (the medicine is not active) since.
    if dose.state not in WAITING:
        return
    if med.state != medicines_service.ACTIVE:
        await _withdraw(dose_id)
        return
    if med.channel == "app":
        await _remind_in_app(med, dose)
        return
    fake = fake_mode()
    if fake:
        logger.info("Care call for dose {} simulated ({})", dose_id, fake)
        await settle(dose_id, FAKE_OUTCOMES[fake], reason="test_mode")
        return
    if not await _hold_slot(med, dose_id):
        await settle(dose_id, FAILED, reason="daily_cap")
        return

    async def link(run_id: int) -> None:
        # Only while the dose is still waiting on this call. If it was
        # settled meanwhile ("I took it", or a sweep that found it never
        # dialled), raising here stops dial_workflow before the provider is
        # asked.
        if not await _link_run(dose_id, med.organization_id, run_id, claimed=True):
            raise _Superseded()

    try:
        run_id = await _dial(med, dose, on_run_created=link)
    except _Superseded:
        logger.info("Care call for dose {} settled before the dial", dose_id)
        await _give_slot_back(med, dose_id)
        return
    except CallRefused as exc:
        # Refused before the provider was asked: nothing rang, and that is
        # what the family is told.
        await _give_slot_back(med, dose_id)
        await settle(dose_id, FAILED, reason=exc.reason)
        return
    except Exception as exc:  # noqa: BLE001 - the dose must say something
        logger.error("Care call for dose {} raised while dialling: {!r}", dose_id, exc)
        async with db_client.async_session() as session:
            linked = await session.scalar(
                select(CareDoseCallModel.workflow_run_id).where(
                    CareDoseCallModel.id == dose_id
                )
            )
        if linked is None:
            # No run recorded: the provider was never asked (dial_workflow
            # records the run first). Verified: Decibyl could not call.
            await _give_slot_back(med, dose_id)
            await settle(dose_id, FAILED, reason="call_error")
        else:
            # The provider may have rung (a timeout after it accepted, say):
            # unknown, reconciled, and the family not told "could not call".
            await reconcile(dose_id, unknown_reason="dial_unconfirmed")
        return
    await _link_run(dose_id, med.organization_id, run_id)


class _Superseded(Exception):
    """The dose was settled before the provider was asked."""


def _shares_the_cap(organization_id: int) -> bool:
    from api.services import reminder_calls
    from api.services.reminder_calls import policy

    return policy.CARE_SHARES_THE_CAP and reminder_calls.enabled(organization_id)


async def _hold_slot(med: Any, dose_id: int) -> bool:
    """A slot of the person's day for this call, where the cap is shared
    (see the module docstring); always True where it is not."""
    if not _shares_the_cap(med.organization_id):
        return True
    from api.services.call_when_done import allowance

    return await allowance.hold(
        CareDoseCallModel,
        dose_id,
        timezone_name=med.timezone,
        now=_now(),
        user_id=med.person_user_id,
    )


async def _give_slot_back(med: Any, dose_id: int) -> None:
    """A verified non-dispatch gives its slot back. A no-op for a dose that
    holds none (always, while the cap is not shared)."""
    from api.services.call_when_done import allowance

    await allowance.give_back(CareDoseCallModel, dose_id, user_id=med.person_user_id)


async def _link_run(
    dose_id: int, organization_id: int, run_id: int, *, claimed: bool = False
) -> bool:
    """Record the dose's run: before the provider is asked
    (``dial_workflow``'s ``on_run_created``), and again after, harmlessly.
    With ``claimed``, only while the dose is still ``calling``. Returns
    whether a row was written."""
    query = update(CareDoseCallModel).where(
        CareDoseCallModel.id == dose_id,
        CareDoseCallModel.organization_id == organization_id,
    )
    if claimed:
        query = query.where(CareDoseCallModel.state == CALLING)
    async with db_client.async_session() as session:
        written = (
            await session.execute(
                query.values(workflow_run_id=run_id).returning(CareDoseCallModel.id)
            )
        ).first()
        await session.commit()
    return written is not None


async def _remind_in_app(med: Any, dose: Any) -> None:
    """An app reminder: the dose already shows in Decibyl (Care, "I took
    it"); this also sends it on the person's own notification channels, once
    per dose, when they have any. Never raises."""
    from zoneinfo import ZoneInfo

    from api.services.identity import notifications

    due_local = dose.due_at.astimezone(ZoneInfo(med.timezone)).strftime("%H:%M")
    outcome = await notifications.notify(
        med.person_user_id,
        topic="reminders",
        title="Time for your medicine",
        body=f"It is {due_local}: time for {med.label}. Tap I took it in Decibyl.",
        link="/care?part=care_medicine_calls",
        dedupe_key=f"care-dose:{dose.id}",
    )
    logger.info("Care reminder for dose {} sent: {}", dose.id, outcome)


def app_readiness(organization_id: int) -> dict[str, str]:
    """App reminders need no number: always ready. Says whether they also
    go out as notifications here, so the screen does not promise a buzz
    that will not come."""
    if features.is_on("identity_notifications", organization_id):
        return {
            "state": "ready",
            "reason": (
                "Reminders show in Decibyl and go to the phones and browsers "
                "where you allowed Decibyl's notifications."
            ),
        }
    return {
        "state": "ready",
        "reason": "Reminders show in Decibyl, under Care, with I took it.",
    }


async def _dial(med: Any, dose: Any, *, on_run_created=None) -> int:
    """The platform's outbound path, with the do-not-call list honoured.
    ``on_run_created`` goes to ``dial_workflow``, which awaits it with the
    run id before asking the provider."""
    from api.services.care import reminder_call
    from api.services.compliance import dnd
    from api.services.telephony.factory import get_telephony_provider_by_id
    from api.services.telephony.outbound import OutboundRefused, dial_workflow

    config = await db_client.get_default_telephony_configuration(med.organization_id)
    if not config:
        raise CallRefused("No phone line for calling out.", "needs_setup")
    try:
        # The window is not applied: the person asked, on the card, to be
        # rung on this number at these times (see CARE.md). The workspace's
        # own do-not-call list still is.
        dialable = await dnd.assert_may_call(
            med.organization_id,
            med.phone,
            timezone_name=med.timezone,
            enforce_calling_hours=False,
            db=db_client,
        )
    except dnd.CallRefused as exc:
        raise CallRefused(str(exc), "do_not_call") from exc
    workflow = await reminder_call.ensure_workflow(
        med.organization_id, user_id=med.created_by_user_id
    )
    provider = await get_telephony_provider_by_id(config.id, med.organization_id)
    name = await _person_name(med)
    try:
        return await dial_workflow(
            workflow=workflow,
            organization_id=med.organization_id,
            to_number=dialable,
            provider=provider,
            telephony_configuration_id=config.id,
            source="care_reminder",
            extra_context={
                "trigger_source": "care_reminder",
                "care_dose_id": dose.id,
                **reminder_call.call_context(
                    medicine=med.label, language=med.language, person=name
                ),
            },
            on_run_created=on_run_created,
        )
    except OutboundRefused as exc:
        raise CallRefused(str(exc), "line_busy") from exc


async def _person_name(med: Any) -> str:
    """What the call calls them: the name their circle gives them, if any.
    Never their email; an empty name greets without one."""
    from api.services.care import circle

    name = await circle.person_name(med.organization_id, med.person_user_id)
    return "" if name == "Your family member" else name


# --- what came of it -------------------------------------------------------


def _entry(prior: str, to: str, reason: str | None, source: str) -> dict[str, Any]:
    return {
        "at": _now().isoformat(),
        "from": prior,
        "to": to,
        "reason": reason,
        "source": source,
    }


async def _move(
    dose_id: int,
    state: str,
    *,
    reason: str | None,
    allowed_from: tuple[str, ...],
    source: str,
) -> str | None:
    """Compare-and-swap a dose's state, appending to its history. Returns
    the state it moved from, or None when it did not move (not in
    ``allowed_from``, or already there: a duplicate report is a no-op)."""
    async with db_client.async_session() as session:
        dose = (
            await session.execute(
                select(CareDoseCallModel)
                .where(CareDoseCallModel.id == dose_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if dose is None or dose.state not in allowed_from or dose.state == state:
            await session.rollback()
            return None
        prior = dose.state
        dose.state = state
        dose.reason = reason
        dose.outcome_at = _now()
        dose.outcome_history = [
            *(dose.outcome_history or []),
            _entry(prior, state, reason, source),
        ]
        await session.commit()
    return prior


async def settle(dose_id: int, state: str, *, reason: str | None = None) -> bool:
    """Move a dose from calling to its outcome, once. Tells the family when
    it was not taken. Returns whether this call moved it."""
    moved = await _move(
        dose_id, state, reason=reason, allowed_from=WAITING, source="settle"
    )
    if moved is not None and state in TELL_FAMILY:
        await tell_family(dose_id)
    return moved is not None


#: Outcomes an answered call reports (the person was on the line).
_ANSWERED_OUTCOMES = (TAKEN, NOT_TAKEN, UNCLEAR)


def _allowed_from(state: str) -> tuple[str, ...]:
    """Where evidence may move a dose from. A report that the person was on
    the line corrects an earlier "not answered"; nothing moves a dose out
    of an answered outcome, and "I took it" in the app is never overwritten."""
    if state in _ANSWERED_OUTCOMES:
        return (*WAITING, UNKNOWN, NOT_ANSWERED)
    return (*WAITING, UNKNOWN)


async def _apply(dose_id: int, state: str, reason: str | None, source: str) -> bool:
    """Move a dose on evidence, and tell the family what needs telling --
    once. ``tell_family`` sends at most one alert per dose; the only second
    one is a correction, when the first said "did not answer" and the
    person in fact answered and took it."""
    told_missed = state in _ANSWERED_OUTCOMES and await _told_not_answered(dose_id)
    prior = await _move(
        dose_id, state, reason=reason, allowed_from=_allowed_from(state), source=source
    )
    if prior is None:
        return False
    if state in TELL_FAMILY:
        await tell_family(dose_id)
    if prior == NOT_ANSWERED and told_missed and state == TAKEN:
        await _correct_family(dose_id)
    return True


async def _told_not_answered(dose_id: int) -> bool:
    """Whether the family was told "did not answer": the dose went straight
    from waiting to ``not_answered`` and an alert went out."""
    async with db_client.async_session() as session:
        dose = await session.get(CareDoseCallModel, dose_id)
    if dose is None or dose.alerted_at is None:
        return False
    return any(
        e.get("from") in WAITING and e.get("to") == NOT_ANSWERED
        for e in dose.outcome_history or []
    )


async def _correct_family(dose_id: int) -> int:
    from zoneinfo import ZoneInfo

    from api.services.care import circle

    async with db_client.async_session() as session:
        dose = await session.get(CareDoseCallModel, dose_id)
        med = await session.get(CareMedicineModel, dose.medicine_id) if dose else None
    if dose is None or med is None or not med.alert_member_ids:
        return 0
    person = await circle.person_name(med.organization_id, med.person_user_id)
    due_local = dose.due_at.astimezone(ZoneInfo(med.timezone)).strftime("%H:%M")
    return await circle.alert(
        med.organization_id,
        med.person_user_id,
        kind="call_corrected",
        share="medicine_alerts",
        title=(
            f"Update: {person} did answer the {due_local} reminder call and said "
            f"{med.label} was taken."
        ),
        subject_id=dose.id,
        only_member_ids=[int(m) for m in med.alert_member_ids],
    )


async def _withdraw(dose_id: int) -> bool:
    """A claimed dose whose reminder stopped before the dial: cancelled,
    without an alert (the family is told about missed doses, not stopped
    reminders)."""
    async with db_client.async_session() as session:
        moved = (
            await session.execute(
                update(CareDoseCallModel)
                .where(
                    CareDoseCallModel.id == dose_id,
                    CareDoseCallModel.state.in_(WAITING),
                )
                .values(state=CANCELLED, reason="stopped", outcome_at=_now())
                .returning(CareDoseCallModel.id)
            )
        ).first()
        await session.commit()
    return bool(moved)


def _alert_line(
    person: str,
    label: str,
    due_local: str,
    state: str,
    reason: str | None,
    channel: str = "call",
) -> tuple[str, str]:
    if state == NOT_ANSWERED and channel == "app":
        return (
            "dose_not_confirmed",
            f"{person} did not confirm taking {label} at {due_local}.",
        )
    if state == NOT_ANSWERED:
        return (
            "call_not_answered",
            f"{person} did not answer the {due_local} reminder call for {label}.",
        )
    if state == NOT_TAKEN:
        return (
            "dose_missed",
            f"{person} said they had not taken {label} at the {due_local} reminder call.",
        )
    if state == UNCLEAR:
        return (
            "dose_missed",
            (
                f"{person} answered the {due_local} call for {label} but did not "
                "say it was taken."
            ),
        )
    if state == UNKNOWN:
        return (
            "call_unconfirmed",
            (
                f"Decibyl could not confirm whether the {due_local} reminder call "
                f"for {label} reached {person}."
            ),
        )
    why = {
        "needs_setup": "reminder calls need a phone line",
        "do_not_call": "the number is on the workspace's do-not-call list",
        "line_busy": "every line was busy",
        "not_connected": "the call could not be connected",
        "daily_cap": "the day's call limit was reached",
    }.get(reason or "", "something went wrong on our side")
    return (
        "call_failed",
        f"Decibyl could not call {person} about {label} at {due_local}: {why}.",
    )


async def tell_family(dose_id: int) -> int:
    from zoneinfo import ZoneInfo

    from api.services.care import circle

    async with db_client.async_session() as session:
        dose = await session.get(CareDoseCallModel, dose_id)
        med = await session.get(CareMedicineModel, dose.medicine_id) if dose else None
    if dose is None or med is None or dose.alerted_at is not None:
        return 0
    if not med.alert_member_ids:
        return 0
    person = await circle.person_name(med.organization_id, med.person_user_id)
    due_local = dose.due_at.astimezone(ZoneInfo(med.timezone)).strftime("%H:%M")
    kind, title = _alert_line(
        person, med.label, due_local, dose.state, dose.reason, med.channel or "call"
    )
    told = await circle.alert(
        med.organization_id,
        med.person_user_id,
        kind=kind,
        share="medicine_alerts",
        title=title,
        subject_id=dose.id,
        only_member_ids=[int(m) for m in med.alert_member_ids],
    )
    async with db_client.async_session() as session:
        await session.execute(
            update(CareDoseCallModel)
            .where(CareDoseCallModel.id == dose_id)
            .values(alerted_at=_now())
        )
        await session.commit()
    return told


#: Extracted answers the reminder agent records (reminder_call.EXTRACTION).
_TAKEN_WORDS = {"taken", "yes", "already_taken"}
_NOT_TAKEN_WORDS = {"not_taken", "no", "not_yet", "will_take_later"}
_NO_ANSWER_CODES = {
    "no_answer",
    "unreachable",
    "busy",
    "failed",
    "voicemail",
    "no-answer",
}


def outcome_of(run: Any) -> str:
    """Taken, not taken, not answered or unclear, from a finished run."""
    gathered = getattr(run, "gathered_context", None) or {}
    extracted = gathered.get("extracted_variables") or {}
    answer = str(extracted.get("dose_taken") or "").strip().lower().replace(" ", "_")
    if answer in _TAKEN_WORDS:
        return TAKEN
    if answer in _NOT_TAKEN_WORDS:
        return NOT_TAKEN
    disposition = (getattr(run, "annotations", None) or {}).get("disposition")
    if isinstance(disposition, dict):
        disposition = disposition.get("code")
    mapped = str(gathered.get("mapped_call_disposition") or "").lower()
    if str(disposition or "").lower() in _NO_ANSWER_CODES or mapped in _NO_ANSWER_CODES:
        return NOT_ANSWERED
    if answer in ("", "none", "not_answered", "no_answer"):
        return NOT_ANSWERED if not extracted else UNCLEAR
    return UNCLEAR


async def record_run_outcome(workflow_run_id: int) -> None:
    """Post-call: if this run was a reminder call, settle its dose. Never
    raises; a run that was not a reminder call is left alone."""
    try:
        run = await db_client.get_workflow_run(workflow_run_id)
        context = getattr(run, "initial_context", None) or {}
        dose_id = context.get("care_dose_id")
        if run is None or not dose_id:
            return
        organization_id = await db_client.get_organization_id_by_workflow_run_id(
            workflow_run_id
        )
        async with db_client.async_session() as session:
            dose = await session.get(CareDoseCallModel, int(dose_id))
        # The run's own workspace must be the dose's: an id in a context is
        # not proof of ownership.
        if dose is None or dose.organization_id != organization_id:
            logger.warning(
                "Care outcome for run {} names a dose it does not own", workflow_run_id
            )
            return
        # The run must be the dose's own, when one is recorded.
        if dose.workflow_run_id not in (None, workflow_run_id):
            logger.warning(
                "Care outcome for run {} is not dose {}'s run", workflow_run_id, dose.id
            )
            return
        if dose.workflow_run_id is None:
            await _link_run(dose.id, organization_id, workflow_run_id)
        await _apply(dose.id, outcome_of(run), "call_finished", source="post_call")
    except Exception as exc:  # noqa: BLE001 - post-call work must not fail a call
        logger.error(
            "Could not record the care outcome of run {}: {}", workflow_run_id, exc
        )


#: How long an unknown dose waits for evidence before the family is told
#: "could not confirm" (once). Evidence that comes later still lands.
UNKNOWN_ALERT_MINUTES = 120
#: How long an unknown dose is re-read against its run.
RECONCILE_HOURS = 24


def _evidence_state(evidence: str) -> tuple[str, str] | None:
    from api.services.telephony import call_evidence

    if evidence == call_evidence.NOT_CONNECTED:
        return NOT_ANSWERED, "no_answer"
    if evidence == call_evidence.CARRIER_FAILED:
        return FAILED, "not_connected"
    return None


async def reconcile(
    dose_id: int,
    *,
    now: datetime | None = None,
    unknown_reason: str = "no_outcome",
) -> str | None:
    """Read the dose's run and settle the call on what the run proves.

    * No run recorded: the provider was never asked (a worker that died
      between the claim and the dial) -- ``failed``, and the family is told
      Decibyl could not call, never that the person did not answer.
    * The carrier's no-answer / busy: ``not_answered`` (alerted). Its
      failure: ``failed`` (alerted).
    * Anything else -- in flight, no callback yet, or answered with the
      post-call report still to come: ``unknown``, nobody told yet. After
      ``UNKNOWN_ALERT_MINUTES`` an answered run is ``unclear`` (the person
      answered; nothing says the dose was taken) and anything else tells
      the family, once, that the call could not be confirmed.

    Returns the dose's state afterwards. Never dials."""
    from api.services.telephony import call_evidence

    now = now or _now()
    async with db_client.async_session() as session:
        dose = await session.get(CareDoseCallModel, dose_id)
    if dose is None or dose.state not in (CALLING, UNKNOWN):
        return dose.state if dose else None
    if dose.workflow_run_id is None:
        if dose.state == CALLING:
            async with db_client.async_session() as session:
                med = await session.get(CareMedicineModel, dose.medicine_id)
            if med is not None:
                await _give_slot_back(med, dose_id)
            await settle(dose_id, FAILED, reason="not_dialled")
        return await _state(dose_id)
    evidence, _run = await call_evidence.read(
        dose.workflow_run_id, dose.organization_id
    )
    final = _evidence_state(evidence)
    if final is not None:
        await _apply(dose_id, final[0], final[1], source=f"reconcile:{evidence}")
        return await _state(dose_id)
    if dose.state == CALLING:
        await _move(
            dose_id,
            UNKNOWN,
            reason=(
                "answered_no_report"
                if evidence == call_evidence.ANSWERED
                else unknown_reason
            ),
            allowed_from=(CALLING,),
            source=f"reconcile:{evidence}",
        )
    elif dose.created_at < now - timedelta(minutes=UNKNOWN_ALERT_MINUTES):
        if evidence == call_evidence.ANSWERED:
            await _apply(
                dose_id, UNCLEAR, "answered_no_report", source="reconcile:late"
            )
        elif dose.alerted_at is None:
            await tell_family(dose_id)
    return await _state(dose_id)


async def _state(dose_id: int) -> str | None:
    async with db_client.async_session() as session:
        return await session.scalar(
            select(CareDoseCallModel.state).where(CareDoseCallModel.id == dose_id)
        )


async def sweep(now: datetime | None = None) -> int:
    """Doses still waiting after ``CARE_CALL_ANSWER_MINUTES``:

    * an app reminder nobody confirmed is ``not_answered`` ("did not
      confirm") -- the app is the whole channel, so silence is the answer;
    * a call is reconciled against its run (``reconcile``): settled on
      evidence, or ``unknown`` -- never "did not answer" for want of a
      report, and never re-dialled.

    Unknown doses are re-read for ``RECONCILE_HOURS``. Returns how many
    changed state."""
    if not features.on_anywhere(MEDICINE_CALLS):
        return 0
    now = now or _now()
    cutoff = now - timedelta(minutes=constants.CARE_CALL_ANSWER_MINUTES)
    horizon = now - timedelta(hours=RECONCILE_HOURS)
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, state FROM care_dose_calls "
                    "WHERE (state IN (:s, :r) AND created_at < :cutoff) "
                    "OR (state = :u AND created_at >= :horizon) "
                    "ORDER BY id LIMIT 500"
                ),
                {
                    "s": CALLING,
                    "r": REMINDED,
                    "u": UNKNOWN,
                    "cutoff": cutoff,
                    "horizon": horizon,
                },
            )
        ).all()
    changed = 0
    for dose_id, before in rows:
        if before == REMINDED:
            if await settle(dose_id, NOT_ANSWERED, reason="no_outcome"):
                changed += 1
            continue
        if await reconcile(dose_id, now=now) != before:
            changed += 1
    return changed
