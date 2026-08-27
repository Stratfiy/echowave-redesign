# Data protection

Decibyl records conversations with people who never signed up for it. That is
the fact everything here follows from: the person on the other end of the call
is a **Data Principal** under India's DPDP Act 2023 and a **data subject** under
GDPR, our customer is the Fiduciary/Controller who decided to call them, and we
are the **Processor** acting on that customer's instruction.

So the controls below are built for the customer to exercise on their own data.
We do not act on a request from a stranger about an account they have not proved
they belong to — confirming that a number appears in an account would itself be
a disclosure.

**This document covers what the code does.** Compliance also needs things code
cannot supply, listed at the end. Neither half works without the other.

---

## What is built

### Storage limitation — DPDP s8(7), GDPR Art 5(1)(e)

`api/services/privacy/retention.py`, swept nightly by
`api/tasks/data_retention.py`.

Personal data stops existing once its purpose is served. That obligation cannot
be met by a policy document; only a job that deletes things meets it.

**Audio and text age separately**, and that is deliberate. A recording is a
person's voice — among the most identifying data there is, and rarely useful a
month after the call. A transcript is text: far less sensitive, and what
reporting and quality review actually read. A single window would be either too
short to run a business on or too long to defend.

| Setting | Default |
|---|---|
| `DEFAULT_RECORDING_RETENTION_DAYS` | 90 |
| `DEFAULT_TRANSCRIPT_RETENTION_DAYS` | 365 |

Per-account overrides live in `data_retention_policies` and are settable at
`PUT /api/v1/privacy/retention`. A window below one day is refused: a retention
period mistyped as `0` would delete calls as they finished, and the data would
be gone before anyone noticed.

**Objects are deleted before rows are cleared.** Deleting the row and leaving
the audio in a bucket is the failure that matters, because it looks exactly like
success — the UI shows nothing, the audit trail says purged, and the recording
is still there. If an object cannot be deleted the row keeps its pointer so the
next sweep retries, rather than orphaning audio nobody can now find.

### Erasure — DPDP s12(3), GDPR Art 17

`api/services/privacy/erasure.py`. `POST /api/v1/privacy/erasure`.

Erase one phone number across every call in an account, or an entire account's
call content at once.

**Number formatting does not defeat a request.** Numbers arrive as
`+91 98765 43210`, `09876543210`, `9876543210` — and somebody asking to be
forgotten will not know which shape theirs was stored in. Punctuation is
stripped from *both* the search term and the stored value; normalising only one
side silently matches nothing and reports a successful erasure of zero calls.

**The request records a hash, never the number.** A register of people who asked
to be forgotten would be its own personal data, and a sensitive one.

**Completed requests are kept.** The record of an erasure is not the erased
data, and destroying it would remove the only evidence the obligation was met
within the deadline both statutes impose.

### What erasure deliberately does not delete

Duration, cost and the receipt survive. The conversation goes; the arithmetic
stays.

This is not an oversight. GST records must be retained for years after the
conversation they describe should have been forgotten, and both statutes carve
out processing required by other law — GDPR Art 17(3)(b) explicitly. What
remains is "this call lasted 94 seconds and cost ₹3.20", which identifies
nobody.

### Access and portability — DPDP s11, GDPR Art 20

`api/services/privacy/export.py`. `GET /api/v1/privacy/export`.

JSON, for one phone number or a whole account. Both statutes ask the same
question in different words, so one function answers both.

Two details worth knowing:

* **Erased data is reported as erased**, not omitted. "We deleted this" and "we
  never had this" are different answers, and somebody exercising a right is
  entitled to the accurate one.
* **Sub-processors are listed per call**, read from the cost items — so the
  answer to "who was my data shared with" is the vendors that actually handled
  *that* call, not a generic list of every integration.

### Access logging — GDPR Art 33, DPDP s11(1)(c)

`api/services/privacy/access_log.py`. `GET /api/v1/privacy/access-log`.

Every signed URL issued for a recording, transcript or KYC document is recorded,
along with every export. Answers two questions that only arrive at the worst
moment: who listened to my call, and — within 72 hours of a suspected breach —
what was actually reached.

Neither can be answered retrospectively. Either the log was being written or it
was not.

It **logs the act of access, not the outcome**: a row is written when the URL is
issued, because that is when access becomes possible. Whether the browser played
the audio is not something a server can know, and recording "played" when it
means "was allowed to play" would make this a worse record than an honest one.

It also **never blocks what it observes**. A failed audit write is logged and
the request proceeds. An audit trail that can take down the product it audits is
one that gets switched off the first time it does.

### Recording disclosure — DPDP s5–6, two-party consent states

`api/services/workflow/pipecat_engine.py`, `RECORDING_DISCLOSURE_TEXT`.

The agent says the call is recorded in its first turn, before the greeting.

**On by default, and omission cannot switch it off.** Only an explicit `False`
on the start node opts out — a workflow built before the setting existed, or by
someone who never scrolled to it, still discloses. The failure of omission is
the one that ends up in front of a regulator, so it is the one made impossible.

**No separate consent record, deliberately.** The disclosure is spoken into the
call, so it is in the recording and the transcript. The artefact is the evidence
it happened, which is stronger than a flag beside it claiming so.

