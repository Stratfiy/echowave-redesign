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

### B1. Four carriers are priced at US rates or at nothing real

`default_rates.TELEPHONY_RATES`. Plivo and Twilio carry real India figures.
Telnyx (`$0.0070`) and Vonage (`$0.0139`) are labelled "US outbound". Cloudonix
and Vobiz say, in the file, `PLACEHOLDER at the Twilio India mobile rate. Not
the published price.`

Carriage is now marked up like everything else, so an under-priced base rate
compounds into the sell price rather than merely eating margin.

**Fix:** real India rates for all four, or remove them from what a customer can
select. Half a day, mostly reading price lists.

### B2. Export accounts on autopay are over-charged by the GST

A pinned Razorpay plan charges everyone the same amount. An account outside
India with an LUT on file is zero-rated and owes the **net** figure, so it is
being asked for ₹3,538.82 against a real ₹2,999.

The guard added on this branch refuses the mismatch rather than silently
over-collecting, which means such an account currently *cannot subscribe* — a
visible failure instead of an invisible one, which is the right direction but
is not a fix.

**Fix, pick one:** keep export accounts on prepaid top-ups (zero code, state it
in the docs), or give the plan row a second provider plan id used when the
billing profile is an export. The plans table already has somewhere to put it.

### B3. The wizard's price excludes carriage

`GET /agent-options` calls `price_per_minute` with no `telephony_provider`, so
the create wizard shows ₹7.46/min where the call will cost ₹8.30 — about 10%
light, systematically.

Defensible (no carrier is chosen yet) and currently unstated, which is the
problem. **Fix:** pass the account's default carrier, or label the figure
"excludes telephony". An hour.

### B4. Granted plan balance never expires

`CreditLedgerKind.PLAN` is an ordinary credit row. `PRICING-FIX-PLAN.md` flagged
expiry-versus-rollover as a decision to take explicitly; it has been taken by
default, in favour of rollover for ever. An account that pays ₹2,999 a month
and never calls accrues ₹2,500 a month indefinitely.

**This is a commercial decision, not a bug.** Expiry is standard and is what
makes a bundle better than a top-up; rollover is friendlier and much harder to
reason about on an invoice. Whichever you choose, it has to be stated at the
point of purchase. Half a day once decided.

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

## D. Two emails that should exist

Both are silences at moments a customer is paying attention.

* **D1 — nothing welcomes a new account.** Signup sends a verification email and
  then nothing.
* **D2 — nothing confirms a plan when it is authorised.** The customer
  authorises at their bank and hears nothing until the first collection lands,
  which on a monthly cycle can be weeks.

The sending machinery, the dedupe table and the sender addresses all exist. Each
is a compose function and a call site. A day for both.

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

### E3. Premium speech-to-speech inside a starter plan

₹25.79/min means ₹2,500 is **97 minutes**. Hide it from the plan, or warn on the
card. A commercial call, not a bug.

---

## F. Engineering hygiene

| # | What | Why |
|---|---|---|
| F1 | **CI never runs `tsc`, `next build` or the 68 vitest tests** | Only lint drift is checked. A type error or broken build reaches `main` and is caught on the deploy box, where `ci_deploy.sh` rolls back — a rollback that could have been a red check. All three are clean today; nothing keeps them clean |
| F2 | `test_privacy.py::TestSubprocessorsAreDerived` flakes on ordering | Passes in isolation and in every targeted combination; appears and vanishes in full runs. Almost certainly `managed_tiers._OVERRIDES` — a module-level cache mutated by another test file. Worth an autouse fixture that restores it |
| F3 | The suite needs an undocumented setup | 10 failures and 9 errors on a clean checkout, all environmental: `ts_validator` npm deps, `moto[s3,server]`, and Python 3.11 against the pinned ≥3.13. CI is green because CI installs all of it. `KNOWN_ISSUES.md` §"Running the test suite" has the list |
| F4 | The Emergent scaffold is still at the repo root | `backend/server.py` (a MongoDB hello-world), `frontend/` (a CRA stub), `memory/`, `tests/`, `test_result.md`. None of it is Decibyl, and it is the first thing anyone new opens |
| F5 | Five dead workflows in `echowave/.github/workflows/` | GitHub only reads `<repo-root>/.github/workflows`, so release automation, the docker image build and the Slack announcements have never run |

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
3. **B1, B3, D1, D2.** A week's worth: real carrier rates, an honest wizard
   price, and the two emails.
4. **Decide B2, B4, E3.** Three commercial calls that need you, not code.
5. **F1.** Put `tsc`, `next build` and vitest in CI before the codebase grows
   further without them.
6. **E1** after a week of real traffic, then re-size the plan balance.
7. **C2–C8** as each feature is actually turned on. None is urgent while its
   flag is off.
