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

| Needed | Which regime |
|---|---|
| **Privacy notice** in clear language, itemising purposes | DPDP s5, GDPR Arts 13–14 |
| **Data Processing Agreement** with every customer — they are the Fiduciary, you are the Processor | DPDP s8(2), GDPR Art 28 |
| **Consent for recording**, captured by your customer from the person called | DPDP s6; Indian telecom rules |
| **Grievance officer**, contactable, published | DPDP s13 |
| **Breach notification process** — the log tells you what was reached; notifying is yours | DPDP s8(6), GDPR Art 33 (72h) |
| **Records of processing** (ROPA) | GDPR Art 30 |
| **DPIA** — voice plus AI is high-risk processing | GDPR Art 35 |
| **Standard Contractual Clauses** if EU data reaches a US region | GDPR Ch. V |
| **Sub-processor list**, published, with notice of changes | GDPR Art 28(2) |

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
