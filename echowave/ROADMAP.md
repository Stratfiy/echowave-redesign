# Launch roadmap and running log

Two things in one file, deliberately: the ordered plan, and the live state of
it. A separate progress file drifts from the plan it tracks.

**If you are an agent picking this up cold, read §0 first.** It is written for
exactly that.

---

## 0. Cold start — read this first

### Where things are

| | |
|---|---|
| Repo root | `/home/user/echowave-redesign` — note `.git` is here, `docker-compose.yaml` is one level down in `echowave/` |
| Working dir | `/home/user/echowave-redesign/echowave` |
| Branch | `claude/pricing-correctness` |
| Live box | `52.200.151.15` — app/api/docs/apex all resolve here |

### Running the tests

Postgres and Redis **die frequently in this container**. When a test run shows
`Connect call failed ('127.0.0.1', 5432)` — that is infrastructure, not your
code. Restart and re-run:

```bash
pg_ctlcluster 16 main start; redis-server --daemonize yes --save ''; sleep 3
```

Then:

```bash
cd /home/user/echowave-redesign/echowave
source venv/bin/activate
export PYTHONPATH=. \
  DATABASE_URL="postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/decibyl_test" \
  REDIS_URL="redis://127.0.0.1:6379/0" \
  ENABLE_AWS_S3=false MINIO_PUBLIC_ENDPOINT=http://localhost:9000 DEPLOYMENT_MODE=oss
python -m pytest api/tests -q --no-header -p no:randomly
```

There is no `api/.env.test` in this container despite what `AGENTS.md` says —
export the variables directly as above.

### Known-failing tests — NOT yours

Six fail on a clean `origin/main`. Verified against a clean worktree on a
separate database. Do not chase them:

- `test_mcp_tool_creation.py::test_sdk_openapi_exposes_create_tool_schema_and_llm_hints`
- `test_user_idle_handler.py::test_idle_does_not_trigger_during_active_conversation`
- `integrations/test_run_pipeline_text_greeting.py::test_text_greeting_speaks_then_user_transcript_triggers_end_call`
- `test_pipecat_engine_tool_calls.py` — three parallel-transition tests

Baseline is **2596 passed, 6 failed**.

### Docs

```bash
cd docs && npm run build && npm run check-links
```

`docs.json` is the single nav source — it drives both the rendered sidebar and
the MCP docs tools. Adding a page means adding it there.

### What is confirmed working in production

Tested by the operator, not just by tests: **outbound calls**, and the
**Google Calendar tool**.

---

## 1. The plan, in order

Ordered by what unblocks the most. **Payments and autopay are deliberately
last** — prepaid plus the dunning schedule already works, and nothing else
waits on them.

**Revised.** Autopay moved out of "later": the number purchase flow is now
*documents → approved → search → select → **mandate** → number issued*, so the
mandate gates issuing rather than following it. Everything else keeps its
order, and general payments work (invoice PDF, credit notes) stays last.

| # | Item | Est | State |
|---|---|---|---|
| 1 | Admin route for `is_platform_managed` | 0.5d | ✅ done |
| 2 | Customer token & spend dashboard | 1.5d | ✅ done |
| 3 | Call-log graphs and metrics | 1d | ✅ done |
| 4 | Provider markup (the 1.3×) | 1d | ✅ done |
| 5 | **Autopay mandate, gating number issue** | 3–4d | ✅ code done, waiting on Razorpay activation |
| 6 | Number provisioning UI (incl. the mandate step) | 1.5d | ✅ done |
| 7 | Low-balance email | 0.5d | ✅ done |
| 8 | *(later)* Invoice PDF, credit notes | 1d | ☐ |

### What item 5 actually involves

Razorpay **Subscriptions / UPI e-mandate**, not the one-off order flow that
exists today. Concretely:

1. Activate Subscriptions on the Razorpay account — **their approval, not our
   code, and it can take days.** Start this before writing anything.
2. A `plan` for the ₹349 rental.
3. `POST /subscriptions` at the point of purchase; the customer authorises the
   mandate (UPI Autopay / card / eNACH).
4. Webhook events none of which are handled today —
   `subscription.activated`, `subscription.charged`, `subscription.halted`,
   `subscription.pending`, `mandate.revoked`. `payments.py:296` currently
   acknowledges subscription events and acts on none.
5. **Provisioning waits on `subscription.activated`.** A number issued against
   an unauthorised mandate is a number we pay for and cannot collect on.
6. Reconcile with the existing rental charge: a mandate-collected month must
   not *also* be debited from the prepaid balance. The idempotency is already
   there (`uq_recurring_charge_period`); what changes is which side collects.