### Sub-processors — GDPR Art 28(2), DPDP s11(1)(c)

`api/services/privacy/subprocessors.py`. `GET /api/v1/privacy/subprocessors`.

**Derived, not maintained.** The usual answer is a hand-written page that goes
stale the first time somebody adds a provider and forgets — and a stale
sub-processor list is worse than none, because it is a specific written claim
that is now false.

Two sources answering different questions: providers this deployment holds keys
for, and providers that actually priced a call. The second is what makes the
per-account answer honest — a customer who only ever used one vendor is not told
their data went to five, and a vendor reached through the customer's *own* key
still appears, because the question is about the data, not about whose account
paid for it. Hosting and payments are declared rather than derived, because
nothing in the application code enumerates its own hosting.

**The rendering is deterministic, and that is a requirement rather than
tidiness.** Art 28(2) makes a *change* to this list something a controller has
to be told about, so an entry whose text moves on its own manufactures a
notification nobody can act on — and teaches the reader to skim the ones that
matter. A vendor serving two components has both folded into one entry, and
until 27 Aug 2026 the fold happened in whatever order PostgreSQL returned the
rows: Sarvam, which does both speech components, rendered as "Speech
recognition; speech synthesis" on one run and the reverse on the next, from
identical data. Both queries are now sorted before folding, in the pipeline's
own order — what the agent hears, thinks, says, and what carries the call —
with anything unrecognised last. `test_privacy.py` pins it by inserting the
rows in the order that used to invert the output.

### Breach scoping — GDPR Art 33

`GET /api/v1/privacy/breach-report`. What was reached between two timestamps, by
whom, across how many calls, with the affected call ids so the people to notify
under Art 34 can be worked out.

**Counts and identifiers, never content.** A breach report that itself contains
the compromised data is a second incident.

### Security controls that support both regimes

* Recordings and transcripts are reachable only by **expiring presigned URL**;
  the bucket carries no public policy.
* Provider API keys are **encrypted at rest** (Fernet) with no read path.
* Every org-scoped query filters by `organization_id` — tenant isolation is
  enforced at the query, not by convention.
* KYC identity documents live in their own bucket and never had a public policy.

---

## What is not built, and is yours

Code cannot supply these. They are the other half.

| Needed | Which regime | State |
|---|---|---|
| **Privacy notice** in clear language, itemising purposes | DPDP s5, GDPR Arts 13–14 | Factual sections drafted: `compliance/PRIVACY-NOTICE-FACTS.md`. Lawful bases and the children's-data position are decisions, not facts. |
| **Data Processing Agreement** with every customer — they are the Fiduciary, you are the Processor | DPDP s8(2), GDPR Art 28 | Annex II (technical measures) drafted: `compliance/DPA-ANNEX-II.md`. The body is a contract. |
| **Records of processing** (ROPA) | GDPR Art 30 | Drafted: `compliance/ROPA.md`. Retention periods for your own business data still need deciding. |
| **Grievance officer**, contactable, published | DPDP s13 | Wired: `GRIEVANCE_OFFICER_*`, served at `GET /privacy/subprocessors`. **Set the values** — the name and address are empty by default. |
| **Sub-processor list**, published, with notice of changes | GDPR Art 28(2) | Derived and served. The *notice period* on changes is a contractual term. |
| **Consent for recording**, captured by your customer from the person called | DPDP s6; Indian telecom rules | Disclosure is spoken on every call. Consent to *what happens next* is still your customer's to obtain. |
| **Breach notification process** — the log tells you what was reached; notifying is yours | DPDP s8(6), GDPR Art 33 (72h) | Scoping report built. Who decides, who signs, and within what hours is a runbook. |
| **DPIA** — voice plus AI is high-risk processing | GDPR Art 35 | Not started. |
| **Standard Contractual Clauses** if EU data reaches a US region | GDPR Ch. V | Not started. |

The drafts in `compliance/` carry the facts that are derivable from the code, so
a lawyer is not guessing at retention windows or inventing a sub-processor list.
Every decision is marked `[TO CONFIRM]` rather than filled with something
plausible.

Two that are easy to miss:

**You are hosting in a US region.** Personal data of EU subjects leaving the EEA
needs a transfer mechanism — SCCs plus a transfer impact assessment. An EU
region is usually the cheaper answer than the paperwork.

**Your sub-processors process the conversation, not just metadata.** OpenAI,
Deepgram and your carrier all receive the audio or its transcript. Each needs to
be in your DPA and each needs terms that permit it. Check whether your provider
accounts are on plans that exclude training on your data — most have a setting,
and the default is not always the one you want.

---

## If you serve healthcare

`AccountType.CLINIC` exists in the enum, so this is worth deciding early.

**US clinics** put PHI in scope and HIPAA applies. That needs a BAA with you
*and* with every sub-processor touching the data. Most AI vendors require an
enterprise agreement to sign one and some will not at all — verify before
selling, not after.

**Indian clinics** are DPDP plus medical records rules, which is a different and
considerably more achievable answer.

The two are not a superset of each other. Pick the market first.
