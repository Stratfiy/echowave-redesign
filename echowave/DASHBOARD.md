# Admin dashboard — pricing and metric definitions

The single reference for how Decibyl prices a call and what every dashboard
number means. Where this document and the code disagree, the code is wrong.

Implementation lives in `api/services/billing/`.

---

## Pricing model

```
total_charged = platform_rate x billed_seconds + Σ(provider costs at cost)
billed_seconds = ceil(connected_seconds / pulse) x pulse
```

* **Platform rate** is ours. List price **$0.02/min**, configurable per account.
* **Billing granularity is a 15-second pulse**, not a whole minute.
* **Provider costs** — STT, LLM, TTS, telephony — are passed through **at cost,
  with no markup**.

### Priced in dollars, settled in rupees

The list price is fixed in **USD** and converted to INR at the rate in force
when the call happened. Voice-AI platforms compete on a dollar figure; if ours
were fixed in rupees, our price against Bolna or Vapi would drift every time the
rupee moved.

`usd_inr_rate_history` holds that rate, effective-dated like every other rate
here, so an old invoice recomputes to the number that was actually charged
rather than to whatever the rupee is worth today. Each call snapshots all three
inputs — `platform_rate_micros_usd_applied`, `usd_inr_paise_applied`,
`pulse_seconds_applied` — so a receipt can show its working rather than
asserting a rupee figure.

A rate row carries **either** a dollar price or a rupee one, never both, enforced
by a check constraint. A contract agreed at "₹1.20 a minute" means ₹1.20 a
minute whatever the rupee does, and two populated columns would leave two
answers to what an account pays.

The fallback constant in `money.py` (₹96.00) exists so a missing FX row cannot
stop costing outright. It is not a rate we intend to bill at — using it logs a
warning, and the KPI screen shows how old the newest real rate is.

### The 15-second pulse

The commercial differentiator, and a single parameter rather than a second code
path: at `pulse_seconds = 60` the engine reproduces whole-minute billing
exactly, which is what makes the comparison against competitors honest.

A 62-second call bills **75 seconds** with us and **120** with a whole-minute
platform. The saving is roughly half a pulse per call — about 7.5 seconds — so
it is worth ~17% on a 45-second call and ~3% on a four-minute one. **Short,
high-volume traffic is where it shows up**, which is the traffic Indian voice
agents run. The claim belongs there and nowhere else.

What it costs us is measured, not estimated: see *Unit economics* below.

Not every call has provider costs. An account that brings its own model keys
pays those providers directly, so Decibyl incurs no inference cost and the
receipt is a platform fee alone. An account on Decibyl-managed model services
does incur them, and they appear as itemised pass-through lines.

### Why a markup is structurally impossible

This is the commercial differentiator, so it is enforced by the schema rather
than by convention:

* Provider cost and the platform fee are **separate rows** in `call_cost_items`
  and **separate columns** (`total_provider_cost_paise`, `total_charged_paise`)
  on `workflow_runs`. Nothing anywhere stores a single blended number.
* Every provider line records `units`, `unit_rate_mpaise` and `cost_paise`, so
  any receipt can be re-derived from the rate that was on file.
* A provider line is only ever measured usage × a rate read from
  `provider_rates`. There is no code path that can inflate one.

`test_cost_engine.py::TestNoMarkupOnInference` asserts this directly, including
that gross margin on a managed call equals the platform fee exactly.

---

## Money handling

| Rule | Why |
|---|---|
| Money is stored as **integer paise**; columns end in `_paise` | Floats cannot represent decimal money exactly. Never use one. |
| Unit rates are **integer millipaise**; columns end in `_mpaise` | Provider rates are routinely fractions of a paise per unit. Quoting them in paise would round the *rate itself* to zero, and that error would then compound across every call using it. |
| Round **once**, at the line item | Rounding an intermediate is what accumulates drift. A line's cost is one exact integer ratio, rounded once. |
| Round **half away from zero** | `round()` in Python is banker's rounding, which would bias totals. |
| Display formatting is never persisted | `format_paise()` is for rendering only. |

**The invoice total is defined as the sum of the rounded line items** — not a
separately-rounded total. That is what makes `sum(line_items) == total` exact by
construction rather than by luck, at any aggregation size.
`test_cost_engine.py::test_ten_thousand_calls_reconcile_exactly` asserts it over
10,000 synthetic calls with deliberately awkward durations and sub-paise rates.

---

## Rate resolution order

Resolved in this order; the first match wins.

1. **Account override** — an explicit effective-dated rate in
   `organization_rate_history`. Enterprise deals live here.
