# Known Issues

Running log of open problems, so they can be worked through one at a time.
Each entry records what is wrong, why, and what fixing it involves.

Status legend: **OPEN** · **FIXED** · **DECISION NEEDED** (needs a product/ops
call, not a code change)

Last updated after the compliance and deployment pass.

> **Current test status: `api/tests` is fully green — 1,911 passed, 0 failed,
> 0 collection errors** (Python 3.13, real Postgres 16 + pgvector, real Redis).
> Every one of the 51 failures and 130 collection errors previously recorded
> here was environmental. None was a code defect.

---

## Open

### 7. Top-level directory is still named `echowave/`

**Status:** DECISION NEEDED · **Severity: low**

The project root is `echowave-redesign/echowave/`. Renaming it to `decibyl/`
is cosmetic but high blast radius: it moves every path in git history, and CI
workflow `working-directory` values, deploy scripts, devcontainer mounts and
the `.gitmodules` submodule path all assume the current layout. Left as-is
deliberately.

---

## Fixed

### 20. The model screen offered three tabs that were never three options

**FIXED.** Full write-up in `MODEL-SELECTION-REDESIGN.md`, including the
competitor comparison. Summary:

The screen asked you to pick one of "Speech to Speech", "Decibyl" or "BYOK".
Two of those are ways of paying for inference and one is an architecture, so
they were never comparable — they were exclusive only because a single stored
`mode` field had been made to carry both meanings.

What that cost:

* **A mixed stack was unrepresentable.** "Your Indic transcriber, my OpenAI
  contract" is what most Indian accounts want, and there was no way to say it.
* **Managed speech-to-speech was impossible**, because realtime lived inside
  the BYOK branch.
* **Nothing said that picking Speech to Speech meant bringing your own key.**
  That fact was in the shape of the JSON, not in any label.
* The screen had grown a warning strip explaining that switching tabs did not
  switch your account over — a reliable sign the model underneath is wrong.

Now two questions, asked separately. Architecture is the only top-level choice.
Whose key each model runs on is a property of the slot: pick `decibyl` in any
provider dropdown and that one model is managed, so half a stack can be managed
and half your own. There is deliberately no `mode` field in v3 — managed-ness is
derived, because a stored summary of the slots would eventually disagree with
them and the summary is what people would trust.

**The change was small, which is the part worth remembering.** The config types
were already discriminated unions including the Decibyl variants, and
`managed_resolution` already rewrote each section independently. One validator
(`_reject_decibyl_provider`) and one dropdown filter (`_byok_provider_schemas`)
were the only things forbidding mixed stacks. The rest is a vault to get keys
out of the configuration JSON.

Keys now live in `organization_provider_credentials` — Fernet-encrypted,
org-scoped, write-only — instead of inline as plaintext inside the model config.
That is what makes "store a key" and "choose a model" two separate jobs; before,
a key could only be entered while choosing a model, and switching a slot's
provider discarded the key you had just pasted.

~~Still open from this work: the per-agent model override.~~ **Done — this
line was stale and cost a later session real time.** The per-agent override is
fully built in `ui/src/app/workflow/[workflowId]/settings/page.tsx`: a
"Give this agent its own models" switch, the shared
`AIModelConfigurationV2Editor` bound to
`workflow_configurations.model_configuration_v2_override`, and a graceful
path when the org has no `organization_v2` configuration yet (an explanatory
message and a link to `/model-configurations`, rather than a hidden section).

Worth noting for anyone auditing coverage the same way: this feature is a
**field on `workflow_configurations`**, saved through the ordinary workflow
update endpoint — not an endpoint of its own. Any "which backend features
have no UI" scan driven by unused generated-client operations is structurally
blind to it, and to anything else shaped like it.

### 19. The two silent billing killers had no signal

**FIXED.** `PRODUCTION-CHECKLIST.md` §2 reproduced both against a real
deployment, and the only thing standing between either one and an operator was
a log line nobody was reading.

With `SUPPLIER_LEGAL_NAME` and `SUPPLIER_GSTIN` unset, a signature-verified
payment was credited to the ledger and issued **zero** tax documents.
`issue_receipt_voucher()` returning `None` rather than raising is correct — a
real payment must not be rolled back over a missing environment variable — so
the fix is not to make it throw. It is to make the consequence visible. With an
empty price book the second one has the same shape: every call bills its
platform fee, provider cost reads zero, and the dashboard reports 100% margin
rather than an error.

