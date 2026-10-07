# Today: the ordered list, approvals, reminders and the daily brief (stream `today`)

Phase 2 of `LAUNCH-PLAN.md`. Handoff sections 10 (summaries), 22 and 31 item
4; design screens 07 (Today), 08 (exact action approval), 09 (task detail and
activity), 10 (routine and reminder editor) and 20 (daily brief settings,
with `settings`); the founder's approval reference (the dock above the
composer); and from the founder's list, missed calls handled and the end-of-day
note. Built on the controls stream's task ledger, versioned cards and events, and
the shell stream's components. Nothing here duplicates them.

Everything ships **off**. Each part has its own switch, and every one honours
per-workspace overrides from the staff console.

| Flag | Constant | What it turns on |
| --- | --- | --- |
| `today_list` | `TODAY_LIST_ENABLED` | `/tasks` becomes Today (screen 07). Also: the approval screen at `/tasks/approvals/{id}` (08), Activity at `/tasks/activity` (09), the next-run sentence in the routine editor, and calling back missed calls through a card |
| `approval_dock` | `APPROVAL_DOCK_ENABLED` | Pending approvals docked above the Chat composer: "Decibyl wants to: …" with Do it / Don't |
| `today_reminders` | `TODAY_REMINDERS_ENABLED` | Reminders, events with linked reminders, the editor (screen 10) and their delivery |
| `daily_brief` | `DAILY_BRIEF_ENABLED` | One daily brief with source coverage, delivered in the app, on WhatsApp and as a push notification at the person's time; Settings → Daily brief (screen 20) |
| `end_of_day_note` | `END_OF_DAY_NOTE_ENABLED` | An end-of-day note: what was done, what is still open, and how many missed calls were handled |
| `routine_start_on` | `ROUTINE_START_ON_ENABLED` | A routine set from Chat starts on once its card is confirmed ("Will do, every Monday at 10:00."); a routine's result in Chat carries a ✓ chip with its name |

## Where things are

| Piece | Backend | UI |
| --- | --- | --- |
| Today list | `services/today/listing.py`, `GET /today` | `components/today/TodayPage.tsx` (at `/tasks`), `TodayDrawer.tsx`, `EventRow.tsx` |
| Approvals | `services/today/approvals.py`, `GET /today/approvals[/{id}]`; deciding is the controls `POST /timeline/actions/settle` and `revise` | `ApprovalDetail.tsx` (screen 08), `ApprovalDock.tsx` (Chat), shell `ActionPreview` (extended additively) |
| Activity | `services/today/activity.py`, `GET /today/activity[/{kind}/{id}]` | `ActivityList.tsx`, `ActivityDetailView.tsx` |
| Reminders and events | `services/today/reminders.py`, `/today/reminders*`, `/today/events*`, `/today/resolve-date` | `ReminderEditor.tsx` (screen 10) |
| Routine editor | `POST /today/routines/preview` | `RoutineNextRun.tsx` inside the existing `SchedulesBoard` |
| Daily brief and end-of-day note | `services/today/brief.py`, `/today/brief*`, `/today/end-of-day*` | `BriefSettings.tsx` (screen 20), the brief card in Today |
| Delivery | `services/today/delivery.py` | — |
| Minute jobs | `services/today/ticks.py`, `tasks/today.py` (ARQ cron, every minute) | — |
| Tables | `db/today_models.py`, migration `20261008today` | — |

## Today (screen 07; handoff 22)

* **Order**: pending approvals, then what is due, then the daily brief, then
  upcoming events (next 7 days), then at most three suggestions, then the
  end-of-day note. Work already under way shows under Due. There are no
  decorative counts.
* **Per-section states**: each section is `ok` or `failed` with a message
  and Try again. "Nothing due in Decibyl" appears only when the server says
  every section answered and nothing is there. A failed section never reads
  as empty.
* **Missing calendar** is a separate line: "Connect your calendar to include
  appointments". It connects right there with the existing
  `GoogleCalendarConnect`, so nobody is sent to another screen.
