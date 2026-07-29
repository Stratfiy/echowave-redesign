# Known Issues

Running log of open problems, so they can be worked through one at a time.
Each entry records what is wrong, why, and what fixing it involves.

Status legend: **OPEN** · **FIXED** · **DECISION NEEDED** (needs a product/ops
call, not a code change)

Last updated after Phase 2 (commit `3b64acb`, "Remove MPS billing dependency").

---

## 1. Test suite cannot run from a fresh clone — `pipecat` is missing

**Status:** OPEN · **Severity: high** — blocks all local and CI testing

`.gitmodules` does not exist anywhere in this repository (checked on `main`
and on the feature branch), and no files are tracked under `pipecat/`. But:

- `scripts/setup_requirements.sh` runs `git submodule update --init --recursive`
  — a no-op with no `.gitmodules`, then `uv pip install -e ./pipecat[...]`,
  which fails because `./pipecat` does not exist.
- `.github/workflows/api-tests.yml` and `pre-pr-drift-check.yml` check out with
  `submodules: recursive` and expect a `pipecat/` tree.
- `scripts/format.sh` runs `ruff format pipecat`.

This is **pre-existing** — not introduced by the rebrand or the billing removal.
The likely cause is that this repo is a nested copy of the upstream project
(`echowave-redesign/echowave/`) and `.gitmodules` was lost in the copy.

**Consequence:** 107 of the 130 test-collection errors below, and 39 of the 51
test failures, are just this one missing dependency.

**To fix:** restore `.gitmodules` pointing at the pipecat fork, at the commit
the app expects, and re-run `./scripts/setup_requirements.sh`.

---

## 2. Test failures — all environmental, none are code defects

**Status:** OPEN (environment) · **Severity: medium**

Full run of `api/tests`: **51 failed, 328 passed, 130 collection errors.**

Verified against a clean worktree at the previous commit: **the failing set is
byte-for-byte identical before and after the billing removal.** No failure in
this list is caused by our changes, and every one traces to a missing package
or a missing service — not to a bug in application code.

### 2a. Failures caused by missing `pipecat` (39)

| Test file | Failures |
|---|---|
| `test_workflow_graph_constraints.py` | 24 |
| `test_workflow_qa_masking.py` | 5 |
| `telephony/test_call_transfer_manager.py` | 3 |
| `test_sdk_sync.py` | 2 |
| `test_decibyl_sdk.py` | 2 |
| `test_agent_stream_route.py` | 2 |
| `test_dto.py` | 1 |

All fail with `ModuleNotFoundError: No module named 'pipecat'`. Fixed by issue #1.

### 2b. Failures caused by no Redis server (10)

| Test file | Failures |
|---|---|
| `test_circuit_breaker.py` | 4 |
| `test_from_number_pool_isolation.py` | 3 |
| `test_call_concurrency.py` | 3 |

All fail with `redis.exceptions.ConnectionError: Error 111 connecting to
localhost:6379`. Needs a running Redis (CI provides one as a service container).

### 2c. Failures caused by missing `aiortc` (2)

`test_public_signaling_origin.py` — `ModuleNotFoundError: No module named 'aiortc'`.

### 2d. Collection errors (130) — missing packages

| Missing module | Files affected |
|---|---|
| `pipecat` | 107 |
| `groq` | 12 |
| `fastmcp` | 7 |
| `mcp` | 4 |
| `aiortc` | 4 |
| `aioboto3` | 3 |
| `google` | 1 |

These are the heavy optional extras installed by
`uv pip install -e ./pipecat[cartesia,deepgram,openai,...]`, so they are also
downstream of issue #1.

### Reproducing a partial run without the full stack

The pure-unit suites (no DB, no Redis, no pipecat) can be run with:

```bash
pip install pytest pytest-asyncio loguru fastapi httpx pydantic sqlalchemy \
    python-dotenv pgvector asyncpg aiohttp openai posthog deepgram-sdk bcrypt
export DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/decibyl_test"
export REDIS_URL="redis://localhost:6379/0"
python -m pytest api/tests/test_quota_service.py api/tests/test_auth_depends.py -q
```

`api/conftest.py` loads `api/.env.test`, which is gitignored and absent here —
hence the manual `DATABASE_URL` / `REDIS_URL` exports.

---

## 3. Pre-existing `ruff format` drift in `cloudonix/provider.py`

**Status:** OPEN · **Severity: low**

