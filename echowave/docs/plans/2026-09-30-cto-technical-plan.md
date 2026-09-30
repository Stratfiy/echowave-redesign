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

**Founder direction, 30 September (after the first draft).** Three sentences, and how the code answers each:

| Direction | State in code | What it changes in this plan |
|---|---|---|
| Decibyl is your assistant with work; it builds or edits the agents that do the work, end to end | The office model is shipped and in Testing (KAN-140); Decibyl proposes cards, `actions.settle` does the doing | Nothing to add to the spine; the E2E journeys (ENG-1) become the proof that it works end to end |
| Outcomes are the result of the product; consumables are API keys | Outcomes exist as post-call actions and a per-bot board; BYOK components carry no vendor line; consumables are metered per vendor unit under M-1 | New story OUT-1 (KAN-253): outcome definitions per bot, cost per outcome with both denominators, a consumables ledger per key. One question for the founder is on the issue |
| The sandbox must work for functions | Own runner built (Step 20), hardened beyond Daytona, wired to Decibyl and hired bots; 21 unit tests on the local path; never verified live; invisible on a Free plan | New task SBX-1 (KAN-252) in Phase 1: live verification on the pilot workspace and a container-path test in CI |

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

## 5. The plan, by horizon (revised 30 September: self-serve first)

### H0 — week 1: unblock and decide
- **Pricing now** (BILL-1, KAN-207): plan ladder, trial length and contents, India voice price, premium-voice handling. The two measurements the costing audit asks for (TTS characters per minute, cache hit rate) start on our own workspace today.
- **Reopen self-serve purchase**: reverse the studio-mode switch (KAN-189/190) so the ladder is listed and checkout works.
- **ENG-2** images off the production box (KAN-241); **ENG-6** security fixes (KAN-245) and key rotation (KAN-233).
- **I7** (KAN-58): Gemini 2.5 Flash-Lite retires 16 October. Hard date.
- Merge PR #490 when green; Netoyed continues only if the founder says so.

### H1 — weeks 2 to 4: verify what is built
- The self-serve set in Testing, verified live on staging or a scratch workspace and closed with evidence: I1 to I7 and I9 to I12 (credits, ladder, markup, packs, metering, KB caps, GST, translation metering, KPI board, USD packs), O1 to O3 (onboarding tranches, referral, promo), P0 office model (KAN-140), Q1/Q2 triggers, connected apps (KAN-142), the four thread bugs (KAN-88 to 91). Harsha's T0/T1 become this pass.
- **PLAN-1** trial replaces Free (KAN-255). **BYOK-1** the account's keys on text paths (KAN-254).
- **ENG-1** the five E2E journeys green in CI (KAN-240), signup → first bot → Try it → publish → top-up above all.
- **SBX-1** sandbox verified on a trial account (KAN-252).

### H2 — weeks 4 to 6: the self-serve surface
- SIG-1 signup and onboarding (KAN-206), UI-1 shell (KAN-208), UI-2 editor (KAN-209), COPY-1 (KAN-213), M1 wording (KAN-74), M5 site claims (KAN-78), lifecycle emails (KAN-126), funnel instrumentation (KAN-75, KAN-122).
- OBS-1 first half, trace IDs (KAN-212). INFRA-A staging, RDS, secrets, restore drill (KAN-204).
- Launch gate: every row above Done, E2E green on `main`, images pulled not built, a restore drill passed, pricing live on the ladder.

### H3 — after launch: pilots and scale
- Pilots (KAN-193): OPS-1 operator sessions and the pilot plan first, then WS-1/MEM-1 live, E-1, M-1, POL-1, ACT-1, VER-1, PROC-1, DEC-1. Each pilot is its own organisation, built inside by the studio.
- OUT-1 outcomes and consumables (KAN-253) once pricing has settled the reading.
- ENG-3, ENG-4, ENG-8, ENG-10, ENG-12 (UI gates, flag hygiene, dead code, SDK, docs); OBS-1 second half; INFRA-B media tier; ENG-7 RLS; CH-1/CH-2, GV-1, KNOW-1, UI-3, S-3/S-4.

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

## 8. Decisions from the founder

**Decided on 30 September**, after the first draft:

| Decision | Consequence | Board item |
|---|---|---|
| Pricing (outcomes vs consumables) is still to be planned | BILL-1 stays the owner; OUT-1 builds the outcome and consumables machinery so it works under either reading | KAN-207, KAN-253 |
| BYOK for models is a requirement | Voice already resolves the account's keys for STT, LLM, TTS, realtime and embeddings; the text paths (Decibyl, builder, routines, channels) do not and run on the platform key. One resolver for text, model choice per surface, no silent fallback to the platform key | KAN-254 (BYOK-1, Phase 1) |
| No Free plan, only a trial | "Free" is the fallback plan for any account without a mandate, the bottom of the limits ladder and the sandbox gate; the onboarding tranches are already trial-kind ledger rows. A time-boxed `trial` replaces it, `expired` keeps read access, pilots get an explicit plan | KAN-255 (PLAN-1, Phase 2 shape, pilot part now) |
| Pilots are built in the pilot's own account, never in ours | Each pilot is its own organisation: its ledger, keys, outcomes and audit log. The studio works inside it through an operator session (OPS-1: time-boxed, logged, admin but never owner). OPS-1 moves from slice 6 to right after WS-1/MEM-1; the staff-granted pilot plan (PLAN-1 item 4) moves into Phase 1. Interim for Netoyed: create the organisation, grant the plan, invite the operators as admin members | KAN-201, KAN-255, KAN-231 |
| Self-serve first, pilots later | Phase order swaps: KAN-194 (self-serve) is Phase 1, KAN-193 (pilots) is Phase 2. Pricing can no longer be last. The self-serve product is largely built and sitting in Testing (40 issues); the gate is verification, trial, BYOK on text, E2E, images off the box, and the surface work (SIG-1, UI-1/2, COPY-1). Self-serve purchase, closed for the studio model, is reopened | KAN-194 comment of 30 Sep carries the ordered gate |

**Still open:**

1. **Capacity.** Is there one engineer-equivalent for platform work in October alongside the Phase 1 slices, or does the E2E gate wait until the pilot is live? My recommendation: ENG-2 and ENG-6 this week regardless (five days total), ENG-1 starts in week two.
2. **INFRA-A ownership and budget** (₹15–25K/month, needs the AWS console). Without it there is no staging and no restore drill; every flag is tried on production.
3. **Pricing inputs**: the two measurements the audit asks for (TTS characters per minute, cache hit rate) can start now on the pilot workspace. Confirm you want them collected before the price decision.
4. **Helm chart**: keep for self-hosters or archive? It costs review time on every infra change.
5. **Repository visibility**: GitHub still reports the repo as public while the README says private and commercial. Confirm intent.
6. **Harsha's access** (KAN-234): Jira and GitHub now; AWS only if he flips flags. Approve so T0–T6 can start.
