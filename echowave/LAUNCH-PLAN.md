# Launch plan: Decibyl, invite-only, 28 October 2026

The single source of truth for the launch build. **Read this first in any new
session.** Update the status table at the bottom whenever a stream moves.

Companion documents (Claude Docs, private to the founder until shared):
positioning and moat, the built-vs-not inventory, the product plan, the
delivery plan. This file is the engineering copy of the last two.

## What we are building

**One assistant for a person's whole life -- home, work and business -- that
acts on the phone and WhatsApp in their language, teaches them at their own
pace, and always asks before it acts.**

The one reason to choose it over ChatGPT, Gemini, Grok or Meta AI: *they talk
to you; Decibyl works for you.* It has its own number, so it calls, answers and
follows up on WhatsApp for your home and your business, remembers both, and
teaches you as it goes.

- **Shape:** Chat and Today are the only two destinations. Settings and Agents
  live in the profile menu. Phones get a bottom bar (Chat, Today, Menu).
- **Brain:** Auto (Claude Haiku / Sonnet / Opus by kind of work; Laya routes
  in shadow until it beats the rules). Voice: Sarvam for STT and TTS.
- **Launch groups:** owner-operators first; families with an older parent in
  relaunch 2. Learners, professionals, traders after.
- **Free beta** with operating limits (usage caps per person per day).

## Rules every stream follows

1. **Behind a switch.** Every new capability ships off by default behind a
   flag in `api/services/features.py` (+ `api/constants.py`), and is switched
   on in production only after it passes on staging.
2. **Ask before acting.** Any send, call, booking, payment or form submit goes
   through an action card (`api/services/workflow/actions.py`): exact preview,
   confirm, runs once (compare-and-swap; see `test_an_approved_action_runs_once.py`).
3. **Personal stays personal.** Personal memory and private threads are per
   person; nothing reaches the workspace unless the person shares it.
4. **Say what is true.** No false empty states, no fake success; a brief says
   which sources it checked; "I can't do that yet" over a guess.
5. **Never send people to another screen.** Offer the fix in the thread.
6. **Tests that fail on the old code** for every bug fix; arrival tests for
   every feature (what must appear, not only what must not).
7. **Verify against a running instance** before calling anything done.
8. No model IDs in commit messages, PR titles or bodies.
9. No new plan, price or positioning string without the founder.

## How to run and test (local)

- Postgres: `su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D /var/lib/postgresql/decibyl-test/data -l /tmp/pgtest.log start"`
- Redis: `redis-server --daemonize yes`
- Backend tests: `DATABASE_URL=postgresql+asyncpg://postgres:<pw>@localhost:5432/test_<stream> REDIS_URL=redis://localhost:6379/<n> venv/bin/python -m pytest api/tests/<files>`
  (each stream uses its own database name so parallel runs do not collide;
  the conftest creates it and runs migrations).
- UI: `cd ui && npx vitest run <paths> && npx tsc --noEmit -p .`
- Format before commit: `scripts/format.sh` (ruff + prettier/eslint).
- New API route used by the UI: `python -m scripts.dump_docs_openapi` then
  `cd ui && npm run generate-client`.
- Migrations chain from the current head; name the revision with the stream
  key (e.g. `202610081000brief`). Parallel streams will create multiple heads;
  the integrator adds one merge migration.

## Staging

The CI EC2 (runner label `ci`) also runs functional staging. Setup and the
check are in `STAGING.md` ("Functional staging on the CI box"),
`.github/workflows/deploy-staging.yml` and `scripts/staging_check.py`.
Founder to do: DNS, `.env`, keys, GitHub `staging` environment and test
accounts. Nothing is switched on in production until it passes there.

## Timeline

| When | What |
| --- | --- |
| 7-13 Oct | Week 1: foundation stream; all build streams start |
| 14-20 Oct | Week 2: daily loop; streams land behind switches |
| 21-27 Oct | Week 3: owner set; staging passes; fix what testers hit |
| **28 Oct** | **Invite-only launch** (owners + personal-assistant users) |
| 11 Nov | Relaunch 1: push, receipts, what-I-learned, pattern-to-routine, voice notes |
| 25 Nov | Relaunch 2: families |
| 9 Dec | Relaunch 3: learning on any subject |
| 23 Dec | Relaunch 4: live voice, call-it-for-me, private browser |
| Jan | Relaunch 5: ordering, meeting capture, team playbook, personal space split |

All streams are built now, in parallel; the dates are when each is switched on
for users, after staging.

## Streams

Each stream is a branch `claude/stream-<key>` off `claude/simpler-rail`, a
separate PR, behind its own flag. Scope, then "done when".

### 1. `foundation` -- first run, limits, ratings, analytics, hiding
- New users land in Chat (`/overview`), not the build-an-agent flow
  (`ui/src/lib/utils.ts` getRedirectUrl); first run asks language and
  timezone, then one question.
- Usage limits on free mode: per person per day for model turns, voice
  minutes and outbound messages (`api/services/quota_service.py`,
  `billing/free_mode.py`); a clear message in the thread when reached.
- Yes / Not quite under every Decibyl reply, stored with the turn.
- Activation events: sign-up, first useful outcome, useful outcome by kind,
  approval outcome, brief opened (`api/services/posthog_client.py`).
- Hide back-office pages from customers (campaigns, missed calls, review,
  analytics, audio clips, widget, billing while free): reachable under an
  agent once it has a number.
