# Next session — start here

Updated 12 Aug 2026. Read this before touching anything; it exists so the next
session does not re-derive what previous ones paid for.

This file lives on `main` deliberately. An earlier copy sat only on a feature
branch, which meant the handoff would have been deleted along with the branch.

---

## 1. Where things stand

`main` is at the merge of the monday.com restyle. Everything below is on `main`
unless it says otherwise.

### Recently landed

- **The app restyle** (`93d409b`). monday.com's structure in Decibyl orange:
  light-only palette on a `#f5f6f8` canvas, orange confined to primary CTAs and
  active states, a desktop top bar with working navigation search, shared
  `PageHeader`/`PageBody`, full-bleed content, pill buttons, 24px cards.
  Screenshot-verified at 1440.
- **Figtree, actually applied.** Worth reading the commit message on this one.
  Every previous attempt loaded a webfont and rendered in the system font:
  `@theme inline` does not emit `--font-sans` as a custom property, so
  Tailwind's preflight fell through to its fallback stack. Both `tsc` and
  `next build` pass whether or not a font renders. Verify type with
  `getComputedStyle` against a running server, never by reading the code.
- **Sign in with Google** (PR #36). Live and configured. **Nobody has completed
  an end-to-end sign-in yet** — worth confirming before anyone relies on it.

### Still open in the UI

- **~17 pages render their own headers** rather than `PageHeader`. Voice Agents
  and Settings are converted; the rest put a bare `<h1>` straight on the grey
  with no white band. Visible immediately on `/provider-keys`.
- **Table density.** monday's screens read dense because of the filter row, the
  checkbox column and inline row actions. Our tables have none of that. This is
  the larger half of "make it look like monday" and it is page-by-page work.

---

## 2. Rollout order

This is the order things actually block each other in, which is **not** the
order they are interesting in.

### 1. DND scrubbing + calling hours — the only P0 left

Grep the API for `dnd`, `do_not_disturb`, `calling_hours`, `quiet_hours` or
`tcccpr` and you get **zero files**. There is no scrubbing anywhere in the
dialling path and no 9am–9pm window.

This is TRAI/TCCCPR exposure the moment you dial someone who has not asked to
be called, which makes it the gate on outbound existing at all. It is the one
remaining P0 in `STATUS.md`; everything else on that list is either done
(Razorpay, MinIO read-back, docs) or blocked on a third party (autopay on
Razorpay Subscriptions, managed numbers on Plivo KYC).

**Where it goes.** There is already an outbound choke point:
`services/kyc/service.py::assert_may_place_calls` and
`assert_configuration_may_place_calls`, called from `routes/campaign.py:572`
and `routes/telephony.py:141`. DND belongs beside those, not scattered through
the providers.

**Watch:** `routes/public_agent.py`'s trigger route does not appear in that
caller list. Confirm whether it reaches the gate by another path before
assuming it is covered.

### 2. Verified test number

Not started, and what exists today is weaker than it looks. `test_phone_number`
is a free-text organization preference (`routes/user.py:118`) that
`routes/telephony.py:156` dials directly. There is no proof the caller owns the
number, so an account can have Decibyl ring anything they type — a missing
feature and a small abuse surface in one.

The user's framing: *"like bolna … they allow to add a number with OTP like a
test number where user can test receiving calls"*. Needs its own table — **not**
`telephony_phone_numbers`, which is for rented numbers bound to inbound
workflows — with OTP expiry, rate limiting and hashed storage. SMS delivery
already exists and is live in production (`services/messaging/send.py`).

This is what unblocks trial calling while managed numbers sit behind Plivo KYC.

### 3. Analytics gaps

Half-built. `/analytics` ships `spend` and `tokens`, and the billing dashboard
carries latency p50/p95. Missing, in value order:

- **Activation funnel** (signup → agent → first call → first top-up). Highest
  value and answerable in plain SQL.
- **Failure reasons.** `grep failure_reason api/` returns nothing — there is no
  first-class field, so "why did calls fail" cannot be answered at all today.
- Account health, then margin per account.

### 4. Latency instrumentation

`t_endpoint_fired_ms` is written and read (`pipeline_metrics_aggregator.py:203`,
`billing_dashboard_client.py:359`) and is **always 0** — pipecat emits no VAD
mark. Endpointing is often the largest share of perceived latency and is
currently invisible, as is the network leg: the timeline starts and ends inside
our own process.

### 5. Reconciliation

Three independent paths to "what did this account use" — the credit ledger, the
daily rollup, raw runs — and nothing asserts they agree.

### Blocked on third parties

- **Autopay** — Razorpay Subscriptions approval.
- **Managed numbers** — Plivo KYC verdict.

### Decided against

- **PostHog.** The wired events are backend events already in Postgres, there is
  no frontend instrumentation so it would not answer the funnel question, and
  its default host is a US endpoint — a DPDP decision, not a config toggle.

---

## 3. Things that cost time if you re-derive them

- **`ui/src/app/globals.css` is fully tokenised.** `--primary` and friends flip
  the whole shadcn surface from one place. Light only — there is no `.dark`
  block, and `ThemeProvider` runs `forcedTheme="light"`.
- **68 call sites still say `--brand-blue*`.** They are aliases onto correctly
  named tokens. Do not rename them; it is a 68-file diff whose only content is
  the rename.
- **`BrandLogo` does not use `icon.svg`.** It points at three files in
  `ui/public/`: `decibyl-mark.svg`, `decibyl-logo.svg`,
  `decibyl-logo-inverse.svg`. A `src/` sweep misses all three, which is exactly
  how the header logo stayed blue on an orange page through a green build.
- **Danger vs brand collide if you are not careful.** `--destructive` sits at
  hue 18 against a primary at 33.6. It was four degrees away once — a delete
  button and a save button the same colour. Keep the separation.
- **Only screenshots find colour bugs.** `tsc` and `next build` both pass while
  a page is unreadable.
- **The nav list lives in `components/layout/navigation.ts`**, shared by the
  sidebar and the top-bar search. Add a destination once, not twice.

---

## 4. Deployment

- **Deploys run from GitHub Actions over SSM** — no SSH key, no long-lived AWS
  credentials. `on: push: branches: [main]`, plus `workflow_dispatch` with a
  `ref` input.
- **You can deploy any branch without merging.** Actions → Deploy → Run
  workflow → put the branch in `ref`. `ci_deploy.sh` checks out the ref, builds,
  and **rolls back to the previous SHA automatically if any step fails**.
- **The UI is built on the box** — `ci_deploy.sh:88` runs
  `docker compose build api ui`. An older note in this file claimed it was a
  pulled image; that was wrong.
- **Every deploy runs `alembic upgrade head`**, so a "UI-only" preview deploy is
  still a full-stack deploy at that ref.
- **The box sits in detached HEAD.** `git pull` fails. Use
  `git fetch origin main && git checkout -B main origin/main`.
- **`docker compose restart` does not re-read `.env`.** Use
  `up -d --force-recreate`.
- **The EC2 box has no `npm`.** Do not `apt install npm` (too old for Astro 7).
  Build docs in a container:
  ```bash
  cd ~/echowave-redesign/echowave/docs
  docker run --rm -v "$PWD":/w -w /w -e npm_config_cache=/tmp/.npm \
    --user "$(id -u):$(id -g)" node:22-alpine sh -c "npm ci && npm run build"
  ```

---

## 5. Verifying UI work

The test environment is rebuilt each session (the container is ephemeral).

```bash
# api/.env.test — recreate if missing
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/decibyl
REDIS_URL=redis://localhost:6379/1
LOG_LEVEL=WARNING
ENVIRONMENT=test
```
`redis-server --daemonize yes` and
`su postgres -c "psql -c \"ALTER ROLE postgres WITH PASSWORD 'postgres'\""`.

For screenshots without a backend: run `next build && next start`, put a stub
API on `:8000` answering `/api/v1/health` with `{"auth_provider": "local"}`,
and proxy both behind one origin so the browser's same-origin API calls land on
the stub. Mint the session by POSTing `{token, user}` to `/api/auth/session` —
that sets the two cookies the middleware and SSR read. Chromium is at
`/opt/pw-browsers/chromium`; launch with `--no-sandbox`.

A stub returning `[]` for everything will make some pages throw
`Cannot read properties of undefined` — that is the stub, not the page. Do not
report those as defects without a real API.

---

## 6. Standing constraints from the user

- **Do not raise pasted API keys.** Verbatim: *"Never worrie abou API KEYS
  DROPPED HERE."* Never commit them either — `PRODUCTION-CHECKLIST.md:164`.
- **The exposed PEM is not to be raised again** — the user assessed it and
  declined rotation.
- **No SSH access to the EC2 box.** Give the user commands to run.
- **No trace of the upstream fork, and the product is not open source.** Both
  the docs and the login page have been cleaned; watch for regressions.
- **`certs/` must never be committed.**
- Declined and not to be revisited: scraping LinkedIn/Indeed.
- **Never disturb working functionality.** Said explicitly about UI work:
  visual changes should not touch logic.