* **Due** lists reminders that are due or overdue, and board tasks that are
  this person's and due today or waiting on their input. It also lists
  **missed calls not yet returned**. "Call back" proposes a
  `return_missed_call` card and opens it in the drawer: nothing is dialled
  until the person approves it. The card is written on the person's own
  Decibyl conversation, so under private threads it is theirs alone.
* **Suggestions** come from records only: a call to return, an overdue
  reminder, and the brief offer ("Would a daily summary help?", 09:00 in
  their timezone, off until accepted). Each says "Why this?", and each can
  be Prepared, deferred (Later) or Dismissed. None of them books or sends
  anything by itself.
* **Details** open in a 440 px drawer on a desktop and full screen on a
  phone, deep-linked as `?open=approval:{id}` or
  `?open=activity:{kind}:{id}`. The list keeps its scroll. Escape closes the
  drawer and focus goes back to the row that opened it.
* **Scope**: the date, the timezone and the workspace stay at the top.
  Everything is read for (workspace, person). The timezone is the person's
  own (member preferences), then the workspace's, then India.

## Approvals (screen 08 and the dock; founder reference)

* **No new approval logic.** The queue lists the controls stream's
  `action_proposed` cards that are genuinely pending. The badge is the
  server's count. A card on a Decibyl conversation follows that
  conversation's privacy (D-1b): with private threads on, only its author
  sees it, and a card without an author is visible to an Admin. Anything
  else in the workspace is visible as before. Another workspace's card
  reads as "not here". Answering follows the same rule: `settle` and
  `revise` refuse a colleague's private card with "That proposal is not
  here." (`actions.thread_refusal`), so knowing its id is not enough.
* **Exact preview**: the verb as the title, then the account (digits masked
  to the last four), recipient, amount, content, attachments, timing and
  consequence. Nothing is truncated. The decision sits in a sticky,
  safe-area footer with the final recipient and action beside the button.
* **Bound to the version**: Approve, and the dock's Do it, send the version
  on screen to `settle`. An edited card's old version is refused ("This
  changed since you looked at it"), and the screen then shows the new
  version to review. Edit goes through `revise`, which makes a new version.
  The same approval from two places arms it once (the card's own
  compare-and-swap).
* **After-states** are the card's own: Approved (undo window), Working, and
  Done only with the server's evidence. An unknown outcome offers Check
  delivery, never Retry.
