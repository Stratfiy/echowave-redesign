# Controls: the launch foundations (stream `controls`)

Phase 1 of `LAUNCH-PLAN.md`. Handoff sections 3, 4, 8 (scoped context), 9,
10 (reliable actions), 15 packets A-B, 29, 31 item 3 and 36, and the design's
"Shared state and data contracts" and "Metrics events and release proof".
Everything here ships **off**; each part has its own switch in
`api/services/features.py`.

| Flag | Constant | What it turns on |
| --- | --- | --- |
| `capability_checklist` | `CAPABILITY_CHECKLIST_ENABLED` | Staff endpoint `GET /api/v1/admin/controls/capabilities` |
| `operational_quotas` | `OPERATIONAL_QUOTAS_ENABLED` | Per-person daily limits, enforced in free mode too; `GET /me/quotas`; staff grants |
| `task_ledger` | `TASK_LEDGER_ENABLED` | Nine-state ledger, payload-bound approvals, idempotency keys, stale-write rejection, outcome unknown |
| `personal_space` | `PERSONAL_SPACE_ENABLED` | `GET /me/personal-space` (made on first ask) |
| `member_preferences` | `MEMBER_PREFERENCES_ENABLED` | `GET/PUT /me/preferences` |
| `event_catalogue` | `EVENT_CATALOGUE_ENABLED` | Catalogue events written to the outbox and sent to PostHog; `POST /events/client`; staff catalogue |
| `reply_feedback` | `REPLY_FEEDBACK_ENABLED` | "Was this useful?" under Decibyl's replies; `POST /feedback`, `GET /feedback/mine`; staff summary |

`task_ledger`, `member_preferences` and `reply_feedback` honour
per-organisation overrides from the staff console, so they can be tried on
one workspace first. `operational_quotas`, `personal_space`,
`event_catalogue` and `capability_checklist` are about the person or the
platform rather than one workspace, and are switched globally.

## 1. Capability checklist (packet A)

"A runtime capability checklist separates source, configuration and tested
behavior." `api/services/capabilities.py` holds the list;
`GET /api/v1/admin/controls/capabilities` (any staff tier) evaluates it on
the running instance:

* **source** -- each module found without importing it: `present`, `partial`,
  or `absent` (with the stream that will build it);
* **configuration** -- the flags it needs (as that organisation sees them,
  with `?organization_id=`) and the settings it reads, each reported as
  present or not. Never a value;
* **tested** -- the test files in this repository that exercise it, and
  `staging: not_verified` until `scripts/staging_check.py` evidence is
  recorded in phase 3. A unit test is not a staging run;
