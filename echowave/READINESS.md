# Product readiness

**14 August 2026.** Written to be argued with. What was verified in the
repository is kept apart from what could not be verified from here, because a
readiness document that blurs those is worse than none.

The previous assessment found one advertised feature that could not work on
this deployment, one loop nobody had ever run end to end, and a suite with
thirty-nine failures. This pass closed everything that lives in the repository
and, in the process, found a defect nobody had reported: **every outbound call
was being handed to the carrier without its leading `+`**.

What remains is real, and none of it is code.

---

## 1. Where it stands

| | Before | Now |
|---|---|---|
| Knowledge base | **Blocked** — no local chunker | **Works** — converts and chunks in-process |
| Outbound dialling | Believed fine | **Fixed** — was dialling undialable numbers |
| Money path | Ready, unproven live | Ready; unproven live, and now says so |
| Recovery point | 24 hours, undocumented | 24 hours, reported as an open finding until decided |
| Backup blast radius | Beside the data | Mirror supported; unconfigured state reported |
| Backup decryptability | Unchecked | Checked on every readiness poll |
| `admin` role | Gates nothing | Gates secrets, mandates, tax identity, DND removal |
| Test suite | 39 failures | **3,126 passed, 4 skipped, 0 failed** |

The suite figure was confirmed twice in a row against the same database rather
than a fresh one, because "passes on an empty database" is a different claim.

---

## 2. What was wrong, and what was done

### 2.1 Knowledge base ingestion could not work — closed

Ingestion is four stages, and exactly one of them left the process:

```
upload → convert & chunk → embed → store → retrieve at call time
```

Conversion and chunking were delegated to the Model Proxy Service, defaulting
to `https://services.decibyl.ai`, with **no local fallback**. A deployment that
could not reach that host advertised a feature that returned a raw
`ConnectError` to whoever had just uploaded a policy document. Everything
downstream — the embeddings, the pgvector write, the per-organization scoping —
was already local, which is why this was one service to stand up rather than a
feature unfinished in several places.

It is now zero services to stand up. `api/services/knowledge_base/` reads PDF,
DOCX, TXT, Markdown, JSON, CSV and HTML in-process, keeps heading paths and
page numbers so a chunk knows where it came from, and packs sentences into
token-budgeted chunks without cutting mid-clause. `docling` is used when it is
installed and is not required. `KB_DOCUMENT_PROCESSOR` selects the backend and
defaults to `local`; `auto` prefers MPS and falls back locally on any failure,
while a deployment pinned to `mps` still sees its own outage.

One of the tests asserts the point directly: it fails if ingestion makes any
outbound HTTP call at all.

### 2.2 Two failures that looked like successes — closed

**Zero chunks read as `completed`.** A scanned PDF with no text layer converted
to nothing, and the document listed as ready. The customer believed the agent
had read their policy and found out otherwise during a call with a real caller
on the line. Both backends now refuse it, and the message names OCR rather than
stating the fact and stopping.

**The customer was shown our stack traces.** `processing_error` is rendered
verbatim on the files screen and held `str(exc)` — for a transport failure,
`[Errno -2] Name or service not known`, which tells the person who uploaded a
document nothing they can act on and names our internal host to anyone
watching. Failures now carry two texts: one for the log, one for the screen.

A failure the customer must fix — wrong format, no text layer, password
protected — no longer re-raises. ARQ was retrying an unreadable `.mp4` four
times to reach the same conclusion four times.

### 2.3 Outbound calls were dialled without a `+` — found and fixed

Not in the previous assessment, because the test that would have caught it was
one of the thirty-nine.

`dnd.assert_may_call` returns the number, and both call sites — the API route
and the campaign dispatcher — assign that result straight back over the number
they are about to dial. It was returning the do-not-disturb **comparison key**:
digits only, no `+`, which is correct for matching a list and is not a number
Twilio accepts (error 21211).

This one is worth dwelling on, because of how it stayed hidden. The four
failing telephony tests were failing with a 451 — the DND gate failing closed
on a lookup that could not succeed under test, since `assert_may_call` imported
its own database client rather than the one the route already had stubbed. Four
tests were red for a reason that had nothing to do with what they tested, so
nobody read past the 451 to the assertion underneath. **The cost of a standing
list of failures to ignore is not the failures. It is the one real defect
hiding behind them.**

### 2.4 The money path — what could be proven, and what could not

The payments code is the strongest part of this codebase and this is not a
criticism of it: idempotent on redelivery, signature-gated with
`hmac.compare_digest`, GST-correct, refusing a top-up outright when no webhook
secret is set, crediting the shortfall net of tax on a partial capture.

What has never happened is a live transaction. Four things fail only there, and
all four fail identically: **the customer is charged and nobody is credited.**
Three of them are now checks:

- **`razorpay_key_mode`** reads the key prefix. A test key is not a broken key,
  which is exactly the problem — orders create, checkout renders, webhooks
  verify, credit lands in the ledger, and nothing has been collected. The
  remedy names the webhook secret as per-mode, because carrying the test one
  over to live keys rejects every delivery.
