# Staff: the staff console (stream `staff`)

Phase 2 of `LAUNCH-PLAN.md`. Handoff sections 32 (admin console and
operating model), 36 (event catalogue: the staff analytics) and 37 (quality,
revenue and decision metrics); screens 29-31 and 34-44. The support inbox
and case screens (32-33) are the `support` stream's and are linked from the
console's navigation as "needs setup" until that stream lands.

Everything ships **off**. Turning `staff_console` off restores /superadmin
exactly as it was.

| Flag | Constant | What it turns on |
| --- | --- | --- |
| `staff_console` | `STAFF_CONSOLE_ENABLED` | The console under /superadmin (eight destinations, role-gated) and `/api/v1/admin/staff/*`; suspensions are enforced at sign-in |
| `staff_roles` | `STAFF_ROLES_ENABLED` | Console roles beyond the staff tier (support, operations, finance, quality), granted by an owner |
| `staff_refunds` | `STAFF_REFUNDS_ENABLED` | The finance-only `refund.request` command |
| `staff_evaluations` | `STAFF_EVALUATIONS_ENABLED` | Evaluation cases, runs and comparison (screens 34-35) |
| `staff_incidents` | `STAFF_INCIDENTS_ENABLED` | Incidents and their runbooks (screen 41) |

Founder placeholders (no value is invented; unset reads "needs setup"):
`STAFF_PILOT_USER_CAPACITY` (people the invite beta admits) and
`STAFF_PILOT_BUDGET_PAISE` (the beta's total spend budget).

## Roles and the capability matrix

`api/services/staff/roles.py` is the one answer, enforced on every console
route (`roles.require(capability)`); the UI only decides what to draw.

| Role | Comes from | Holds |
| --- | --- | --- |
| owner | `users.staff_role = superadmin` | everything except refunds; roles, budgets, policy, audit, key rotation |
| support | `users.staff_role = support`, or a grant | users and invitations, suspension requests, allowances, support, operations reads |
| operations | grant | operations, incidents and runbooks, provider metadata, policy reads |
| finance | grant | revenue, ledger, refunds (request and approve), policy reads |
| quality | grant | evaluation datasets, cases and runs |

* Refunds are finance-only, including for an owner: an owner who refunds
  grants themselves finance, and that grant is audited.
* Only an existing staff member can hold a console role; the console cannot
  make anyone staff. Owner is not grantable from the console.
* Every non-owner staff member is on the support tier today, so they carry
  support's capabilities as well as any granted role (the tier is what lets
  them reach staff routes at all). Splitting "staff" from "support" is a tier
  change for later.
* API keys never carry staff powers (`_handle_api_key_auth` clears the tier).

## Every change is a typed, approved command

`api/services/staff/commands.py`: a command has a name, the capability that
may ask, the capability that must approve (a different person) or none, a
pydantic target, an eligibility check, a preview and a handler. A request
records the full contract (command, roles, environment, target, reason,
idempotency key, approval, result) in `staff_commands` before anything
changes; request, approval, rejection and outcome each write
`admin_action_log` with the command id.

