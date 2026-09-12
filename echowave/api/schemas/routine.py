"""What a routine looks like over the wire.

Deliberately not the cron string the runtime could have taken. The person
filling this in runs a clinic, and the difference between ``0 9 * * 1-5`` and
``0 9 * * 1,5`` is a support ticket waiting to happen -- so the schedule
arrives as a cadence, an anchor and a minute, and
``services/workflow/routines.py`` turns those into the moment.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from api.services.workflow.routines import MINUTES_IN_DAY, Anchor, Cadence


class RoutineWrite(BaseModel):
    """Creating or changing a routine.

    ``is_active`` is absent on purpose. Arming is its own endpoint, because it
    is the one field with a precondition -- a routine cannot be switched on
    until it has been test-run -- and folding it into a general save would
    make that precondition a surprise in the middle of an edit.
    """

    name: str = Field(min_length=1, max_length=120)
    #: What the bot should do on each run, in the operator's words.
    instruction: str = Field(default="", max_length=4_000)
    cadence: Cadence = Cadence.DAILY
    anchor: Anchor = Anchor.OPENING
    #: Minute of the day for a clock anchor; for hourly, the minute past each
    #: hour. Ignored by the opening and closing anchors, which compute it.
    at_minute: int = Field(default=0, ge=0, lt=MINUTES_IN_DAY)
    #: Signed minutes from the anchor. -30 with closing is "half an hour
    #: before you shut". Bounded to a day either way so a typo cannot put a
    #: run a week out.
    offset_minutes: int = Field(default=0, gt=-MINUTES_IN_DAY, lt=MINUTES_IN_DAY)
    #: 0 = Monday, matching ``date.weekday()``. Read only for weekly.
    weekday: int = Field(default=0, ge=0, le=6)
    #: Connector slugs this routine cannot do its job without. Capped: a
    #: routine that needs eleven apps connected is not a routine anybody will
    #: ever see run.
    needs_apps: list[str] = Field(default_factory=list, max_length=10)

    model_config = ConfigDict(extra="forbid")


class RoutineResponse(BaseModel):
    """One routine, as a screen needs it.

    Carries the derived answers as well as the stored fields, because every
    question an operator asks of this screen -- when does it run next, why did
    it not run, may I switch it on -- is a computation over the schedule and
    the business's hours, and a screen that recomputed them would eventually
    disagree with the tick.
    """

    id: int
    workflow_id: int
    name: str
    instruction: str
    cadence: str
    anchor: str
    at_minute: int
    offset_minutes: int
    weekday: int
    needs_apps: list[str]
    is_active: bool

    #: When it was last test-run. None means never, which is why it cannot arm.
    tested_at: Optional[datetime] = None
    #: Whether it may be switched on at all.
    may_arm: bool = False
    #: The slot that last fired, not the moment of firing.
    last_fired_at: Optional[datetime] = None
    #: Why the last tick declined, and when. Only the reasons worth telling
    #: somebody about are stored, so a populated one means something.
    last_skipped_reason: Optional[str] = None
    last_skipped_at: Optional[datetime] = None

    #: When it runs next, in the organisation's timezone. None when nothing is
    #: due inside a fortnight -- a weekly routine on a day the business never
    #: opens, which is worth showing as "never" rather than as a date it will
    #: not honour.
    next_run_at: Optional[datetime] = None
    #: One line for the card: "Every weekday when you open".
    schedule_summary: str = ""


class RoutineListResponse(BaseModel):
    routines: list[RoutineResponse] = []


class RoutineTestResponse(BaseModel):
    """The answer to pressing Test run."""

    started: bool
    #: Present when the run was enqueued, so a screen can follow it.
    workflow_id: Optional[int] = None
    detail: str = ""
