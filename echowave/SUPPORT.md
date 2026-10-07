# Support: customer help and staff support (stream `support`)

Phase 2 of `LAUNCH-PLAN.md`. Handoff section 33 ("Customer support and
requested actions"); screens 28 (customer help and ticket), 32 (support
inbox and case) and 33 (support action preview and execution). Built on
`controls` (task ledger, quotas, member preferences, event catalogue,
`admin_action_log`) and `shell` (AuditTimeline, TaskStatus, ErrorState,
EmptyState, motion tokens). Everything ships **off**.

| Flag | Constant | What it turns on |
| --- | --- | --- |
| `support_help` | `SUPPORT_HELP_ENABLED` | `/help` (screen 28), `GET/POST /api/v1/help/*`, Help in the profile menu and the top bar's Help menu, "Get help" beside a failed reply. Honours per-workspace overrides. |
| `support_inbox` | `SUPPORT_INBOX_ENABLED` | `/superadmin/support` (screen 32), `/api/v1/admin/support/*`, "Support" in the console strip and "Support inbox" in the staff menu. |
| `support_actions` | `SUPPORT_ACTIONS_ENABLED` | `/superadmin/support/actions` (screen 33), `/api/v1/admin/support/actions/*`, the ARQ job `run_support_action` and the `sweep_support_actions` cron. |

## 1. Help and tickets (screen 28)

`api/services/support/sharing.py`, `tickets.py`, `attachments.py`;
`api/routes/support.py`; `ui/src/app/help/*`, `ui/src/components/support/*`.

* **What is shared, before it is sent.** A request about a task or a reply
  builds its share from named sections, and the same function builds the
  preview the person reads and the snapshot stored on the ticket:
  * `account` -- always (email, workspace), and the preview says why;
  * `task_metadata` -- on by default: ids, state, times, helper, model, the
    last ledger move. No words;
  * `content` -- off unless switched on: the task's title, brief and result,
    or the reply's words and the person's message just before it in the
    same thread.
  Audio and recordings are never offered, and the preview says so with the
  rest of what is never shared. Staff see exactly the stored snapshot.
* **Scope.** A ticket is its requester's, in the workspace it was opened
  from: a colleague in that workspace, or the same person in another
  workspace, gets 404. A task or reply from another workspace cannot be
  attached (404). A private-thread reply is checked the way feedback is.
* **Once.** Submit carries an `Idempotency-Key` (one ticket per key per
  person, a unique index); a reply carries a `client_key` (one message per
  key per ticket). The UI makes the key once per draft and keeps it, and the
  words, after a failure, so "Send again" cannot send twice.
* **Thread.** Customer, support and system lines; internal notes never (they
  live in `support_notes`, a table no customer route selects from). Statuses:
  open, waiting on customer, in progress, resolved; a customer reply to
  "waiting" moves it to in progress. Resolve and Reopen are on the ticket;
  reopening keeps every message, the share and any action evidence.
* **Files.** Screenshots, PDFs, text, up to 5 MB, through `services/storage`
  under `support/<org>/<ticket>/`. If storage is not set up or refuses the
  file, the answer is 503 "could not be stored; your request and message are
  kept" -- the design's "attachment failure" state -- and nothing is recorded.
  Staff open files through a five-minute signed link, audited.
* **Where Help is.** The profile menu, the top bar's Help menu ("Ask Decibyl
  support"), and "Get help" beside a failed Decibyl reply in the Chat thread
  (`chat_shell`), which opens a request about that one reply.

## 2. Staff inbox and case (screen 32)

`api/routes/support_admin.py` (any staff tier, `get_staff`);
`ui/src/components/support/staff/*`.

* **Queue**: status, assignee (mine / unassigned), severity and overdue
  filters; each row shows severity, assignee, status, next step (Assign,
  Reply, Waiting on customer, Follow up), age and requester. Oldest first and
  stable, so a case being worked does not move. Overdue is first response
  past a target by severity (urgent 1 h, high 4 h, normal 24 h, low 72 h --
  **internal placeholders until the founder sets support hours**).
* **Case**: the customer thread, internal notes on their own dashed surface
  labelled "only staff see these", one composer with two explicit modes
  (Reply to customer / Internal note) whose drafts are kept separately,
  "Then" status after a reply, assignee / severity / status / incident.
  Changes name the case `version`; a second person changing it at the same
  time gets 409 with the case as it now is, shown in words.
* **Context**: customer and workspace, exactly what they shared, read-only
  diagnostics no wider than the share (the task's live state and ledger
  moves only if they shared its details; today's allowances when quotas are
  on), files, actions on the case, and the case's audit history.
* **Audit**: opening a case (`support_case_viewed`, at most once per person
  per case per 30 minutes), replies, notes, changes, file opens -- all in
  `admin_action_log`, shown on the case and in the console's audit log.
* **Layout**: 280 px queue, flexible thread, 300 px context at 1280 px and
  wider; the context becomes a drawer below 1280; on a phone, queue -> case
  (with Back) -> context sheet, each with a deep link.
