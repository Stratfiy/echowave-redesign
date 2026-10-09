"""The founder's open decisions for reminder calls, one constant each.

docs/plans/reminder-calls.md section 10 asks seven questions (D1-D7) that
were not answered when Stage 2 was built. Each is a conservative default,
chosen pending the founder's decision, and lives here (or, for D5, in the
one function it changes) so that changing it is a one-line change.
"""

from __future__ import annotations

from datetime import time

from api import constants

# --- D1. Retry policy ---------------------------------------------------------
#: One retry, 15 minutes after the first ring, only from a verified no-answer
#: (never from ``unknown`` or ``failed``), only inside the calling window;
#: then a notification. Zero means "notification only".
MAX_RETRIES = 1
RETRY_GAP_MINUTES = 15
#: A retry is a ring: it takes a slot of the person's daily cap like the
#: first one. False would let a retry ring past the cap.
RETRY_COUNTS_AGAINST_CAP = True

# --- D2. Approved exceptions to quiet hours -----------------------------------
#: None for general reminders: every reminder call rings only between
#: CALLING_HOURS_START and CALLING_HOURS_END in the person's zone. Care's
#: medicine calls keep their own exemption, unchanged (services/care/calls).
QUIET_HOURS_EXCEPTIONS_ALLOWED = False
#: When a reminder call may ring, in the person's own zone: the one place
#: the window is set for reminder calls. The draft (what the card offers),
#: the card's words, the gate and the retry all read these two, so a change
#: here is the whole change. "08:30 in Tamil" asked under the default window
#: becomes a card for 09:00 that says why; set the start to "08:00" and the
#: same words ring at 08:30. By default they follow the platform's window
#: (``CALLING_HOURS_START``/``_END``: 09:00-21:00, the TRAI commercial
#: window); whether a self-requested reminder may ring earlier is D2,
#: the founder's to decide (legal applicability not assessed).
CALLING_WINDOW_START = constants.CALLING_HOURS_START
CALLING_WINDOW_END = constants.CALLING_HOURS_END

# --- D3. The daily cap ----------------------------------------------------------
#: The cap's day is the person's local day (midnight to midnight in their
#: zone), and one cap is shared by call-when-done, reminder calls and care's
#: medicine calls: the same per-person counter
#: (``call_when_done.allowance``, ``CALL_WHEN_DONE_DAILY_CAP`` = 5).
#: False keeps care's calls out of the count (uncapped, as before).
CARE_SHARES_THE_CAP = True
#: Founder decision (9 Oct 2026), kept as built: care's medicine calls count
#: against that shared cap **only while ``reminder_calls`` is on** for the
#: workspace. With it off, care rings at the times the person confirmed on
#: its card, uncapped, exactly as before reminder calls existed. False
#: would count care's calls whatever the switch says.
CARE_CAP_ONLY_WITH_REMINDER_CALLS = True

# --- D4. Under-18s ------------------------------------------------------------------
#: The number card carries an "I am 18 or over" confirmation that must be
#: ticked to confirm it; no reminder call rings a number without it.
REQUIRE_ADULT_CONFIRMATION = True
ADULT_ATTESTATION = "I am 18 or over"

# --- D5. The window when do-not-call enforcement is off -----------------------------
# Lives with the gate it changes: ``dnd.WINDOW_HOLDS_WITHOUT_DND_ENFORCEMENT``
# (True: the 09:00-21:00 window holds even when the list is switched off).

# --- D6 / D7. Who hears what -------------------------------------------------------
#: Somebody other than the person, a voicemail or an answering machine hears
#: only that there is a message for the person's first name and to ask them
#: to check Decibyl: never the reminder itself. "silent" would end the call
#: without a word.
UNKNOWN_ANSWERER_HEARS = "content_free_line"
VOICEMAIL_HEARS = "content_free_line"
CONTENT_FREE_LINE = (
    "This is Decibyl with a message for {first_name}. "
    "Please ask {first_name} to check Decibyl."
)

# --- Not decisions, but bounds ----------------------------------------------------
#: A due reminder is still rung this late (a tick that was down for a
#: deploy); older than this it goes as a notification instead.
LATE_MINUTES = 10
#: How long a dispatched call may go without an outcome before it is
#: reconciled against its run.
ANSWER_MINUTES = 20
#: How long an ``unknown`` call keeps being re-read against its run.
RECONCILE_HOURS = 24
#: Snooze bounds, said on the call ("snooze 15"). There is no default: a
#: snooze with no clear length, or one outside these bounds, is asked back
#: on the thread and the task stays open (``calls.snooze_minutes``).
SNOOZE_MIN_MINUTES = 5
SNOOZE_MAX_MINUTES = 120


def window() -> tuple[time, time]:
    """The calling window for reminder calls, as two local times."""
    from api.services.compliance import dnd

    return (
        dnd._parse_hhmm(CALLING_WINDOW_START, time(9, 0)),
        dnd._parse_hhmm(CALLING_WINDOW_END, time(21, 0)),
    )


def window_words() -> str:
    """ "9:00 and 21:00", as the card and the thread say it."""
    start, end = window()
    return f"{start.hour}:{start.minute:02d} and {end.hour}:{end.minute:02d}"


def within_window(timezone_name: str | None, now) -> bool:
    """Whether ``now`` is inside the reminder-call window in that zone."""
    from api.services.compliance import dnd

    return dnd.within_calling_hours(
        timezone_name=timezone_name,
        now=now,
        start=CALLING_WINDOW_START,
        end=CALLING_WINDOW_END,
    )


def retry_policy() -> dict[str, int]:
    """What a schedule records, and its card shows."""
    return {"max_retries": MAX_RETRIES, "gap_minutes": RETRY_GAP_MINUTES}