- Done when: a new account reaches a useful answer in 2 minutes; limits stop
  a run cleanly; ratings and events are recorded.

### 2. `daily` -- the brief, push, per-person delivery
- One daily brief replacing the Monday digest, Sunday review and morning
  routine; built from Today's data; says which sources it checked; delivered
  by WhatsApp to the person and in the app, at the person's chosen time.
- Installable web app (manifest + service worker) with web push.
- Notifications per person (`InAppNotificationModel` gains `user_id`);
  Decibyl's replies and routine results reach the bell.
- Routines from chat start switched on after a confirm, with an editable time
  and hour-level reminders; an editor for Decibyl's routines.
- WhatsApp from an unknown sender is a stranger, never the workspace's first
  user (`whatsapp_inbound.py` `_first_user`).
- Done when: the brief arrives daily for 5 days with no silent failure.

### 3. `habit` -- receipts, what I learned, patterns, voice notes
- A receipt card under every completed action: what, where, when, how to check.
- "What I learned about you": weekly list of new personal memories, each
  editable or deletable.
- Pattern to routine: after the same action 3+ times, suggest a routine.
- Voice note in, actions out: a WhatsApp or in-app voice note (Sarvam STT)
  becomes tasks, reminders and drafts.

### 4. `owners` -- the business owner set
- Who owes me: unpaid invoices from mail, WhatsApp and Tally into one list,
  with a one-tap reminder (approval card).
- Missed call, handled: call-back or WhatsApp within minutes, summary to the
  owner (builds on `services/telephony/missed_call.py`).
- End-of-day note for the owner and team.
- Bill and renewal radar from mail.

### 5. `families` -- simple mode and care
- Simple mode: large text, voice first, one thing at a time.
- Medicine call: a daily call in the parent's language ("did you take the 8am
  tablet?"), alert to family on a miss or no answer.
- Scam check: forward a message or describe a call; plain verdict and what
  to do.
- Family circle: link a parent and a child with consent; the child sees
  medicines taken and alerts, never private chats.

### 6. `learning` -- teach anything, at your pace
- Learner profile in personal memory (goal, skills, attempts, mistakes).
- Lessons on any subject in Chat: baseline question, short steps, practice
  that makes you think (no answer-dumping), specific feedback.
- Suggestions from improvement and upcoming events; reviews in Today.
- "Teach me this" on any answer; progress counts evaluated practice only.

### 7. `voice` -- live voice and call it for me
- Live voice with Decibyl (reuse the agents' pipecat pipeline): listen,
  interrupt, mute, captions, continue in text.
- Call it for me: Decibyl calls a business, announces itself as an
  assistant, confirms on WhatsApp; approval first.

### 8. `browser` -- Decibyl's private browser
- browser-use (MIT) in our sandbox: one isolated browser per person and
  task, internet on, private addresses blocked (reuse `web_tools.check_url`).
- Live view and step list in Chat; Take over for logins and CAPTCHAs; only
  site cookies kept, encrypted, per person; never passwords.
- Approval before submit, pay, send or book; step, time and cost limits;
  page text treated as data, never instructions; a prompt-injection test set.

### 9. `reach` -- tools from chat, ordering, meetings
- Outside AI tools (MCP) usable from chat, not only calls
  (`connected_tools.is_connected` accepts only Composio today).
- Ordering: Zomato first; Swiggy when Builders Club access arrives.
- Meeting capture: consent, record, transcript, actions into approvals.

### 10. `space` -- personal space, Today, merges
- A personal space for every person, separate from business workspaces they
  join; personal memory and threads belong to the person.
- Today as an ordered list: approvals, due, brief, events, up to three
  suggestions; the board stays as a view.
- One way to create an agent (describe it in Chat), one phone page, one usage
  view; remove dead switches and orphan pages.

## Decisions still open (founder)

- [ ] One-line positioning (draft: "Your assistant, with a number. It handles your day and your business, in your language.")
- [ ] Usage limits on the free beta (numbers)
- [ ] Who pays for numbers in the beta
- [ ] Business model and prices after the beta
- [ ] Apply to Swiggy Builders Club
- [ ] Staging: DNS, keys, GitHub environment, test accounts

## Done so far (PR #523, branch `claude/simpler-rail`)

- Chat and Today navigation; phone bottom bar; Settings and Agents in the profile menu
- Auto brain with Laya in shadow; Claude-on-calls fixes; Models page per workspace
- Honest states (memory, phone numbers, models); grouped Settings on phones
- Recents; Activity usage per agent and model; Sentry
- Fixes found by switching the assistant features on: invites (500 on every
  call), per-thread reply drafts (leak between people), run-once approvals
- Staging workflow and `scripts/staging_check.py`

## Status

Update this table as streams move. "Staging" means it passed
`staging_check.py` plus its own manual checks.

| Stream | Branch | PR | Built | Tests | Staging | On for users |
| --- | --- | --- | --- | --- | --- | --- |
| foundation | claude/stream-foundation | | | | | |
| daily | claude/stream-daily | | | | | |
| habit | claude/stream-habit | | | | | |
| owners | claude/stream-owners | | | | | |
| families | claude/stream-families | | | | | |
| learning | claude/stream-learning | | | | | |
| voice | claude/stream-voice | | | | | |
| browser | claude/stream-browser | | | | | |
| reach | claude/stream-reach | | | | | |
| space | claude/stream-space | | | | | |
