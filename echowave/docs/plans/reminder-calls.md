# Reminder calls: the contract (Stage 1)

"Call me tomorrow at 8:30 in Tamil to remind me to send the proposal."

This is the design for a **general reminder call**: Decibyl rings a person, at
a time they confirmed, to remind them of something they asked about. It is
Stage 1 of the research and intelligence plan: design, measurement and tests.
Nothing here is built yet; no migration ships with it. Everything below is
mapped onto code that exists on `main` today, with the places where that code
disagrees with the contract called out, and tests that pin the disagreement
(`xfail` with a reason) so that Stage 2 turns them green rather than
rediscovering them.

Settled by the founder and not re-opened here: calls only between **09:00 and
21:00** in the person's time, at most **5 calls a day per person**, voice
approvals are **read-out only** (a call never moves a card), and **no
under-18s**.

---

## 1. What already exists, and what each piece is good for

| Piece | Where | Reuse for reminder calls | Do not copy |
|---|---|---|---|
| Typed schedule, full local date, schedule key | `services/today/reminders.py` (`preview`, `_schedule_key`, `resolve_date`, `next_recurring`) | The draft, the "tomorrow is your tomorrow" resolution, DST-safe recurrence, and the rule that a save must send back the key of the schedule the person saw | `status` as one field for both "was it delivered" and "is it done" (see 4.3) |
| Occurrence-keyed delivery row | `services/today/delivery.py` (`_claim`, `deliver`, statuses `sent / accepted / needs_setup / skipped / failed / unknown`) | The pattern exactly: write the row before sending, unique per (subject, occurrence, channel), never retry an `unknown` blind | Nothing; this is the model to follow |
| Minute tick with compare-and-swap advance | `services/today/ticks.py` (`deliver_due_reminders`, `_advance`) | Advancing the schedule only from the time it was read | Reading due rows once and not rechecking before send (4.4) |
| Consent card for a number | `services/call_when_done/number.py`, `services/care/medicines.py` (card + `approved_version`) | The confirmation card; a versioned approval that is exactly what runs | |
| Reminder voice agent per workspace | `services/care/reminder_call.py` (`ensure_workflow`, `GREETINGS`, `_RULES`, `EXTRACTION`) | One small agent per workspace found by a configuration mark; greeting written per language; "say who is calling"; "never ask for anything secret" | The medicine-specific script; the dose extraction |
| Read-out agent for results | `services/call_when_done/agent.py` | Read-out only, never moves an approval card (founder rule 1) | |
| Queue, gather, cap, window | `services/call_when_done/calls.py` (`queue`, `tick`, `_claim`, `place`, `over_cap`, `next_opening`, `_requeue`, `sweep`) | Window judged in the person's own zone (`person_timezone`); next opening at 09:00; cap per person across workspaces; "never silent" fallback to the thread and notifications (`tell_in_app`) | The count-based cap (races, see 5.4); `sweep` turning "no report" into "not answered" (4.1) |
| The outbound gate | `services/compliance/dnd.py` (`assert_may_call`, `within_calling_hours`, `next_opening` via callers) | Fail-closed do-not-call check immediately before the dial | `enforce_calling_hours=False` (care's exemption, 4.2); the early return that skips the window when `DND_ENFORCEMENT_ENABLED` is false (fixed for call-when-done, 4.5) |
| The dial | `services/telephony/outbound.py` (`dial_workflow`) | Concurrency slot, run, quota, `initiate_call`, one metering path | It creates the run **before** `initiate_call` but raises without returning the run id, and it discards the provider's call id (`CallInitiationResult.call_id`), so an unknown outcome cannot be reconciled (5.2) |
| Provider status read | `providers/*/provider.py` `get_call_status(call_id)` (Plivo and others) | The reconcile step before any retry | |
| Post-call hook | `tasks/workflow_completion.py` calls `care_calls.record_run_outcome` and `done_calls.record_run_outcome` | One more hook for reminder calls, keyed by the run's `initial_context` | Settling only from `calling`, which drops late truth (4.1) |
| Per-person daily allowance | `services/quotas.py` (`consume`: one conditional upsert, no race) | The daily call cap as an atomic reservation, not a count | Its day is the UTC day; the cap's day is the person's (decision D3) |
| Notifications | `services/identity/notifications.py` (`notify`, `dedupe_key`) | Push / web push / in-app fallback with a dedupe key | |

## 2. The flow

```
"Call me tomorrow at 8:30 in Tamil to remind me to send the proposal"
  │
  ├─ 1. Draft (typed, deterministic)          services/reminder_calls/draft.py      (new)
  │     title, local date+time, zone, language, number, recurrence, retry policy
  │     zone: member_preferences.timezone_of → org timezone → refuse ("which city?")
  │     date words: today/reminders.resolve_date (+ weekday names, "in 2 hours")
  │
  ├─ 2. Card in the thread                     workflow/actions card, versioned
  │     "Thu 10 Oct 2026, 08:30 IST · +91 ••••• •3210 · Tamil
  │      'Send the proposal' · once · if no answer: one retry at 08:45, then a notification"
  │     confirm / edit / cancel. Nothing is scheduled the person did not see.
  │     Outside 09:00–21:00 → the card says so and offers 09:00 (no silent move).
  │
  ├─ 3. Persist schedule (version = card version)  reminder_call_schedules      (new table)
  │
  ├─ 4. Tick (every minute)                     claim occurrence by unique key
  │     occurrence_key = schedule_id + version + local due ISO + attempt
  │
  ├─ 5. Gate, immediately before dialling (one function, in this order):
  │     a. occurrence not cancelled/snoozed, schedule version still current
  │     b. recipient still a member, number still confirmed, not under-18
  │     c. quiet hours: 09:00–21:00 in the person's zone, unless an approved
  │        exception covers this exact occurrence (see 4.2)
  │     d. do-not-call list (dnd.assert_may_call, fail closed)
  │     e. daily cap: quotas.consume("reminder_calls", 1) — atomic
  │     f. spend: dial_workflow's quota check (unchanged)
  │
  ├─ 6. Dispatch record → provider             reserve row (state=dispatching) BEFORE the
  │     dial; store run id and provider call id as soon as known
  │
  ├─ 7. The call                                read-out; accepts done / snooze / cancel / repeat
  │     identity check before any content (see 6)
  │
  └─ 8. Outcome                                 post-call hook + provider callback + reconcile
        delivery: answered | no_answer | failed | unknown
        task: user_reported_done | snoozed | cancelled | open
        no answer → the agreed bounded retry, else notification fallback
```

## 3. State machines

Two states, never one. A reminder is a **task** (did the person deal with it)
and each ring is a **delivery** (did the call happen). Folding them into one
field is the defect in all three existing paths (4.3).

### 3.1 Delivery (per occurrence attempt)

```
queued ──claim──▶ dispatching ──provider accepted──▶ accepted ──answered──▶ answered
   │                  │                                   │
   │                  │ timeout / 5xx / crash              ├──no answer / busy──▶ no_answer
   │                  ▼                                   └──carrier error─────▶ failed
   │               unknown ──reconcile(get_call_status)──▶ accepted | answered | no_answer | failed
   │                  │ (never re-dialled while unknown)
   ▼
 skipped(reason)   — gate said no: quiet_hours | cap | dnd | not_member | cancelled | no_line
```

Rules:

* `dispatching` is written **before** `initiate_call`; the run id and the
  provider call id are written the moment they exist.
* A row in `dispatching` or `unknown` is never re-dialled. The sweep moves a
  stale `dispatching` to `unknown`, not to `no_answer`.
* `no_answer` is only reachable from `accepted`: a claim that never dialled
  cannot be "not answered" (today it is; xfail tests below).
* Late truth wins: a post-call report for an occurrence already settled by
  timeout overwrites `unknown` and `no_answer` with the real outcome, and the
  history keeps both (`outcome_history` JSON, append-only).
* Duplicated webhooks are idempotent: settle is a compare-and-swap on the
  current state, and a second identical report is a no-op.

### 3.2 Task (per reminder)

```
open ──"done" on the call / in the app──▶ user_reported_done
 │  ──"snooze 15"──────────────────────▶ snoozed ──due──▶ open (new occurrence)
 │  ──"cancel" / card cancel───────────▶ cancelled
 │  ──"repeat"─────────────────────────▶ open (re-read on the same call; no new ring)
 └─ delivery outcomes never move the task. "No answer" leaves it open.
```

A recurring reminder's task state is per occurrence; cancelling the series is
a schedule change (new version), not a task state.

## 4. Where existing code contradicts the plan

Each has a test. "Fixed" means fixed in this branch with its test; "xfail"
means the test is in and marked with the reason.

### 4.1 "No report" becomes "not answered", and late truth is dropped
`care/calls.sweep` and `call_when_done/calls.sweep` settle anything still
`calling` after 20 minutes as `not_answered`, and tell the family / the
person. `settle` then only moves rows out of `calling`, so a real answer that
arrives later (a slow post-call job) is dropped.
*xfail:* `test_care_reminder_call_races.py::test_an_answer_that_arrives_after_the_sweep_is_kept`,
`test_call_when_done_races.py::test_an_answer_after_the_sweep_is_kept`.

A claim whose worker died before dialling is also swept as "did not answer".
*xfail:* `...::test_a_claim_that_never_dialled_is_not_reported_as_unanswered` (both suites).

**Fixed** (call outcomes and limits): both paths now have an `unknown`
state. The sweep reconciles against the run (`telephony/call_evidence`):
the carrier's no-answer is "not answered", silence is `unknown`. A claim
with no run recorded was never dialled: call-when-done queues it again
(at most three claims), care says "could not call". Late reports move
`unknown` and correct "not answered" (history in `outcome_history`); a
correction notice goes out only where an earlier one said something false.
The four xfails above now pass.

### 4.2 The care path's calling-hours exemption
`care/calls._dial` passes `enforce_calling_hours=False` because the person
confirmed the exact times on a card. That is a defensible rule for a medicine
the person asked to be rung about at 22:30; it must **not** be copied into
general reminders. The contract models quiet hours explicitly and an
**approved exception** as data: `{occurrence or schedule, window, approved_by,
approved_at, card_version}`, shown on the card in words ("I'll ring at 22:30,
outside normal calling hours, because you asked"), and checked by the gate
for that occurrence only.
*Pinned:* `test_care_reminder_call_races.py::test_care_calls_are_exempt_from_the_calling_window`.

### 4.3 One status for delivery and task
* `care_dose_calls.state` holds both the call outcome and "taken";
  `medicines.mark_taken` upserts `taken` over a dose already reported as
  `not_answered`, and the call's outcome is gone.
  **Fixed**: once a call was dialled or has an outcome, "I took it" keeps
  that outcome and records the acknowledgement beside it
  (`marked_by_user_id`, an `outcome_history` entry, `taken_in_app` in both
  views); the family is not told of a dose the person marked taken. Only a
  dose nothing has rung for becomes `taken`. The press locks the dose row,
  so it is ordered against the dial linking its run.
  Tests: `test_i_took_it_after_a_missed_call_keeps_the_calls_outcome`,
  `TestTakenBesideTheCall` (6).
* `today_reminders.status` moves a one-off to `done` after its occurrence
  whatever the delivery said: a reminder that reached nobody (`needs_setup`,
  `failed`, `unknown`) reads as finished.
  **Fixed**: a one-off is `done` only when delivered (sent or accepted);
  otherwise `missed`, still listed, and its delivery row says why.
  Test: `test_today_reminder_states.py::test_an_undelivered_one_off_does_not_read_done`.
* `done_calls.state` (`queued / calling / answered / not_answered / failed /
  notified`) is delivery only, which is right; there is no task side because
  there is no task.

### 4.4 Cancellation not rechecked right before dispatch
* Care: the tick read the active medicines, claimed the dose, then placed the
  call; pausing, removing or "I took it" in between still rang the person,
  and a paused dose left in `calling` was later swept as missed with the
  family told. **Fixed**: `care/calls.place` re-reads the dose and the
  medicine; a stopped reminder's dose is `cancelled` without an alert.
  Tests: `TestCancellationRaces` (3).
* Today reminders: a cancel after the tick read the row is still sent.
  **Fixed**: the delivery claim re-checks the reminder (active, at the time
  read) under a row lock in the claim's own transaction; a cancel, pause or
  edit lands before the claim (nothing sent) or after it (the next
  occurrence is not). Tests: `test_a_cancel_after_the_tick_read_it_is_not_sent`
  and the concurrent ones in the same file.
* Call when done: once a finish has queued the call (for example overnight,
  waiting for 09:00), `optin.cancel` only moves `pending` callbacks, so the
  person cannot stop it. **Fixed**: cancel also stops a call not yet dialled
  (`queued`, or claimed with no run recorded): the call becomes `cancelled`
  when no other task is left on it, the thread says so, and the dial's
  compare-and-swaps (`_claim`, `link`) see it, so it never rings.
  Tests: `TestCancellation` (7).

### 4.5 The window disappears with the do-not-call switch
`dnd.assert_may_call` returns early, window included, when a deployment sets
`DND_ENFORCEMENT_ENABLED=false`. Call-when-done relied on the gate for the
window. **Fixed** for call-when-done: `place` asks `within_calling_hours`
itself first. Campaigns still share the early return; whether the window
should be independent of the list for every caller is decision D5.
Test: `test_the_window_holds_with_do_not_call_enforcement_off`.

### 4.6 Recipient authorisation not rechecked
A person removed from the workspace after a call was queued was still rung.
**Fixed** for call-when-done (`not_member`). Care has the same shape
(`care_medicines.person_user_id`), lower risk because removal is rare; the
general gate (step 5b) covers both.
Test: `test_a_person_removed_after_the_call_was_queued_is_not_rung`.

### 4.7 Provider timeout is "failed"
`dial_workflow` raising after `create_workflow_run` (a timeout inside
`initiate_call`, which uses an `aiohttp` session with the default 5-minute
total timeout) is recorded as `failed` / `call_error`, and the person or
family is told "could not call", although the carrier may have rung.
*xfail:* `test_a_provider_timeout_is_unknown_not_failed` (both suites).
**Fixed**: `dial_workflow(on_run_created=...)` records the run on the call
before `initiate_call`; an exception after that is `unknown` (reconciled,
never re-dialled), before it a verified non-dispatch.

## 5. Failure and duplicates at the provider boundary

1. **Two ticks / two workers.** Claim by unique key (insert-on-conflict) or
   compare-and-swap, as all three paths already do. Pinned:
   `test_two_ticks_at_once_place_one_call` (care and call-when-done, run
   concurrently with `asyncio.gather`), `test_the_tick_delivers_once_however_often_it_runs`.
2. **Unknown outcome.** `dial_workflow` changes shape (Stage 2): it takes a
   `dispatch_id` and writes `workflow_run_id` onto the dispatch row before
   `initiate_call`, then writes `provider_call_id` from
   `CallInitiationResult.call_id`. On any exception after the run exists the
   dispatch row is `unknown`, never `failed`. A reconcile job reads
   `provider.get_call_status(provider_call_id)` (or, with no call id, the
   run's hangup callback state) before any retry or any "could not call".
   *Done for care and call-when-done* with the run as the dispatch
   identity (`on_run_created`) and the run's webhook-written state as the
   evidence. Not done: storing `CallInitiationResult.call_id` and asking
   `provider.get_call_status` -- every provider answers in its own shape,
   and none is exercised against a live carrier.
3. **Duplicated webhooks.** Settle by compare-and-swap on the current
   delivery state plus an idempotency key of `(provider_call_id, event)`.
   Pinned: `test_a_post_call_report_delivered_twice_*` (both suites).
4. **Cap under concurrency.** `call_when_done.over_cap` counts rows with a run
   id, so two workers placing calls for one person at once (two workspaces)
   can both pass at cap-1. The general path reserves with
   `quotas.consume("reminder_calls", 1)` inside the gate (atomic), and
   releases nothing on failure: a ring attempt counts, which is what protects
   the person. Pinned (sequential): `test_the_cap_counts_calls_from_every_workspace`.
   *Fixed for call-when-done*: `call_when_done/allowance.py` reserves one
   slot per call in `person_call_allowances` (person, local day) by a
   conditional upsert right before the dial, counting in-flight and
   unknown calls, and releases only on a verified non-dispatch (refused
   before the provider was asked, or no run recorded). Care calls are not
   counted (D3 still open).
5. **Retry.** Bounded and agreed on the card: at most one retry, at least 10
   minutes later, inside the window, only from `no_answer` (never from
   `unknown` or `failed`), and it consumes a cap slot. After that, the
   notification fallback (`notifications.notify` with a dedupe key per
   occurrence) and a line on the thread.
6. **No line.** Push / in-app reminders keep working with no outbound line
   (the "every feature works on Free with no number" rule). The card offers
   them in place of a call; nothing is scheduled as a call that cannot ring.

## 6. Privacy on the call

* The greeting names Decibyl and that this is the reminder they asked for,
  and nothing else, until the person confirms it is them ("Is this Asha?").
  An unknown answerer, a voicemail or an answering machine hears only:
  "This is Decibyl with a reminder for Asha. Please ask her to check
  Decibyl." Never the reminder text.
* Voicemail / machine detection, where the provider offers it, ends the call
  with that line; where it does not, the identity question does the job.
* The text read out is the person's own words, as written on the card. No
  amounts, no OTPs, no names of third parties beyond what the person wrote.
* The thread line after the call says what happened (answered / no answer /
  could not call / unknown) and never the content heard by someone else.
* Numbers are shown masked on cards and screens (`•••• 3210`), as care does.
* No under-18s: the number card is refused for a person whose profile says
  so; where age is unknown, the card's confirmation includes "I am 18 or
  over" (decision D4).

## 7. Data model (Stage 2; no migration in this stage)

```
reminder_call_schedules   one per confirmed card version
  id, organization_id, user_id, title (person's words), language,
  phone (E.164, confirmed), timezone, local_time, recurrence, weekday, date,
  retry_policy JSON {max_retries:1, gap_minutes:15}, fallback ("push"|"in_app"),
  quiet_exception JSON | null, card_event_id, version, state (active|paused|cancelled),
  created_at, updated_at

reminder_call_occurrences one per due time
  id, schedule_id, organization_id, user_id, schedule_version,
  due_at, occurrence_key UNIQUE, task_state (open|snoozed|user_reported_done|cancelled),
  snoozed_until, created_at

reminder_call_dispatches  one per ring attempt
  id, occurrence_id, organization_id, attempt, state
  (queued|dispatching|accepted|answered|no_answer|failed|unknown|skipped),
  reason, workflow_run_id, provider, provider_call_id,
  outcome_history JSON (append-only), reserved_at, dialled_at, settled_at
  UNIQUE (occurrence_id, attempt)
```

The care and call-when-done tables are not migrated into these in Stage 2;
they adopt the same dispatch/unknown rules in place (4.1, 4.7).

## 8. Where each check lives

| Check | Function | Called from |
|---|---|---|
| Draft validity, zone, full local date | `reminder_calls.draft.clean` (new), reusing `today.reminders.resolve_date`, `next_recurring`, `scope.full_local` | tool + route |
| Card version == schedule version | `reminder_calls.schedule.save` | card confirm |
| All pre-dial checks, in order (5a–f) | `reminder_calls.gate.may_dial(dispatch, now)` (new) | worker, right before `dial_workflow` |
| Window | `dnd.within_calling_hours` called **directly** by the gate (not via `assert_may_call`) | gate |
| Do-not-call | `dnd.assert_may_call(..., enforce_calling_hours=False)` after the gate's own window check | gate |
| Cap | `quotas.consume` | gate |
| Spend | `dial_workflow` → `authorize_workflow_run_start` | dial |
| Reconcile | `reminder_calls.reconcile.sweep` (new) using `provider.get_call_status` | ARQ cron |

## 9. Test plan

Already in this branch (fake clock passed in; the carrier replaced):

| Suite | Pinned | Fixed here | xfail |
|---|---|---|---|
| `test_care_reminder_call_races.py` | 2 ticks at once → 1 call; duplicate post-call report → family told once; calling-window exemption | pause / remove / "I took it" after claim → no ring | late answer after sweep; provider timeout; claim never dialled; "I took it" overwrites missed call |
| `test_call_when_done_races.py` | 2 ticks at once → 1 call; duplicate report → one notice; 20:59 still rings; cap across workspaces | window holds with DND enforcement off; removed member not rung | late answer after sweep; provider timeout; claim never dialled; cancel after queued |
| `test_today_reminder_states.py` | delivered one-off reads done | | undelivered one-off reads done; cancel after read still sent |

Stage 2 adds, for the new path:

* Draft: "tomorrow 8:30" at 23:50 IST and at 00:10 IST; a person in Dubai;
  no timezone on file; "next Friday" on a Friday; 21:30 (outside the window:
  card says so, offers 09:00); Tamil, Hindi and code-mixed phrasings (see
  `evals/assistant/`).
* Gate: each of 5a–f refusing on its own, in order, with the reason stored.
* Provider: timeout → `unknown` → reconcile → `answered`; 5xx → `unknown`;
  4xx invalid number → `failed`; duplicate callbacks; callback before the
  run id is written; late post-call report after the sweep.
* Retry: one retry only from `no_answer`, inside the window, cap counted;
  none from `unknown`.
* Privacy: unknown answerer and voicemail hear no reminder text.
* No line: card offers push / in-app, and they are delivered.

## 10. Decisions needed from the founder (Stage 2)

* **D1. Retry policy.** One retry after 15 minutes, then a notification —
  or no retry at all (notification only)? Does a retry count against the 5?
  (Recommended: yes, it is a ring.)
* **D2. Approved exceptions to quiet hours.** Allowed for general reminders
  at all? If yes, per occurrence only, and how late (22:00? never past 22:00)?
  Care keeps its exemption as is unless you say otherwise.
* **D3. "A day" for the cap.** The person's local day (what call-when-done
  does) or the UTC day (what `quotas` does)? One shared cap of 5 across
  call-when-done, reminder calls and care, or separate?
* **D4. Under-18s.** Is there an age field to check, or should the number
  card carry an "I am 18 or over" confirmation?
* **D5. The window when DND enforcement is off.** Should
  `dnd.assert_may_call` keep the 09:00–21:00 window even when a deployment
  turns the do-not-call list off (affects campaigns)? Recommended: yes.
* **D6. Unknown answerer.** Is "a reminder for Asha, please ask her to check
  Decibyl" acceptable, or should an unknown answerer hear nothing but
  "wrong number, sorry"?
* **D7. Voicemail.** Leave the content-free line, or hang up silently?