The dunning schedule stays as the fallback for a revoked or failed mandate —
day 7 suspend, day 45 release-eligible — because a mandate that stops working
is exactly the situation it was written for.

Each item is done only when it is **built, tested, and verified against a real
request** — not when it compiles.

---

## 2. Why this order

**1 before everything.** Nothing in the managed-number path is reachable
without it. Provisioning, rental billing and the KYC gate are all built and all
dormant because one flag has no setter.

**2 and 3 before 4.** You cannot price confidently against numbers you cannot
see. The token dashboard already exists for superadmins — `/tokens` even
accepts an `organization_id` — so this is a scoped route and a page, not a
rebuild.

**4 before 5.** Charging correctly matters more than a nicer way to buy.

**6 before 7.** A customer whose number is suspended at day 7 with no email is
the worst version of the dunning schedule. The email is worth more than the
mandate for MVP, at a tenth of the cost.

**7 last.** Razorpay e-mandate is a separate product with its own activation —
partly outside our control, and prepaid works today.

---

## 3. Running log

Newest last. Each entry: what changed, how it was verified, what is next.

### 2026-08-10 — baseline

Managed numbers (provisioning, Plivo compliance, rental billing, dunning,
release, reconciliation), 4 read-only MCP telephony tools, docs migrated to
Astro Starlight and served from nginx, apex served empty, subdomain config
rendered by `decibyl-init`.

Verified: 2596 tests pass (6 pre-existing failures); docs build 130 pages with
16,954 links and none broken; all four hostnames probed through real nginx.

Not proven: **nothing has run against Plivo's live Compliance API.** No
application filed, no number bought.

Next: item 1.

### 2026-08-10 — item 1 done: `is_platform_managed` has a setter

`api/routes/telephony_admin.py`, mounted at `/api/v1/admin/telephony`, behind
the superuser gate declared at router level. Two endpoints: list the
configurations staff administer, and set or clear the managed flag.

The part worth knowing is the refusal. **Clearing the flag is rejected with 409
while numbers we bought are still attached**, and names them. The flag is what
marks a number as ours to bill and ours to release, so clearing it with rentals
outstanding leaves us paying a carrier with nothing pointing at the money — the
same orphaned-rental failure the reconciliation job exists to catch, except
caused by us. A released number does not block, and neither does a customer's
own number (no `carrier_number_id` means we never bought it).

Verified: 11 tests. The one that matters most asserts the *effect* rather than
the write — a configuration is ungated before the flag is set and raises
`TelephonyNotVerified` after, which is the whole reason the flag exists.

Next: item 2, the customer token and spend dashboard. The backend is largely
there — `dash.token_usage_series` and `token_usage_by_model` already accept an
`organization_id`; what is missing is a customer-scoped route (the existing one
is superadmin-only) and a page.

### 2026-08-10 — item 2, backend half: customers can read their own numbers

Two routes on `api/routes/organization_usage.py`:

- `GET /organizations/usage/tokens` — series, **by model**, context growth
- `GET /organizations/usage/spend` — daily cost split by component, plus
  balance and a days-remaining projection

Thin by design. The aggregation already existed for the superadmin screens and
already accepted an `organization_id`; the only real work was scoping it
safely.

**The security property is the whole point and is tested first.** The
superadmin equivalents take an `organization_id` parameter. These force it from
the authenticated user and ignore one in the query — a test passes another
org's id and asserts their spend does not appear.

Two bugs caught while writing it, both of the silent kind:

- summing spend by `key.endswith("_paise")` totals **zero**, because
  composition rows are `{"day":…, "stt": n, "llm": n}` with plain ints. The
  burn rate would have read "no spend" on a spending account.
- days-remaining divides by the daily average, so a zero-spend account needs to
  return `null` rather than raise.

Verified: 12 tests. 511 pass across usage, billing, organization and telephony.

Next: the **UI page** for these two endpoints, then item 3 (call-log graphs).
`recharts` is installed; reusable chart primitives are at
`ui/src/app/superadmin/billing/_components/primitives.tsx`, and the customer
page to extend is `ui/src/app/usage/page.tsx`.

### 2026-08-10 — cross-origin audit for the app/api split

Asked whether the code holds up once the app is on `app.decibyl.ai` and the API
on `api.decibyl.ai`. Checked the places a split usually breaks.

**Fine:**

- **CORS.** `DEPLOYMENT_MODE=oss` allows any origin without credentials, which
  is correct because the UI authenticates with a Bearer token rather than a
  cookie. No SameSite problem to solve. (If auth ever moves to cookies, this
  becomes a real change — see the note in the nginx template.)