2. **Volume tier** — optional. Applies when the account's billable minutes in
   the current billing period reach a tier's `min_period_minutes`. The **highest
   matching threshold** wins.
3. **Global default** — a volume tier at threshold **zero**, set from the Rate
   card screen. Every account has reached zero minutes, so a zero-threshold
   tier is exactly "the price everyone pays unless something more specific
   applies". It needs no extra table and no extra branch in the resolver.

If no such tier exists, resolution falls back to
`DEFAULT_PLATFORM_RATE_MICROS_USD = 20_000` ($0.02/min) — a constant in
`money.py`, for a fresh install or a test. The Rate card screen says so plainly
rather than presenting it as a configured price.

The **pulse** resolves alongside the rate, from the same row, falling back to
`DEFAULT_PULSE_SECONDS = 15`. An account that negotiated whole-minute billing
carries `pulse_seconds = 60` on its override.

### Effective dating

Rates are **never updated in place**. Changing a rate closes the current row by
setting `effective_to` and inserts a new row with a new `effective_from`.

* The window is `effective_from <= at < effective_to`, so a change has no gap
  and no overlap at the instant it takes effect.
* Every lookup takes an `at` timestamp and answers *what was in force then*, so
  recomputing an old invoice reproduces the original number.
* A partial unique index enforces at most one open (`effective_to IS NULL`) row
  per account.

Provider unit rates work identically, in `provider_rates`, so a Deepgram or
Twilio price change never rewrites the cost of calls already made.

**Belt and braces:** the resolved rate is also snapshotted onto the call row as
`platform_rate_mpaise_applied` at call time, so a historical receipt survives
even a corrupted history table.

A missing provider rate resolves to `None`, **not zero** — the cost engine
reports it as uncosted usage rather than silently pricing it at nothing, which
would understate provider cost and overstate margin.

---

## Metric definitions

| Metric | Definition |
|---|---|
| Billable minutes | `ceil(billable_seconds / 60)`, summed. Connected calls only. **Reporting only** — not what the fee is computed from. |
| Billed seconds | `ceil(billable_seconds / pulse) x pulse`. What the platform fee is actually charged on. |
| Revenue | `sum(total_charged_paise)` |
| Provider cost | `sum(cost_paise)` where `component != 'platform'` |
| Gross margin | `revenue − provider_cost` |
| Gross margin % | `gross_margin / revenue` |
| Perceived latency | `t_audio_out − t_user_stopped`, per turn |
| p50 / p95 | `percentile_cont(0.5 / 0.95) WITHIN GROUP (ORDER BY latency_ms)` |
| Answer rate | `answered_calls / dialled_calls` |
| Completion rate | `completed_calls / answered_calls` |
| Cost per completed | `campaign_spend / completed_calls` |
| Concurrency | max calls in flight at once, per bucket (see below) |

Billable minutes are ceilinged **per call, then summed** — never summed as
seconds and ceilinged once. They are a reporting figure: "minutes used" is what
a customer asks about. The platform fee is computed from `billed_seconds`, so a
30-second call reports one minute and is charged for 30 seconds.

Every **per-minute** figure on the unit-economics screen divides by *connected*
seconds, never by billed seconds or whole minutes. A per-minute cost is a
statement about service delivered, and connected time is the only one of the
three that measures it — the other two carry our own rounding convention and
would flatter the number.

**Percentiles must be computed in SQL over raw `call_turn_metrics` rows**, never
averaged from pre-aggregated buckets: the average of two percentiles is not a
percentile. `latency_ms` is stored denormalised on each turn so those queries
stay cheap at scale.

**Concurrency** is genuine overlap, not a calls-started rate. Each call
contributes `+1` at its start and `−1` at its end; a running sum over that event
stream gives the number in flight at every transition, and we report the maximum
per bucket. Where a call ends at the same instant another starts, the `−1` is
applied first, so a clean handoff reads as one call and not two. A call that is
still up has no `ended_at`, so its billed duration stands in.

The bucket adapts to the campaign's span — hourly up to three days, daily beyond
— because hourly buckets over a month-long campaign produce hundreds of points
that read as noise. Daily buckets are **IST** days, matching the rollups. The
response carries the bucket it chose so the axis can label itself.

---

## Timezone

Timestamps are stored in **UTC** everywhere and displayed in **Asia/Kolkata**.

`daily_organization_rollup.day` is an **IST calendar day**, not a UTC one.
Bucketing by UTC day would split an Indian working day across two rows and make
every daily figure look wrong.

---

## Aggregation and performance

