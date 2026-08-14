# Running Decibyl

The operator's manual. Not how to build a voice agent — that is the product
documentation. This is what the person who owns the platform needs to know:
who can do what, where each lever is, and what to do when something is wrong.

Three companion documents, each doing a job this one deliberately does not:

- **`DEPLOY-ENV.md`** — every environment variable, plus the two runbooks that
  need exact commands (rotating the encryption secret; proving the backups
  restore).
- **`docs/account/roles-and-permissions.mdx`** — the customer-facing version of
  the hierarchy below.
- **`README.md`** — what the product is.

---

## 1. The hierarchy

Four relationships, and keeping them distinct is what stops "admin" meaning
four different things in one conversation.

```
Decibyl staff  ──────────────►  every account          (superadmin / support)
      │
      │ sets the link and the rate
      ▼
  Agency ─────── manages ──────►  Client account       (roll-up only, never inside)
                                        │
                                        ├── Owner      (runs the account, manages people)
                                        ├── Admin      (reserved — see below)
                                        └── Member     (full use of the product)
```

### Inside one account

Every membership carries exactly one role, and checks ask *at least this role*,
so an Owner passes anything an Admin would.

| Role | What it can do |
|---|---|
| **Member** | Everything the product does: build agents, run campaigns, place calls, read usage, manage API keys. |
| **Admin** | Reserved. Ranked between the two, gates nothing today. |
| **Owner** | All of the above, plus inviting, promoting, demoting and removing members. |

**Admin gates nothing yet.** It is safe to assign and it is in the ranking, so
permissions can be split later without a migration — but today an Admin has a
Member's access. Assign it if it matches how you think about your team; do not
assign it expecting it to restrict anything.

**Owner-only is exactly one area: membership.** Adding a member grants standing
access to every workflow, recording and phone number the account holds, which
is not a decision a Member should be able to make. Everything else — agents,
campaigns, telephony, keys, usage — is open to any member.

**An account can never be left without an Owner.** Demoting or removing the
last one is refused. To hand over an account: promote the new Owner first, then
step down.

### Decibyl staff

Set by us, independent of any membership. Almost everybody has no staff role at
all.

| Role | Reach |
|---|---|
| **Support** | KYC document review across accounts. Read-and-review, nothing further. |
| **Superadmin** | Everything Support can do, plus billing, the platform key vault, per-account limits, refunds, and acting as an account. |

The split is about consequence, not seniority. Reviewing a KYC document is
cross-account but bounded. Changing a rate, reading a platform key, or signing
in as a customer can move money or expose secrets.

Grant with `python -m scripts.grant_superuser`.

### Agencies

Not a role — a link between two accounts, with a commission rate attached.
Staff set both, at `/superadmin/agency`.

An agency sees which accounts it manages, what they were charged, and what it
earned. It **cannot** open a client's account, edit their agents, or read their
recordings and transcripts. Those are conversations with the client's own
customers, and managing an account is not consent to listen to them.

The rate is 0–50% and set by us. A rate the earner can edit is not a rate.
Detaching a client keeps past accruals: the agency did earn them.

---

## 2. Where each lever lives

The single most common question, and the answer is almost never "an environment
variable". Anything that differs between customers is set per account, from the
dashboard, and takes effect without a deploy.

| Lever | Where | Notes |
|---|---|---|
| Provider API keys (ours) | `/superadmin/provider-keys` | Write-only. One key covers every slot a vendor serves. |
| Per-unit rates, platform fee | `/superadmin/billing/rate-card` | Effective-dated — changing one never rewrites a past call. |
| One account's price | `/superadmin/billing/accounts/<id>` | Overrides the global rate, in ₹ or $. |
| Credit adjustments | same page | Audited; a note is required. |
| Concurrency, daily spend ceiling | same page, **Limits** | Empty box = platform default. |
| Refunds | `/superadmin/billing/payments` | Full or partial; refuses to refund spent credit. |
| Agency link and commission | `/superadmin/agency` | |
| Managed markup | `/superadmin/billing/rate-card` | Requires a code emailed to you — it moves every managed call's price at once. |
| Encryption secret rotation | `/superadmin/provider-keys` | And `DEPLOY-ENV.md`. |
| SMTP, storage, database | environment | Properties of the deployment, not of a customer. |

### The defaults worth knowing

| | Default | Set per account? |
|---|---|---|
| Inbound calls at once | **5** | yes |
| Outbound calls at once | **10** | yes |
| Daily spend ceiling | **₹50,000** | yes (0 disables) |
| Referral share | 20% of the referred account's first payment | no |
| Referral hold | 7 days after the payment settles | no |
| Agency commission | 0–50%, no default | yes |
| Signup bonus | $5 of credit | env |

Inbound is the lower of the two ceilings on purpose. An outbound call that
cannot get a slot dials a moment later and nobody notices; an inbound one is a
person already holding a ringing phone, and the only thing to do with them is
refuse. The gap reserves capacity a campaign in full flight cannot eat.