- **`webhook_reachable`** posts an unsigned request to this deployment's own
  public URL and expects **400**: proof the request crossed DNS, TLS and the
  load balancer and was turned away by our signature check — Razorpay's whole
  path minus a valid signature. A 404 means the proxy does not route it. A
  **2xx is reported as worse than a failure**, because it means something is
  accepting unsigned webhooks. Opt-in via `?probe=true`; a check that opens a
  connection every time a polled endpoint is read is a check somebody switches
  off.
- **`live_round_trip_rehearsed`** is `needs_a_human` and can never read ready.

`scripts/verify_payment_round_trip.py` runs those checks, posts a correctly
signed event that the handler ignores — separating "the secret is wrong" from
"the endpoint is unreachable" — and with `--order` follows one real payment
through to its ledger row and its numbered voucher.

### 2.5 Backups: three questions "there is a recent backup" does not answer

**Recovery point.** With no WAL archiving, a failure at 17:00 loses that day's
calls, costings and top-ups. The ledger is the only record of what customers
paid, against invoices already issued, so that is money that cannot be
reconstructed. `DATABASE_PITR_ENABLED` reports the managed-Postgres answer;
`ACCEPTED_RECOVERY_POINT_HOURS` records a deliberate decision to live with the
gap. Unset, it is an open finding — an operator who has weighed this deserves a
different answer from one who has never been told.

**Decryptability.** A dump is restorable only while the secret it was written
under is still configured. Rotate `PLATFORM_CREDENTIAL_SECRET` and every
earlier backup becomes an unreadable file that still lists, is still the right
size, and still passes every "is there a recent backup" check ever written — a
fact discovered during a restore, at the one moment there is nothing to fall
back on. Each dump now carries a 200-byte canary encrypted at the same instant
under the same secret, so proving the current secret still opens it is cheap
enough to run on every readiness poll. Which is the only reason it will ever be
run.

**Blast radius.** Backups go to a prefix in the same bucket, under the same
credentials, in the same account as the recordings; ransomware and a mistaken
`aws s3 rm` both have that shape. `BACKUP_MIRROR_*` writes a byte-identical
second copy under credentials this deployment holds for nothing else. A mirror
without its own credentials is reported as *partial* rather than as protection.
A mirror failure is logged rather than raised — failing the nightly job over an
unreachable mirror leaves no backup at all.

**The rehearsal now reconciles rather than counts.** Every ledger row records
the running balance it produced, so each must equal the previous plus its own
delta. This was run against a real dump both ways: intact, it passes; with
three rows deleted from the middle, every row count still reads plausible and
only the reconciliation notices. It also times the restore and appends the
number to a log, because an RTO nobody has measured is an RTO nobody should
quote. Measured here at one second on a 264KB dump — a number whose only value
is that the measurement now happens; run it against a production-sized dump for
one you can use.

### 2.6 The `admin` role gated nothing — closed

It was in the enum, in the picker, and in the rank table, and restricted
nobody. That is worse than having two roles: an operator who assigns "admin"
believes they have withheld something and has withheld nothing.

Admin now covers the surfaces where one person's action binds the whole
account — the BYOK vault and integration credentials, the billing profile
(which decides what tax the customer is charged), the autopay mandate (a
standing authority to debit a bank account), and removing a number from the
do-not-disturb list.

Two deliberate exclusions, written into the enum docstring so the next person
inherits the reasoning rather than the outcome. **Adding** to the DND list
stays open to members: honouring a request to stop calling somebody must never
wait for an admin to be available. **Buying credit** stays open too — a member
who cannot top up when the balance runs out is a member who cannot work, and
paying us more money is not a privilege that needs protecting.

Nothing regresses on upgrade: the migration backfilled every existing member as
OWNER.

### 2.7 `MPS_API_URL` was undocumented — closed

It appeared nowhere in `DEPLOY-ENV.md`, so an install that never set it
inherited a hostname nobody had decided to depend on. It is now documented with
a table of what still calls MPS and what happens without it, and the API logs a
warning at boot when the default is inherited. A log line rather than a
refusal: the default is correct for the managed product, and refusing to boot
over it would take down the deployment it is right for.

Ingestion no longer needs it. **Recording transcription and service keys still
do, with no fallback** — either point `MPS_API_URL` at something real, or
accept that those two screens do not work on this deployment.

---

## 3. The gates before you take a customer

Only you can close these. They are outside the repository, and no amount of
code will move them.

- [ ] **A ₹1 live top-up, end to end**, with the credit landing in the ledger
      and a numbered voucher against it.
      `docker compose exec api python -m scripts.verify_payment_round_trip --order <id>`
- [ ] **Razorpay webhook registered, reachable, and subscribed** to
      `payment.captured` and `payment.failed`.
      `GET /admin/billing/readiness?probe=true` answers the reachability half.
- [ ] **Live keys, not test keys.** The one failure that looks exactly like
      success.
- [ ] **A decision recorded on the 24-hour recovery point** — managed Postgres
      with PITR, or `ACCEPTED_RECOVERY_POINT_HOURS=24` in writing.