Dashboard pages are served from `daily_organization_rollup`, refreshed by a
scheduled job — they never scan `workflow_runs`. Raw tables are kept for
drill-down.

Indexes that matter:

* `workflow_runs(created_at)` and `workflow_runs(workflow_id, created_at)` —
  drill-down is always a time-range scan, either global or narrowed to one
  account. `workflow_runs` has no `organization_id` of its own; it is reached
  through `workflows`, and that join is cheap because `workflows` is small.
* `call_cost_items(workflow_run_id)` — receipt rendering.
* `call_turn_metrics(workflow_run_id)` and `(created_at, latency_ms)` —
  per-call breakdown and percentile scans.
* `daily_organization_rollup(organization_id, day)` and `(day)`.

The budget is every dashboard page under 500ms at 1M call rows. If a query
cannot meet it, add a rollup rather than an index hack.

---

## Access and audit

### Reaching the dashboard

The dashboard lives at **`/superadmin/billing`** and every route behind it
requires `is_superuser`. Nothing in the normal signup flow ever sets that flag,
so on a fresh install one account has to be promoted by hand:

```bash
set -a && source api/.env && set +a

python -m scripts.grant_superuser --list          # who has access today
python -m scripts.grant_superuser you@example.com # grant
python -m scripts.grant_superuser you@example.com --revoke
```

Sign up through the UI first — the script promotes an existing account, it does
not create one. (The demo seed also inserts a staff row, but with no password,
so it exists to own the seeded data rather than to be logged into.)

Once promoted, sign out and back in, then open `/superadmin/billing`. A
non-staff account gets a 403 from every route under it, and an unauthenticated
one a 401.


* Every dashboard route is behind a **staff role check** (`is_superuser`, via
  the existing `get_superuser` dependency). It shows cross-account financial
  data.
* Rate changes and manual credit adjustments are written to
  `billing_audit_log`: who, when, old value, new value, note.
* `credit_ledger` is **append-only**. A balance is derived from it, never
  edited. A partial unique index stops a retried completion task debiting the
  same run twice.

---

## Visual system

The dashboard uses a **liquid-glass** surface treatment, defined once in
`ui/src/app/globals.css` and consumed by every screen. There is one recipe, not
a per-page collection of card styles:

| Class | Used for |
|---|---|
| `.glass-canvas` | The page shell. Paints the tinted atmosphere the panes refract, via `::before`. |
| `.glass-panel` | Chart and table panels — translucent fill, backdrop blur, hairline ring, inset top highlight. |
| `.glass-tile` | The headline figures. Claymorphic: puffier radius, diffuse shadow, a second inset from below. |
| `.glass-nav` / `.glass-nav-active` | The floating pill tab bar and its filled blue lozenge. |

Two accents carry the whole surface: **Decibyl blue** (`--brand-blue`) and a
warm **amber** (`--brand-amber`) — the same pairing the charts already lead with
in categorical slots 1 and 2, so the chrome and the data agree rather than
introducing a third palette.

Both themes are first-class. On light the panes are white at ~62% over a blue
and amber wash; on dark they invert to a *lighter* tint of the surface colour
rather than to black, because glass is defined by what shows through it and a
black tint over a near-black background reads as a hole. Dark's wash is
deliberately weaker: at full strength the amber sat under the latency chart and
competed with the orange p95 line, and **chrome must never compete with data**.

Because the six screens use the shadcn `Card` primitive inline for their table
panels, a scoped un-layered override restates the same recipe for
`.glass-canvas .card-weave`, from the same tokens. That keeps one visual
definition of "glass" instead of two that can drift.

Motion is minimal and respects `prefers-reduced-motion`: the atmosphere drifts
slowly, and tiles lift 2px on hover. Both are disabled when reduced motion is
requested.

---

## Cost estimate (forward-looking)

Everything above prices a call *after* it happens. `POST /cost-estimate/per-minute`
answers the question asked *before*: what will a minute cost on this stack?

It prices from the same effective-dated rate rows the receipts use, so an
estimate and the invoice it predicts cannot drift apart, and it uses the
caller's own account platform rate rather than a list price.

What has to be assumed is consumption — tokens and characters per minute.
Rather than ship a constant, that is the **median of our own completed calls on
that exact model** over the last 30 days, once there are at least 20 of them.
A vendor's generic figure is a guess about someone else's traffic; the median
of ours is a statement about ours. Below that threshold it falls back to a
documented default, and every line reports which basis it used (`measured`,
`default`, or `exact` for components already quoted per minute).

