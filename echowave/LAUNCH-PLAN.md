# Launch plan: Decibyl, one launch with everything

The single source of truth for the launch build. **Read this first in any new
session**, then the two handoff documents in `handoff/`. Update the status
table at the bottom whenever a stream moves.

## The founder's instructions (7 October 2026)

1. **Nothing from the two handoff documents is skipped.** Everything in
   `handoff/product-engineering-handoff.txt` (sections 1-38) and
   `handoff/screen-design-handoff.txt` (screens 01-45, shared rules, motion,
   contracts, metrics) is in scope. The map below assigns every section and
   every screen to a stream.
2. **All capabilities ship at launch.** No relaunch cadence and no dates: we
   start now and keep going, phase by phase and stream by stream, until every
   item is done and proven. Launch is when phase 3 passes.
3. **Anything Claude adds beyond the documents is a suggestion**, marked as
   such, and does not displace document scope.
4. Capabilities the founder asked for in conversation are in scope too:
   private browser (browser-use), ordering (Swiggy/Zomato), care for older
   people (medicines, scams, simple mode, tech help), learning anything at
   one's own pace with improvement-based suggestions, trading summaries by
   interest, person + business as one with shared agents, knowledge and
   learnings across teammates, "ask it to build or do anything".

Where the documents and later founder decisions differ, the founder's later
decision wins: the brain is **Auto** (Claude Haiku / Sonnet / Opus by kind of
work, Laya in shadow) with **Sarvam for voice**; the documents' "prefer Sarvam
for models" applies to voice.

## What we are building

**One assistant for a person's whole life -- home, work and business -- that
acts on the phone and WhatsApp in their language, teaches them at their own
pace, and always asks before it acts.** Chat and Today are the only two
destinations; Settings and Agents live in the profile menu; a separate,
role-gated staff console runs the operation.

## Rules every stream follows

1. **Behind a switch.** New capability ships off by default behind a flag in
   `api/services/features.py` (+ `api/constants.py` + `DESCRIPTIONS`); the UI
   hides it while off. Production switches on after staging passes.
2. **Ask before acting.** Every send, call, booking, payment, form submit or
   staff mutation goes through an exact preview bound to an immutable payload
   version; runs once (compare-and-swap, `actions.py`); "outcome unknown" is a
   state, never a blind retry.
3. **Scope.** Every read and write is scoped by `organization_id`; personal
   things by member; staff actions by role, target and reason, audited.
4. **Honest states.** Loading, empty, stale, partial and failed are distinct;
   no false empty, no fake success; capability states are available / needs
   setup / disabled by policy / unavailable with a reason.
5. **Never send people to another screen** to finish something.
6. **Tests** that fail on the old code for every fix; arrival tests for every
   feature; privacy tests (another person or workspace cannot see it).
7. **Verify against a running instance** before calling anything done.
8. No model IDs in commits, PR titles or bodies. No new price or positioning
   string without the founder.
9. Shared design rules: `handoff/screen-design-handoff.txt` "Visual system",
   "Layout and responsive behavior", "Interaction rules", "Motion" (M1-M8),
   "Shared state and data contracts". Reuse existing components and tokens.

## How to run and test (local)

- Postgres: `su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D /var/lib/postgresql/decibyl-test/data -l /tmp/pgtest.log start"`
  (if absent in a fresh container, install postgresql-16 with pgvector, or run
  `docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg16`)
- Redis: `redis-server --daemonize yes`
- Backend: `api/.env` style variables; tests with your own database name:
  `DATABASE_URL=postgresql+asyncpg://postgres:<pw>@localhost:5432/test_<stream> venv/bin/python -m pytest api/tests/<files>`
- UI: `cd ui && npx vitest run <paths> && npx tsc --noEmit -p .`
- Format only changed files (ruff check --select I,F401,F821 --fix; ruff
  format; prettier; eslint). The CI drift check runs `scripts/format.sh`.
- New API route used by the UI: `python -m scripts.dump_docs_openapi` then
  `cd ui && npm run generate-client`.
- Migrations chain from the branch's current head; revision ids carry the
  stream key. The integrator adds merge migrations.

## Staging

