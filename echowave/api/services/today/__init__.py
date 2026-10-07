"""Launch stream `today` (LAUNCH-PLAN.md, phase 2; handoff 10, 22, 31.4;
screens 07-10 and 20). See ``TODAY.md`` at the repository's ``echowave/``
root for the design.

Modules:

* ``scope`` -- who is looking, in which workspace, in which timezone.
* ``approvals`` -- pending cards a person may see, and the exact preview of
  one (screen 08, and the approval dock above the composer).
* ``listing`` -- Today as one ordered list (screen 07).
* ``activity`` -- finished work with evidence, and one item's detail
  (screen 09).
* ``reminders`` -- events, reminders and event-linked reminders (screen 10).
* ``brief`` -- the daily brief and the end-of-day note, with source coverage.
* ``delivery`` -- putting one occurrence in front of a person on a channel,
  once, with an honest status.
* ``ticks`` -- the minute jobs that deliver reminders and briefs.
"""

#: Flag names (services/features.py).
TODAY_LIST = "today_list"
APPROVAL_DOCK = "approval_dock"
REMINDERS = "today_reminders"
DAILY_BRIEF = "daily_brief"
END_OF_DAY = "end_of_day_note"
ROUTINE_START_ON = "routine_start_on"

ALL_FLAGS = (
    TODAY_LIST,
    APPROVAL_DOCK,
    REMINDERS,
    DAILY_BRIEF,
    END_OF_DAY,
    ROUTINE_START_ON,
)