- [ ] **`MPS_API_URL` pointed at something real, or transcription and service
      keys retired** from what you sell.
- [ ] **One restore rehearsal against production backups**, with the time
      written down.
- [ ] **Billing readiness and privacy readiness both clear** on the box itself.

---

## 4. The roadmap, in order

The ordering rule: **things that lose money invisibly, then things that lose
data, then things that lose a deal.** A visible failure gets fixed. An
invisible one accrues.

### Week 1 — before the first paying customer

1. **The ₹1 round trip.** Nothing else on this list matters if this is broken,
   and it is the only item whose failure mode is a customer charged and not
   credited. Half a day, most of it waiting for Razorpay activation.
2. **PITR, or a recorded decision not to.** An afternoon of configuration on
   managed Postgres. The alternative is defensible; leaving it undecided is
   not, because the deployment behaves identically either way until the day it
   does not.
3. **Time a restore against a production-sized dump.** Until this number
   exists, any RTO quoted in a sales conversation is invented, and quoting an
   invented RTO to an enterprise buyer is the kind of thing that surfaces
   during diligence rather than during the pitch.

### Week 2 — before the first customer who reads a contract

4. **Off-account backup mirror with object lock.** The code is in; this is a
   bucket, an IAM identity, and four environment variables. It is also the
   single most credible line in a security questionnaire, which is why it moves
   ahead of things that feel more urgent.
5. **Decide what to do about transcription and service keys.** They are the
   last two MPS dependencies without a fallback. Either stand up MPS or take
   those screens out of the product — a feature that fails on a deployment you
   sold is worse than a feature you never offered.
6. **Watch `payments_have_vouchers`.** Designed to read zero. Any other value
   is an accrued GST liability rather than a statistic, and it accrues silently.

### Weeks 3–6 — before volume

7. **The pricing decision the PRD already argues for.** Margin per minute is
   fixed at $0.020 regardless of what the customer runs, so a premium-stack
   customer consumes four times the vendor risk and working capital and pays
   exactly the same. A percentage-of-provider-cost fee with a floor fixes it,
   the rate card already stores per-account rates, and it can be piloted on new
   accounts without touching existing ones. **This is a pricing decision, not
   an engineering one** — the engine supports either today.
8. **OCR for scanned documents.** Ingestion now refuses them with a clear
   message, which is honest and is not the same as handling them. Every
   insurance and lending customer will upload a scan in their first week.
9. **A concurrency and cost ceiling rehearsal.** Per-account concurrency is
   split 5 in / 10 out with a daily spend circuit breaker. Nobody has watched
   those two interact under a real campaign, and the tender assessment assumes
   throughput that has not been observed.

### Standing

10. **Keep the suite at zero failures.** Not a hygiene item. The `+` on every
    outbound number was hidden behind four tests that were red for an unrelated
    reason, and it would still be hidden today.

---

## 5. Where this sits against the field

The PRD's positioning survives contact with this assessment, and it is worth
restating why the work above was the right work rather than the visible work.

The agent runtime is commoditised — everyone is running the same handful of
STT/LLM/TTS vendors over the same open-source pipeline, and no buyer will
choose on it. The differentiation is **everything around the call**: billing a
CFO accepts, tax documents a CA accepts, and cost visibility that survives
contact with a finance team. Against a field of US products with an India
problem — priced in dollars, billed monthly on a card, no GST invoice, no rupee
ledger, per-minute rounding — that is a real position.

Which is why every remaining gate is a *proof* obligation rather than a feature
gap:

- The 15-second pulse is a 25–50% saving against per-minute billing on a book
  of short outbound calls, and it is **provable from the invoice**. That
  argument dies the moment an invoice is wrong, so the voucher check matters
  more than any feature on the roadmap.
- Tenant isolation, effective-dated rates, audited adjustments and encrypted
  secrets are the answers to an enterprise security questionnaire. The two that
  were missing are now answerable: where the backups live, and what the
  recovery point is.
- The knowledge base is table stakes, not a differentiator — which is precisely
  why shipping it broken was expensive. Nobody wins a deal on it and everybody
  loses one to it.

**The honest summary: this is a system whose hard parts are done and whose
remaining risk is entirely in things nobody has yet run once.**

---

## 6. What could not be checked from here

Stated so the confidence above is not read as broader than it is.

- **Whether `services.decibyl.ai` is running.** Outbound network from this
  environment goes through a proxy that refuses arbitrary hosts. The code
  dependency is certain; the host's status is yours to confirm. It no longer
  blocks ingestion either way.
- **Anything about the production box** — what is deployed, which variables are
  set, whether the worker is alive. The readiness endpoints answer all three
  from the box itself.
- **Real provider behaviour** — Razorpay, the carriers, the model vendors.
  Every test here stubs them at the boundary, deliberately: a mocked payment
  proves the code, and the code was never the doubt.
- **The Twilio `+` fix against a live trunk.** The defect is unambiguous from
  the code and the carrier's documented error, and the fix is covered by tests
  in both directions — but the first live outbound call after this change is
  worth watching.