`GET /admin/billing/readiness` now answers both, in the shape
`/privacy/readiness` already used. The split that carries the weight is
configuration versus evidence:

* **Configuration** — is the supplier identity set, is there a webhook secret,
  is there a price book. Cheap, and worth little alone.
* **Evidence** — *are there captured payments carrying no receipt voucher*, and
  *are there costed calls that used a provider we hold no rate for*. These are
  the ones that matter, because setting the supplier identity today does
  nothing about the payments already taken without one. Those are an accrued
  liability and only a query finds them.

The headline check is designed to read zero missing vouchers. Any other value
is an incident, not a statistic.

A fresh install reports `unknown` rather than `ready` for every evidence check.
Reporting a pass because nothing has happened yet is the specific dishonesty
the module exists to avoid, and it is why one live ₹10 top-up remains on the
pre-launch checklist — it is the only thing that proves payment, credit and
document issuance work end to end.

The `Check`/`Readiness` vocabulary moved to `api/services/readiness.py` and is
shared with the privacy assessment, which re-exports it so its own callers and
tests are unaffected. Two independent status vocabularies that both meant
"ready" would have drifted.

### 18. A dead background worker was invisible

**FIXED.** One container runs uvicorn, the ARQ workers, the ARI manager and the
campaign orchestrator (`start_services_docker.sh`). When the ARQ worker dies
the API keeps answering 200 on every endpoint while completed calls stop being
costed, the rollups the entire dashboard reads stop refreshing, monthly tax
invoices stop being issued, and the nightly purge and backup stop running.

The dashboard does not go blank, which is the problem — it serves the last
figures it had. A quiet morning and a dead worker look identical.

The absence of work cannot be the signal, because a night with no calls
produces no costing either. So the worker now states positively that it is
alive: a one-minute ARQ cron writes a timestamp to Redis, and
`GET /health/workers` reports its age. Behind the devops secret, like
`/health/active-calls`.

Two design points worth keeping:

* **The key's TTL is a day, far longer than the five-minute staleness
  threshold.** An expiring key would answer "the worker is gone" and destroy
  the more useful answer — *when* it stopped, which is what lines the failure
  up against a deploy or an OOM kill.
* **`alive` is tri-state.** `null` means no heartbeat on record or Redis
  unreachable, and is deliberately not folded into `false`. A deployment whose
  worker was never started and one whose worker died an hour ago need different
  responses, and collapsing them sends an operator hunting a process that never
  existed.

The billing readiness check consumes the same signal, because a dead worker is
precisely what makes every billing number on the dashboard stale.

### 17. The campaign report had no aggregate, and no Language column

**FIXED.** `GET /campaign/{id}/report` streams a row per call and always did.
What it could not answer is what tender §10 asks for: connection rate,
completion rate, retry statistics, language distribution, daily progress.

`GET /campaign/{id}/summary` now does. The arithmetic is division; the content
is the denominators, so they are fixed in one module and match the admin
dashboard's campaign query exactly — `/admin/billing/campaigns` and this
endpoint cannot disagree about the same campaign.

The choice that changes the answer: **completion rate is over connected calls,
not over attempts.** A call nobody picked up cannot complete a conversation, so
putting it in the denominator reports the agent as failing at something it
never got the chance to attempt. Connection rate keeps attempts as its
denominator, and reach against the supplied contact list is reported separately
again — a reach target is written against the list you were given, not the
dials you chose to make.

A rate with nothing in its denominator is `null`, never `0.0`, consistent with
the rest of the codebase (HANDOVER.md §6): a campaign that has not dialled has
measured nothing, and 0% reads as total failure.

Daily progress buckets by **IST** calendar day. In UTC the boundary sits 5h30m
off an operator's own day, so a call at 01:00 IST would be filed under
yesterday.

Separately, the per-run CSV now carries a **Language** column.
`workflow_runs.language` was populated all along and the report query simply
did not select it, so language distribution — which the tender explicitly
requires — was underivable from the export a customer is handed.

### 16. Recordings defaulted to a US region

**FIXED.** `S3_REGION` defaulted to `us-east-1` in `api/constants.py`, in
`docker-compose.yaml`, and across the Helm values. That bucket holds call
recordings and transcripts of conversations with people in India, and the
region is where that personal data comes to rest — so every deployment that
never set the variable put it in Virginia.

Now `ap-south-1` (Mumbai) in all four places. The safe location should be what
you get by not thinking about it; a deployment whose data subjects are
elsewhere can still override deliberately.

