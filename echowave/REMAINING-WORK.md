# What is left, and the order to do it

**Written 21 Aug 2026**, after the pricing, plans and catalogue work on
`claude/decibyl-launch-checklist-nxqump`. This is the to-do list that survives
that branch: everything I found and did not fix, plus everything the branch
raised that needs a decision rather than code.

`LAUNCH-CHECKLIST.md` is the audit — what was wrong and what happened to it.
`OPERATOR-RUNBOOK.md` is the how-to. **This is the queue.**

Nothing here is a mystery. Every item names the specific failure, so any of it
can be picked up without re-deriving why it matters.

---

## A. Before the first customer — configuration, not code

These are terminal commands and dashboard clicks. None needs a release, and
none of them can be done from here.

| # | What | Why it matters if skipped |
|---|---|---|
| A1 | Set `SUPPLIER_LEGAL_NAME` and `SUPPLIER_GSTIN` | Money is captured and credited and **no tax document is ever issued**. Under GST an advance is taxable on receipt, so every payment is an accruing liability with a log line as its only trace |
| A2 | Put a real USD/INR rate on file | Everything is quoted in dollars and settled in rupees. An empty history bills at the ₹96 fallback — roughly 8% light against a real ₹104, on every charge |
| A3 | Razorpay live keys + `RAZORPAY_WEBHOOK_SECRET` | Test keys work perfectly, produce orders, fire webhooks and take no money |
| A4 | Create the Razorpay plan at **₹3,538.82**, not ₹2,999 | A plan pinned at the net figure collects no GST at all, monthly, by standing instruction. The code now refuses the mismatch — it does not create the plan for you. `OPERATOR-RUNBOOK.md` §2 |
| A5 | SMTP, and prove one receipt arrives | Every document below is issued whether or not it is delivered |
| A6 | Seed the price book, then fix the rates that ship wrong (B1) | An empty price book reports 100% margin rather than an error |
| A7 | Work **Superadmin → Billing → Readiness** to zero blockers | It is the only screen that checks all of the above at once. New on this branch |

---

## B. Money still wrong, in code

### B1. Four carriers have no verified India rate — *made safe, not yet fixed*

`default_rates.TELEPHONY_RATES`. Plivo and Twilio carry real India figures.
Telnyx (`$0.0070`) and Vonage (`$0.0139`) carried their **US outbound** rates
against traffic that is Indian; Cloudonix and Vobiz carried stand-ins at the
Twilio India mobile rate.

Carriage is marked up like everything else, so there is no safe direction: a
stand-in that is too high overcharges the customer, one that is too low sells
the minute at a loss, and **the invoice reads identically either way**.

**Done on 21 Aug.** Telnyx and Vonage are off the US rates and on the same
India stand-in as the other two. All four are flagged `provisional`, which is
carried into the seeded row's note, and `billing/carrier_rates.py` refuses to
put a configuration on the managed path while its carrier's rate still carries
the marker. Superadmin → Billing → Readiness reports them: `ready` while
nothing is sold on them, `action_required` the moment a platform-managed
configuration is (which catches anything marked managed before the guard).
Nothing changes for a customer on their own account with these carriers — we
bill them no carriage at all.

**Still to do, and it needs a person, not a release:** read each carrier's
published India outbound rate off their pricing page and enter it at
`/superadmin/billing/rate-card`. That supersedes the stand-in, clears the
marker and makes the carrier sellable. Neither Telnyx nor Vonage publishes an
India figure this repository can cite, which is why the code refuses rather
than guessing.

### ~~B2. Export accounts on autopay are over-charged by the GST~~ — decided and done, 21 Aug

**Decision: a second plan at the net amount.** `subscription_plans` rows carry
`razorpay_plan_id_export` alongside the domestic id, and `create_plan_mandate`
picks it whenever the supply resolves to an export. The two cannot point at one
provider plan — a single pinned plan cannot collect two amounts, and `save`
refuses that rather than letting the guard pass or fail depending on who
subscribes.

Null until an operator creates it, and until then an export account is refused
with the net figure to create the plan at. That is still better than the
alternative: subscribing them to the domestic plan overcharges by the tax,
monthly, for as long as the mandate lives.

**Operator step:** create the second Razorpay plan at the **net** price and put
its id in the export field at `/superadmin/billing/plans`. Only needed once you
actually have an export customer wanting autopay.

### ~~B3. The wizard's price excludes carriage~~ — done 21 Aug

`GET /agent-options` called `price_per_minute` with no `telephony_provider`, so
the create wizard showed ₹7.46/min where the call would cost ₹8.30 — about 10%
light, systematically, on the one screen a first-time buyer reads before paying.

