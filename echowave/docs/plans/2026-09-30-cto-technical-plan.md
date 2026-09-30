# Decibyl technical plan — CTO / solutions-architect view

Date: 30 September 2026. Author: Claude (CTO session for Nithish). Baseline: `main` at `fa81da3` (PR #489), open PR #490, Jira project KAN (238 issues), PRD v2 phases KAN-193 / KAN-194 / KAN-195, costing audit on `finance/costing-audit`.

Status: **proposal for the founder's decision.** Nothing in the code, the server or the board's existing issues was changed. New Jira items are listed in §7 and carry the label `cto-plan`.

---

## 1. Where the product is

Decibyl is no longer a voice-bot builder. The code and the board both say the same thing: a business hires named bots (`@reception`, `@retention`) that work on phone, WhatsApp, email, web chat and routines, coordinated by Decibyl, the manager in the Home thread. Voice is the hardest channel, not the product (`AGENTS.md`). Commercially the company runs a **studio model**: NAutomation Labs sells the work; the self-serve plan ladder is unlisted behind `studio_mode` (KAN-188, done). The first pilot (Netoyed, KAN-229 / KAN-231) is blocked only on merging PR #490 and flipping two flags.

What exists, measured on this checkout:

| Area | Size / state |
|---|---|
| Backend | FastAPI, ~255k lines non-test Python, 79 route modules, 636 service files, 107 models, 222 Alembic migrations |
| Backend tests | 672 files, ~167k lines; CI runs `pytest -x` on a self-hosted runner with pgvector 17 + Redis 7 |
| Frontend | Next.js 15 / React 19, ~109k lines TSX, ~110 pages (23 staff-only), 140 Vitest files, **no browser E2E** |
| Runtime | Pipecat fork (submodule), 37 model/speech vendors, 7 telephony carriers, 7 realtime adapters |
| Billing | Integer-paise ledger, effective-dated rate card, GST/LUT documents, Razorpay webhook-only credit, 15-second pulses |
| Governance | Approval matrix + append-only audit log, agent visibility by role, per-member connections and memory — all behind flags |
| Feature flags | 15 in the registry (`services/features.py`), ~18 more env toggles outside it |
| Deploy | One EC2 box (ap-south-1), docker-compose, SSM-driven deploy from `main`; images still **built on the production box** |
| SDK / MCP | Python + TS SDK (17 methods), MCP server with 25 tools at `/api/v1/mcp` |

## 2. Architecture as found

```
carrier webhook ─► routes/telephony.py ─► signature + inbound guard + Redis org slot
                                       └► carrier opens WS ─► provider.handle_websocket
                                                              └► pipecat/run_pipeline.py (inside the uvicorn process)
                                                                   STT ─► LLM (graph engine) ─► TTS   (+ realtime S2S path)
browser  ─► routes/webrtc_signaling.py ─► run_pipeline_smallwebrtc
chat/WA/email/routine ─► services/workflow/* (stateless, IO-bound) ─► ARQ (30 jobs, 26 crons)
Decibyl thread ─► services/workflow/decibyl.py ─► propose card ─► actions.settle (10 s undo) ─► audit log
Postgres (pgvector) │ Redis (ARQ, pub/sub worker sync, rate limiter, call slots) │ MinIO │ FalkorDB (graph) │ sandbox svc
```

Two shapes share one process today: the **voice** half (stateful, CPU-bound, cannot cold-start or retry, holds a WebSocket) and **everything else** (stateless, tolerant). `DECIBYL_ROLE=media|control|all` exists in compose but the default is `all`, `FASTAPI_WORKERS=1`, and the two singletons (ARI manager, campaign orchestrator) are enforced by nobody.

## 3. Assessment

**Strengths worth protecting**

1. The money layer is unusually solid: integer arithmetic, effective-dated rates, webhook-only credit, GST done properly, gap-free document numbering. This is the moat the PRD names and the audit confirms.
2. Discipline in the codebase: thin-route rule, org-scoping rule, every slice dark behind a flag, guard tests for silent-absence bugs, long commit messages that explain why.
3. The office model (propose → confirm → undo → audit) is the right interaction model and it is already shipped in code; the P0 items of `docs/product/coordinator-interaction-model.md` are in Testing (KAN-140).
4. The test base is large (0.65 test:code) and CI is real, on real Postgres and Redis.

**Risks, ranked by blast radius**

| # | Risk | Evidence | Consequence |
|---|---|---|---|
| R1 | Production builds images on the live box; `build-images.yml` runs only by hand | `scripts/ci_deploy.sh:372`, `.github/workflows/build-images.yml:39` | Every deploy steals the CPU that carries live calls; rollback by SHA is unreliable |
| R2 | No browser E2E in CI | audit item 10 (7 Sep) still open | Auth, builder, billing pages (1–1.9k lines each) regress invisibly; the founder finds it on the phone |
| R3 | Voice pipeline runs inside the API worker, one core, unenforced singletons | `docker-compose.yaml:418-436`, `active_calls.py` | A deploy or a duplicate orchestrator cuts calls or doubles the carrier bill |
| R4 | Two exposed keys not yet rotated; arq Redis TLS `CERT_NONE`; Stack Auth 500 returns exception text | KAN-233, `tasks/arq.py:24-28`, `services/auth/depends.py:~108` | Security review fails; pilot data at risk |
| R5 | Tenant isolation is convention only, no RLS, no lint | `AGENTS.md:94-104` | One missed filter is a cross-org leak; enterprise buyers will ask |
| R6 | Billing code bills below cost in known places | costing audit §6: Decibyl chat turns unbilled, premium chat inside allowance, text runs at 1 credit vs ₹0.81 cost | Self-serve launch (Phase 2) would lose money on power users |
| R7 | Flag sprawl: 15 registered + ~18 unregistered, no expiry | `constants.py:535-701` | Untestable combinations; the UI cannot see half of them |
| R8 | No data-fetching layer in the UI; generated client not drift-checked; UI lint not in CI | audit §2, `ui-tests.yml` | Slow pages, stale contracts, style drift |
| R9 | Single box, no staging, secrets in `.env`, no restore drill | KAN-204 (Idea) | A bad migration or disk failure is an outage with no rehearsal |
| R10 | Pricing is undecided and three sources disagree | pricing study §3, KAN-207 | Every billing test is written against a moving target |

## 4. Strategic technical position

1. **Compete on the loop, not the runtime.** The STT/LLM/TTS stack is rented from the same vendors as everyone else. What compounds is: build → try → check → publish → observe → improve, with a ledger and an audit row under every step. Every quarter's engineering budget should widen that loop, not add a vendor.
2. **Phase 1 is a pilot, so engineering's job is reliability and evidence, not features.** The slices for October are already on the board (WS-1, MEM-1, E-1, M-1, POL-1, ACT-1, VER-1). What the board lacks is the platform work that lets those slices be trusted: images off the box, E2E gate, staging, traces. That is the new epic in §7.
3. **Split media from control before scale, not after.** Voice needs its own tier with drain-on-deploy and enforced singletons. Do the cheap part now (role, workers, Redis locks), the expensive part (separate hosts, autoscaling) when minutes justify it.
4. **Price after measuring, but bill correctly now.** The founder's instruction is "keep pricing for last". The costing audit shows four code gaps that are wrong under any price (unbilled chat turns, premium chat inside the allowance, builder not pinned, metering holes). Fix those in Phase 1 as metering work; they do not wait on the number.
5. **Do not build a mode switch, a second builder screen, or a multi-region story yet.** Each was considered and rejected in the docs; the reasoning still holds.

## 5. The plan, by horizon

Horizons follow the board's phases. Each item names the Jira key it lives under; keys starting `ENG-` are new (§7).

### H0 — this week: unblock the Netoyed pilot
- Merge PR #490 once `pytest` finishes green (ui and drift-check are green; `mergeable_state` is `unstable` only because pytest is in progress). Harsha reviews as code owner.
- Nithish flips `TABLE_TOOLS_ENABLED` + `DECIBYL_TOOLS_2026_09_ENABLED` per AWS-0/AWS-1 (KAN-230, KAN-231); confirm `/api/v1/health` shows `table_tools: true`.
- Rotate the SerpApi key now; revoke the `dcb_` key when the build session ends (KAN-233, Highest).
- Harsha T0: test workspace and accounts (KAN-222), then T1 flags-off regression (KAN-223).

### H1 — October to November (Phase 1, KAN-193): pilots run on a platform we can trust
Product slices (already on the board, order unchanged): WS-1 → MEM-1 → E-1 verification → M-1 → POL-1 → ACT-1 → VER-1 → PROC-1 → OPS-1 → DEC-1.

Platform work to run alongside, one engineer-equivalent:
1. **ENG-2 Images built on merge, deploy pulls from GHCR** (closes R1). Re-add the push trigger, flip `IMAGE_SOURCE=pull`, rollback = previous SHA. Two days.
2. **ENG-1 Browser E2E gate** (closes R2). Playwright against compose with fake STT/LLM/TTS and a fake carrier: signup → verify → create bot → Try it → publish → Hear it (WebRTC, fake audio) → billing top-up (Razorpay test webhook). Runs on PRs that touch `ui/` or `routes/`. Two weeks to the first five journeys.
3. **INFRA-A** (KAN-204, closes R9): RDS + PITR restore drill, S3, secrets in SSM, staging stack, CloudWatch alarms. Founder or ops, needs the console.
4. **ENG-6 Security fixes** (closes R4): arq TLS verification, Stack Auth error masking, WS token in header not query, key-rotation runbook. Three days.
5. **ENG-5 Media/control split, cheap half** (halves R3): `DECIBYL_ROLE=control` for arq + singletons with a Redis lock, `media` for uvicorn with `FASTAPI_WORKERS=2`, drain verified in `rolling_update.sh`. One week.
6. **ENG-9 Billing correctness from the costing audit** (closes R6 without deciding price): charge Decibyl chat turns, premium chat always credits, pin the builder model, record Composio / translation / graph-extraction cost lines, Deepgram batch rate row. Feeds M-1 (KAN-205) and is a prerequisite of BILL-1.
7. **OBS-1 trace IDs** (KAN-212, first half only): one `trace_id` per run across events and ledger. The golden sets and staff Quality pages stay in Phase 2.

Exit gate for Phase 1 (unchanged from KAN-193): WS-1 and MEM-1 verified on the pilot workspace. Added: E2E gate green on `main`, images pulled not built, restore drill passed.

### H2 — December to January (Phase 2, KAN-194): self-serve launch
- **BILL-1** (KAN-207) once the founder decides margin SLO, India voice price and premium-voice handling; the tests listed on the issue are the acceptance.
- **ENG-4 Flag hygiene**: every env toggle into the registry, each with an owner and a remove-by date; flags older than two phases are removed or made permanent.
- **ENG-3 UI quality gates**: `npm run lint` in CI, generated-client drift check against the dumped OpenAPI, a data-fetching layer (TanStack Query) introduced page by page starting with billing and the thread.
- **ENG-8 Dead code and hotspots**: delete orphaned `SimpleAgentEditor` / `FlowAgentEditor`, consolidate the five model-config components, split `routes/workflow.py` and `routes/billing_dashboard.py` below 1k lines.
- **OBS-1 second half**: golden sets per pack that block publish, online sampling, staff Quality pages.
- **ENG-10 API/SDK surface**: SDK families per `docs/audits/2026-09-14-docs-and-api-plan.md` (bots, calls, campaigns, numbers, billing), a publish workflow, MCP tool annotations. This is what "Secondary — Indian SaaS embedding voice" buys.
- UI-1/UI-2/UI-3, SIG-1, COPY-1, KNOW-1 as already planned.

### H3 — from February (Phase 3, KAN-195): scale
- **INFRA-B** (KAN-216): dedicated media tier, measured sizing, autoscaling on concurrent calls.
- **ENG-7 Tenant isolation, hard form**: Postgres row-level security on org-scoped tables behind a session variable, after the lint-based guard from H1 has run clean for a phase.
- **ENG-11 Helm chart decision**: either give it CI (kind cluster smoke test, parity with compose) or archive it. It has drifted already (no falkordb, embeddings, sandbox, gotenberg).
- Native Slack/Teams (CH-1/CH-2), graph view, marketplace creators as planned.
- Multi-region and SOC 2 only when a signed enterprise deal needs them.

## 6. How the team works (engineering system)

- **Board rules stay as written on KAN-193**: one issue per slice, "Done when" + tests on the issue, branch = key + slug, PR title starts with the key, every slice behind its own flag, comment when touching a shared file.
- **Definition of done adds three things from H1 onward**: E2E journey green where the slice touches a customer screen; a `trace_id` on any new run kind; a flag entry with owner and remove-by date.
- **Agent tooling installed in this environment** (for Claude sessions on this repo):
  - ECC developer profile (68 agents, 128 skills, 122 rules, no hooks): use `planner` → `tdd-guide` → `fastapi-reviewer` / `react-reviewer` / `security-reviewer` → `e2e-runner`; `/fastapi-review`, `/react-review`, `/security-scan`, `/test-coverage` before each PR.
  - Agency roster (108 agents): `engineering-backend-architect`, `engineering-payments-billing-engineer` for anything under `services/billing/`, `engineering-sre` and `engineering-devops-automator` for ENG-2/ENG-5/INFRA-A, `engineering-privacy-engineer` for DPDP items, `testing-*` for the E2E gate, `product-*` and `project-management-*` for board grooming.
  - Hooks were deliberately not installed in this cloud session; enable them locally with `npx ecc-universal@2.2.2 setup` if wanted.
- **Reviews**: Harsha remains code owner. Claude PRs self-review with the ECC reviewers before requesting him.
- **Release evidence**: the CI run, the E2E report and the `/health` flag map are the release record. Historical pass counts in markdown are not.

## 7. New Jira items (epic KAN-239, label `cto-plan`)

| Key | Title | Horizon | Closes |
|---|---|---|---|
| ENG-1 (KAN-240) | Browser E2E gate on PRs: Playwright + fake providers, five core journeys | H1 | R2 |
| ENG-2 (KAN-241) | Build images on merge; deploy pulls from GHCR; rollback by SHA | H1 | R1 |
| ENG-3 (KAN-242) | UI CI gates: lint, generated-client drift check; TanStack Query on billing and thread | H2 | R8 |
| ENG-4 (KAN-243) | Feature-flag hygiene: single registry, owner and remove-by date, expiry sweep | H2 | R7 |
| ENG-5 (KAN-244) | Media/control split (cheap half): roles, workers, Redis singleton locks, verified drain | H1 | R3 |
| ENG-6 (KAN-245) | Security fixes: arq TLS verification, Stack Auth error masking, WS token header, rotation runbook | H1 | R4 |
| ENG-7 (KAN-246) | Tenant isolation: org-scope lint guard now, Postgres RLS in Phase 3 | H1 → H3 | R5 |
| ENG-8 (KAN-247) | Dead code and hotspots: orphan editors, model-config consolidation, split 2k-line routes | H2 | — |
| ENG-9 (KAN-248) | Billing correctness from the costing audit (independent of the price decision) | H1 | R6 |
| ENG-10 (KAN-249) | API/SDK surface: SDK families, publish workflow, MCP tool annotations | H2 | — |
| ENG-11 (KAN-250) | Helm chart: give it CI parity or archive it | H3 | — |
| ENG-12 (KAN-251) | Documentation consolidation: one STATUS.md, ADRs for settled decisions | H2 | — |

Existing issues these depend on or feed: KAN-204 (INFRA-A), KAN-212 (OBS-1), KAN-205 (M-1), KAN-207 (BILL-1), KAN-233 (key rotation), KAN-216 (INFRA-B), KAN-39 (rollback guard).

## 8. Decisions needed from the founder

1. **Capacity.** Is there one engineer-equivalent for platform work in October alongside the Phase 1 slices, or does the E2E gate wait until the pilot is live? My recommendation: ENG-2 and ENG-6 this week regardless (five days total), ENG-1 starts in week two.
2. **INFRA-A ownership and budget** (₹15–25K/month, needs the AWS console). Without it there is no staging and no restore drill; every flag is tried on production.
3. **Pricing inputs**: the two measurements the audit asks for (TTS characters per minute, cache hit rate) can start now on the pilot workspace. Confirm you want them collected before the price decision.
4. **Helm chart**: keep for self-hosters or archive? It costs review time on every infra change.
5. **Repository visibility**: GitHub still reports the repo as public while the README says private and commercial. Confirm intent.
6. **Harsha's access** (KAN-234): Jira and GitHub now; AWS only if he flips flags. Approve so T0–T6 can start.