This does **not** discharge the `ap-south-1` migration on the pre-tender
checklist — an existing deployment's data does not move because a default
changed. It stops the next one from starting out wrong.

The migration of the deployment that already exists is `MIGRATE-TO-MUMBAI.md`,
and it is verified rather than declared: `scripts/verify_region_migration.py`
asks S3 where each bucket actually is, rather than reading back the variable
that says where it should be. Until that is green on the box, this issue is
half-fixed — right for every future deployment, and unchanged for the data
already sitting in Virginia.

### 15. `usage/daily-breakdown` returned 400 for an unconfigured account

**FIXED.** The guard was right — an account with no `price_per_second_usd` has
nothing to break down — but a 400 made a correct guard render as a broken
dashboard tile on the first screen a new customer sees.

Now an empty series with `pricing_configured: false`. The flag is the point:
without it a caller cannot distinguish *not priced yet* from *priced, but
nobody has called*, and would render the same empty chart for a configuration
problem and for a normal Sunday. `total_cost_usd` stays `null` rather than
`0.0` — there is no price, so there is no cost, as distinct from a cost that
was measured and came to nothing.

### 13. Nothing backed up the database

**FIXED.** There was no automated backup of Postgres anywhere — no `pg_dump`, no
WAL archiving, no volume snapshot, nothing in `docker-compose.yaml` and nothing
in any deploy script. `DEPLOY.md` told the operator to "take a backup, and check
you can restore it", which is an instruction to a human, not an implementation.

The credit ledger is the only record of what every customer has paid, and it
cannot be reconstructed: Razorpay knows what was charged but not what was
consumed, reserved or adjusted, and the tax invoices issued against it become
unreproducible — a GST problem on top of a customer-trust one.

Found while writing `compliance/DPA-TEMPLATE.md`, where the security annex has
to state the backup position to a customer either way.

Now a nightly `run_database_backup` job: `pg_dump` in custom format, Fernet
encrypted before it leaves the process, uploaded, then read back and size-checked
before the local file goes. Old dumps are pruned on `BACKUP_RETENTION_DAYS`,
because a dump holds every phone number in the system and ages under the same
obligation as the data inside it. The privacy readiness check reports the age of
the newest object, so "is there a backup" is answered by the object store rather
than by the presence of the code.

Unlike the other scheduled jobs it re-raises after logging, so a failure reaches
Sentry rather than leaving the readiness check reporting the last good one.

The restore has been rehearsed end to end — dump, encrypt, decrypt, restore into
a scratch database, verify — and `scripts/rehearse_restore.sh` repeats it on
demand against a database it creates and drops itself, never the live one.

Re-run it after any schema change, secret rotation or storage migration. Those
are what silently break a working backup, and the failure is only visible on the
day it cannot be fixed.

---

### 12. `scripts/format.sh` reformatted the documentation, and its result depended on where you ran it

**Status:** FIXED

Two problems in the same place, both of which made the CI format-drift check
fail on a clean checkout.

Ruff formats Python code blocks inside Markdown as of 0.14, and `format.sh`
passes it the whole `api` tree. Every run reflowed the annotated snippets in
`AGENTS.md` and the service READMEs, where comment columns are aligned to be
read rather than executed — so the docs were a moving target and the drift
check failed on files nobody had touched. `extend-exclude = ["*.md"]` in
`api/pyproject.toml` stops it; Python is still formatted.

Adding that section made `api/pyproject.toml` ruff's configuration root, which
exposed the second problem: with no config file anywhere, ruff had been
resolving isort's first-party packages against the *current directory*, so
`ruff check api` from the repo root and `ruff check .` from `api/` disagreed
about whether `from api.x import y` was first-party. `src = [".."]` settles it
for both. `.` is deliberately not on that path — with it, the `api/alembic/`
package makes the third-party `alembic` distribution look first-party and every
migration's import block gets reordered instead.

### 11. The recordings bucket was published to the world

**FIXED.** `MinioFileSystem.__init__` applied a `Principal: {"AWS": "*"}` policy
granting `GetObject`, `PutObject` **and** `DeleteObject` on every
initialisation, and `aget_signed_url` returned a bare bucket path that only
worked *because* of it. So call recordings and transcripts — recordings of real
conversations with real customers — were readable by anyone who could reach the
endpoint and could guess a URL, and writable and deletable by them too.

