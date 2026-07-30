# Known Issues

Running log of open problems, so they can be worked through one at a time.
Each entry records what is wrong, why, and what fixing it involves.

Status legend: **OPEN** · **FIXED** · **DECISION NEEDED** (needs a product/ops
call, not a code change)

Last updated after the known-issues resolution pass.

> **Current test status: `api/tests` is fully green — 1206 passed, 0 failed,
> 0 collection errors** (Python 3.13, real Postgres 16 + pgvector, real Redis).
> Every one of the 51 failures and 130 collection errors previously recorded
> here was environmental. None was a code defect.

---

## Open

### 4. Sentry organization slug still says `echowave`

**Status:** DECISION NEEDED · **Severity: medium**

`ui/next.config.ts` sets `org: "echowave"` for `withSentryConfig`. This is a
live external Sentry organization identifier, not a brand string, so the
rebrand deliberately left it alone — renaming it without a matching Sentry org
silently breaks error reporting for the UI.

**Needs:** confirmation of the real Sentry org slug, changed here and in Sentry
together.

---


---

### 10. Pre-existing schema drift between models and the database

**Status:** OPEN · **Severity: medium** — `alembic check` fails

Two differences between `db/models.py` and a migrated database predate this
work and are identical on `main`:

| Drift | Direction |
|---|---|
| `idx_queued_runs_campaign_state_optimized` on `queued_runs(campaign_id, state)` | in the model, missing from the database |
| `workflow_definitions.call_disposition_codes` | in the database, missing from the model |

Because of these, `alembic check` fails and **every `--autogenerate` run sweeps
them into the new migration**. They were removed by hand from the billing
migration (`810aaefd657d`); the column drop in particular would have destroyed
data. Anyone generating a migration must do the same until this is resolved.

**To fix:** decide each direction deliberately — add the missing index via a
migration, and either restore `call_disposition_codes` to the model or write an
explicit, reviewed migration to drop it. Do not let autogenerate decide.

---

### 7. Top-level directory is still named `echowave/`

**Status:** DECISION NEEDED · **Severity: low**

The project root is `echowave-redesign/echowave/`. Renaming it to `decibyl/`
is cosmetic but high blast radius: it moves every path in git history, and CI
workflow `working-directory` values, deploy scripts, devcontainer mounts and
the `.gitmodules` submodule path all assume the current layout. Left as-is
deliberately.

---

### 8. Runs are not gated on any balance

**Status:** OPEN by design · **Severity: high once billing goes live**

Removing MPS billing took the prepaid-credit check out of
`authorize_workflow_run_start`. Nothing currently stops a run on an unfunded
account. This is the intended intermediate state — Decibyl is not selling
prepaid inference credits — but it means **there is no spend ceiling today.**

The tenant-isolation checks in that function (workflow belongs to the org,
actor is a member of the org, a supplied run belongs to the workflow, DB read
failures fail closed) were all preserved; only the credit gate went away.

`api/tests/test_quota_service.py::test_authorization_module_exposes_no_credit_gating`
guards against an external billing service being quietly reintroduced onto the
critical path of every call.

**Next:** the local paise-denominated ledger check goes back into this same
function when the cost engine lands.

---

## Fixed

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
  Now `support@decibyl.com` and `security@decibyl.com`.
* **The fork-and-PR contributor flow** described working against a public
  upstream. Replaced with direct clone and branch.
* **`README.md` claimed "100% open source" and BSD 2-Clause** throughout, which
  contradicts the product. Rewritten as a private-repo README.
* **`README.zh-CN.md` and `README.ja-JP.md`** were translations of that
  positioning. Removed rather than left contradicting the English one — they
  need a translator, not a find-and-replace, if they come back.

**Two placeholders need confirming:** `security@decibyl.com` and
`support@decibyl.com` are assumed from the `decibyl.com` domain already used in
`api/constants.py`. Point them at real inboxes.

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

### 11. The MinIO bucket policy is anonymous read/write/delete

**OPEN — pre-existing, found 2026-07-29.** `MinioFileSystem.__init__`
unconditionally applies this policy to whatever bucket it is constructed with,
on every initialisation:

```
"Principal": {"AWS": "*"},
"Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
```

So the call-recording bucket is anonymously readable, writable **and
deletable** by anyone who can reach the endpoint. The code carries its own
warning — "Only use in local development, not production!" — but nothing
enforces that, and `aget_signed_url` returns a plain unsigned bucket URL that
only works *because* of this policy.

Two consequences worth separating:

* Call recordings and transcripts are exposed to anyone with the URL, and the
  URLs are guessable in shape.
* Anonymous `PutObject`/`DeleteObject` means a third party can overwrite or
  destroy recordings.

Not fixed here because it is pre-existing and changing it breaks every existing
recording URL, which needs a migration to presigned reads. **KYC documents
deliberately do not use this class** — `api/services/kyc/documents.py` talks to
MinIO directly and never sets a bucket policy, precisely so identity documents
are not published this way.

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