The Models screen had the mirror of it: it priced whatever the default outbound
configuration named, ours or not, so an account dialling on its **own** Twilio
was quoted a carriage line on top of the invoice Twilio already sends it.

Both now ask `carriage.billable_carrier`, which answers the only question that
decides it — *whose invoice do these minutes land on* — in three states, each
with its own sentence on the screen: no number connected (price will grow),
their own carrier (we bill no carriage at all), ours (included, carrier named).
`GET /agent-options/carriage` is the endpoint; `/agent-options/minutes` uses it
too, since a missing carrier line there reads as *more minutes* than the
balance buys.

### ~~B4. Granted plan balance never expires~~ — decided and done, 21 Aug

**Decision: it expires at the end of each cycle.** Two paths, because an
account that keeps paying and one that stops need different machinery:

* the arriving collection retires the cycle before it (`grant_plan_cycle`);
* a daily sweep retires a cycle nobody renewed, with `CYCLE_GRACE_DAYS = 34` of
  grace so a collection that lands late never expires balance the customer is
  about to renew.

Both key the expiry on the grant it retires, so they can race on a late renewal
and only one debit lands. **A top-up is never touched** — the customer bought
that outright — and plan money is spent first, which is the ordering that makes
that true rather than merely intended. The expiry cannot drive a balance
negative: suspending calls over a date rather than over a spend would read as a
fault.

Stated at the point of purchase, which is the only place stating it counts: on
the plan card, and in the authorisation email.

---

## C. Built and unreachable

Backend complete, no way to use it. Each is a screen.

> **C1 is done.** The customer Models screen reads the priced catalogue
> (`GET /agent-options/catalogue`) instead of the provider registry's
> `examples`. Every managed model is listed with what it costs a minute; a
> vendor we do not sell appears only when the account holds its own key for it,
> and the models inside it are what that key actually reaches. Choosing a
> managed model now also sets `use_platform_key`, which is the half that used
> to be missing: the Advanced screen could show a vendor we hold the key for
> and still save the slot as BYOK, so it resolved no credential at dial time.

| # | What | Consequence today |
|---|---|---|
| C2 | Managed telephony admin (4 routes) | `MANAGED_TELEPHONY_ENABLED` cannot be operated: no way to mark a config platform-managed or manage shared outbound |
| C3 | `POST /managed-numbers/{id}/release` | A number can be bought and not given back |
| C4 | Partner commissions and statement issuing (3 routes) | Commissions cannot be set; statements cannot be issued |
| C5 | MFA enrol/verify/disable | Implemented, and nobody can turn it on |
| C6 | `/privacy/readiness`, `/metrics`, `/breach-report` | DPDP reporting has no screen. Relevant for a healthcare buyer |
| C7 | `POST /knowledge-base/search` | No way to test what the agent will actually retrieve |
| C8 | Model configuration v2 migration preview + migrate | The migration path exists and cannot be run |

---

## ~~D. Two emails that should exist~~ — done 21 Aug

Both were silences at moments a customer is paying attention.

* **D1 — nothing welcomed a new account.** Signup sent a verification code and
  then nothing. `services/auth/welcome_email.py`, sent from
  `provision_new_account` so both front doors produce it, addressed to the
  person who signed up and deduplicated per account for ever.
* **D2 — nothing confirmed a plan when it was authorised.**
  `services/billing/plan_email.py`, sent from the Razorpay webhook on the
  *transition* into an authorised state — Razorpay sends `authenticated` then
  `activated` and retries both, so "is authorised" is true four times and "just
  became authorised" once. It quotes the **gross**, because that is what the
  bank is authorised for and quoting the net would set up an argument with the
  first statement.

The claim-then-send that both use is now one function,
`services/messaging/announce.py`, rather than a third hand-written copy. The
low-balance job and the dunning notice still carry their own copies; folding
them in is tidying, not a fix.

---

## E. Numbers that are still guesses

### E1. TTS characters per minute — the largest single unknown

Three internal documents said 850, 900 and 2,300. The code ships 2,300. On the
Lite managed stack that constant alone decides:

| chars/min | Sell | What ₹2,500 of plan balance buys |
|---:|---:|---:|
| 850 | ₹5.26 | 475 min |
| 900 | ₹5.36 | 466 min |
| **2,300 (shipped)** | **₹8.30** | **301 min** |

TTS is 34–58% of the sell price and it is the one line that is assumed rather
than measured. `/superadmin/billing/pricing-inputs` exists to answer this and
has no data until real calls flow.

**Do after a week of traffic**, before quoting anyone a volume price or sizing a
second plan: `psql "$DATABASE_URL" -f scripts/pricing/measure.sql`.