- **The UI's backend URL.** `NEXT_PUBLIC_BACKEND_URL` is not set anywhere, so
  the UI resolves the API at runtime from `/health` → `backendApiEndpoint` →
  `PUBLIC_BASE_URL`. **No UI rebuild is needed for the hostname change**, which
  also makes it reversible in one restart.
- **Razorpay.** The webhook is inbound to the API host; there is no
  callback/redirect URL built anywhere in the payment flow.
- **Google Calendar OAuth.** Redirect is
  `{BACKEND_API_ENDPOINT}/api/v1/integrations/google-calendar/callback` — on
  the API host, which is where it should be. Confirmed working in production.

**Broken, now fixed:** `UI_APP_URL` defaulted to `http://localhost:3010`, was
absent from the env template, and is what builds the **embed widget snippet**.
Every customer who copied that snippet into their own site got a script tag
pointing at localhost — silently doing nothing on their visitors' machines, and
failing nowhere we would ever see it. It now derives from `DECIBYL_APP_HOST`,
so naming the app host once fixes this too.

Verified: 513 tests pass across embed, telephony, usage and billing.

### Other agent's work — reviewed

All of it is merged into `origin/main` and already in this branch: the BYOK
double-charge fix (947325c), Google Calendar conflict checking (864da35), the
managed model catalog (e874a78), the supplier address correction (32aeac6).
The remaining `claude/*` branches are the merged PR branches, not unmerged
work. Nothing of theirs is waiting to be picked up.

### 2026-08-10 — item 4 done: provider usage is marked up, and both numbers survive

Usage bought with our keys is charged at **1.3×** what the vendor charged us
(`MANAGED_PROVIDER_MARKUP_BPS=13000`, in basis points so the arithmetic stays
integer — 1.3 as a float is 1.2999999999999998 and reconciles differently
depending on the order lines are summed).

Two decisions worth knowing:

**The markup covers STT, LLM and TTS only.** Telephony is deliberately excluded
(`MARKED_UP_COMPONENTS` in `cost_engine.py`). The 1.3× is for provider API keys
we resell; the telephony price is set in the rate card and marking it up too
would apply the multiplier twice to a number that was already retail.

**Both figures are stored.** `call_cost_items.provider_cost_paise` holds what
the vendor charged, `cost_paise` what the customer paid. The alternative —
baking 1.3 into the rate card — needs no code and destroys every margin report:
`provider_rates` would hold retail, and unit economics would read zero margin on
a call that earned 30%. Migration `d3f5a81c62b7` backfills the new column from
`cost_paise`, which is correct for history charged at cost.

The platform fee is never marked up (it is already our margin), and a **BYOK**
component produces no line at all — the customer paid the vendor directly, so
there is nothing to mark up.

Verified: 16 new tests in `test_provider_markup.py`; full suite **2647 passed,
6 failed** — the same six known failures listed in §0.

Next: item 2's UI page, then item 3.

### 2026-08-10 — items 2 and 3 done: an Analytics section the account owns

`/analytics`, three tabs, in the order the questions get asked.

**Calls** is new work, not a re-skin. `call_analytics()` in
`billing_dashboard_client.py` is five grouped queries over the runs in range:
outcome, direction, duration distribution, hour of day, and the busiest agents.
Answer rate leads the page because it is the one number that moves for reasons
outside the agent — a bad list, a blocked caller ID, a carrier problem — and a
team tuning prompts while the answer rate collapses is debugging the wrong
thing.

**Tokens** and **Spend** render the routes built earlier the same day.

Three decisions worth carrying forward:

- **The daily series is re-projected, not passed through.** `daily_series` is a
  staff query and carries `provider_cost_paise` and `margin_paise` — what the
  vendors charged us and what we kept. Handing a customer those two columns
  publishes our markup on every account, permanently, and no UI has to render
  them for it to be readable in the response. There is a test for it.
- **The range lives in the URL** (`?days=`), so switching tabs keeps the window
  and a link to "last 90 days" is a link someone can send.
- **Empty ranges report null, not zero.** A zero average call length reads as
  "every call was instant", which is a very different alarm from "there were no
  calls".

`primitives.tsx` and `chartTheme.ts` moved out of
`app/superadmin/billing/_components/` to `components/charts/` — they are no
longer staff-only. All 11 superadmin importers were updated; nothing else
changed in them.

Verified: 16 new tests in `test_call_analytics.py`, 29 across both analytics
files; `tsc --noEmit` clean, eslint clean, `next build` clean with all three
routes emitted.

