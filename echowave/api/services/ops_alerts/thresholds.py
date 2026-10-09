"""Every number the operational alerts decide on, in one place.

Changing when the founder is woken up should be a one-line change here and
nothing else. Each detector reads its numbers from this module and from no
other; the tests import them rather than repeating them, so a change here is
a change the tests follow.

Amounts are paise (the metering unit of ``services/billing``). Times are
minutes unless the name says otherwise.
"""

from __future__ import annotations

# --- the incident machinery --------------------------------------------------

#: How often the background job evaluates the detectors that read the
#: database (calls, providers, spend, invites).
EVALUATION_INTERVAL_MINUTES = 5

#: One email per incident key per this many minutes. A condition that is
#: still true after it gets one "still happening" reminder, not one mail
#: every five minutes.
ALERT_COOLDOWN_MINUTES = 60

#: A condition must read clear this many evaluations in a row before its
#: incident resolves, so one quiet sample in the middle of a spike does not
#: send "resolved" and then a fresh alert five minutes later.
RESOLVE_AFTER_CLEAR_EVALUATIONS = 2

#: Two processes may evaluate the same detector (the API's watchdog and the
#: worker's job). The first in this many seconds does; the other skips.
EVALUATION_LOCK_SECONDS = 50

#: Resolved incidents kept for the staff page.
HISTORY_LENGTH = 100

# --- call failure spikes -----------------------------------------------------

#: Calls placed through a carrier in this window are looked at.
CALL_WINDOW_MINUTES = 15
#: Fewer finished calls than this in the window is too few to call a spike.
CALL_FAILURE_MIN_CALLS = 10
#: Share of finished calls that failed at the carrier or ended with no
#: outcome at all, at or above which the alert fires.
CALL_FAILURE_SHARE = 0.30

# --- provider error spikes ---------------------------------------------------

#: Provider errors and attempts are counted in one-minute buckets; this many
#: of the most recent are summed.
PROVIDER_WINDOW_MINUTES = 15
#: Fewer errors than this for a component is not a spike, whatever the rate.
PROVIDER_ERROR_MIN_COUNT = 5
#: Errors over attempts for one component, at or above which it fires.
PROVIDER_ERROR_SHARE = 0.20

# --- background job health ---------------------------------------------------

#: The worker heartbeat's own staleness rule is reused
#: (``WORKER_HEARTBEAT_STALE_AFTER_SECONDS``); this is how often the API's
#: watchdog looks.
WATCHDOG_INTERVAL_SECONDS = 60
#: A scheduled tick that runs every minute and has not completed for this
#: long is not running.
TICK_STALE_MINUTES = 10
#: A tick that has never completed is only reported once monitoring has been
#: watching for this long, so a fresh deploy is not an alert.
TICK_NEVER_SEEN_GRACE_MINUTES = 15
#: The oldest job that is due and still waiting for a worker.
QUEUE_OLDEST_DUE_MINUTES = 10

# --- spend anomaly -----------------------------------------------------------

#: Today's metered provider cost against the average day of the trailing
#: window before today.
SPEND_BASELINE_DAYS = 7
#: Fires when today is at least this many times the trailing daily average...
SPEND_ANOMALY_MULTIPLE = 3.0
#: ...and at least this much in absolute terms, so a workspace that went
#: from Rs 2 to Rs 8 is not an incident.
SPEND_ANOMALY_MIN_ORG_PAISE = 50_000
SPEND_ANOMALY_MIN_PLATFORM_PAISE = 200_000

# --- invite requests ---------------------------------------------------------

#: An invite request pending longer than this is a person left waiting.
INVITE_WAIT_HOURS = 12

# --- the daily cost summary --------------------------------------------------

#: 03:15 UTC is 08:45 IST.
DAILY_SUMMARY_HOUR_UTC = 3
DAILY_SUMMARY_MINUTE_UTC = 15
#: How many workspaces and agents the summary ranks.
DAILY_SUMMARY_TOP_N = 5
#: How many past summaries the staff page shows.
DAILY_SUMMARY_HISTORY_DAYS = 7