### E2. Nobody has reconciled a provider invoice

Every cost figure on every margin screen is a published list price that was
read, not an invoice that was checked. Until one month is reconciled against
`provider_cost_paise`, the cost side is an assumption — and so is every margin
number derived from it.

### ~~E3. Premium speech-to-speech inside a starter plan~~ — decided and done, 21 Aug

**Decision: show it, with the minutes on the card.** ₹25.79/min reads as "a bit
more than ₹8.30" right up until it is stated as 97 minutes against 301. The
Simple picker now puts an approximate minutes figure beside every price —
bundle card, variant row and the summary line — computed from the account's own
balance through the same rule the server uses. Null wherever the price is null,
because quoting minutes at a price missing its largest line multiplies the
error rather than surfacing it.

---

## F. Engineering hygiene

> **F1 is done.** `.github/workflows/ui-tests.yml` runs `tsc --noEmit`, the
> vitest suite and `next build` on any PR touching `echowave/ui/**`, as three
> separately-named steps — a type error reported as "build failed" sends
> whoever reads it to the wrong file. Split from the drift check because
> `next build` writes `.next/` and that job compares `git diff --exit-code ui`.

| # | What | Why |
|---|---|---|
| F2 | `test_privacy.py::TestSubprocessorsAreDerived::test_one_vendor_serving_two_components_is_one_entry` appears and vanishes in full runs | **The earlier diagnosis here was wrong.** It is not `managed_tiers._OVERRIDES` — `subprocessors.in_use` never touches it — and it is not test ordering: `pytest-randomly` is not installed, so collection order is stable, and the test failed on one full run and passed on the next three with the same order. That leaves state surviving a test transaction, or the `IN_USE_WINDOW_DAYS` boundary. Next step is to catch the assertion text: it has not yet been seen, so nobody knows *which* half of the assertion fails. `pytest tests/ -xvs` means this can stop a CI run |
| F3 | The suite needs an undocumented setup | 10 failures and 9 errors on a clean checkout, all environmental: `ts_validator` npm deps, `moto[s3,server]`, and Python 3.11 against the pinned ≥3.13. CI is green because CI installs all of it. `KNOWN_ISSUES.md` §"Running the test suite" has the list |
| F4 | The Emergent scaffold is still at the repo root | `backend/server.py` (a MongoDB hello-world), `frontend/` (a CRA stub), `memory/`, `tests/`, `test_result.md`. None of it is Decibyl, and it is the first thing anyone new opens |
| ~~F5~~ | ~~Five dead workflows in `echowave/.github/workflows/`~~ | **Removed 21 Aug.** GitHub only reads `<repo-root>/.github/workflows`, so none of these — release automation, the docker image build, PR conventional-commit labeling, a Vercel deploy, Slack announcements — had ever run. The Vercel deploy in particular was pointed at the wrong target: this repo's production deploy is EC2 via SSM (`.github/workflows/deploy.yml`), not Vercel — reviving it as-is would have deployed nowhere real. Decided not to revive any of the five for now; `release-please-config.json` and `.release-please-manifest.json` stay, since `KNOWN_ISSUES.md` still cross-references the version string in the manifest independent of the automation |

---

## G. Compliance and claims

* **G1 — the "Stays in India" badge is a data-residency claim.** It is computed
  from an allow-list of one vendor (`sarvam`) and shown to clinics. Have the
  Sarvam DPA and processing-location confirmation on file before it goes in
  front of a doctor.
* **G2 — `docs/` still describes a flat $0.02/min.** Correct while the Phase 2
  flags are off. The moment `BYOK_TIERED_FEE_ENABLED` flips, the fee is tiered
  on *which* component the customer brought — $0.020 / $0.022 / $0.035 — and
  both the docs and every quote pasted from them are wrong.
  `PRICING-FIX-PLAN.md` Phase 5 has the table to publish.

---

## The order I would work it

1. **A1–A7.** Configuration. Nothing below matters if money cannot be taken
   correctly, and the readiness screen now tells you when it can.
2. ~~**C1.** The customer Models screen.~~ **Done** — see the note in §C.
3. ~~**D1, D2.**~~ Done. **B1** now needs only the four published carrier rates
   typing into the rate card; **B3** is done.
4. ~~**Decide B2, B4, E3.**~~ Decided 21 Aug and implemented: a second
   Razorpay plan at the net price for exports, plan balance expires with its
   cycle, and Premium is shown with what your balance buys.
5. ~~**F1.**~~ Done — `tsc`, vitest and `next build` now run on every UI PR.
6. **E1** after a week of real traffic, then re-size the plan balance.
7. **C2–C8** as each feature is actually turned on. None is urgent while its
   flag is off.