The spend ceiling is a circuit breaker, not a price cap. The default is
deliberately generous, because one that trips on an ordinary busy day is one
somebody switches off — and an account with it switched off is the account it
exists to protect.

---

## 3. How money moves

Worth reading once, because most support questions are really questions about
this sequence.

1. **A customer tops up.** Razorpay settles; a `topup` row lands in the credit
   ledger. The ledger is **GST-exclusive** — the tax is on the payment, never
   in the ledger.
2. **A call runs.** Credit is reserved up front, then costed against the rate
   card when the call ends. `cost_paise` is what the customer is charged;
   `provider_cost_paise` is what it cost us. The gap is the margin.
3. **The balance crosses zero.** The live call is **never cut off** — the person
   on the phone is the customer's customer. The balance may go slightly
   negative, bounded by roughly one call, the account is emailed immediately,
   and the next run is refused.
4. **Monthly.** Tax invoices are issued, agency commission accrues, matured
   referral awards are credited.

A refund reverses both halves: gross back to the customer, net out of the
ledger. It refuses if the credit has already been spent — refunding then would
hand back the money *and* keep the service, and leave a negative balance every
downstream check misreads.

---

## 4. When something is wrong

### "Calls are failing"

Check in this order — cheapest first, and each rules out the one below.

1. **`/superadmin` → billing readiness.** Says plainly what would silently cost
   money or break compliance.
2. **The account's balance.** A refused run at zero credit is the system working.
3. **Agent Runs → failure reason.** A first-class field now, not a guess from a
   log line. `insufficient_credit`, `concurrency_limit`, `no_platform_key`,
   `telephony_error`, `dnd_listed`, `outside_calling_hours` and the rest each
   point somewhere different — and several of them are the system working
   correctly rather than a fault.
4. **`/superadmin/provider-keys`.** A paused or missing key fails at the vendor
   with their own 401, which is nowhere obvious.
5. **The account's limits.** A concurrency ceiling somebody lowered looks
   identical to a carrier problem from the customer's side.

### "The customer says they were overcharged"

The account detail page carries the credit ledger and the rate history side by
side. Rates are effective-dated, so the rate that applied to a call in March is
still on the page in August. If the charge is wrong, refund part of it — partial
refunds exist because a dispute is usually about part of a purchase.

### "A key leaked"

1. Revoke it at the vendor first. Everything else is secondary.
2. Paste the replacement at `/superadmin/provider-keys`. Rotation replaces in
   place; there is no history to clean up, deliberately.
3. If it was the *encryption* secret rather than a vendor key, follow
   *Rotating the encryption secret* in `DEPLOY-ENV.md`. Do not shortcut it — a
   single-step change breaks provider keys, every customer's own key, MFA,
   calendar grants and the backups at once, and breaks them silently.

### "Are the backups real?"

`/privacy/readiness` answers two separate questions and you want both:
*a backup exists* (the newest object's age) and *a backup restores* (the last
rehearsal). The second is the one that matters. Run it on demand with:

```bash
python -m scripts.rehearse_restore
```

It restores into a scratch database, checks the credit ledger came back and its
running balance reconciles, and drops the scratch copy. Nothing touches live.
It runs itself on the 4th of each month.

---

## 5. Things that are true and non-obvious

Collected because each one has cost somebody an afternoon.

- **One pasted key covers every slot a vendor serves.** Sarvam does speech,
  language and voice on one account. Entering it once per component is three
  saves to say one thing, and it is the third that gets forgotten. Google is the
  exception that proves it: one service account covers both speech slots, but
  Gemini is a separate credential.
- **Rates are effective-dated everywhere.** Rate card, account overrides,
  managed markup, agency commission. Nothing is ever updated in place, so a
  historical invoice always reproduces.
- **A paid agency month is closed.** Re-running the accrual will not rewrite it.
- **A referral award is held for 7 days.** Refund the payment inside that window
  and the award goes with it, silently, because nothing had been credited yet.
  After it matures it is somebody's spendable balance and reversing it is a
  human decision.
- **Staff cannot read a stored key back.** Only the last four characters. A
  reveal button would turn one compromised staff session into every provider key
  on the platform.
- **An empty limit box is not zero.** Empty means "follow the platform default,
  and move when it moves". Typing today's default in pins the account there
  forever.
- **`certs/` is never committed.** Nor is any `.env`.

---

## 6. First hour on a new deployment

1. Set `PLATFORM_CREDENTIAL_SECRET`, the database and Redis URLs, and SMTP.
   See `DEPLOY-ENV.md`.
2. Run migrations — the deploy does this, but check `alembic upgrade head` was
   clean.
3. Grant yourself superadmin: `python -m scripts.grant_superuser you@example.com`
   (superadmin is the default; `--list` shows who already has it).
4. Paste the provider keys at `/superadmin/provider-keys`.
5. Set the rate card at `/superadmin/billing/rate-card`. Until this exists the
   margin figures read wrong rather than failing, which is worse.
6. Open `/superadmin` and clear everything the readiness checks flag.
7. Wait for the first nightly backup, then run
   `python -m scripts.rehearse_restore` once. Until that passes, the backups
   are a hypothesis.