| Command | Ask | Approve | Runs |
| --- | --- | --- | --- |
| `role.grant`, `role.revoke` | roles.manage | -- | inline |
| `invite.issue` (from the waitlist; capacity preview; per-item outcomes) | users.invite | -- | inline |
| `invite.revoke` | users.invite | -- | inline |
| `user.suspend` | users.suspend.request | users.suspend.approve (owner) | inline on approval |
| `user.unsuspend` | users.suspend.request | -- | inline |
| `allowance.grant` (the controls stream's temporary allowance) | users.allowance | -- | inline |
| `refund.request` | refunds.request | refunds.approve (finance) | worker |
| `incident.open`, `incident.step`, `incident.state` | incidents.manage | -- | inline |
| `eval.case.save`, `eval.case.review`, `eval.results.record` | quality.manage | -- (review must be another person) | inline |
| `eval.run` | quality.manage | -- | worker |

* `POST /admin/staff/commands/preview` returns the exact effect without
  writing anything; the request computes it again.
* **Accepted is queued, not succeeded.** Worker commands answer `queued`;
  `run_staff_command` claims queued -> running by compare-and-swap, so a
  command runs once however often it is enqueued. A run that never reports
  back is `outcome_unknown` after 15 minutes and is never retried;
  unapproved requests expire after 24 hours (`sweep_staff_commands`, every
  2 minutes).
* The same idempotency key returns the same request; the same key with a
  different command, target or person is a 409.
* A command for another environment is refused: production and staging
  are separate consoles.

**Overlap with the ops stream (PR #524).** Ops owns platform and
infrastructure commands (`/admin/ops/commands`: pauses, retries, `flag.set`,
cost stop, drains, key rotation) with the same contract and life. The
console calls those directly and shows "needs setup" while they are absent;
it does not wrap or copy them. The staff commands above are the console's
own domain (people, money, incidents, evaluations) and are kept in their own
table so neither stream's merge depends on the other. At integration they can
be registered into the ops registry unchanged if one table is preferred.

## The destinations

| Destination | Screens | Backed by |
| --- | --- | --- |
| Overview | 29 | `staff/overview.py`: attention queue (failed and unknown tasks, waiting approvals, unknown sends, dead letters, budget alerts, KYC waiting, staff approvals, stuck refunds, open incidents, support as needs setup), three metrics, two evidence tables; ops health linked |
| Users and access | 30, 31 | `staff/users.py`: search, access state, last useful outcome, KYC status only, waitlist with the requested first task, capacity; detail with Overview/Tasks/Support/Usage/Access, connections and effective limits; task states only, never titles or messages |
| Support | 32-33 | the `support` stream (needs setup); the KYC queue stays here |
| Quality and evaluations | 34, 35 | `staff/evaluations.py`; Laya report from ops |
| Product analytics | 36 | `staff/analytics.py` (new: activation analytics did not exist) |
| Revenue and costs | 37, 38 | `staff/revenue.py`, `staff/refunds.py` (billing dashboard sources reused) |
| Operations | 39, 40, 41 | `staff/operations.py`, `staff/incidents.py`; ops commands for pause, retry, drain |
| Controls and audit | 42, 43, 44 | ops credentials (42), `staff/policy.py` + ops `flag.set` (43), `staff/role_admin.py` and the audit stream (44) |

The existing /superadmin screens keep their URLs and appear under their
destination for owners (they are superadmin-only routes).

## Metric definitions (handoff 37)

* **Useful outcome**: a completed ledger task (completion needs evidence),
  a board task a person marked done, or an approval card that ran. A chat
  reply alone does not count. Times are when it happened, never when read.
* **Activation**: a person's first useful outcome after signing up.
* **Weekly useful users**: distinct people with a useful outcome in the
  seven days ending now.
* **Seven-day repeat**: activated people with another useful outcome in
  days 1-7, over activated people whose seven days have passed; the rest
  are "not yet eligible".
* **Task success**: completed / (completed + failed + unknown); cancelled,
  declined and undone are excluded and shown.
* **Usefulness**: yes / answered; exposure is Decibyl's replies in the
  period (an upper bound on prompts shown).
* **Cost per success / per useful user**: provider cost, failed attempts
  included, over successes or useful users; undefined (never 0) with none.
* **Voice latency**: user stopped speaking to first audio out on the
  pipeline's clock. The client-side measure the handoff asks for is not
  instrumented yet (`voice` stream) and the screen says so.

## Evaluations

Cases are versioned; a save is a draft; another quality person approves.
Inputs are sanitized (addresses, phone numbers, long digit runs). A run uses
the latest approved version of every case, records the set's and the
configuration's hashes, and never overwrites an earlier run. Only
deterministic checks decide a case; judge commentary is stored apart and
never counted. Gate: `partial` (any unknown), `insufficient_sample` (< 10),
`regression` (a permission or approval case failed, or a baseline pass now
fails), `needs_baseline` (failures with nothing to compare to; no invented
threshold), else `passed`. The built-in `routing_rules` runner calls no
model; model runs are recorded from staging with `eval.results.record`.

## Refunds

Finance asks, a second finance person approves, the worker runs it once:
lock the payment, recompute what is refundable (collected less every
non-failed refund), write `staff_refunds`, call the provider with the
command's key as the receipt, read the status back. `refunded` only when the
provider says processed; otherwise `pending` until the sweep reads it again;
a call that raised is `outcome_unknown` and never resent. Without provider
keys the command is refused as needs setup. The credit balance is not
changed by a refund (an accounting rule for the finance owner).

## Privacy

No customer content in any console response: task titles, briefs, results,
messages, memory and KYC documents are never read. Provider references are
masked; analytics drill-down is keyed pseudonyms only. The console's main
region carries `ph-no-capture`, and the secret form never echoes a value.

## Migration

`20261008staff` (revises `202610071500shell`): `staff_role_grants`,
`staff_commands`, `staff_refunds`, `staff_incidents`,
`staff_incident_steps`, `quality_eval_cases`, `quality_eval_runs`,
`quality_eval_results`, and `users.staff_suspended_at`. Additive only.

## Rollback

* `staff_console` off: /superadmin is the previous gate and strip; every
  `/admin/staff/*` route is a 404; a suspension is no longer enforced.
* `staff_roles` off: only owner (superadmin) and support (tier) apply;
  grants stay in the table, unused.
* `staff_refunds`, `staff_evaluations`, `staff_incidents` off: those
  commands and routes are 404s; records stay.
* Schema: `alembic downgrade 202610071500shell` drops the tables and the
  column (losing role grants, staff command history, refund records,
  incidents and evaluation results; the audit log keeps a line for each).