The CI EC2 (runner `ci`) runs functional staging: `STAGING.md` ("Functional
staging on the CI box"), `.github/workflows/deploy-staging.yml`,
`scripts/staging_check.py`. Founder: DNS, `.env`, keys, GitHub `staging`
environment, test accounts.

## Phases (in order, no dates)

| Phase | Streams | Why in this order |
| --- | --- | --- |
| 1. Foundations | `controls`, `shell` | Everything else builds on the task ledger, quotas, preferences, personal space, event catalogue and the shell |
| 2. Capabilities | `today`, `agents`, `learning`, `voice`, `meetings`, `identity`, `settings`, `support`, `staff`, `ops`, `browser`, `reach`, `care`, `desktop` | Independent once phase 1 has merged; each starts as soon as the one before it is reviewed |
| 3. Integrate and prove | integrator + staging | Merge, resolve, staging checks, launch acceptance (handoff 16, 27, 38) |
| **Launch** | Invite-only | When phase 3 passes: every capability on; anything still waiting on a provider shows an honest "setting up" state, never a fake |

## Coverage map: every handoff section and screen

### Product and engineering handoff (sections 1-38)

| Section | Stream |
| --- | --- |
| 1 Demand validation, 2 Positioning, 13 Research methods, 17 Evidence register, 18 Technical source register | Reference (no code); positioning per founder |
| 3 Existing vs required platform, 4 Launch surfaces, 29 Source check and reuse map | `controls` (capability checklist: source / configuration / tested) |
| 5 One assistant and simple interface, 19 Product direction and navigation, 20 Screen inventory and route contracts | `shell` |
| 6 Five launch agents (Inbox, Research, Follow-up, Learning Guide, Call and Appointment) | `agents` (+ `learning` for Learning Guide data, `voice` for Call and Appointment runtime) |
| 7 Identity, channels and mobile | `identity` (+ `shell` for responsive web, `voice` for mobile voice) |
| 8 Models, context, skills and memory | `settings` (model defaults, skills), `controls` (scoped context) |
| 9 Free beta with operating limits | `controls` |
| 10 Summaries, privacy and reliable actions | `today` (summaries), `controls` (reliable actions) |
| 11 Clean AWS deployment plan, 34 Credentials and routine AWS operations | `ops` |
| 12 Voice latency and quality targets | `voice` |
| 14 Jev, Laya and guardrails | `ops` (shadow evaluation, labels, rollback) |
| 15 Work packets A-H | A `controls`, B `controls`, C `shell`, D `identity`, E `agents`/`today`/`learning`, F `voice`/`shell`, G `ops`, H `ops`/`staff` |
| 16 Launch proof and growth plan | Phase 3 + `staff` (pilot analytics) + `shell` (waitlist/invite) |
| 21 Chat, voice and everyday assistance | `shell` (composer, starters, attach), `voice` |
| 22 Today, reminders and proactive help | `today` |
| 23 Meeting mode and learning journeys | `meetings`, `learning` |
| 24 Settings catalogue for users, 25 Advanced settings and identity states | `settings`, `identity` |
| 26 Dynamic components and accessible behavior | `shell` (shared components), every stream follows |
| 27 Implementation contracts and verification, 38 Implementation acceptance | Phase 3 |
| 28 Claude handoff and release checklist | Phase 3 |
| 30 Settings changes and concrete failure cases | `settings` (+ already-fixed honest states in PR #523) |
| 31 Concrete implementation order (items 1-8) | 1-2 `shell`, 3 `controls`, 4 `today`, 5 `agents`, 6 `voice`/`meetings`, 7 `identity`, 8 `controls`/`staff` |
| 32 Admin console and operating model | `staff` |
| 33 Customer support and requested actions | `support` |
| 35 Observability and privacy | `ops` |
| 36 Event catalogue and repository gaps | `controls` (catalogue), `staff` (analytics) |
| 37 Quality, revenue and decision metrics | `staff` |

### Screen design handoff (screens 01-45)

| Screens | Stream |
| --- | --- |
| 01 Early access and invitation, 02 Language and first task | `shell` |
| 03 Chat start, 04 Conversation and useful result | `shell` |
| 05 Live voice session | `voice` |
| 06 Helper picker and capability detail | `agents` |
| 07 Today, 08 Exact action approval, 09 Task detail and activity, 10 Routine and reminder editor | `today` (08 contract from `controls`) |
| 11 Meeting capture, 12 Meeting record and actions | `meetings` |
| 13 Learning session, 14 Learning progress | `learning` |
| 15 Search and saved items, 16 Memory manager | `settings` |
| 17 Account and settings shell, 18 Personalization, 19 Voice and language, 20 Daily brief settings, 21 Notifications | `settings` (20 with `today`, 21 with `identity`) |
| 22 Connected apps and channel detail, 23 Email identity, 24 Phone and verification | `identity` |
| 25 Privacy and security, 26 Model defaults and overrides, 27 Skills workspace and developer | `settings` |
| 28 Customer help and ticket | `support` |
| 29 Founder overview, 30 Users and access, 31 User and workspace detail | `staff` |
| 32 Support inbox and case, 33 Support action preview and execution | `support` (inside the staff console shell from `staff`) |
| 34 Evaluation runs, 35 Case comparison, 36 Product analytics, 37 Revenue and costs, 38 Ledger and refund | `staff` |
| 39 Operations and delivery, 40 Task trace and voice latency, 41 Incident and runbook, 42 Providers and secrets, 43 Flags, budgets and model policy, 44 Staff roles and audit | `staff` (with `ops` for the backing services) |
| 45 Advanced workflow editor | `shell` (kept reachable for authorized users; read-only step list on phone) |
| Shared: visual system, layout, interaction, motion M1-M8, state contracts, metrics/events, privacy | `shell` builds shared pieces; every stream follows |

### Founder-requested capabilities (from conversation)

| Capability | Stream |
| --- | --- |
| Private browser for Decibyl (browser-use, isolated per person, live view, Take over, approvals) | `browser` |
| Ordering (Zomato now, Swiggy on Builders Club access), outside AI tools from chat | `reach` |
| Older people: simple mode, medicine calls with family alerts, scam check, tech help, family circle | `care` |
| Learning anything at one's pace, improvement-based suggestions | `learning` |
| Trading summaries by interest (information only, no advice) | `agents` (Research helper) |
| Person + business as one; shared agents, knowledge and learnings across teammates | `controls` (personal space), `settings` (sharing), `agents` |
| Ask Decibyl to build or do anything (agents, routines, trackers, pages) | `agents` (describe-it builder) |
| Desktop app for Windows and Mac; work on my computer (Claude computer use, per-app permission, Stop, approvals); local files | `desktop` |
| Missed calls handled, who owes me, end-of-day note | `today` + `agents` (Follow-up, Call and Appointment) |

### Design reference: approvals (founder-supplied, 7 Oct)

From a comparable product's approval screen (Bops; design only, its FSL
license forbids reusing code):

- A pending approval docks directly above the composer until answered.
- One plain sentence: "Decibyl wants to: Pay Acme Print's invoice: ₹4,800".
- One line of exact detail: item, date, account with masked digits.
- Two buttons: "Do it" and "Don't". Run-once, undo window and honest
  after-states stay as in `actions.py`.
- Routines set from chat are confirmed in one sentence ("Will do, every
  Monday at 10."); results read as sentences with numbers, with a small ✓
  chip naming the task that ran.

Applies to `shell` (ActionPreview, Chat) and `today` (screen 08).

### Suggestions from Claude (not required by the documents)

Receipts under every action, "what I learned about you", pattern to routine,
voice note in and actions out, bill and renewal radar, team playbook,
new-hire lesson path, "sent by my assistant". Build where they fall inside a
stream at little cost; never at the expense of document scope.

## Streams

Each stream is a branch `claude/stream-<key>` off `claude/simpler-rail` (or
off the merged phase-1 result for phase 2), one draft PR with base
`claude/simpler-rail`, behind its own flag(s). Read the mapped handoff
sections and screens in full before coding.

### Phase 1

**`controls`** -- handoff 3, 4, 8 (scoped context), 9, 10 (reliable actions),
15 A-B, 29, 31.3, 36 (event catalogue). Capability checklist (source /
configuration / tested). Operational quotas outside billing (per person per
day: model turns, voice minutes, outbound messages, browser minutes; staff can
grant temporary allowances). Task ledger with the full state set (queued,
running, needs input, awaiting approval, scheduled, completed, failed,
cancelled, outcome unknown), approval binding to payload versions,
idempotency keys, stale-event rejection. Personal space: every person has a
personal scope distinct from business workspaces they join; member-owned
preferences (language, timezone, voice, summary) that never change another
member's settings. Event catalogue and envelope (handoff 36; design
"Metrics events and release proof"). Yes / Not quite feedback with reasons,
stored against output, model and task versions.

**`shell`** -- handoff 5, 19, 20, 21, 26, 31.1-2; screens 01-04, 45; shared
components. Waitlist and invitation redemption (01); language, timezone and
first task (02); new users land in Chat; Chat start and conversation per
03-04 (three starters, attach menu: files, voice note, meeting mode, paste
notes; Dictate vs Talk; stream text; New content; Stop; sources panel);
route migration with redirects for old links; mobile per the design (bottom
Chat/Today, profile in header, hide bar while the keyboard is open,
safe-area); shared components (TaskStatus, ActionPreview, SourceCoverage,
ScopedSearch, SettingsSection, ConnectionRow, SaveBar, EmptyState,
ErrorState, MetricDefinition, AuditTimeline, CommandPreview) reusing existing
ones; motion tokens M1-M8 with reduced motion; workflow editor kept reachable.

### Phase 2

**`today`** -- handoff 10, 22, 31.4; screens 07-10, 20 (with `settings`).
Today as the ordered list; exact action approval screen; task detail and
activity; routine and reminder editor (event-linked reminders); one daily
brief with source coverage, delivered in-app, by WhatsApp and push at the
person's time; routines from chat start on after confirm; missed calls
handled and end-of-day note.

**`agents`** -- handoff 6, 31.5; screen 06. The five launch agents as
configurations over the existing runtime (Inbox, Research, Follow-up,
Learning Guide, Call and Appointment) with their acceptance boundaries;
helper picker with capability states; saved research reports with matching
export; "who owes me" in Follow-up; trading summaries by interest in
Research (information only); describe-it builder for agents, routines and
trackers ("build or do anything").

**`learning`** -- handoff 6 (Learning Guide data), 23; screens 13-14.
Learner profile, lessons on any subject, practice and feedback, reviews in
Today, improvement-based suggestions, progress from evaluated practice only,
adults first.

**`voice`** -- handoff 7 (mobile), 12, 15 F, 31.6; screens 05, 19 (with
`settings`). Live voice with Decibyl distinct from dictation; interruption,
mute, captions, reconnect, permission states; Call and Appointment runtime;
"call it for me" with approval and announcement; latency measurement per
handoff 12.

**`meetings`** -- handoff 23, 31.6; screens 11-12. Meeting capture with
consent and a stated audio source, interruption gaps, transcript (Sarvam),
summary / decisions / transcript, individually confirmed actions.

**`identity`** -- handoff 7, 15 D, 25, 31.7; screens 21 (with `settings`),
22-24. Connected apps per person with consent and revocation; channels
(WhatsApp, Telegram, Slack, Teams) with verified capability flags; Decibyl
email identity lifecycle (virtual card "coming soon" row only); phone and
KYC lifecycle with an explained number payment flow; notifications and web
push; unknown WhatsApp senders treated as strangers.

**`settings`** -- handoff 8, 24, 25, 30; screens 15-19, 25-27. Settings shell
per 17 (Personal, Connections, Privacy, Advanced; workspace sections under a
named heading; settings search with everyday synonyms; Save / Discard;
conflict handling); personalization; voice and language; search and saved
items; memory manager with provenance and sharing to the team; privacy and
security (export, deletion, retention, MFA); model defaults showing
inheritance; skills, knowledge, team, developer and compliance.

**`support`** -- handoff 33; screens 28, 32-33. Customer help and tickets with
a data-sharing preview; staff support inbox and case with internal notes;
support action preview and typed, approved, audited execution.

**`staff`** -- handoff 32, 36, 37; screens 29-31, 34-44. Role-gated staff
console with eight destinations, reusing `/superadmin`: founder overview
attention queue, users and invitations, user and workspace detail,
evaluation runs and case comparison, product analytics, revenue and costs,
ledger and refunds, operations, task trace and voice latency, incidents and
runbooks, providers and secrets, flags / budgets / model policy, staff roles
and audit.

**`ops`** -- handoff 11, 14, 15 G-H, 34, 35. Clean AWS deployment, credential
lifecycle and routine AWS operations via typed allowlisted commands;
observability (Sentry, PostHog, infrastructure health) with privacy
exclusions and replay exclusion on sensitive screens; Laya shadow evaluation
with labels and rollback; backup restore drill; capacity review. Claude through
AWS (founder request): `CLAUDE_BACKEND` = anthropic | aws_platform (Claude
Platform on AWS: Anthropic-operated, full API parity, IAM, AWS billing --
recommended) | bedrock (Amazon Bedrock: feature subset), for chat, routing,
the builder and the call pipeline's managed tier; instance roles, no keys.
And an AWS model gateway beyond Claude: Bedrock as a managed provider per
slot -- a fallback brain when Claude fails (Nova Pro or an open-weight model;
Auto says so honestly), an optional cheap tier for labelling, embeddings
(Cohere multilingual or Titan), and optional Nova 2 Sonic for Hindi and
Indian English only. Sarvam stays for Indian-language voice (not on AWS).

**`browser`** -- founder request. browser-use in the sandbox: one isolated
browser per person and task, private addresses blocked, live view and Take
over, cookies only (encrypted, per person), approval before submit / pay /
send / book, step / time / cost limits, page text as data, injection tests.

**`reach`** -- founder request. Outside AI tools (MCP) usable from chat;
Zomato ordering (Swiggy when access arrives) through approvals; price and
coupon comparison only across officially connected apps.

**`desktop`** -- founder request. Electron app for Windows and macOS loading
the web app: tray, notifications, global shortcut to ask or talk, start at
login, deep links, auto-update and signing (founder provides Apple Developer
ID and a Windows code-signing certificate). Work on my computer with Claude
computer use behind `desktop_computer_use`: per-app allow list, visible
working bar with Stop, approvals for send/pay/delete/submit, never password
fields, limits, receipts. Local files and an opt-in watched folder.

**`care`** -- founder request. Simple mode (large text, voice first, one thing
at a time); medicine calls in the parent's language with family alerts;
scam check; step-by-step tech help; family circle with consent.

### Phase 3

Integrator: merge every stream into `claude/simpler-rail`, merge migrations,
resolve overlaps, full backend and UI suites, staging deploy and
`staging_check.py`, the handoff's launch acceptance (sections 16, 27, 38 and
the design's "Claude execution brief"), screenshots at 360 / 390 / 768 /
1024 / 1440, and the rollback notes per flag.

## Decisions still open (founder)

- [ ] One-line positioning
- [ ] Usage limit numbers for the free beta
- [ ] Who pays for numbers in the beta
- [ ] Business model and prices after the beta
- [ ] Apply to Swiggy Builders Club
- [ ] Staging: DNS, keys, GitHub environment, test accounts

## Done so far (PR #523, branch `claude/simpler-rail`)

- Chat and Today navigation; phone bottom bar; Settings and Agents in the profile menu
- Auto brain with Laya in shadow; Claude-on-calls fixes; Models page per workspace
- Honest states (memory, phone numbers, models); grouped Settings on phones
- Recents; Activity usage per agent and model; Sentry
- Fixes: invites (500 on every call), per-thread reply drafts, run-once approvals
- Staging workflow and `scripts/staging_check.py`

## Running cloud sessions

Each stream runs as its own Claude Code cloud session (one machine each) and
opens a draft PR into `claude/simpler-rail`. The parent session checks them
hourly, reviews and merges each PR, and starts the next streams.

| Stream | Session |
| --- | --- |
| controls | session_019YoNV9e1VRicijtBy3dqkK |
| shell | session_017pePFdhf7extTGvF4vmJTU |
| ops | session_01KBVvNANs8kwyrHBMKDox7d |
| aws-gateway | session_01K3AYRzykvKavBj3jXmZ454 |
| browser | session_01Me1mkRqDovMsuhnAMz4NUK |
| desktop | session_01HnRghELvdY4pxEZobCWn4Z |

## Status

| Stream | Phase | Branch | PR | Built | Tests | Staging |
| --- | --- | --- | --- | --- | --- | --- |
| controls | 1 | claude/stream-controls | | | | |
| shell | 1 | claude/stream-shell | | | | |
| today | 2 | claude/stream-today | | | | |
| agents | 2 | claude/stream-agents | | | | |
| learning | 2 | claude/stream-learning | see PR | yes (`LEARNING.md`) | see PR | |
| voice | 2 | claude/stream-voice | | | | |
| meetings | 2 | claude/stream-meetings | | | | |
| identity | 2 | claude/stream-identity | | | | |
| settings | 2 | claude/stream-settings | | | | |
| support | 2 | claude/stream-support | | | | |
| staff | 2 | claude/stream-staff | | | | |
| ops | 2 | claude/stream-ops | #524 | yes | see PR | |
| aws-gateway | 2 | claude/stream-aws-gateway | | | | |
| browser | 2 | claude/stream-browser | | | | |
| reach | 2 | claude/stream-reach | | | | |
| care | 2 | claude/stream-care | | | | |
| desktop | 2 | claude/stream-desktop | | | | |
