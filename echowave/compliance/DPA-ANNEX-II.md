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
| Backups | ✅ | Enabled by default and encrypted with the platform credential secret. 30-day retention (`BACKUP_RETENTION_DAYS`); a backup older than 36 hours raises an alert (`BACKUP_STALE_AFTER_HOURS`), so a silently-stopped backup job is noticed rather than discovered during a restore. Optional off-account mirror via `BACKUP_MIRROR_*`, unset by default — see § 2 note. |
| Documented disaster recovery with RTO/RPO | ❌ | No RTO or RPO is committed. The recovery *point* is the last backup (up to 24 hours) until the database moves to managed Postgres with point-in-time recovery, which takes it to minutes; `MIGRATE-TO-MANAGED-POSTGRES.md` and `scripts/cutover_to_managed_postgres.sh` are the plan and the procedure. Stated rather than promised: a number here is a commitment. |
| Multi-region redundancy | ❌ | Single region. |

## 3. Restoring availability after an incident — Art 32(1)(c)

| Measure | Status |
|---|---|
| Error monitoring and alerting (Sentry) | ✅ |
| Breach-window report: what was reached between two timestamps, by whom, over how many calls | ✅ `GET /api/v1/privacy/breach-report` — counts and identifiers only, never content, because a breach report containing the compromised data is a second incident |
| Documented incident response runbook with named roles | ❌ No runbook exists. The breach-window report above supplies the facts an incident needs; who is called and in what order is not written down. |
| Tested restore procedure | ✅ `scripts/rehearse_restore.sh` restores the newest backup into a scratch database, reconciles the ledger and prints how long it took. It is written to be run on a schedule rather than once, because backups break silently — a schema change, a rotated secret — and the failure only shows on the day it cannot. It never touches the live database. |

## 4. Testing and evaluating effectiveness — Art 32(1)(d)

| Measure | Status |
|---|---|
| Automated test suite covering the privacy controls themselves — retention, erasure, export, access logging, recording disclosure | ✅ `api/tests/test_privacy.py`, `api/tests/test_recording_disclosure.py` |
| Retention enforcement verified by test, including that storage objects are deleted and not merely dereferenced | ✅ The failure mode that matters: clearing the row and leaving the audio looks exactly like success |
| Dependency vulnerability scanning | ❌ Not configured. `.github/` carries an issue template and a release workflow and no scanning. A known gap, not an unknown one. |
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

Sub-processor changes are notified 30 days in advance, and the Customer may
object within that period — stated in § 6 of the DPA rather than here, because
it is an obligation rather than a measure.

## 8. Transfers — Ch. V

Hosting is in **AWS `ap-south-1` (Mumbai)**, chosen for DPDP residency — call
recordings and transcripts are conversations with people in India and the region
holding them is where that data comes to rest. `MIGRATE-TO-MUMBAI.md` records the
move and `scripts/verify_region_migration.py` checks it, because a green health
check does not prove the bucket moved.

Model vendors differ by the tier a customer selects, and the answer is computed
rather than declared: `api/services/configuration/residency.py` derives whether
speech and language stay in India from the stack a call will actually run on, so
pointing a tier at a foreign vendor removes the guarantee by itself. Embeddings
are the documented exception — a knowledge base sends text to a foreign vendor at
ingest even when every model on the call is Indian.

Personal data of EU data subjects leaving the EEA needs SCCs plus a transfer
impact assessment, which is `[TO CONFIRM]` and only arises once an
EU-established customer appears.

## 9. Personnel

[TO CONFIRM — confidentiality undertakings, access on a need-to-know basis,
offboarding. These are organisational and cannot be evidenced from code.]

---

*Facts in this annex are derived from the codebase as of the last commit that
touched `compliance/`. Everything marked `[TO CONFIRM]` is a decision or an
operational fact that is not visible in code.*
