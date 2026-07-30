# Trust page content

For decibyl.ai/trust. Written to be read by the person in a procurement process
who has to decide whether to send you their security questionnaire or their
rejection.

The governing rule: **every claim is either true and evidenced in code, or it is
absent.** A trust page whose claims collapse under one question does more damage
than no trust page, because it moves the buyer from "unknown" to "overstates
things" — and that is the assessment they carry into every later conversation.

---

## Data protection

**Recordings and transcripts are never publicly reachable.** Media is served
only through short-lived signed links; the storage buckets carry no public
policy. Identity documents uploaded for phone-number provisioning live in a
separate bucket from call media.

**Deletion is enforced by a job, not by a policy.** Recordings are deleted after
90 days and transcripts after 365 by default, and you can set both shorter for
your account. The object is deleted from storage before the database row is
cleared — the opposite order looks identical in the UI and leaves your audio
sitting in a bucket.

**Every access is recorded.** Each time a signed link is issued for a recording,
transcript or identity document, we record who asked, when, and from where. You
can read your own account's access history in the product.

**Tenant isolation is enforced at the query.** Every read and write that touches
account-scoped data filters by organisation. It is a rule the codebase enforces,
not a convention it relies on.

**Your API keys are encrypted at rest** and there is no endpoint that reads one
back — not for you, not for us.

**Card details never reach us.** Payments go through Razorpay; we hold their
identifiers and the amount.

## Privacy rights, built in

Not a support ticket — endpoints you can call:

| | |
|---|---|
| Export everything about a phone number or an account | `GET /api/v1/privacy/export` |
| Erase a phone number across every call | `POST /api/v1/privacy/erasure` |
| Set your own retention windows | `PUT /api/v1/privacy/retention` |
| See who accessed your recordings | `GET /api/v1/privacy/access-log` |
| See which vendors handled your calls | `GET /api/v1/privacy/subprocessors` |
| Scope an incident to a time window | `GET /api/v1/privacy/breach-report` |

Two design decisions worth stating outright, because they are the ones a careful
reviewer probes:

**Erasure survives phone-number formatting.** `+91 98765 43210`, `09876543210`
and `9876543210` all match. Somebody asking to be forgotten does not know which
shape their number was stored in, and normalising only the search term reports a
successful erasure of zero calls.

**Erased data is reported as erased, not omitted.** "We deleted this" and "we
never had this" are different answers, and you are entitled to the accurate one.

## Recording disclosure

Agents announce that the call is recorded, in their first sentence, before the
greeting. It is on by default: turning it off takes a deliberate action, so a
workflow built by someone who never found the setting still discloses.

Because the disclosure is spoken into the call, it is in the recording and the
transcript. The evidence that it happened is the artefact itself.

## Sub-processors

The list is **generated from the running system** — the provider credentials
configured and the vendors that actually priced your calls — rather than
maintained by hand on a page somebody forgets to update. A stale sub-processor
list is worse than none: it is a specific written claim that is now false.

Your account's own list is in the product. It shows the vendors that handled
*your* calls, not every integration the platform supports.

## What we do not claim

The honest section, and the one that earns the rest of the page.

- **No SOC 2 report.** Not audited. If you require one to buy, we cannot meet
  that requirement today.
- **No ISO 27001 certificate.**
- **No HIPAA BAA.** PHI should not be put through the platform. A BAA needs
  every sub-processor touching the data to sign one too, and several AI vendors
  will not.
- **No independent penetration test** has been performed.
- **Single region, no multi-region failover.**
- **We are not the controller of your calls.** You decided to call those people;
  we carry out that instruction. A data subject's rights run against you first,
  and our job is to give you the tools to honour them — which is what the
  endpoints above are.

## Regulatory position

- **DPDP Act 2023 (India):** the technical obligations — storage limitation,
  erasure, access, a published grievance officer — are implemented. Full
  compliance is required by **13 May 2027**; penalties commence **13 November
  2026**.
- **GDPR:** processor-side controls under Arts 5, 17, 20, 28, 30, 32 and 33 are
  implemented. [TO CONFIRM — transfers out of the EEA require SCCs and a
  transfer impact assessment before this section can say more.]

## Reporting a vulnerability

[TO CONFIRM — the mailbox, the response commitment, and whether you offer safe
harbour for good-faith research. A trust page without a route to report a
problem tells a researcher to post it publicly instead.]

---

*Every claim above is derived from the codebase. `compliance/DPA-ANNEX-II.md`
gives the file-level detail behind each one, for reviewers who want it.*
