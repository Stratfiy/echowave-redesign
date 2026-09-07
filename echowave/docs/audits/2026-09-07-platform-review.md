# Decibyl platform review

Reviewed 7 September 2026. Repository: `Stratfiy/echowave-redesign`, baseline commit `4a1eeaf60c06d3b5ca4a8955d7e75cdbc43bc7fb`.

**Direction: preserve the complete platform and build toward Vapi-level capability. No feature removals are proposed.** Make advanced capabilities easier to discover and prove, while keeping a guided path for new customers.

**Audit status: code review and public UI review completed within the scope below; authenticated end-to-end verification is blocked on test-account access.** This is not a production-readiness certification. The user supplied the live URL, but no account credentials. Docker was unavailable locally, so the PostgreSQL/Redis/provider-backed backend suite was not executed. Competitor dashboards were not accessed behind authentication; comparisons use their official public product pages and documentation, not their private implementation.

## What was actually checked

| Check | Result |
|---|---|
| Repository identification | Production application is `echowave/ui` + `echowave/api`; top-level `frontend` redirects to an older preview and `backend/server.py` is a scaffold |
| Baseline frontend tests | 222 passed, 21 files |
| Frontend tests after focused auth fix | 227 passed, 22 files, including five new regression tests |
| TypeScript | Passed before and after the fix |
| Full frontend lint | Passed; `next lint` emits a deprecation notice |
| Baseline production build | Passed; Next.js 15.5.20 resolved from lockfile |
| Final changed-code build | Passed with filesystem access required by Next.js workspace tracing |
| Live public app | Root redirects to login; login/signup render; empty login submission focuses required email field |
| Mobile | Login/signup visually checked at 375 × 812 after hydration; readable forms, no observed clipping in captured views |
| Desktop | Login checked at 1280 × 720; clear hierarchy and restrained form layout |
| Runtime configuration | Reproduced failed support-widget DNS request and unavailable Google-auth probe |
| Deployment provenance | GitHub records a successful deployment of the reviewed baseline commit; this is deployment evidence, not an E2E test |
| Backend integration tests | Not run: required services are unavailable; documentation's historical pass counts are not results from this audit |
| Real voice calls / campaigns / billing | Not exercised; require an isolated test organization, controlled numbers and test credits |