The response splits into the three groups the UI bar renders — agent cost
(STT + LLM + TTS), telephony, and the platform fee — and the total is defined
as the sum of its own lines, the same reconciliation rule the receipt uses. A
component with no rate on file is reported in `unpriced` rather than priced at
zero, which would silently understate the estimate.

`CostPerMinuteBar` renders it live on the model-configuration screen, so
switching model moves the number immediately.

---

## Setting prices

`GET/PUT /admin/billing/rate-card/*` and the screen at
`/superadmin/billing/rate-card`. The only place prices are decided; everything
else in this document describes what they produce.

| Settable | Where it lands |
|---|---|
| Global platform price and pulse | `platform_volume_tiers` at threshold 0 |
| Volume tiers | `platform_volume_tiers` |
| Per-account override, either currency, own pulse | `organization_rate_history` |
| Provider unit costs, per provider and per model | `provider_rates` |
| USD→INR rate | `usd_inr_rate_history` |

Every write goes through `services/billing/rate_card.py`, which **closes the
outgoing row and opens a new one** rather than updating in place. That is what
keeps "what did this account pay in June" answerable after the price has moved.
A new row must start strictly after the one it replaces — two rows opening at
the same instant would leave the resolver choosing between them arbitrarily.

Three rules the service enforces so a form cannot produce an inconsistent card:

* **Exactly one currency per row.** Enforced in the service for a readable
  error and by a database check constraint for the guarantee.
* **The exchange rate cannot be backdated.** Repricing calls that have already
  been invoiced is not something an endpoint should be able to do.
* **The global tier cannot be retired** — only repriced. Removing it would drop
  every account silently back onto the constant.

Retiring a provider rate leaves that usage **uncosted, not free**. The cost
engine reports it and the unit-economics screen counts it; pricing it at zero
would understate provider cost and overstate margin.

---

## Unit economics

`GET /admin/billing/unit-economics` and the screen at
`/superadmin/billing/unit-economics`. Where every other page answers "how much
did we bill", this one answers the question behind the pricing decision: does a
minute make money, and where does it go when it does not.

| Figure | What it decides |
|---|---|
| Revenue, provider cost and margin **per connected minute**, in ₹ and $ | Whether $0.02 holds up |
| Cost split by component (STT / LLM / TTS / telephony) | Which component to attack |
| Cost intensity **per model** | Which model to switch off — "our LLM cost" is not actionable, "gpt-4o costs 3x mini a minute" is |
| Pulse give-away, in rupees | Whether the differentiator is still affordable |
| Thinnest margins by account | Which customer is underwater; it is rarely the biggest |
| Unpriced usage | How much of the book is knowingly incomplete |
| FX rate and its age | When to reprice |

**The pulse has a price and the screen states it.** For every costed call it
computes what a 60-second-pulse platform would have billed, prices the gap at
the rate *that call* was actually billed at, and reports the total. Calls whose
duration already landed on a whole minute are counted separately, so the
headline is not read as applying to every customer.

**Unpriced usage is reported, not hidden.** `workflow_runs.uncosted_usage`
records, per call, which measured usage we held no rate for. This was previously
only a log line, which made it the one thing capable of making every margin
figure quietly optimistic — unpriced usage is provider cost we paid and did not
record. SQL `NULL` means "costed before we tracked this" and is deliberately
distinguishable from the empty list a freshly costed call writes; "we do not
know" is not "nothing was missing".

The model league table attributes a call's minutes to every model that appeared
on it, so a mixed call counts twice. That is why those figures are labelled cost
*intensities* for comparison rather than shares of the invoice.

---

## Not built yet

* **Export**, **alerting** and **role management** — deliberately out of scope.
* **Runs are not gated on balance.** Removing the previous external billing
  service took the credit check out of `authorize_workflow_run_start`; the
  tenant-isolation checks in that function were all preserved. There is
  currently no spend ceiling. The local ledger check goes back into that same
  function when balance enforcement is turned on. See `KNOWN_ISSUES.md` #8.
* **Telephony is billed from the rate card, not from the carrier invoice.**
  Every provider implements `get_call_cost()`, which returns what the carrier
  actually charged, and nothing calls it. Telephony cost is therefore our rate
  row times measured seconds, which is close but is not the real number.
* **Payments and balance enforcement.** Nothing writes a `topup` ledger entry
  except a staff credit adjustment, and no run is refused for lack of balance.
  See `KNOWN_ISSUES.md` #8.
* **Number provisioning.** `telephony_configurations.is_platform_managed` is
  the flag the KYC gate keys on, and nothing sets it yet — buying and assigning
  numbers under our own carrier account does not exist, so the gate is correct
  but dormant.