* **state** -- `available`, `needs_setup`, `disabled_by_policy` or
  `unavailable`, with the reason (the design's capability contract).

`api/tests/test_capability_checklist.py` fails if any module, flag, setting or
test named in the list stops existing, so a rename cannot silently shorten it.

Snapshot of this build (source as found; configuration is per environment, so
read the endpoint for the live answer):

| Capability | Handoff | Source | Modules | Flags and settings | Tests |
| --- | --- | --- | --- | --- | --- |
| Decibyl, the central assistant | 3, 5 | present | `services.workflow.decibyl` | — | `test_decibyl_assistant.py`, `test_decibyl_keeps_working.py` |
| Auto model routing | 8 | present | `services.routing.brain` | — | `test_auto_brain.py` |
| Connected app reads and proposed writes | 3 | present | `services.workflow.connected_tools` | decibyl_tools, COMPOSIO_API_KEY | `test_connected_tools.py`, `test_decibyl_connected_tools.py` |
| Approval cards that run once | 3, 10 | present | `services.workflow.actions` | — | `test_an_approved_action_runs_once.py`, `test_one_ask_is_one_card.py`, `test_a_send_is_a_card.py` |
| Task ledger, payload-bound approval, idempotency, stale rejection | 10, 15 B | present | `services.workflow.task_ledger` | task_ledger | `test_task_ledger.py` |
| Long tasks carried on in the background | 3 | present | `services.workflow.decibyl_tasks` | decibyl_long_tasks | `test_decibyl_keeps_working.py` |
| Routines and schedules | 3, 10 | present | `services.workflow.routines` | — | `test_routines.py`, `test_decibyl_has_routines.py` |
| Memory that belongs to one person | 3, 8 | present | `services.knowledge_graph.personal` | personal_memory | `test_personal_memory.py` |
| A personal space beside the workspaces a person joins | 8, founder | present | `services.personal_space` | personal_space | `test_personal_space.py` |
| A person's own language, timezone, voice and summary time | 31.3 | present | `services.member_preferences` | member_preferences | `test_member_preferences.py` |
| Daily limits per person, in free mode too | 9, 15 B | present | `services.quotas` | operational_quotas | `test_operational_quotas.py` |
| Versioned analytics events through an outbox | 35, 36 | present | `services.events.catalogue`, `services.events.outbox` | event_catalogue, ANALYTICS_PSEUDONYM_KEY, POSTHOG_API_KEY | `test_event_catalogue.py` |
| Was this useful? on replies and finished tasks | 2, 4, 6 | present | `services.feedback` | reply_feedback | `test_reply_feedback.py` |
| Learning practice and progress | 3, 6, 23 | present (stream `learning`, see `LEARNING.md`) | `services.learning.core`, `services.learning.teacher`, `services.learning.guide`, `routes.learning` | learning | `test_learning.py`, `test_learning_routes.py` |
| Skills shelf, catalogue and imports | 3, 8 | present | `services.skills.shelf`, `services.skills.catalogue`, `services.skills.imports` | — | `test_skills_shelf.py`, `test_skills_catalogue.py` |
| Invite-only signup | 3, 16 | present | `services.auth.signup_invites` | invite_only_signup | `test_invite_only_signup.py` |
| Decibyl on WhatsApp | 3, 7 | present | `services.messaging.channels.whatsapp` | decibyl_channels, WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_APP_SECRET | `test_whatsapp_inbound.py`, `test_decibyl_channels.py` |
| Decibyl on Telegram | 3, 7 | present | `services.messaging.channels.telegram` | decibyl_channels, decibyl_telegram, TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET | `test_decibyl_channels.py` |
| Decibyl in Slack | 3, 7 | present | `services.messaging.channels.slack` | decibyl_channels, decibyl_slack, SLACK_CLIENT_ID, SLACK_CLIENT_SECRET, SLACK_SIGNING_SECRET | `test_decibyl_channels.py` |
| Decibyl in Microsoft Teams | 3, 7 | present | `services.messaging.channels.teams` | decibyl_channels, decibyl_teams, MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD | `test_decibyl_channels.py` |
| Inbound trigger address (a webhook, not a mailbox) | 4, 7 | present | `routes.public_email` | INBOUND_EMAIL_DOMAIN | `test_email_triggers.py` |
| Phone number after KYC | 4, 7 | present | `services.kyc` | managed_telephony | `test_kyc_state.py`, `test_kyc_flow.py` |
| Live voice in the browser | 4, 12 | present | `routes.webrtc_signaling`, `services.pipecat.run_pipeline` | — | `test_webrtc_signaling_concurrency.py` |
| Free while early (no plans, nothing charged) | 4, 9 | present | `services.billing.free_mode` | free_mode | `test_free_mode.py` |
| Exception and release tracking | 35 | present | `observability.sentry` | SENTRY_DSN | `test_sentry_scrub.py` |
| One daily brief with source coverage | 10, 22 | present (stream `today`) | `services.today.brief`, `services.today.ticks` | daily_brief, WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID | `test_today_brief.py` |
| Today as one ordered list, exact approvals, activity | 22 | present (stream `today`) | `services.today.listing`, `services.today.approvals`, `services.today.activity` | today_list, approval_dock | `test_today_list.py`, `test_today_approvals.py` |
| Reminders and event-linked reminders | 22, 31.4 | present (stream `today`) | `services.today.reminders`, `services.today.delivery` | today_reminders | `test_today_reminders.py` |
| Meeting capture and record | 23, 31.6 | present (stream `meetings`, MEETINGS.md) | `services.meetings.records`, `.transcription`, `.reading`, `.follow_ups` | meeting_capture | `test_meetings.py` |
| Decibyl's private browser | founder | absent (stream `browser`) | `services.browser` | — | — |
| Virtual card | 4, 7 | absent (stream `identity`) | — | — | — |

## 2. Operational quotas (handoff 9, packet B)

`api/services/quotas.py`. Free mode bypasses every plan limit by design, so
these live outside billing and hold whatever the plan or free mode says.

| Allowance | Spent by | Default (env) |
| --- | --- | --- |
| `model_turns` | A person's line to Decibyl (web, WhatsApp, Slack, Teams, Telegram -- all go through `decibyl.ask`), a direct message to an agent, a channel line that addresses an agent | 50 (`OPERATIONAL_QUOTA_MODEL_TURNS`) |
| `voice_minutes` | A browser voice session: 1 minute at start (`routes/webrtc_signaling.py`), the rest settled from its length at the end (`services/voice_controls.py`) | 15 (`OPERATIONAL_QUOTA_VOICE_MINUTES`) |
| `outbound_messages` | A confirmed card that reaches somebody (a connected-app send, a document delivered), charged to whoever confirmed it when the send is attempted -- a send refused by the app afterwards still counts, the conservative side for a cost limit | 20 (`OPERATIONAL_QUOTA_OUTBOUND_MESSAGES`) |
| `browser_minutes` | Nothing yet: the `browser` stream calls `quotas.consume(user, BROWSER_MINUTES)` per minute | 30 (`OPERATIONAL_QUOTA_BROWSER_MINUTES`) |

The numbers are placeholders for the founder's decision ("Usage limit
numbers for the free beta" is still open); each is an environment variable.

* **Day**: the UTC day, deliberately -- a day that followed the person's own
  timezone could be restarted by changing the timezone. The message gives
  the reset in the person's local time (their preference, else India time).
* **No bypass**: one conditional `INSERT ... ON CONFLICT DO UPDATE ... WHERE
  used + n <= limit` per spend, so two requests at once cannot both take the
  last unit (tested with eight concurrent spends); amounts are positive whole
  numbers up to a day, so nothing can be "refunded"; an unknown allowance is
  an error, never unlimited; the only raise is a staff grant.
* **Staff grants** (`POST /admin/controls/quotas/users/{id}/grants`, any staff
  tier): extra per day, a reason of at least a few words, an expiry of at
  most 31 days; revocable (`DELETE /admin/controls/quotas/grants/{id}`);
  every grant and revocation writes `admin_action_log`.
* **In the thread**: over the limit, Decibyl's thread gets a line saying how
  many were used, when it resets, and that Help can raise it; nothing is
  asked of any model or agent. A voice session over the limit is refused
  before it connects with `voice_limit_reached` and the same sentence; a
  session already running is not cut off -- its minutes are recorded and
  the next one is refused. A send over the limit fails on its card with the
  reason (`reason_code: quota_outbound_messages`).
* Not counted yet: concurrency (1 live voice session, 2 background jobs) and
  background runs, files and proactive contact from handoff 9 -- those belong
  with the streams that own those paths (`voice`, `today`, `agents`).

## 3. Task ledger (handoff 5, 10; packet B; design "Task state")

`api/services/workflow/task_ledger.py`, extending `agent_tasks` (no second
task table) and keeping `actions.py`'s compare-and-swap run-once.

**States**: queued, running, needs input, awaiting approval, scheduled,
completed, failed, cancelled, outcome unknown. Allowed moves:

| From | To |
| --- | --- |
| queued | running, scheduled, awaiting approval, needs input, cancelled, failed |
| scheduled | queued, running, cancelled, failed |
| running | completed, failed, needs input, awaiting approval, outcome unknown, cancelled |
| needs input | queued, running, cancelled, failed |
| awaiting approval | queued, scheduled, running, cancelled, failed |
| outcome unknown | completed, failed (reconciliation only -- never back to running) |
| completed, failed, cancelled | nothing |

* **Board**: every ledger state shows in a board column (`todo`,
  `in_progress`, `in_review`, `blocked`, `done`, `cancelled`), and a row the
  ledger never touched reads its state from its column. A person moving a
  card on the board is followed by the ledger (`follow_board`), and marking
  it done is the evidence.
* **Stale writes lose**: every move names the `state_version` it read; the
  update is conditional on it, so a late worker or a second tab changes
  nothing and gets `409` with the current version and state. Each move is a
  row in `agent_task_transitions` numbered by version (unique per task), so
  history has no gaps and two writers cannot both write step N.
* **Idempotency**: `POST /api/v1/tasks/ledger` with an `Idempotency-Key`
  header; a retry with the same key returns the first task (`created:
  false`). Unique per workspace at the database.
* **Completion needs evidence** (`outcome_evidence`: a message id, a card id,
  "marked done by").
* **Cards** (`actions.py`) speak the same vocabulary: each card's payload
  carries `ledger_state` (proposed -> awaiting approval, armed -> scheduled,
  running, done -> completed, failed, declined/undone/cancelled -> cancelled,
  outcome unknown).

**Approval bound to a payload version** (design "Approval state"):

* At proposal the card's payload gets `version`, a hash of its action and
  arguments (not the model's label or reason).
* Confirm must name that version (`SettleActionRequest.version`; the web card
  sends what it shows; channel buttons carry it as
  `card:<id>:confirm:<version>`). A different version is refused: "This
  changed since you looked at it."
* `POST /api/v1/timeline/actions/revise` edits a waiting card (a connected
  app's arguments, or a document's note/recipient/channel). The edit is a new
  version, the card goes back to awaiting approval, any armed run is
  disarmed, and earlier versions are listed under `revisions`.
* At run time the approved version is checked against the payload again;
  arguments changed after approval are refused, not run.
* `confirmed.version` and `idempotency_key = card:<id>:<version>` are stamped
  on the card.
* The same Confirm from two channels arms it once (compare-and-swap from
  proposed), and two run jobs execute it once (claim before act) -- both
  tested together.

**Outcome unknown**: a send that raises something other than a refusal
midway (a timeout from the provider) is `outcome_unknown` with "We are
checking whether this was delivered. Please do not send it again." -- never
"failed" and never retried. A card claimed by a worker that died is swept to
the same state after 10 minutes (`sweep_unknown_outcomes`, every 5 minutes).
Reconciliation of what actually happened is per provider and belongs to the
streams that own those sends (`identity`, `reach`).

Not built here: approval expiry (24 h / 15 min, handoff 9) -- the card has
no expiry yet; `approval_expired` is catalogued for when it does.

## 4. Personal space (founder: person + business as one; handoff 8)

**Decision**: a personal space is an `organizations` row with
`kind = 'personal'` and `personal_owner_user_id` set, holding exactly one
membership: its owner, as owner.

Alternatives considered:

* *A `scope` column on every personal record inside each business
  workspace* -- every table and every query would need a second filter, and
  one forgotten filter shows a person's private things to their colleagues.
  It also ties the person's things to a workspace they may leave.
* *A separate set of personal tables* -- duplicates memory, threads, tasks,
  files and connections, and every feature would have to be built twice.
* *A personal organisation* (chosen) -- everything in the product is already
  scoped by `organization_id`, so a person's memory, private threads, tasks,
  files and connections in their personal space are theirs by the same rule
  that separates one business from another. No existing filter is loosened.
  A work agent in a business workspace cannot read the personal space
  because it is another tenant (handoff 8: a work agent must not inherit
  personal email or learning history).

**Guarantees**

* A database trigger (`personal_space_one_member`) refuses any membership
  row in a personal space other than its owner's -- insert or update, from
  any code path (invitations, the team screen, a login re-sync). Inviting
  to one is also refused in words.
* Created lazily by `GET /api/v1/me/personal-space`; race-safe (one space,
  one membership under concurrent first requests). It gets no API key and
  no signup credit, and does not change the person's selected workspace.
* Switching into it is the existing workspace switch, which checks
  membership like any other.

**Migration**: additive (`202610071800controls`): two nullable columns on
`organizations`, a partial unique index on the owner, and the trigger. No
backfill. Existing `personal_memory` (MEM-1) facts a member keeps inside a
business workspace stay there and keep their member-only rule.

Not built here (stream `shell`/`settings`): the switcher entry for the
personal space, "share to the team" from the personal space, and moving a
fact between the two.

## 5. Member preferences (handoff 31 item 3, 30)

`member_preferences` (one row per person) with `language` (BCP 47 from a
fixed list), `timezone` (IANA), `voice` (an id), `summary_time` (`HH:MM`) and
`revision`. `GET/PUT /api/v1/me/preferences` takes no user id -- it is always
the caller's row -- and never touches `organization_configurations`, so a
person's timezone cannot move a team's schedules. Saves name the revision
they read; an older one is `409` with the stored value (the design's save
contract: keep the draft, show both). Only the fields sent change; `null`
clears one. The quota message already reads the person's timezone; the
`today` and `voice` streams read `summary_time` and `voice`.

## 6. Event catalogue and envelope (handoff 35, 36)

`api/services/events/`: `catalogue.py` (names, owner, allowed properties,
version), `envelope.py` (the contract), `outbox.py` (durable dispatch).

| Domain | Events |
| --- | --- |
| onboarding | `invite_accepted`, `onboarding_completed`, `first_useful_task_completed` |
| tasks | `task_started`, `task_completed`, `task_failed`, `task_cancelled` |
| approval | `approval_requested`, `approval_granted`, `approval_rejected`, `approval_expired`, `approval_viewed` (client) |
| voice | `voice_session_started`, `voice_session_ended`, `voice_session_failed` |
| meetings | `capture_started`, `capture_failed`, `meeting_processed`, `action_confirmed` |
| reminders | `reminder_scheduled`, `reminder_delivered`, `reminder_failed`, `reminder_cancelled` |
| connections | `connection_started`, `connection_ready`, `connection_revoked` |
| support | `ticket_created`, `support_action_requested`, `support_action_approved`, `support_action_executed`, `support_action_failed`, `ticket_resolved`, `ticket_reopened` |
| quality | `feedback_submitted`, `evaluation_completed`, `regression_detected`, `feedback_prompt_dismissed` (client) |
| finance | `payment_succeeded`, `payment_failed`, `refund_completed`, `usage_cost_recorded` |

* **Owners**: server events state outcomes and are only emitted by the
  backend from the change itself; client events describe intent and are the
  only names `POST /api/v1/events/client` accepts -- a browser cannot record
  an outcome.
* **Envelope**: `event_id`, `schema_version`, `envelope_version`, `owner`,
  `occurred_at` (UTC), `environment`, `release`, `user_id` and
  `workspace_id` (HMAC pseudonyms with `ANALYTICS_PSEUDONYM_KEY`, `u_`/`w_`),
  `task_id`, `trace_id`, `configuration_version`, typed `properties`
  (`channel`, `language`, `agent_type`, `status`, `reason_code`,
  `duration_ms`, plus the event's own). Nullable fields are always present.
* **Privacy**: a property must be named by the event's entry; a fixed list of
  names (prompt, transcript, audio, body, text, message, content, email,
  phone, name, key, secret, token, password, kyc, card, address, error) can
  never be sent; string values must look like codes (`[A-Za-z0-9_.:-]`, at
  most 64), so a sentence, an address or a number is refused. A refused event
  is logged as an error and not written; it never breaks the change.
* **Outbox**: `analytics_outbox`, written in the same transaction as the
  change where there is one (ledger moves, feedback, invitation accept);
  `deliver_analytics_outbox` sends every 2 minutes with the event id as the
  PostHog uuid (a resend is the same event), no person profile, no GeoIP;
  unconfigured PostHog leaves rows waiting without spending attempts; rows
  are pruned after 30 days.
* **Wired today**: `task_*` (ledger moves and card runs), `approval_requested`
  / `_granted` / `_rejected` (cards), `voice_session_started` / `_ended` /
  `_failed` (beside the existing `call_*` capture, with duration and a
  normalised reason), `invite_accepted` (workspace invitations),
  `feedback_submitted`, and the two client events. The existing
  `PostHogEvent` constants keep flowing unchanged, so no dashboard loses its
  history.
* **Owned elsewhere**: onboarding (`shell`), meetings (`meetings`), reminders
  (`today`), connections (`identity`), support (`support`), evaluation
  (`ops`), finance (`staff`/billing). Each emits with
  `events.emit(name, ...)`; the catalogue already lists them.

Staff read the catalogue at `GET /api/v1/admin/controls/events/catalogue`.

## 7. Yes / Not quite (handoff 2, 4, 6)

`api/services/feedback.py`, `output_feedback`. On Decibyl's replies (the UI
row under each reply, `ui/src/components/channel/ReplyFeedback.tsx`) and on
completed ledger tasks (API). Stored against the output's version (a hash of
the words shown), the model that wrote it (now recorded on each reply as
`provider:model`) and, for a task, its ledger version. One answer per person
per output; answering again replaces it. Reasons are fixed codes (wrong,
irrelevant, too late, too long, wrong language). Only replies the person can
see can be judged (their workspace; with private threads, their thread).
Dismissible; the dismissal is remembered in the browser and sent as the
client event `feedback_prompt_dismissed`. Analytics gets the verdict and
reason codes only. Staff counts at `GET /api/v1/admin/controls/feedback/summary`;
exposure counts (how many were asked) are the `staff` stream's join.

## Rollback

Every part is off by default; turning a flag off restores the previous
behaviour at once:

* `operational_quotas` off: nothing is counted or refused (rows stay).
* `task_ledger` off: cards confirm without a version, `revise` is a 404,
  failures read "failed" as before; ledger columns are ignored by the board.
* `personal_space` off: the route is a 404; spaces already made remain
  ordinary one-member organisations.
* `member_preferences`, `reply_feedback`, `capability_checklist` off: routes
  are 404s and the UI shows nothing.
* `event_catalogue` off: nothing is written; queued rows stay undelivered.

Schema: `alembic downgrade 202610071200auto` drops the tables, the trigger
and the columns (additive migration, nothing else depends on it).