Evidence: [baseline deployment](https://github.com/Stratfiy/echowave-redesign/actions/runs/34135126896), local logs under `.audit`, and screenshots in this report's `screenshots` directory. Source review sampled architecture, auth, generated-client handling, agent creation, versioning, squad assembly, model failover, billing reservations/payments, analytics, integration catalogue and CI. It did not inspect every line of all 2,206 tracked files.

## Fixes delivered alongside the audit

Neither branch is merged or deployed. [Draft PR #122](https://github.com/Stratfiy/echowave-redesign/pull/122) fixes auth validation errors and failed session persistence. The separate `codex/sidebar-billing-audit` branch starts from the reviewed main baseline and carries the sidebar/billing fixes and this audit.

- **Sidebar:** Overview and Billing stay at the top. Build, Deploy, Monitor, Developers and Workspace organize the complete platform. Collapsible sections remember browser preferences and reopen for their active destination. All 19 existing customer destinations remain; Settings and Missed calls gain direct entries. Related integration/tool/telephony pages select their parent, and nested staff pages select only their most specific destination. The collapsed rail scrolls to all 21 customer links.
- **Payment confirmation:** Match the exact server-recorded order, rather than any increase in wallet balance. Read payment status before the balance to avoid pairing a newly paid order with a pre-webhook balance. Concurrent call spending cannot mask a paid order, and unrelated top-ups cannot falsely confirm it. Polling is bounded and stops on unmount; it does not reset the billing profile being edited.
- **Billing recovery:** Share checkout script loading, time out hanging loads and remove failed scripts for retry. Recover from initial network failure with an explicit retry action instead of a permanent skeleton or fake zero balance. Expose profile/document read errors and catch rejected save, email and download requests.
- **Repository entry point:** Replace the placeholder root README with links to the real application, contributor guide, SDKs and this audit. Retain the legacy preview/scaffold files.

**Verification:** The sidebar/billing branch passes 242 frontend tests across 25 files, including 20 new tests. The separate auth branch passed 227 tests, including five new tests. These totals include the common baseline and must not be added together. TypeScript, lint and production builds were checked.

Local browser checks use the actual sidebar component and CSS with synthetic account context in a Vite harness, not production authentication. Desktop 1280 × 720 and mobile 375 × 812 were visually inspected. Checks cover expanding Developers, selecting Integrations on a tool detail route, the mobile drawer and scrollable collapsed rail. The harness uses a system-font fallback and does not validate the Next.js authentication shell or live API.

Sidebar screenshots are included in `screenshots/sidebar-organized-desktop.png`, `screenshots/sidebar-organized-rail.png`, and `screenshots/sidebar-organized-mobile.png`. Public-page screenshots and full command logs remain in the local `audit-results` / `.audit` artifact directories.

## Executive assessment

This is substantially more than a redesigned dashboard. The repo already contains agent templates, conversational creation, a flow editor, audio/text testing, versioned workflows, squad assembly, managed/BYOK models, speech-provider backups, campaigns, contacts, telephony, recordings, billing, privacy controls, SDKs and MCP.

The strongest next investment is **turning these subsystems into a measurable build → test → publish → observe → improve loop**. The risk is mistaking an implemented subsystem for a customer-verifiable capability. Keep the breadth; add evidence, failure recovery, API consistency and automated journeys around it.

## Competitor comparison

These are current vendor claims and visible product affordances, not independently benchmarked latency, reliability or compliance results.

| Platform | What its public product does particularly well | What Decibyl should match or improve |
|---|---|---|
| [Vapi](https://vapi.ai/platform) | Presents a full developer platform: model flexibility, tools/MCP, telephony controls, automated testing, A/B experiments, squads, failover and observability | Make all these capabilities discoverable through a coherent agent lifecycle, with API/UI parity and repeatable tests |
| [Bolna](https://www.bolna.ai/agent-studio) | Clear agent-creation sequence: identity, conversation, closing; live examples and explicit assessment before launch | Preserve our conversational and manual creation paths; add visible readiness and evaluation results to each generated agent |
| [Bolna public product](https://www.bolna.ai/) | Industry demos with playback/clone actions, Indian-language messaging and customer outcome stories | Connect a real demo to its template and working account flow; measure demo → created agent → successful test conversion |
| [Gnani](https://www.gnani.ai/) | Own-model positioning, interactive STT/TTS exploration, enterprise agents, analytics, assist and biometrics | Offer transparent provider/language benchmarks and enterprise deployment evidence; do not confuse using a provider with owning its models |
| [Decibyl](https://www.decibyl.ai/) | Distinctive visual identity, strong business scenarios, Indian-language emphasis, broad platform underneath | Add immediate product proof and continuity into the app; retain developer controls and all supported use cases |

**Pricing:** Vapi currently lists $0.05/minute hosting plus model-provider costs, with BYOK support. Therefore, provider costs passed through at cost are not a unique Decibyl differentiator. Bolna publishes both usage-based credits and volume plans; its displayed pilot uses 30-second pulses. Compare actual total call cost for the same language, model stack, carrier, duration and optional services. Do not compare a bundled rate to a platform-only fee. [Vapi pricing](https://vapi.ai/pricing), [Bolna pricing](https://www.bolna.ai/pricing).

## Vapi-level capability map

“Present” below means located in source, not confirmed by a live call.

| Capability | Decibyl evidence | Next increment, preserving existing features |
|---|---|---|
| Agent creation | `ui/src/app/workflow/create`, `components/agent-builder`, `api/services/agent_templates` | Persistent builder session, recoverable retries, template preview with a sample call |
| Visual and simple editing | Workflow editor and `SimpleAgentEditor.tsx` | Keep both; preserve selection, deep links and unsaved-change protection across views |
| Versioning / release | Draft and published definitions in `api/routes/workflow.py` | Visible version diff, last passing evaluation, explicit promotion and rollback workflow |
| Multi-agent squads | `api/services/workflow/squad.py` and `squad_loader.py` | First-class squad view, specialist version visibility and handoff traces; test cycles and missing specialists |
| Model flexibility | Managed/BYOK configuration and model catalogue | Comparable cost, supported languages, region and quality evidence in the picker |
| Provider resilience | STT/TTS ServiceSwitcher backups in `service_factory.py` | Expose/test failover events, limits and language compatibility; verify LLM/realtime behavior separately |
| Calls, phone numbers, SIP | Telephony providers, managed/verified numbers, public agent routes | One readiness checklist tied to real provider state; verified controlled-number tests |
| Campaigns | Campaign routes/services and management UI | Preview audience, cost/concurrency limits and suppression counts; prove pause/resume/retry behavior |
| Browser audio and chat | WebRTC, text-chat and embed routes | Mic-denied/offline recovery, transcript replay, latency and cost shown beside test results |
| Knowledge base | Files UI, knowledge-base routes and integration tests | Source citations, ingestion diagnostics and retrieval test panel; retain existing retrieval |
| Tools/integrations | Catalogue, calendar, messaging and webhook mechanisms | Native CRM adapters alongside generic webhooks, test connection, mapping, delivery log and replay |
| SDK/API/MCP | Generated UI client, Python/TS SDKs, MCP server | Contract tests and equivalent tutorial journeys for API and UI |
| Observability | Runs, outcomes, analytics, detailed staff latency screen | Tenant-scoped customer latency view and linked transcript → tool → provider → cost timeline |
| Quality / evaluation | Existing post-call QA and test-chat machinery | Reusable pre-release scenario suites, repeated runs and failure comparison; no dedicated customer evaluation workspace was found in reviewed routes |
| A/B experiments | Not found in reviewed route/UI inventory | Sticky traffic allocation, pinned versions, outcome metrics, minimum sample sizes and rollback |
| Environments / access | Sandbox keys, organizations, admin controls | Explicit dev/staging/production resources, permissions and promotion records; a sandbox key is not complete environment isolation |
| Billing / governance | Ledger reservations, payment webhooks, privacy, DNC and verification | Failure-injection tests and visible operational readiness; retain all controls |

Vapi explicitly advertises automated testing, experiments, squads and model fallbacks on its [platform page](https://vapi.ai/platform), and publishes [versioning](https://docs.vapi.ai/assistants/versioning) and [environment guidance](https://docs.vapi.ai/documentation/best-practices/enterprise-environments-dev-uat-prod). The recommendation is to close workflow depth and verification gaps, not to assume Decibyl lacks entire subsystems that are already present.

## Findings and changes

### 1. Authentication loses useful error state — fixed in draft PR #122

**P1, source + regression-tested.** Login and signup cast FastAPI `detail` to a string and pass it to a toast. FastAPI validation failures can contain arrays of objects. The existing `detailFromError` helper handles those correctly, but these forms did not use it.

Both forms also ignored the HTTP result from `/api/auth/session` and redirected even when cookie persistence failed. That can produce a sign-in loop after the backend accepts the credentials.

The focused fix uses the existing error normalizer and requires a successful session response before redirecting. Signup explicitly says the account exists and directs the user to sign in if session persistence fails. Five tests cover validation arrays, rejected session responses for both forms, and preservation of the MFA challenge.

Files: `echowave/ui/src/app/auth/login/LoginForm.tsx:64`, `echowave/ui/src/app/auth/signup/page.tsx:50`, `echowave/ui/src/app/auth/authForms.test.tsx`.

### 2. Password recovery is missing from local auth

**P1, observed public UI + source review.** The login page has email/password and signup, but no recovery link. The reviewed local-auth routes include login, signup, MFA, Google OAuth and email verification; no password-reset route was found.

Add recovery with a short-lived single-use token, generic account-existence response, rate limits and session invalidation. Include lost-MFA recovery as a separately designed path. Test expiration, replay and unknown accounts. Do not put a nonfunctional “Forgot password” link on the page.

Evidence: `screenshots/login-desktop.png`, `screenshots/login-mobile-ready.png`; `api/routes/auth.py` and `ui/src/app/auth/login/LoginForm.tsx`.

### 3. Configured support widget does not load

**P2, live and reproduced on reload.** The browser requests `https://chat.decibyl.com/packs/js/sdk.js` and gets `ERR_NAME_NOT_RESOLVED`. This removes a configured support channel. The widget URL is supplied through `NEXT_PUBLIC_CHATWOOT_URL`, not hardcoded in the component.

Verify the intended DNS/host and deployed build-time value, then test the support flow. Provide an ordinary support link if the widget cannot load. Do not guess a replacement domain and deploy it.

Evidence: `login-recheck.txt`, `screenshots/login-recheck.png`, `ui/src/components/ChatwootWidget.tsx:26`.

### 4. The first-agent conversation is easy to lose

**P2, source-confirmed behavior; not reproduced in an authenticated browser.** `AgentBuilderPanel` stores turns/history only in component state. It clears the draft and adds the visible user turn before a request succeeds. Failed requests leave visible dialogue and accepted server history out of step; refresh loses the whole conversation.

Persist a tenant/user-scoped builder session with retention controls, restore the draft on failure and show Retry on the failed turn. Use an idempotency identifier when a retry may create an agent. A timeout after creation must not create duplicates on retry.

Evidence: `ui/src/components/agent-builder/AgentBuilderPanel.tsx:69`, `:140`, `:166`.

### 5. Builder availability failures can be misdiagnosed

**P2, source-confirmed error handling.** A failed configuration request becomes `available: false` with no reason. The UI then says the builder is not switched on for the deployment, even if the real cause was a temporary request/auth failure.

Represent loading, explicitly disabled, quota exhausted and failed request separately. Preserve the manual editor and add retry for transient failure. This adds resilience without removing a feature.

Evidence: `ui/src/components/agent-builder/AgentBuilderPanel.tsx:104`, `:209`.

### 6. Customer diagnostics lag the existing staff tooling

**P1 product improvement, based on route/source inventory.** Detailed perceived latency, TTFT, TTFB and p50/p90/p95/p99 are implemented in the staff latency screen. Customer analytics prominently covers counts, costs, answer rates and distributions.

Add an organization-scoped latency and failure view using the existing measurements. Show the chosen models, language, cold/warm first turn, failover event and recording/transcript timestamps. Never expose the cross-tenant staff endpoint to customers.

Evidence: `ui/src/app/superadmin/billing/latency/page.tsx`, `ui/src/app/analytics/page.tsx`.

### 7. Integration breadth needs delivery-level proof

**P1 platform improvement.** Zoho, HubSpot and Salesforce entries currently route through a generic webhook mechanism in the catalogue. That is a useful extensibility feature; it is not equivalent to a native authenticated connector with mappings and delivery diagnostics.

Keep the generic webhook. Add native adapters incrementally, with connection health, field mapping, retries, idempotency and delivery replay. Clearly distinguish native, webhook, beta and requested integrations. Align those labels between the website and app.

Evidence: `ui/src/constants/integrationCatalogue.ts:226` onward; the [public website](https://www.decibyl.ai/) also labels integration maturity.

### 8. “Cost per successful outcome” needs an additional denominator

**P2 analytical improvement, not an arithmetic bug in the existing table.** `cost_by_outcome` correctly computes average charge within each disposition. That is not the same as total campaign spend divided by successful outcomes, which includes the cost of unsuccessful calls.

Keep the current breakdown and add an explicitly named acquisition/booking metric. Example: 100 calls cost ₹500; 10 book. The campaign cost per booking is ₹50 even if the ten booked calls cost only ₹50 in total and average ₹5 each. Let organizations define success dispositions and show both denominators.

Evidence: `api/services/reports/org_metrics.py:83`; `ui/src/components/OutcomesSummary.tsx` already labels the current column “Cost per call.”

### 9. Public pages should expose more of the platform's proof

**P2 design recommendation.** The marketing hero has a strong illustrated identity and a seven-part story; the desktop sign-in page is clear but visually more utilitarian. Preserve the identity and the whole product range. Add a prominent “Hear an agent” / “Explore the platform” pair, an actual builder/run-detail preview and a direct path from the demo to its template. Keep the story optional and accessible with reduced motion.

The login form should add password visibility, explicit autocomplete metadata and a support/recovery route. Mobile layout itself did not show a defect in the views tested. Authenticated dashboard layout still requires a logged-in review.

Evidence: `screenshots/decibyl-marketing.png`, `login-desktop.png`, `login-mobile-ready.png`; compare `vapi-platform.png` and `bolna-studio.png`. Do not interpret loading-state screenshots as broken final layouts.

### 10. Browser journeys are a missing release gate in the checked CI

**P1 engineering improvement.** Root CI runs backend tests, drift checks, UI tests/typecheck/build. No browser E2E job was found in those workflows. The substantial unit/integration suite should be retained and complemented with real user journeys.

Add disposable Postgres/pgvector + Redis + object storage and deterministic fake model/telephony adapters for pull-request tests. Separately run controlled real-provider checks in staging with strict usage limits. Unit tests passing do not prove microphone permissions, cookie persistence, webhook reachability or audio quality.

### 11. Sidebar organization and route state — fixed on the sidebar/billing branch

**P2, source, component tests and local browser.** Recordings and developer access were mixed into Build, while billing/compliance were buried in a long Manage list. Related routes lost selection, prefix matching could select multiple staff links, and hidden overflow made lower rail icons unreachable. The changes above preserve all destinations and role gates. Tests cover preferences, active-group reopening, route boundaries, aliases and collapsed access. Production account navigation remains an authenticated E2E check.

### 12. Payment confirmation watches the wrong signal — fixed on the sidebar/billing branch

**P1, source and mocked component tests.** The previous callback waited for a wallet increase. An unrelated top-up could falsely confirm the current order, while concurrent calls could consume the new credit and leave an already paid order looking pending. The client now uses this exact order's server status. Tests cover both races, gross checkout amount including tax, exact order ID, failure and timeout. The checkout callback never credits money locally; only the server webhook does.

**Still unverified:** real Razorpay test-mode capture, webhook delivery/signature configuration, duplicate deliveries against PostgreSQL, tax PDF/email delivery, refunds/chargebacks, subscriptions/mandates and concurrent call settlement. These remain required before declaring billing verified end to end.

### 13. Billing network failures can trap the customer — fixed on the sidebar/billing branch

**P1/P2, source and regression tests.** Rejected initial requests left loading unfinished, failed reads could look like empty data, and SDK retries listened for an event that had already fired. The changes provide retryable errors, bounded SDK loading and cleanup. Profile/document actions handle rejected requests.

### Operational facts to resolve

- GitHub reports this repository as **public**, while `echowave/README.md` says it is private and commercial. Confirm that visibility is intentional. No visibility or licensing changes were made; this review did not audit Git history for secrets.
- The placeholder root README is replaced on the sidebar/billing branch with an accurate application entry point. Preview scaffolding is retained and identified.
- `DEVELOPING.md` and `KNOWN_ISSUES.md` contain different historical backend pass counts. Generate current validation evidence from CI rather than using either as a release claim.
- Next.js inferred a parent workspace root from an unrelated lockfile during this nested audit checkout. A restricted build failed on parent-directory access; the baseline build with appropriate access passed. Consider an explicit tracing root after verifying Docker packaging. This is not evidence that the live deployment is broken.
- Google sign-in's 503 probe is intentionally hidden by the UI when unconfigured. The anonymous session 401 is expected. Neither should be reported as a failed login attempt by a real user.

## Additive delivery roadmap

| Order | Workstream | Acceptance criterion |
|---|---|---|
| P0 | Authentication resilience and recovery; support configuration; repository-visibility decision | No silent cookie failure; recovery works; support opens; visibility matches owner intent |
| P1 | Evaluation workspace | Stored scenarios, repeated runs, pass/fail reasoning and pinned agent/model versions; manual override of release gate is recorded |
| P1 | Customer observability | Trace one failed call from recording to transcript, tool request, provider event, outcome and cost without staff intervention |
| P1 | Environment and release controls | Test changes cannot affect live resources; promote a passing version; rollback a controlled release |
| P1 | Durable builder sessions and full-feature UI | Resume after refresh, retry without duplicate agents, preserve Simple/Advanced editors and all configuration options |
| P1 | Browser E2E CI | Core build/test/publish/run/review journeys run on each relevant PR with reproducible fixtures |
| P2 | Native CRM connectors + webhook delivery centre | Mapped updates, retries, replay and duplicate suppression verified against connector sandboxes |
| P2 | Experiments | Version-pinned traffic splits; comparable cohorts; success, cost and latency metrics; emergency rollback |
| P2 | Squad and failover experience | Specialist handoff trace, language-compatible backups, forced provider failures and correct final billing |
| P2 | Outcome economics | Both average per-call cost and total spend per successful result, with documented denominators |
| P3 | Enterprise platform depth | Organization-scoped SSO/RBAC/environment controls, audit export and deployment evidence, prioritized against actual customer requirements |

This ordering sequences delivery; it does not cut features or restrict Decibyl to one industry.

## End-to-end completion matrix

| Journey | Required checks | This audit |
|---|---|---|
| Discover → signup page | CTA destination, responsive form, empty validation | Public pages inspected; account creation not submitted |
| Sign in / MFA / recovery | Valid/invalid credentials, MFA, expired session, recovery | Empty validation observed; five mocked regression tests; authenticated flow blocked |
| Create agent | Templates + conversation + manual flow, save/reload, duplicated request | Source reviewed; live flow blocked |
| Configure model | Managed/BYOK, missing key, unsupported language, disabled provider | Source reviewed; live/provider flow blocked |
| Knowledge base | Upload, ingest status, cited answer, no-answer behavior, cross-org denial | Source inventory; service test blocked |
| Test chat/audio | Multi-turn, edit/replay, interruptions, mic denial, disconnect | Blocked on authenticated test environment |
| Publish and rollback | Draft isolation, invalid graph, version pin, rollback | Source reviewed; execution blocked |
| Squads | Handoff, context preservation, invalid/circular/cross-org target | Source reviewed; execution blocked |
| Inbound/outbound | Controlled numbers, human transfer, hangup, voicemail, transcript | Not run on live customer resources |
| Campaign | CSV validation, DNC, duplicate prevention, rate/concurrency cap, pause/resume | Not run on live customer resources |
| Billing | Verified payment, duplicate webhook, concurrent reservations, final settlement | Client fixes and mocked regression tests pass; real payment and database-backed checks remain blocked |
| Integrations | Expired OAuth, tool timeout, 429/500, webhook retry/replay | Not executed |
| API/SDK/MCP | Equivalent agent lifecycle, sandbox restrictions, tenant isolation | Source inventory; not executed end to end |
| Observability | Accurate status/cost/latency, filters, export, failure correlation | Source reviewed; authenticated visual validation blocked |
| Permissions/privacy | Member/admin separation, cross-org resources, erasure, retention | Source review only; not a security certification |
| Load and speech quality | Per-language latency distributions, noise/code-switching, controlled concurrency | Not measured; competitor latency claims not used as measured baselines |

For voice qualification, use a fixed corpus across English, Hindi, Hinglish, Tamil and Telugu initially, then expand to every advertised language. Include names, addresses, numbers, noise, silence, interruptions and tool errors. Record task success, transcript accuracy, handoff correctness, p50/p95/p99 response latency and cost per successful result. Report first-turn and subsequent-turn latency separately. Define targets after measuring each supported stack, not from a competitor's headline.

**To complete the blocked rows:** provide an isolated test login and controlled provider/telephony/payment environment. The live URL alone cannot support a complete authenticated E2E verdict.