Access is now by presigned URL, which carries its own expiring signature and
needs no bucket policy at all. Reads and uploads are both signed. Because a
presigned URL is signed for a specific host, and the internal endpoint
(`minio:9000`) differs from the public one, there are two SDK clients — one
bound to each, each signing for its own audience. That mismatch is the reason
the original code gave for not signing in the first place.

The anonymous policy survives behind `MINIO_PUBLIC_BUCKET=true` for a local
stack, off by default and logging a warning when on.

KYC documents were never exposed this way — `api/services/kyc/documents.py`
talks to MinIO directly and sets no policy, deliberately.

---

### 10. Schema drift between models and the database

**FIXED.** `alembic check` failed, and the real cost was worse than a failing
check: every `--autogenerate` run proposed **dropping
`workflow_definitions.call_disposition_codes`**, a NOT NULL column holding data
on every published version of every workflow. Anyone generating a migration had
to know to delete that line by hand, and the billing migration
(`810aaefd657d`) records having done exactly that.

Resolved in both directions deliberately rather than by accepting whatever
autogenerate suggested:

* `call_disposition_codes` existed in the database but not on the model, so it
  is now declared on the model. The data is the reason.
* `idx_queued_runs_campaign_state_optimized` was declared on the model but never
  created — a partial index on the campaign dispatcher's hot query. Created in
  `c8f31a604be7`.
* Several `server_default`s were set by migrations but not declared on the
  models, which was drift introduced by this billing work. Now declared on both
  sides. No DDL: the database was already correct.

`alembic check` now reports no operations, and a database built from an empty
schema by replaying every migration matches the models exactly — verified.

---

### 4. Sentry organization slug still said `echowave`

**FIXED.** `ui/next.config.ts` hardcoded `org: "echowave"`, a live external
identifier the rebrand deliberately left alone because renaming it without a
matching Sentry org breaks stack traces rather than fixing anything.

Now `SENTRY_ORG` and `SENTRY_PROJECT`, so a deployment sets its own and one
that sets neither uploads no source maps. Errors are reported either way; only
the readability of the trace depends on it.

---

### 8. Runs are not gated on any balance

**FIXED.** Removing MPS billing had taken the prepaid-credit check out of
`authorize_workflow_run_start`, so there was no spend ceiling at all. Both
halves of prepaid now exist: credit is bought through
`api/services/billing/payments.py`, and `api/services/billing/reservations.py`
refuses a run on an unfunded account.

A balance check alone would not have been enough, and that is worth recording
because it is the non-obvious part. A call's cost is unknown until it ends, so
two calls starting in the same instant both read the same balance, both find it
sufficient, and both proceed — an account with 10 rupees could start fifty
concurrent calls, each of which passed the check. Concurrency is what the
product sells, so that was the normal case rather than an edge one.

So a call holds an estimate before it starts, as an ordinary negative ledger
row taken under a per-organization `SELECT ... FOR UPDATE`. The lock is
load-bearing: with it removed, the concurrency test in
`api/tests/test_billing_reservations.py` allows 8 of 8 simultaneous starts on a
balance covering 2. The hold is released at costing and replaced by the real
charge, so an account is billed for what it used and never for the estimate,
and a cron sweeps holds stranded by a worker that died mid-call.

The tenant-isolation checks in that function were preserved throughout, and the
credit check deliberately runs *after* them — a security check must not be
reachable around, and consulting a balance before proving the caller owns the
workflow would leak whether an unrelated account has credit.

`test_quota_service.py::test_authorization_module_exposes_no_external_credit_gating`
still guards the thing that actually mattered: the check reads our own paise
ledger, and the external billing service that used to sit on the critical path
of every call does not come back.

Enforcement is on by default and can be disabled with
`BALANCE_ENFORCEMENT_ENABLED=false`. See DASHBOARD.md.

---

### 5. Links pointed at upstream community infrastructure Decibyl does not own

**FIXED.** ~40 references across READMEs, docs, `CONTRIBUTING.md`, `SECURITY.md`,
the issue template and the Helm chart pointed at a `decibyl-hq` org, a Slack
invite whose token was upstream's, a Trendshift badge for upstream's repo id,
and a `decibyl-plugins` repo that does not exist.

Resolved by removal rather than repointing, because Decibyl is not open source
and most of these were OSS-community artifacts with no equivalent to point at:

* **`curl | bash` installers** (22×) fetched `raw.githubusercontent.com/decibyl-hq/...`
  and 404'd for anyone following the docs. Anyone deploying has a clone, so they
  now run the script that is already there. The `ghcr.io/decibyl-hq` registry
  option went with them.