* **Metrics** (`GET /admin/support/summary`): first response, resolution and
  reopening measured separately; satisfaction "Not collected yet".

Support-tier staff may open `/superadmin/support*` (SuperadminGate); the
superadmin console strip lists Support while the flag is on.

## 3. Typed support actions (screen 33)

`api/services/support/commands.py` (the typed commands) and `actions.py`
(the lifecycle); `api/tasks/support.py`.

Record request -> verify identity and scope -> preview exact action ->
approval by a second person -> execute on the worker -> reconcile -> notify.

| Command | Approver | Needs the customer's ticket | Run | Reconcile |
| --- | --- | --- | --- | --- |
| Grant temporary usage | a second staff member who is **superadmin** | no | `quotas.grant` (reason carries `[support action #id]`) | finds the grant by that marker |
| Cancel a task | a second staff member | yes | ledger move to cancelled at the previewed version | task is cancelled |
| Retry a task | a second staff member | yes | a new queued ledger task (key `support-retry:<task>:<action>`), handed to its agent if it had one | the new task exists |
| Pause a routine | a second staff member | yes | `is_active` true -> false, compare-and-swap | routine is paused |
| Change a personal setting (language, timezone, summary time) | a second staff member | yes, from that person | `member_preferences.save` at the previewed revision | value is in place |
| Refund a payment | -- | -- | **unavailable**: finance role and screen 38 (stream `staff`); free while early | -- |
| Export / delete customer data | -- | -- | **unavailable**: the customer does it from Privacy; staff-run needs identity verification across stores | -- |
| Repair a connection | -- | -- | **unavailable**: the customer completes the app's sign-in; staff reply with what to reconnect | -- |

* **No free form.** A command is a registry entry with a pydantic parameter
  model (`extra="forbid"`); there is no shell, SQL or AWS command, and an
  unknown kind or an extra parameter is a 422.
* **Preview**: target workspace / person / ticket, environment, old and new
  values, impact (e.g. "Up to 60 more messages in total, until 10 Oct"),
  dependencies (the state it was previewed against) and who must approve.
  `version` hashes kind, target, parameters and that captured state.
* **Request**: needs a reason and an `Idempotency-Key`; the same key again
  is the same action (repeat clicks make one command). Scope is verified:
  the person is a member of the workspace, the ticket is from it, and a
  command that changes the customer's own things needs their ticket.
* **Approve**: a different person (the service refuses, and a check
  constraint `approved_by <> requested_by` refuses in the database), at the
  command's tier, naming the version they read. The preview is recomputed:
  if the customer's account moved, approval is refused and the requester
  previews again. Revising parameters makes a new version and clears any
  approval.
* **Run**: the requester or approver; compare-and-swap approved -> queued,
  then `run_support_action` claims queued -> running. Accepted reads as
  "queued, accepted, not finished" until the worker settles it. A repeat
  click while in flight changes nothing. The run re-checks the hash against
  the approved version.
* **Outcomes**: succeeded, failed (refused cleanly, nothing changed), or
  `outcome_unknown` on an unexpected error mid-run or a run whose worker died
  (swept after 10 minutes) -- "We are checking whether this happened",
  never re-run; **Check what happened** reconciles against the real state.
* **Expiry**: a request lapses 24 h unapproved, an approval 15 min unrun.
* **Notify**: the customer's thread gets "Done: ..." or "We could not do
  this: ...". The customer sees each action's summary and state, never who
  asked, who approved or why.
* **Audit and events**: every step in `admin_action_log` (actor, ticket,
  target, kind, version, parameters, reason, result) and the catalogue's
  `support_action_requested / _approved / _executed / _failed`, plus
  `ticket_created / _resolved / _reopened` (codes only, never words).
* A support request never approves a send, booking or payment for the
  customer: no command sends, books or spends.

Not built here: AI-drafted replies (none are sent; the design's rule holds
trivially), converting a case to an evaluation (stream `ops`/`staff`),
assisted access (the existing impersonation stays superadmin-only and
separate), staff-run export / deletion / refund (above), and email or push
when support replies (the inbox bell is per workspace, so it would tell
colleagues a person asked for help; the Help list marks "Support replied").

## Placeholders for the founder

* First-response targets by severity (1 h / 4 h / 24 h / 72 h) in
  `services/support/tickets.FIRST_RESPONSE_TARGET` -- internal, never shown
  to customers.
* No price, plan or positioning string was added.

## Rollback

Each flag off restores today's behaviour at once:

* `support_help` off: `/api/v1/help/*` is 404; `/help` says "This page is
  not available"; no Help entry, no "Get help" under failed replies.
* `support_inbox` off: `/api/v1/admin/support/*` (cases) is 404; the
  console pages say not available; no Support link.
* `support_actions` off: action routes 404; the worker job and the sweep
  do nothing. An action that was already queued when the switch went off is
  not run and stays "queued" (visible as such once the switch is back on);
  request it again rather than expecting it to start by itself.

Schema: `alembic downgrade 202610071400ops` drops the five `support_*`
tables (additive migration; nothing else depends on them).
