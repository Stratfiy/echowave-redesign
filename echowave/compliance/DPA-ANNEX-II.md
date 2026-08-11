# Annex II — Technical and organisational measures

The annex every enterprise customer's Data Processing Agreement attaches, and
the one most often filled with aspirations. GDPR Art 32 asks what measures are
*implemented*, so each line below says where it is implemented and, where a
measure is absent, says that instead.

A measure that is written here and not built is a contractual warranty that is
false from signature.

**Status key:** ✅ implemented · ⚠️ implemented with a stated limit · ❌ not
implemented

---

## 1. Pseudonymisation and encryption — Art 32(1)(a)

| Measure | Status | Where |
|---|---|---|
| Recordings and transcripts reachable only by expiring presigned URL; no public bucket policy | ✅ | `api/services/filesystem/` |
| Presigned URL lifetime | ✅ | 1 hour for reads, 15 minutes for uploads (`base.py` defaults) |
| Provider API keys encrypted at rest (Fernet), no read path through any endpoint | ✅ | `platform_provider_credentials`, keyed by `PLATFORM_CREDENTIAL_SECRET` |
| Account passwords stored as bcrypt hashes | ✅ | `api/utils/auth.py` |
| API keys stored as hashes, never recoverable | ✅ | `api_keys.key_hash` |
| Erasure requests store a SHA-256 hash of the phone number, never the number | ✅ | `api/services/privacy/erasure.py` |
| Encryption in transit (TLS) to the application and to every provider | ✅ | Deployment-level; providers are HTTPS-only |
| Encryption at rest for the database and object store | ⚠️ | Provided by the hosting layer (EBS/RDS/S3 default encryption), not by the application. [TO CONFIRM — enabled on your volumes and buckets] |
| Application-layer encryption of recording audio | ❌ | Not implemented. Objects rely on bucket-level encryption. |

## 2. Confidentiality, integrity, availability and resilience — Art 32(1)(b)

| Measure | Status | Where |
|---|---|---|
| Tenant isolation enforced at the query, not by convention — every org-scoped read and write filters by `organization_id` | ✅ | Repo-wide requirement, `api/AGENTS.md`; foreign keys pointing at other org-scoped rows are validated on write, because an FK proves existence, not ownership |
| KYC identity documents in a separate bucket from call media | ✅ | `KYC_BUCKET` |
| Access to recordings, transcripts, KYC documents and exports recorded | ✅ | `data_access_log` — the act of access, not the outcome |
| Audit logging cannot block the operation it observes | ✅ | By design: a failed audit write is logged loudly and the request proceeds |
| Billing changes recorded with actor | ✅ | `billing_audit_log` |
| Role separation between customer users and platform staff | ✅ | `StaffRole` (support/superadmin) separates platform staff from customer accounts entirely; `OrganizationRole` (member/admin/owner) separates standing within a customer account. |
| Backups | ⚠️ | [TO CONFIRM — schedule, retention, and the last time a restore was actually tested. An untested backup is a hypothesis.] |
| Documented disaster recovery with RTO/RPO | ❌ | [TO CONFIRM] |
| Multi-region redundancy | ❌ | Single region. |

## 3. Restoring availability after an incident — Art 32(1)(c)

| Measure | Status |
|---|---|
| Error monitoring and alerting (Sentry) | ✅ |
| Breach-window report: what was reached between two timestamps, by whom, over how many calls | ✅ `GET /api/v1/privacy/breach-report` — counts and identifiers only, never content, because a breach report containing the compromised data is a second incident |
| Documented incident response runbook with named roles | ❌ [TO CONFIRM] |
| Tested restore procedure | ❌ [TO CONFIRM] |

## 4. Testing and evaluating effectiveness — Art 32(1)(d)

| Measure | Status |
|---|---|
| Automated test suite covering the privacy controls themselves — retention, erasure, export, access logging, recording disclosure | ✅ `api/tests/test_privacy.py`, `api/tests/test_recording_disclosure.py` |
| Retention enforcement verified by test, including that storage objects are deleted and not merely dereferenced | ✅ The failure mode that matters: clearing the row and leaving the audio looks exactly like success |
| Dependency vulnerability scanning | ⚠️ [TO CONFIRM] |
| Independent penetration test | ❌ Not performed |
| SOC 2 / ISO 27001 | ❌ Not held |

## 5. Data minimisation and storage limitation — Art 5(1)(c), 5(1)(e)

| Measure | Status | Detail |
|---|---|---|
| Automated deletion past the retention window | ✅ | Nightly at 19:00 UTC; objects deleted before rows are cleared; a failed object deletion leaves the pointer intact so the next sweep retries rather than orphaning audio nobody can now find |
| Separate windows for audio and text | ✅ | 90 / 365 days by default, configurable per account |
| Zero-day retention refused | ✅ | Minimum 1 day — a window mistyped as `0` would delete calls as they finished |
| Customer-controlled retention | ✅ | `PUT /api/v1/privacy/retention` |
| Billing data survives erasure | ✅ | Duration and cost identify nobody; GST retention is a legal obligation under Art 17(3)(b) |

## 6. Data subject rights — Arts 15–20, DPDP s11–12

| Right | Status | Endpoint |
|---|---|---|
| Access / portability | ✅ JSON export, per number or per account | `GET /api/v1/privacy/export` |
| Erasure | ✅ Per number or per account | `POST /api/v1/privacy/erasure` |
| Erased data reported as erased, not silently omitted | ✅ "We deleted this" and "we never had this" are different answers | |
| Know the recipients | ✅ Derived from the calls that actually happened, so a customer who used one vendor is not told their data went to five | `GET /api/v1/privacy/subprocessors` |
| Know who accessed the data | ✅ | `GET /api/v1/privacy/access-log` |
| Rectification | ❌ Not implemented as a right-handling flow. A recording of what somebody said is not correctable; the underlying record can be edited through the normal API. |

Requests are executed **by the customer on their own account**. We do not act on
a request from a stranger about an account they have not proved they belong to:
confirming that a number appears in an account would itself be a disclosure.

## 7. Sub-processors — Art 28(2)

Derived from configured provider credentials and from the priced line items of
calls that actually ran, so the list cannot drift from reality without the code
changing too. `GET /api/v1/privacy/subprocessors`.

Infrastructure sub-processors are declared rather than derived, because nothing
in the application enumerates its own hosting: **Amazon Web Services** (hosting,
database, object storage) and **Razorpay** (payments; card details never reach
us).

[TO CONFIRM — the notice period for sub-processor changes and the customer's
right to object belong in the DPA body, not in this annex.]

## 8. Transfers — Ch. V

[TO CONFIRM] Hosting region is US. Model vendors are predominantly US. Personal
data of EU data subjects leaving the EEA needs SCCs plus a transfer impact
assessment. An EU deployment region is usually cheaper than the paperwork.

## 9. Personnel

[TO CONFIRM — confidentiality undertakings, access on a need-to-know basis,
offboarding. These are organisational and cannot be evidenced from code.]

---

*Facts in this annex are derived from the codebase as of the last commit that
touched `compliance/`. Everything marked `[TO CONFIRM]` is a decision or an
operational fact that is not visible in code.*