* **The dock** sits above the composer while anything is waiting. It shows
  one plain sentence ("Decibyl wants to: Pay Acme Print's invoice:
  ₹4,800"), one line of detail, Do it and Don't, a link to see everything,
  and how many more are waiting. Undo stays available through the window.

### Every card kind on the branch

Who may answer a card is decided in one place,
`actions.answer_refusal(payload, user_id)`. `settle` raises its reason. The
queue, the badge and the dock list only the cards the viewer may answer, so
nobody is offered a Do it that would be refused:

| Card | Who answers it | In the preview |
| --- | --- | --- |
| Care (`care_*`, `only_user_id`) | The person it is about | Read-only for anyone else, with the reason |
| Reach order and outside-tool write (`owner_user_id`) | The owner | Read-only for others |
| `browser_step` (`requested_by`) | The person whose browser it is | Button, page and every field |
| `desktop_step` (`args.user_id`) | The person whose computer it is | App and device, the step; `released` reads "Handed to your computer. It takes this step once." (executing, never done) |
| `meeting_follow_up` | Whoever captured the meeting | Not there for anyone else |
| Identity acts (`private_to`) | Their owner | Not there for anyone else; an email shows the from-address, recipient, subject and body, editable before approval |
| Any card whose outcome is unknown | — | Check delivery, never Retry |

Activity and the end-of-day note hide private cards the same way.

## Activity (screen 09)

Approval cards, board tasks and deliveries share one list in the ledger's
vocabulary, each with its evidence. Filters (status, helper) live in the URL
and open as a disclosure on a phone. An item's detail shows its goal, owner
and scope, a vertical timeline of stages, what it was given, its evidence and
related records. Cancel stops what has not happened yet (an armed card is
undone; a task moves to cancelled with its expected version). **Retry is
never offered**: a failed act is asked for again in Chat, which makes a new
card, and an unknown outcome offers Check delivery. The screen says that work
carries on if the person leaves the page.

## Reminders and events (screen 10; handoff 22, 31.4)

* **Event time and reminder time are separate.** An event has a confirmed
  time. Each linked reminder is the event's time plus an offset: "At event
  time" (0) or "One day before" (−1440).
* **Moving an event** recalculates every linked reminder once, by its
  approved offset, and keeps the old and new times in its history. A
  reminder the move puts in the past is marked missed and the conflict is
  named. A second tab's move with an old revision gets `409`.
* **Cancelling an event** cancels its linked reminders. A second cancel
  finds nothing left to cancel.
* **Only the schedule the person saw is saved.** The preview returns the
  next occurrence as a full local date with the timezone ("Once. Next: Fri 9
  Oct 2026, 09:00 IST (Asia/Kolkata).") and a `schedule_key`. Save must send
  that key back; a schedule that changed in between is refused.
  "Tomorrow" is resolved in the person's timezone. A past date is
  `invalid_past` and cannot be saved. A zone other than the person's own is
  flagged `timezone_conflict`.
* **Pause** stops future occurrences; one already being delivered finishes,
  because deliveries are claimed before sending. Resume works out the next
  occurrence again. Test delivery is one labelled occurrence per minute and
  never creates a schedule. A stale edit is a `409` that keeps the draft and
  shows the saved version.
* **Delivery** happens in a minute tick (`deliver_due_reminders`). A
  reminder more than 20 minutes late (the routines' catch-up rule) is
  `missed`: recorded, never sent hours later. A recurring reminder moves on
  to its next day, and DST keeps the wall time.

## The daily brief and the end-of-day note (handoff 10, 22; screen 20)

* **Off until accepted**, suggesting 09:00 in the person's confirmed
  timezone. The settings are the person's own in each workspace: time,
  timezone, days, channels, quiet hours (21:00–08:00 by default, for
  optional suggestions only), pause, and the end-of-day note with its time.
  Saves name the revision they read. A conflict shows both versions and
  offers "Use the saved settings" or "Keep mine". Nothing is overwritten
  silently.
* **Sources and coverage**: approvals, calendar (the workspace's connected
  Google Calendar), reminders and events, board tasks, missed calls, and
  mail. Every brief lists every source with what happened to it: read,
  needs setup, unavailable or failed. A calendar that was not read never
  becomes "no meetings". A failed read is labelled partial ("Calendar could
  not refresh. This brief includes approvals, reminders and events, tasks,
  missed calls only."), never "nothing needs attention". **Mail is
  `unavailable`**, and each brief says so: reading the inbox belongs to the
  Inbox helper (stream `agents`).
* **The brief is written from records, not by a model**: one sentence with
  numbers, then sections. The covered period, the last refresh time and the
  sources are always shown. Refresh rebuilds the day's brief **in place**.
  An older brief is kept with its own refresh time and labelled stale.
* **One schedule, one occurrence**: one row per (workspace, person, kind,
  local date). The tick builds the brief, delivers it on each channel (one
  delivery row per occurrence and channel), then claims `delivered_at`
  once. Test sends one labelled occurrence and does not count as the day's
  delivery.
* **The end-of-day note** lists what was done today with evidence (cards
  that ran, tasks marked done, reminders delivered), what is still open,
  and missed calls: "1 of 2 missed calls called back", plus who is still
  waiting.

## Delivery and honest channels

`services/today/delivery.py` claims a delivery row before sending anything:
unique per (subject, occurrence, channel), so a double tick, a restart or two
Test presses deliver an occurrence once. The status says what happened:

| Channel | Behaviour |
| --- | --- |
| In the app | `sent`; the evidence is the delivery row, and it is shown in Today |
| WhatsApp | `needs_setup` until the platform sender is configured (`WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`) **and** the person has linked WhatsApp (`channel_identities`). `skipped` with the reason while WhatsApp's 24-hour window is closed (there is no template path yet). `accepted` when Meta takes the message, never "delivered". It spends the person's `outbound_messages` allowance, and a send that breaks midway is `unknown` and never retried |
| Phone notification | Through the `identity` stream's web push (`notifications._push`). It is `needs_setup` until `identity_notifications` is on, the VAPID keys are set and the person has allowed a device. Then it is `accepted` (a push service took it, which is not proof it was seen), with generic lock-screen text while the person's private previews are on |

Sending to a person's own WhatsApp at the time they confirmed is what they
asked for in the editor or settings. The confirmation is the exact schedule
and channel, so it is not an approval card. Calling back a missed call is
always a card.

## Routines from Chat (founder: "Will do, every Monday at 10.")

With `routine_start_on`, confirming a `schedule_routine` card saves the
routine **on**, with `armed_by_card_event_id` set to that card.
`routines.may_arm` accepts a test run **or** that confirmation. It is never
recorded as a test run. The reply under the card is one sentence: "Will do,
every Monday at 10:00." With the flag off, the routine is saved switched
off and untested, exactly as before. The routine editor shows the next run
directly above Save ("Every Monday at 10:00. Next run: Mon 12 Oct 2026,
10:00 IST (Asia/Kolkata)."), in the workspace's zone and hours, and says that
switching a routine off stops future runs while a run already started
finishes.

A routine armed from a card in someone's private chat stays theirs: the
routines list hides it from colleagues (`routines.hidden_from`), and its
runs and its started/skipped lines go to that chat's thread, not the shared
one. A routine saved with `routine_start_on` off has no card link, so it is
workspace-visible as before.

## Events (catalogue)

`reminder_scheduled`, `reminder_delivered`, `reminder_failed` (including
`missed`) and `reminder_cancelled` carry `channel`, `status` and
`reason_code`, and nothing personal. They are written only while
`event_catalogue` is on.

## Migration

`20261008today` (down to `20261008identity`, the head after phase-2 integration) is additive. It adds the tables
`today_events`, `today_reminders`, `today_deliveries`, `daily_brief_settings`,
`daily_briefs` and `today_dismissals`, all with `organization_id` and
`user_id` foreign keys (cascade) and the unique keys described above, plus one
nullable column, `agent_routines.armed_by_card_event_id`. Upgrade, downgrade
and upgrade again were run on a fresh database.

## Also fixed

* `DELETE /api/v1/routines/{id}` (deleting one of Decibyl's own routines)
  raised a `TypeError`, because `delete_routine` required a `workflow_id`.
  It now matches `workflow_id IS NULL`. Tested.
* `AgentRoutineModel.workflow_id` now says `nullable=True`, matching the
  migration that made it so (`f3c9d1a7b2e4`).

## Rollback

Every flag is off by default. Turning one off restores the previous
behaviour at once:

* `today_list` off: `/tasks` is the task board again, the `/today` and
  `/today/activity` routes are 404s, the approval screen says it is not
  switched on, and the routine editor loses its sentence.
* `approval_dock` off (with `today_list` also off): no dock, and
  `/today/approvals` is a 404. Cards still work in the thread as before.
* `today_reminders` off: routes 404, the tick does nothing, and rows stay.
* `daily_brief` or `end_of_day_note` off: routes 404, the tick does
  nothing, Settings hides Daily brief, and rows stay.
* `routine_start_on` off: chat routines are saved switched off again.
  Routines already armed by a card stay on; switch them off on Routines if
  needed.

Schema: `alembic downgrade 20261008identity` drops the six tables and the
column. Nothing else depends on them.
