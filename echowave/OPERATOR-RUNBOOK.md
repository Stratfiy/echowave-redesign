# Operator runbook — plans, bundles, models and mail

**Written 21 Aug 2026.** The four things somebody has to actually *do* before a
customer can buy anything, in the order they have to be done.

Every money figure in this system is **net of GST**. The customer is charged the
grossed-up amount, computed per account against their own billing profile. The
one place that rule does not hold is the Razorpay plan, and that exception is
§2 — it is also the one that loses money silently, so start there if you read
nothing else.

---

## 1. Put models on sale

Nothing can be priced or bundled until it is in the catalogue.

**Superadmin → Provider Keys.**

1. **Install the vendor key.** Component, provider, key. It is checked against
   the vendor before it is stored — a rejected key is refused rather than
   failing later on a live call. `apply_to_all_components` stores one key
   against every slot that vendor serves, which is what you want for Sarvam
   (speech at both ends) and ElevenLabs.
2. **Models we offer** appears below the key list. Pick a provider/component
   and the screen reads that vendor's own model list *with the key you just
   installed*. Where a vendor has no list endpoint it falls back to the models
   this codebase already integrates, and says so — "listed by openai" versus
   "models we know of" is the difference between a fact and our best guess.
3. **Tick what we sell.** Name each one if the vendor's id is not something a
   clinic owner would recognise ("Smart" reads better than `gpt-4.1`).
   Unticking withdraws a model from sale; it does not touch any agent already
   built.
4. The summary at the top lists anything **offered but not sellable** and why:
   no platform key, or no rate. Work it to zero.

**Superadmin → Billing → Rate card.** Set a price for each model you ticked.

> **Carriers, too.** Telnyx, Vonage, Cloudonix and Vobiz ship on a *stand-in*
> rate — the Twilio India mobile figure — because nobody has read their
> published India outbound price. Carriage is marked up and sold, so until one
> of them carries a real rate it cannot be put on the managed path: the route
> refuses it and Readiness says why. Enter the published rate here and the
> carrier becomes sellable. A customer on their own account with any of them is
> unaffected — those minutes are on their carrier's invoice, not ours.

> A model on sale with no rate does not fail. It bills the platform fee alone,
> records its provider usage as uncosted, and reports margin we did not earn.
> That is why the customer's picker omits it entirely rather than showing it
> unpriced — but *you* only see the gap on the summary above.

A rate keyed to the provider with no model prices every model on that vendor
the same. The catalogue flags those as approximate: two models showing one
number reads as a broken calculator, and on an expensive model beside a cheap
default it is a real mispricing.

**What the customer then sees:** every sellable model in its slot, with what it
costs a minute, marked up, priced through the same estimator the invoice
reconciles against.

**What a customer's own key is for:** anything we do *not* offer. Their Provider
Keys screen lists what their key unlocks, minus our catalogue — so a key that
only reaches models we already sell is reported as adding nothing.

---

## 2. Create the Razorpay plan — the amount is the gross

This is the step that goes wrong quietly.

`RAZORPAY_STARTER_PLAN_ID` pins a plan at the provider. Once pinned, **the
amount on that plan is what the bank collects**, and nothing in this codebase
can change it. A plan created at ₹2,999 — the figure on the pricing page —
collects **no GST at all**, monthly, by standing instruction, for as long as
nobody queries it.

Since 21 Aug the mandate path reads the pinned plan back and refuses to
subscribe anyone if the amount does not match. Get it right anyway.

**The arithmetic, for the starter plan:**

| | |
|---|---:|
| Call balance | ₹2,500.00 |
| One number | ₹499.00 |
| **Net** | **₹2,999.00** |
| GST at 18% | ₹539.82 |
| **Amount to create the Razorpay plan at** | **₹3,538.82** = `353882` paise |

Razorpay takes paise, so the plan's `item.amount` is `353882`.

```
Razorpay Dashboard → Subscriptions → Plans → Create plan
  Billing frequency   Monthly, every 1 month
  Plan name           Decibyl starter plan
  Amount              ₹3,538.82
```

Then set `RAZORPAY_STARTER_PLAN_ID=plan_...` and restart with
`docker compose up -d --force-recreate` — `restart` does not re-read `.env`.

**For any other plan:** net price × 1.18, rounded to the paise, and create the
provider plan at that. Put the id on the plan row (§3), not in an environment
variable — one variable cannot be right for two plans.

**Export customers.** An account outside India with an LUT on file is
zero-rated and owes the *net* figure. A pinned plan charges everyone the same
amount, so an export account on autopay is currently over-charged by the GST.
Either keep export accounts on prepaid top-ups, or create a second plan at the
net amount and put its id on a plan row reserved for them. This is not handled
automatically and the guard in §2 will refuse the mismatch rather than silently
over-collect.

---

## 3. Make a bundle

Two different things are called bundles. Both are operator-editable; neither
needs a release.

### 3a. A **plan** — what a customer pays monthly

**Superadmin → Billing → Plans → New plan.**