Next: item 5, the autopay mandate that gates issuing a number.

### 2026-08-10 — items 5 and 6: autopay, and the purchase flow that uses it

The purchase order is now **documents → approved → search → select → mandate →
number**, enforced in code rather than only in the UI.

`api/services/billing/mandates.py` owns the standing authorisation. It creates
the Razorpay subscription, tracks its state from webhooks, and answers one
question for provisioning — *may we hand this account a number?* The collection
that happens under it is recorded by `rentals.record_mandate_collection`.

**Where the gate sits is the design.** `search()` needs verification only;
`provision()` also needs an authorised mandate. Asking someone to authorise a
standing instruction before they have seen what is available is the wrong order;
handing over the number before they have is a bill we pay and cannot collect on.

Four things worth carrying forward:

- **Only the signature-verified webhook authorises a mandate.** There is no "I
  have authorised it" button, for the same reason there is no "I have paid"
  button. `payments.handle_webhook` routes `subscription.*` events into
  `apply_subscription_event`; the browser cannot reach that state machine.
- **A terminal mandate is never revived.** Webhooks arrive out of order, and a
  late `activated` after a `cancelled` would otherwise resurrect an
  authorisation the customer's bank has already withdrawn.
- **The idempotency key for a collection is the provider's payment id, not the
  period.** A test caught this: recording a collection advances the charge to
  the next period, so a redelivered `subscription.charged` computed a *different*
  `period_start`, missed the existing unique constraint entirely, and billed a
  month forward. New partial unique index on `provider_payment_id`.
- **A mandate collection writes no ledger entry.** The prepaid ledger tracks
  credit the customer bought from us; rent collected from their bank never became
  credit, and debiting it there takes the rent twice.

`charge_period` now returns `mandate_collects` and does nothing when an
authorised mandate is attached — and resumes debiting the balance the moment the
mandate stops being authorised, which is what makes revocation a fallback rather
than a hole. This does forgo the prorated first part-month when a mandate is
collecting: the mandate's first cycle charges a full month, and losing part of
one month is the right direction to err when the alternative is billing a month
twice.

`REQUIRE_MANDATE_FOR_NUMBERS` defaults **true**. Set it false only while Razorpay
Subscriptions activation is pending — that activation is their approval and can
take days, and it is the one remaining thing between this and a real purchase.

UI: `/numbers` is a four-step purchase screen; the autopay step reads its state
back from the server and has no way to mark itself done. `/analytics` and
`/numbers` both build clean.

Verified: 25 tests in `test_autopay_mandates.py`, 108 across provisioning,
rentals, payments and mandates. Migration `e5b27c0a91d4` round-trips.

Next: item 7, the low-balance email.

### 2026-08-10 — item 7 done: the warning that makes dunning humane

The dunning schedule suspends a number seven days after a rental it could not
collect. That policy is right and it was **silent**, which made the experience
wrong: the money was almost always there and nobody said it was needed.

Three pieces:

- `api/services/notifications/email.py` — a deliberately small SMTP sender.
  There was **no email infrastructure in this codebase at all** before this, so
  it uses the standard library over `asyncio.to_thread` rather than adding an
  async SMTP dependency to a path that has to work on a box we cannot easily
  test against.
- `api/services/billing/low_balance.py` — pure policy, in one place like the
  dunning schedule it complements. Two ways to qualify: **days of runway**
  (balance ÷ recent daily burn, the number a customer can act on and the one
  that scales across account sizes) and an **absolute floor** for accounts with
  no measurable burn rate, because "infinite days" on ₹120 of credit is not a
  useful answer.
- `api/tasks/low_balance.py` — the daily job, 03:30 UTC / 09:00 IST so a warning
  arrives when somebody can act on it, and deliberately *after* the nightly
  rental run so the balance quoted is the one they have now.

Two properties carry it:

- **The dedupe row is claimed before the send**, not after, so two workers
  racing produce one email and a re-run produces none. That is what makes this
  job safe to re-run, which is what makes a missed cron harmless.
- **Email being unconfigured is never an error.** `send()` returns False rather
  than raising, because every caller is a billing job whose real work is
  billing. A rental must not fail because a courtesy could not be sent. The job
  logs one line a day when SMTP is off.

Severity earns a repeat, repetition does not: one notice per account per day per
level, and a second the same day only if the account crossed from low into
critical.

Verified: 23 tests. Migration `f18a4d3c07e9` round-trips.

Next: item 8 (invoice PDF, credit notes) is the only roadmap item left, and it
was always the deferred one. The real blocker to launch is now external —
Razorpay Subscriptions activation, and Plivo's reseller approval.
