# Launch: 4 October 2026

Decided by the founder on the evening of 30 September. Four days. Everything on the self-serve gate is wanted; here is how that becomes true rather than late.

## The rule

Every story is built on its own branch by its own builder session, in parallel, and merged **behind its flag**. Harsha verifies on a scratch workspace and writes the result on the issue. A flag turns on the morning of 4 October only with a passing check on the issue. Anything not green by 20:00 on 3 October **ships dark**, is listed on the LAUNCH epic with its turn-on date, and the launch proceeds. Nothing reaches an invited user without a check.

## What an invited user sees on 4 October, at minimum

Invite code → verify → the Start screen with one Build card → Hear it → a bot on a number (from a pre-provisioned pool, see W7) or on web chat → the My Decibyl thread with cards and grant scopes → the trial meter → Settings with the rate card and their own keys. Guardrails on the bot page. Projects if W5 lands. WhatsApp if the Meta prerequisites are met (W7); otherwise "coming this month" on the card.

## Workstreams, owners, and what "done" means for each

| W | Story | Board | Builder | Done on 4 Oct means |
|---|---|---|---|---|
| 1 | Rotate the two exposed keys | KAN-233 | Nithish | Old keys refused |
| 1 | Security fixes (TLS, error masking, WS token) | KAN-245 | Claude | Merged, on by default |
| 1 | Images off the production box | KAN-241 | Claude | Not before launch (the box cannot pull); rollback stays rebuild-on-box, so merges are batched and deployed once a day |
| 1 | Monitoring and on-call | LAUNCH-5 | Nithish + Claude | Sentry alerts to a phone, uptime check on /health, one named person on call each day 4–6 Oct |
| 2 | UI-0 cut and rename, tokens | KAN-257 | Claude | Merged; shell on for all; dead code gone |
| 2 | UI-1 seven homes, Projects in rail | KAN-208 | Claude | Behind `shell`; redirects table test green |
| 2 | UI-4 faces | KAN-260 | Claude | Component in roster and header |
| 3 | UI-2 bot page, ten sections | KAN-209 | Claude | Behind flag; Chat/Canvas; Publish with diff |
| 3 | VOICE-1 number flow + corpus | KAN-261 | Claude + Harsha | Flow from the bot page; corpus run on the default stack; live test call answered |
| 3 | CH-0 WhatsApp connect | KAN-258 | Claude | Flow built; on only if W7 prerequisites hold |
| 4 | PLAN-1 trial replaces Free (new plan row + end date + `ALLOWED_PLANS`, not a rename) | KAN-255 | Claude | Invited accounts land on `trial` with an end date on Start; expiry pauses with a card |
| 4 | BYOK-1 keys on Decibyl, builder and Decibyl routines (bot text paths already resolve the vault) | KAN-254 | Claude | Decibyl turn on the account's key; card on a missing key, never a silent platform fallback |
| 4 | POL-2 grant scopes, pause, retire | KAN-259 | Claude | Scopes on cards; ask-first wins; audit row with rule id |
| 4 | OUT-1 per-bot budget (exists behind `BUDGET_POLICIES_ENABLED`: turn on and verify) + outcome definition | KAN-253 | Claude | Budget on the bot page; a hard stop is a card, not an error |
| 5 | PRJ-1 Projects | KAN-266 | Claude | Brief, files, members, checklist on the channel |
| 5 | MKT-1 publish from the superadmin org | KAN-256 | Claude | One pack published from the UI, hireable |
| 5 | ORG-1 light: mission line, goal per bot | KAN-219 | Claude | Mission in Team and Tasks subtitle |
| 6 | Verification pass of the Testing set | KAN-222, KAN-223 | Harsha | Each issue closed or reopened with evidence |
| 6 | E2E journeys | KAN-240 | Claude | Five Playwright specs runnable locally with a recording each on the issue; the CI job the week after |
| 7 | Invites: waitlist, codes, first 20 names | LAUNCH-1 | Nithish | Codes issued; emails ready |
| 7 | Number pool: 10 numbers on the platform account, KYC cleared | LAUNCH-2 | Nithish | A trial account can be given a number without its own KYC |
| 7 | WhatsApp prerequisites: Meta business verification, BSP/Tech Provider status, 3 pre-approved templates | LAUNCH-3 | Nithish | Or CH-0 stays dark with a date |
| 7 | Support and legal: working support link (Chatwoot DNS or a WhatsApp number), privacy notice, AUP, recording disclosure copy | LAUNCH-4 | Nithish | Links resolve; copy in place |
| 7 | Minimal staging box | KAN-204 | Nithish | A second compose stack answering /health, for Harsha's pass |
| 8 | INVITE-1 invite-only signup (codes table, check at signup and Google sign-in, superadmin mint) | KAN-273 | Claude | Behind `INVITE_ONLY_SIGNUP`; a code creates one account; no code, no account |
| 8 | NUM-1 assign a pool number to a trial bot (superadmin re-home route, "Get a number" on the bot page) | KAN-274 | Claude | A fresh trial account is given a pool number and a test call is answered |
| 8 | FLAG-1 flags-only PR first, per-org flag override, flag-flip runbook | KAN-275 | Claude | Merged 1 Oct before any feature PR; a feature can be lit for one org |
| 8 | CI-1 required checks, heads check before deploy, batch deploy, health probes | KAN-276 | Claude + Nithish | A red PR cannot merge; two heads cannot reach the box; /health 503 when the worker dies |