* **Slack invite, Trendshift badge, GitHub Discussions, the plugins repo** —
  deleted outright.
* **Issue tracker and security advisory form** — a private repo has neither.
  Now `support@decibyl.ai` and `security@decibyl.ai`.
* **The fork-and-PR contributor flow** described working against a public
  upstream. Replaced with direct clone and branch.
* **`README.md` claimed "100% open source" and BSD 2-Clause** throughout, which
  contradicts the product. Rewritten as a private-repo README.
* **`README.zh-CN.md` and `README.ja-JP.md`** were translations of that
  positioning. Removed rather than left contradicting the English one — they
  need a translator, not a find-and-replace, if they come back.

Now `security@decibyl.ai` and `support@decibyl.ai`, confirmed as the owned
domain. The remaining `decibyl.com` references are inherited docs and marketing
URLs (`docs.decibyl.com`, `www.decibyl.com/privacy-policy`,
`api-leads.decibyl.com`) pointing at hosts nobody here owns — a separate
decision, since repointing them at `.ai` would produce the same number of broken
links.

### 1. Test suite could not run from a fresh clone — `pipecat` missing

**FIXED.** `.gitmodules` did not exist anywhere in the repository (verified on
`main` too) and nothing was tracked under `pipecat/`, yet
`scripts/setup_requirements.sh`, `scripts/format.sh` and both CI workflows all
expected a pipecat submodule. This single missing declaration caused **107 of
the 130 collection errors and 39 of the 51 failures.**

Restored with the exact URL and pin upstream uses, rather than a guess:

```
[submodule "echowave/pipecat"]
    path = echowave/pipecat
    url = https://github.com/dograh-hq/pipecat.git
```

pinned at `aadd1d5dd606d2871b082e6f2ca1ad1eee53785b` — the `pipecat` gitlink
recorded at upstream tag `dograh-v1.42.0`, the release matching this repo's own
version (`1.42.0`, consistent across `.release-please-manifest.json`,
`api/pyproject.toml` and `ui/package.json`). The path is `echowave/pipecat`
because the project sits one level below the git root.

This also independently confirmed the pipecat revert done during the billing
removal: the pinned commit ships `src/pipecat/services/dograh/` exporting
`DograhLLMService`, `DograhSTTService`/`DograhSTTSettings`,
`DograhTTSService`/`DograhTTSSettings` and `DograhFluxSTTService` — exactly the
six symbols reverted. Had the rebrand's `pipecat.services.decibyl` been left in
place, every one would have failed to import at startup.

### 2. Test failures — all environmental

**FIXED.** Root causes, in order of impact: the missing pipecat submodule
(issue #1); no Postgres/Redis running; missing `ts_validator` npm deps (CI
installs these in a dedicated step); a locally-corrupted `alembic` install
mixing files from two versions; and **Python 3.11 vs the `>=3.13` this project
requires** (`api/pyproject.toml`), which produced every remaining
`pydantic.errors.PydanticUserError: Please use typing_extensions.TypedDict`
error.

Progression while fixing these:

| | failed | passed | collection errors |
|---|---:|---:|---:|
| starting point | 51 | 328 | 130 |
| + pipecat submodule | 51 | 365 | 120 |
| + full dependency set | 41 | 1061 | 63 |
| + Postgres, Redis, ts_validator npm | 6 | 1096 | 61 |
| + clean alembic reinstall | 6 | 1142 | 20 |
| **+ Python 3.13 (correct version)** | **0** | **1206** | **0** |

### 3. Pre-existing `ruff format` drift in `cloudonix/provider.py`

**FIXED.** Committed the formatting so `pre-pr-drift-check` passes. Confirmed
pre-existing by diffing formatter output at `main` versus the rebrand commit.

Note: with pipecat installed, ruff correctly classifies its imports as
first-party, so the spurious isort churn that previously appeared across ~20
unrelated test files no longer happens.

**Still worth doing:** ruff is not pinned (`api/requirements.dev.txt` has no
ruff entry and there is no `[tool.ruff]` config), so CI installs whichever
version is current and formatting will drift again on the next ruff release.

### 6. Brand PNGs carried stale `dograh` metadata

**FIXED.** Stripped the XMP metadata from `ui/public/decibyl-logo.png`,
`decibyl-logo-inverse.png` and `decibyl-mark.png`, verified pixel-identical
before and after. (These files are not referenced anywhere in the app — only
the SVGs are — so they are legacy assets and could simply be deleted instead.)