`ruff format api` reformats
`api/services/telephony/providers/cloudonix/provider.py` on `main` as well as
on this branch, so `pre-pr-drift-check.yml` ("Check for Python format/lint
drift") will fail on any PR until it is committed.

Ruff is not pinned anywhere in the repo (`api/requirements.dev.txt` has no ruff
entry, and there is no `[tool.ruff]` config), so CI installs whichever version
is current — the checked-in formatting was produced by an older ruff.

**To fix:** run `./scripts/format.sh` and commit, and pin a ruff version in
`requirements.dev.txt` so formatting stops drifting with upstream releases.

---

## 4. Sentry organization slug still says `echowave`

**Status:** DECISION NEEDED · **Severity: medium**

`ui/next.config.ts` sets `org: "echowave"` for `withSentryConfig`. This is a
live external Sentry organization identifier, not a brand string, so the
rebrand deliberately left it alone — renaming it without a matching Sentry org
silently breaks error reporting for the UI.

**Needs:** confirmation of the real Sentry org slug, changed here and in Sentry
together.

---

## 5. Community URLs point at a GitHub org that does not exist

**Status:** DECISION NEEDED · **Severity: medium**

The rebrand mechanically rewrote `github.com/dograh-hq/dograh` to
`github.com/decibyl-hq/decibyl`. That org does not exist, so these links are
now dead:

- `SECURITY.md` — vulnerability disclosure link
- `CONTRIBUTING.md` — issue links
- `docs/contribution/setup.mdx` — issues, ideas, and a Slack invite
- `docs/contribution/reference.mdx`, `docs/integrations/overview.mdx`,
  `docs/integrations/telephony/agent-stream.mdx`
- `README.md`, `README.zh-CN.md`, `README.ja-JP.md`,
  `docs/getting-started/index.mdx`, `docs/deployment/*.mdx` — `curl`
  install commands fetching `docker-compose.yaml` from `raw.githubusercontent.com`
- `.github/ISSUE_TEMPLATE/config.yml`

The install commands are the urgent ones: anyone following the docs gets a 404.

Since Decibyl is no longer open source, the right fix is probably to **remove**
the OSS-contribution sections rather than repoint them. Needs a decision on the
real repo URL and support channels.

---

## 6. Brand PNGs carry stale `dograh` metadata

**Status:** OPEN · **Severity: low (cosmetic)**

`ui/public/decibyl-logo.png`, `decibyl-logo-inverse.png` and `decibyl-mark.png`
were renamed, but the binary files still contain "dograh" in embedded metadata.
Not user-visible. The SVGs are clean. Fix by regenerating the PNGs from the SVGs.

---

## 7. Top-level directory is still named `echowave/`

**Status:** DECISION NEEDED · **Severity: low**

The project root is `echowave-redesign/echowave/`. Renaming it to `decibyl/`
is cosmetic but high blast radius: it moves every path in git history, and CI
workflow `working-directory` values, deploy scripts and devcontainer mounts all
assume the current layout. Left as-is deliberately.

---

## 8. Runs are not gated on any balance

**Status:** OPEN by design · **Severity: high once billing goes live**

Phase 2 removed the MPS prepaid-credit check from
`authorize_workflow_run_start`. Nothing currently stops a run on an unfunded
account. This is the intended intermediate state — Decibyl is not selling
prepaid inference credits — but it means **there is no spend ceiling today.**

The tenant-isolation checks in that function (workflow belongs to the org,
actor is a member of the org, a supplied run belongs to the workflow, DB read
failures fail closed) were all preserved; only the credit gate went away.

`api/tests/test_quota_service.py::test_authorization_module_exposes_no_credit_gating`
guards against an external billing service being quietly reintroduced onto the
critical path of every call.

**Next:** Phase 3 puts the local paise-denominated ledger check back into this
same function.

---

## 9. `docs/api-reference/openapi.json` and the TS client need a real regeneration pass

**Status:** OPEN (verification) · **Severity: low**

Both were regenerated for the endpoint removals, but not by the normal route:
`scripts/dump_docs_openapi.py` imports `api.app`, which needs `pipecat`
(issue #1), so it could not run here. Instead the three removed paths and the
six now-unreferenced component schemas were pruned from the spec
programmatically (with transitive reachability, matching what FastAPI emits),
written with the generator's exact `json.dumps(..., separators=(",", ":"))`
formatting, and the TS client was then regenerated from that spec with the real
`openapi-ts` toolchain.

The result should be identical to a real run, but `pre-pr-drift-check.yml`
regenerates the spec from the live app and will be the authoritative check.
Worth confirming on the first CI run.

---

## Fixed

### F1. Rebrand broke `pipecat` imports — voice pipeline would not start

**Fixed in `3b64acb`.** The rebrand commit rewrote `pipecat.services.dograh`
to `pipecat.services.decibyl`, along with the class names imported from it
(`DograhLLMService`, `DograhSTTService`, `DograhTTSService`,
`DograhFluxSTTService`, `DograhSTTSettings`, `DograhTTSSettings`).

Those modules live inside the **pipecat submodule** — external code this repo
does not own — so the imports would have raised `ModuleNotFoundError` at
startup and taken down the entire voice pipeline.

Reverted in `api/services/pipecat/service_factory.py`,
`api/tests/test_decibyl_managed_correlation.py`,
`api/tests/test_camb_tts_integration.py` and
`api/tests/test_decibyl_stt_service_factory.py`.

This needed care: `DecibylLLMService` / `DecibylSTTService` /
`DecibylTTSService` exist **twice** — once in pipecat and once as our own
config-registry classes with identical names. Only the pipecat ones were
reverted; ours (`DecibylGoogleLLMService`, `DecibylGoogleVertexLLMService`,
`DecibylGeminiJSONSchemaAdapter`) stay renamed.

### F2. Rebrand left `ruff format` drift

**Fixed in `3b64acb`.** The longer "Decibyl" identifiers pushed several lines
past the formatter's width. Confirmed by diffing formatter output at `main` vs
the rebrand commit; the newly-drifting files were
`api/services/configuration/ai_model_configuration.py`,
`api/services/managed_model_services.py`,
`api/services/pipecat/service_factory.py`, `api/services/quota_service.py`,
`api/services/mps_service_key_client.py`,
`api/tests/test_ai_model_configuration_v2.py` and
`api/tests/test_gemini_json_schema_adapter.py`. (Issue #3 is separate and
pre-dates the rebrand.)