## Day plan

- **1 Oct (T-3):** FLAG-1 (KAN-275) merged first, before anything else, so no feature PR touches the flag files; then KAN-245 (open: PR #493), KAN-273, KAN-255, KAN-257; every other branch open with a draft PR; W7 started; the ruleset on `main` in place (CI-1).
- **2 Oct (T-2):** UI-1, UI-2, UI-4, VOICE-1 flow, NUM-1, CH-0, POL-2, BYOK-1, OUT-1, PRJ-1, MKT-1, ORG-1 light merged behind flags in the serial order on the epic (migration-bearing PRs one at a time); `deploy.yml` on dispatch; one deploy at 16:00; Harsha starts the Testing pass on staging or a scratch workspace.
- **3 Oct (T-1):** fixes only, no new scope; additive-only migrations; one deploy at 16:00, then tag `launch-2026-10-04`; the flag flip and the rollback rehearsed once on staging; each story's live check written on its issue by 20:00; the flag list finalised; dark list written.
- **4 Oct (T-0), 07:45:** flags on (an api restart of 30–60 s, treated as a deploy); 08:00 smoke by Harsha on a fresh invited account (the first-ten-minutes journey, timed); invites go out; on-call all day; no deploys and no flag flips after 10:00 unless a blocker.

## Review of 30 September, evening: what the code says the plan missed

Three read-only reviews (agent runtime, delivery pipeline, product and compliance) ran against the code after the plan above was written. Findings with evidence are on the issues; this is the short form.

**Three things the day-1 journey assumed that do not exist.**

1. **No invite gating.** `POST /signup` is open; `ENABLE_SIGNUP` is all-or-nothing and would refuse invitees too; Google sign-in provisions with no gate. Filed as INVITE-1 (KAN-273), Highest, 1 Oct.
2. **No trial plan.** There is no `trial` code; the fallback is Free with no end date; PlanSection copy says Free keeps bots off the phone. PLAN-1 (KAN-255) is a plan row, an end date on the subscription, a nightly pause, `ALLOWED_PLANS`, and copy, not a rename.
3. **No way to give a trial account a number without its own KYC.** `assert_may_provision` requires carrier-approved KYC and nothing re-homes a platform-owned number to another org. Filed as NUM-1 (KAN-274). If it does not land, the day-1 journey is web chat plus shared outbound caller ID, and pool numbers go on the dark list with a date.

**Three things the plan under-counted.**

4. **Cards do not cover voice or custom HTTP tools.** Voice bots register no card tools; connected-app writes run without a card unless `approve_sends` is set; custom HTTP tools go out with any method. For launch: `approve_sends` on by default for trial accounts, non-GET HTTP tools through the same branch, honest copy on the bot page. Voice-side cards proper are after launch. On KAN-259.
5. **BYOK is narrower than the story said, in the good direction.** Bot text turns already resolve the vault. The gap is Decibyl's own turns, the builder, Decibyl routines, STT and the knowledge graph. One resolver and three call sites. On KAN-254.
6. **Per-bot budgets already exist** behind `BUDGET_POLICIES_ENABLED`; OUT-1 is turn-on-and-verify plus a card on hard stop. Pause does not exist; `workflows.paused_at` and one check at the run-start choke point, on KAN-259.

**Three things the pipeline cannot do as written.**

7. **Merge is unguarded and every merge is a full build on the production box.** No required check; `deploy.yml` fires per push to `main`; rollback is a second full build and refuses once a migration ran; the migration-heads test runs only on the PR's own tree. CI-1 (KAN-276): ruleset, heads check in `ci_deploy.sh`, batch deploy once a day, cache.
8. **A flag flip is an api restart with no per-org scope.** Flags are env vars read at import. FLAG-1 (KAN-275): flags-only PR merged first so feature PRs never touch the flag files; a per-org override so a feature goes on for the NAutomation org and one invited account before everyone; a runbook rehearsed on staging.
9. **No E2E harness exists**, so "journey green in CI" by 3 Oct is not true. KAN-240 descoped to five local specs with recordings; Harsha's manual pass is the gate.

**Compliance, for the record.** Recording and AI disclosure, DND on campaign dials, and the DPA/Terms gate are enforced in code. Not enforced: TRAI predeclaration (flag off until the pool numbers are predeclared), WhatsApp marketing opt-in (no record exists; day 1 is utility templates only, on KAN-258), one-off outbound calls from the bot page skip the DND gate. Support: the Chatwoot widget renders nothing without its build args; the rail needs a plain support entry (LAUNCH-4). Legal: the sign-in footer links to decibyl.ai/terms, which has no in-app route; point it at `/privacy` and `/trust` and publish the Terms.

## What I am not promising

That all nineteen stories are verified by 3 October. That WhatsApp is on if Meta's verification is not done. That images are built off the box before launch. The launch is still a launch if the dark list is short and honest.