### 6b. The login/app background watermark still said "dograh"

**FIXED.** `ui/public/brand-imprint-{light,dark}.svg` — the giant faded wordmark
behind the auth pages and the app surface (`--brand-imprint` in `globals.css`) —
were a single traced `<path>` spelling "dograh". Because the letters are vector
outlines and not `<text>`, neither `grep` nor a DOM text query found them; it
only surfaced in a screenshot. Note the CSS comment already *claimed* the asset
was the "decibyl" wordmark, so the file and its documentation disagreed.

Regenerated from the app's own typeface: Geist (the `next/font` subset the UI
already ships) instantiated at weight 700, glyph outlines for "decibyl" laid out
by advance width and emitted as one path — so the asset still needs no font at
render time. Same fills as before (`#000` @ 1.8% light, `#fff` @ 0.9% dark).
Verified by screenshot, both inline and through the `background-image` path CSS
actually uses.

### 9. `openapi.json` needed verification by the real generator

**FIXED — verified.** The endpoint removals were originally applied to the spec
by pruning it programmatically, because `scripts/dump_docs_openapi.py` imports
`api.app`, which needs pipecat. With the submodule restored and a Python 3.13
venv, the real generator now runs, and its output is **semantically identical**
to the hand-pruned spec: same 129 paths, same 243 schemas, equal when compared
as parsed JSON. The only byte difference was key ordering inside a
discriminator mapping; the generator's ordering is now committed, since
`pre-pr-drift-check` compares bytes.

### F1. Rebrand broke `pipecat` imports — voice pipeline would not start

**FIXED.** The rebrand rewrote `pipecat.services.dograh` to
`pipecat.services.decibyl` along with the class names imported from it. Those
modules live in the pipecat submodule — external code this repo does not own —
so the imports would have raised `ModuleNotFoundError` at startup and taken
down the entire voice pipeline. Reverted in
`api/services/pipecat/service_factory.py`,
`api/tests/test_decibyl_managed_correlation.py`,
`api/tests/test_camb_tts_integration.py` and
`api/tests/test_decibyl_stt_service_factory.py`.

This needed care: `DecibylLLMService` / `DecibylSTTService` /
`DecibylTTSService` exist **twice** — once in pipecat and once as our own
config-registry classes with identical names. Only the pipecat ones were
reverted; ours (`DecibylGoogleLLMService`, `DecibylGoogleVertexLLMService`,
`DecibylGeminiJSONSchemaAdapter`) stay renamed. Confirmed correct against the
actual pinned pipecat commit — see issue #1.

### F2. Rebrand left `ruff format` drift

**FIXED.** The longer "Decibyl" identifiers pushed several lines past the
formatter's width. Confirmed by diffing formatter output at `main` versus the
rebrand commit.

---

## Running the test suite

The project requires **Python 3.13** (`api/pyproject.toml`:
`requires-python = ">=3.13,<3.14"`). Running on an older interpreter produces
a wave of pydantic `TypedDict` errors that look like code bugs but are not.

```bash
python3.13 -m venv .venv && source .venv/bin/activate

# Order matters: api requirements first, pipecat (with extras) last, so
# pipecat's pinned extras win. Installing pipecat first lets tuner-pipecat-sdk
# pull pipecat-ai from PyPI, which shadows the submodule and reintroduces
# "No module named 'pipecat.services.dograh'".
pip install -r api/requirements.txt -r api/requirements.dev.txt pytest pytest-asyncio
git submodule update --init --recursive
pip install -e "./pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,\
sarvam,soundfile,silero,webrtc,speechmatics,openrouter,camb,mcp,inworld,smallest]"

# ts_validator needs its own npm deps or ~22 MCP tests fail
(cd api/mcp_server/ts_validator && npm install)

# Services. Postgres needs the pgvector extension: a migration runs
# CREATE EXTENSION vector.
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/decibyl_test"
export REDIS_URL="redis://127.0.0.1:6379/0"
export ENABLE_AWS_S3=false MINIO_PUBLIC_ENDPOINT=http://localhost:9000 DEPLOYMENT_MODE=oss

python -m pytest api/tests -q
```

`api/conftest.py` normally loads `api/.env.test`, which is gitignored and not
present in a fresh clone — hence the manual exports above.

A handful of `ERROR [asyncio] Task was destroyed but it is pending!` lines in
the output are log noise from torn-down pipeline tasks, not test errors; pytest
reports them separately from its pass/fail counts.