| Field | What it means |
|---|---|
| Code | Stable id. **Never renamed** once anyone is on it — a collection is reconciled against it months later |
| Price a month | Net of GST |
| Balance granted | Credit added when each cycle is collected |
| Numbers included | The entitlement. Number N+1 bills separately every month |
| Extra number | What number N+1 costs. Blank follows the platform rental price |
| Razorpay plan id | The provider plan, created at the **gross** — see §2 |

The editor shows the contents at list price beside your price while you type.
₹2,999 for ₹2,500 of balance and a ₹499 number is a deliberate zero-margin
bundle; the same ₹2,999 against ₹3,500 of balance is a loss, and **the server
refuses it** — granted balance is spendable at our cost the moment it lands.

Editing a plan does **not** re-price anyone already on it. Their bank holds an
instruction for a specific amount and their mandate records the plan code it
bought; the monthly grant follows that, not the screen.

**Sizing the balance.** At today's list rates a managed minute is ₹8.30–9.00, so
₹2,500 is roughly 300 minutes — parity with Agni at the same price, not an
advantage. That figure rests on an unmeasured TTS characters-per-minute
assumption which swings it between 300 and 475 minutes. Run
`scripts/pricing/measure.sql` after a week of real traffic before sizing a
second plan. See `LAUNCH-CHECKLIST.md` §3.1.

**Speech-to-speech.** Premium (OpenAI realtime) is ₹25.79/min, so ₹2,500 is 97
minutes. Selling it inside a starter plan will generate support tickets — hide
it from the plan or warn on the card.

### 3b. A **model bundle** — what a customer picks on the Models screen

**Superadmin → Billing → Bundles.** Everyday / Natural / Premium: the cards on
the Simple picker. Changing what one *runs on* is a **tier** edit
(Billing → Models), not a bundle edit — a customer's stored configuration names
a tier, so moving a vendor there reaches every agent already built rather than
only the ones created next.

**Billing → Bundles** also shows cost, price and margin per variant, from the
same estimator that quotes the customer. Check it after any tier change: moving
a tier moves the price of every bundle that names it, and finding that out from
next month's unit economics is finding out too late.

---

## 4. Mail

Five things go out today, all over plain SMTP — SES, Sendgrid, Mailgun,
Postmark and a plain Workspace account all speak it, so switching provider is
an environment change and not a deploy.

```
SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_USE_TLS
EMAIL_FROM_ADDRESS      fallback sender
EMAIL_FROM_BILLING      receipts and dunning — what a customer keeps
EMAIL_FROM_NOTIFICATIONS everything else — what they act on
UI_APP_URL              every link in every message is built from this
```

Quote `EMAIL_FROM_ADDRESS` in the shell. Unquoted, `[email` parses as an
identifier, the variable never gets set, and the API logs "SMTP is not
configured" — which reads exactly like a code fault.

| What | When | From |
|---|---|---|
| Email verification | Signup | notifications |
| Receipt voucher / tax invoice | Every top-up, **every autopay collection**, monthly invoice | billing |
| Low balance | Daily at 09:00 IST while below the threshold | billing |
| Rental unpaid / number suspended | Day 7, 15, 25, 45 of the dunning ladder | billing |
| Organisation invitation | On invite | notifications |
| Markup change code | Superadmin markup edit | notifications |

Two of those are new as of 21 Aug and both were silences rather than bugs in
the sending:

* **The autopay receipt was issued and never sent.** The route enqueued the
  email off the top-up path's field, which the collection path does not set. A
  bank taking ₹3,538.82 a month against silence is the most reliable way to be
  asked for a chargeback.
* **The dunning ladder said nothing at all**, and worse, the schedule did not
  even flag the day calls stop — the first warning was day 15, so a number went
  quiet on day 7 and the customer heard eight days later. Day 7 now warns.

**Verify mail before launch.** Run a local SMTP sink and place a real top-up:

```bash
venv/bin/python -m aiosmtpd -n -l 127.0.0.1:1025 -d > smtp.log 2>&1 &
# then run the API with SMTP_HOST=127.0.0.1 SMTP_PORT=1025 SMTP_USE_TLS=false
```

**Still missing, and worth adding before volume:** nothing welcomes a new
account, and nothing confirms a plan the moment it is authorised — the customer
authorises at their bank and hears nothing until the first collection lands.

---

## 5. The order to do all of it

1. Install provider keys, tick models, price them on the rate card (§1)
2. Set a real USD/INR rate — everything is quoted in dollars and settled in
   rupees, and an empty history bills at the ₹96 fallback, ~8% light
3. Supplier identity — **without `SUPPLIER_LEGAL_NAME` and `SUPPLIER_GSTIN` no
   tax document is ever issued**, and money is taken regardless
4. SMTP, and prove one receipt arrives (§4)
5. Razorpay live keys, webhook configured and reachable
6. Create the provider plan at the **gross** (§2)
7. Create the plan row and point it at that id (§3a)
8. One real payment end to end; confirm the voucher exists *and* arrives
9. Check Superadmin → Billing → readiness reports zero blockers
