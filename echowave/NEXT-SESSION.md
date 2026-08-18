# Next session — start here

Updated 13 Aug 2026. Read this before touching anything; it exists so the next
session does not re-derive what previous ones paid for.

This file lives on `main` deliberately. An earlier copy sat only on a feature
branch, which meant the handoff would have been deleted along with the branch.

---

## 1. What shipped, and what it means

### Merged and deployed

- **The app restyle** (`93d409b`). monday.com's structure in Decibyl orange.
  Light-only, `#f5f6f8` floor, orange confined to primary CTAs and active
  states, top bar with working navigation search, shared `PageHeader`/
  `PageBody`, pill buttons, 24px cards.
- **Figtree, actually applied.** Read the commit message. Every previous
  attempt loaded a webfont and rendered in the system font: `@theme inline`
  does not emit `--font-sans` as a custom property, so Tailwind's preflight
  fell through to its fallback. Both `tsc` and `next build` pass either way.
  **Verify type with `getComputedStyle` against a running server, never by
  reading the code.**
- **DND scrubbing + calling hours** (`316e930`). The last P0. Enforced per call
  in both dial paths, failing closed, refusals terminal so they never enter the
  retry path. Screen at `/do-not-call`.

### Merged this session (see §2 for what is and is not enforced)

- **Email verification at signup.** Works end to end. Delivered over the
  existing SMTP path (Resend).
- **Verified test numbers.** Complete except delivery — see §3.
- **Three Google sign-in fixes.** See §4.

---

## 2. Two switches that are deliberately OFF

Both are off because the alternative is an outage, and both should be turned on
later. Neither is an oversight.

### `REQUIRE_VERIFIED_TEST_NUMBER` — false

The gate works and is tested. It is not enforced because
`VERIFICATION_CHANNEL` is `log` on every real deployment, and `log` refuses to
run outside dev. With the gate on, nobody could verify a number and **every
test call would be refused with no way for the user to proceed**. A permission
nobody can obtain is not a permission.

`api/tests/test_verified_numbers.py::TestTheGateDefault` asserts it is false.
**That test exists to be deleted** — in the same change that makes delivery
work and flips the default. If it starts failing, somebody turned the gate on;
check a code can actually reach a user first.

### Email verification — nothing is gated on it

Every account predating the feature has `email_verified_at` NULL. Refusing them
would be an outage dressed as a security improvement. What exists is the proof
and the record; deciding what to withhold from an unverified account is a
separate, reversible decision — and a decision somebody should actually make.

---

## 3. Verified test numbers: blocked on delivery, not on code

Table, service, three rate limits, routes, gate and screen (`/verified-numbers`)
are done. 32 tests. What is missing is a way to get the code to the phone.

**The India problem.** TRAI's TCCCPR requires commercial SMS to Indian numbers
to be registered on a DLT platform first: a Principal Entity (PAN/GST), a
registered 6-character alphanumeric header, and a registered content template
with the variable marked. Roughly ₹5,900 and 7–10 business days. Unregistered
traffic is **blocked by the operator**, and the failure looks like success —
the carrier accepts the request and the message never arrives.

**Changing carrier does not help.** The requirement attaches to the sending
entity and the Indian destination, not the carrier. Twilio and Plivo both drop
unregistered A2P traffic. Both channels exist so the choice is a config value
once the paperwork lands, not so one can route around it.

`VERIFICATION_CHANNEL` takes:

| | |
|---|---|
| `log` | default; dev only, refuses outside a dev/test `ENVIRONMENT` |
| `voice` | call the number and read the code out — **not wired** |
| `plivo_sms` / `twilio_sms` | written, blocked on DLT |

`voice` is the interesting one: transactional voice carries no DLT template
requirement, and the thing a trial user is verifying is precisely that Decibyl
can ring them. It returns "not enabled" because `STATUS.md` records that
outbound on the platform account has **never completed a real call** in this
deployment. Prove that path with one call before building on it.

**`_body()` is asserted character for character in a test.** Once SMS is live
the operator matches on the registered template; a message differing by a full
stop is rejected as unregistered. When the template is approved, change the
code to match it — not the other way round.

**A second gap.** A test call from an account with no telephony configuration
fails at `telephony_not_configured` (`routes/telephony.py:122`) *before* the
verification gate. That ordering is right — you cannot dial without a carrier
to dial from — but it means verifying a number is not by itself enough for
trial calling. That needs platform origination, which is the managed telephony
path behind `MANAGED_TELEPHONY_ENABLED` and Plivo KYC.

---

## 4. Google sign-in

Three defects were fixed, none of which produced a log line. If it is still
broken, the third fix means the login page now shows Google's actual error.

1. **One OAuth client, two callbacks.** `GOOGLE_OAUTH_CLIENT_ID/SECRET` are
   shared by Sign in with Google and the google_calendar tool, and they have
   different redirect URIs. The env template documented only the calendar one,
   so a client registered from it has no authorised URI for sign-in and Google
   refuses with `redirect_uri_mismatch` *before* redirecting to us — nothing in
   our logs, and the calendar tool keeps working. Both URIs are now documented
   in `deploy/decibyl.env.template`. **The user added the sign-in URI to the
   console on 13 Aug; whether that fixed it was not confirmed.**
2. **The button hid itself** wherever the API is served separately, because it
   read `NEXT_PUBLIC_API_BASE_URL`, a name defined nowhere. Now resolved like
   every other API call.
3. **The `?error=` redirect was never read.** The callback has always sent
   failures to `/auth/login?error=<message>` and nothing displayed it.

Two more silent failures, both now in the env template: a consent screen left
in **Testing** refuses everyone not on the test-user list, and an unset
`UI_APP_URL` sends a successful sign-in to `http://localhost:3010`.

---

## 5. What is next, in priority order

1. **Analytics — the activation funnel.** signup → agent created → first call →
   first top-up. Highest value and answerable in plain SQL. `email_verified_at`
   now gives it a real first step: "signed up" and "signed up with a working
   address" are different numbers.
2. **Failure reasons.** `grep failure_reason api/` returns nothing — there is
   no first-class field, so "why did calls fail" cannot be answered at all.
   Note `queued_runs.refusal_reason` now exists for the DND case and is the
   obvious shape to follow.
3. **Account health, then margin per account.**
4. **Latency instrumentation.** `t_endpoint_fired_ms` is written and read and is
   **always 0** — pipecat emits no VAD mark. Endpointing is often the largest
   share of perceived latency and is invisible, as is the network leg.
5. **Reconciliation** between the credit ledger, the daily rollup and raw runs.
   Three paths to "what did this account use", nothing asserts they agree.
6. **UI: ~17 pages still render their own headers** rather than `PageHeader` —
   visible on `/provider-keys`, title straight on the grey with no white band.
   Plus monday's table density: filter row, checkbox column, inline row actions.

### Blocked on third parties

- **Autopay** — Razorpay Subscriptions approval.
- **Managed numbers** — Plivo KYC verdict.
- **SMS verification** — DLT registration (§3).

### Decided against

- **PostHog.** Wired events are backend events already in Postgres, there is no
  frontend instrumentation so it would not answer the funnel question, and its
  default host is a US endpoint — a DPDP decision, not a config toggle.

---

## 6. Things that cost time if you re-derive them

### The app

- **`ui/src/app/globals.css` is fully tokenised.** `--primary` and friends flip
  the whole shadcn surface. Light only; `ThemeProvider` runs
  `forcedTheme="light"` and there is no `.dark` block.
- **68 call sites still say `--brand-blue*`.** They are aliases onto correctly
  named tokens. Do not rename them.
- **`BrandLogo` does not use `icon.svg`.** It reads three files from
  `ui/public/`. A `src/` sweep misses all three — which is how the header logo
  stayed blue on an orange page through a green build.
- **Only screenshots find colour bugs.** `tsc` and `next build` both pass while
  a page is unreadable.
- **The nav list lives in `components/layout/navigation.ts`**, shared by the
  sidebar and the top-bar search. Add a destination once.
- **OTP primitives are shared.** `services/auth/otp.py` is imported by both
  email and phone verification, and a test asserts they are the same function.
  Two copies of those decisions drift, always towards the weaker one.

### The container is reclaimed, and it takes the environment with it

`venv/`, `api/.env`, `api/.env.test` and `ui/.env` are all gitignored and all
disappear when the session's container is recycled — but Postgres, Redis and
the `test_db` database survive, so the symptom is `ModuleNotFoundError:
fastapi` rather than anything that looks like a lost machine. There is no
`.env.example` in this repository to copy from. `api/constants.py` requires
exactly two variables; everything else has a default:

```
ENVIRONMENT=test
LOG_LEVEL=WARNING
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/test_db
REDIS_URL=redis://127.0.0.1:6379/1
```

`conftest.py` points at the test database directly — it does not append
`_test` — so `DATABASE_URL` must name `test_db` itself.

### Running the API in a fresh container

The test suite cannot collect ~40 modules out of the box because optional
provider SDKs are missing. This is pre-existing and unrelated to any change —
unmodified `main` produces 41 collection errors. To get a full run:

```bash
python3 -m venv venv
venv/bin/pip install -r api/requirements.txt -r api/requirements.dev.txt
venv/bin/pip install pytest pytest-asyncio
git submodule update --init --recursive echowave/pipecat   # the FORK, not PyPI
venv/bin/pip install -e ./pipecat
venv/bin/pip install soundfile aiortc deepgram-sdk google-genai groq \
    azure-cognitiveservices-speech google-api-core google-cloud-speech \
    sarvamai speechmatics-voice opencv-python-headless pgvector \
    google-cloud-texttospeech camb-sdk
apt-get install -y postgresql-16-pgvector    # the SERVER extension, not the wheel
pg_ctlcluster 16 main start && redis-server --daemonize yes
su postgres -c "createdb decibyl"
su postgres -c "psql -c \"ALTER ROLE postgres WITH PASSWORD 'postgres'\""
```

**The last two packages are new to this list and each costs an hour to
rediscover.** Without `google-cloud-texttospeech` 43 modules fail to collect;
without `camb-sdk` exactly one test fails
(`test_camb_tts_integration::test_create_tts_service_camb`), which reads like a
real regression and is not one. The extra `pip install pytest` line matters for
the same reason: `scripts/setup_requirements.sh --dev` does **not** install
pytest, so a bare `pytest` runs a system copy from outside the venv and dies on
`ModuleNotFoundError: dotenv` — which looks like a broken conftest.

With all of it in place the suite is green with no collection errors, so the
"41 collection errors on unmodified main" note above is a description of a
missing environment rather than of the repository.

`tests/test_tts_endframe_with_audio_write_failure.py` failed once in one full
run and passed in isolation and in a clean re-run. Pre-existing flakiness; do
not chase it as a regression, but do not assume every failure there is flaky
either.

Postgres and Redis stop between long gaps — a sudden wall of
`ConnectionRefusedError ('127.0.0.1', 5432)` means restart them, not that you
broke something.

---

## 7. Verifying work without production

### UI screenshots

```bash
cd ui && npx next build && BACKEND_URL=http://127.0.0.1:8000 npx next start -p 3014
```

Put a stub API on `:8000` answering `/api/v1/health` with
`{"auth_provider": "local"}`, and proxy both behind **one origin** so the
browser's same-origin calls reach it. Mint a session by POSTing `{token, user}`
to `/api/auth/session` — that sets the two cookies middleware and SSR read.
Chromium is at `/opt/pw-browsers/chromium`; launch with `--no-sandbox`.

A stub returning `[]` for everything makes some pages throw
`Cannot read properties of undefined`. That is the stub, not the page.

**Dump the real payloads rather than hand-writing them.** `/model-configurations`
reads deep into the defaults shape and throws on anything approximate; calling
`api.routes.organization.get_model_configuration_v2_defaults` offline and
serving the JSON is faster than guessing. `/provider-keys` needs
`/api/v1/user/auth/user` to carry `staff_role` and `organization_role`, or
`useAccessRoles` reports unprivileged and every admin control is hidden.

Four traps that each cost real time:

* **`ss` is not installed in this container.** Any "kill whatever holds the
  port" incantation built on `ss -ltnp` silently kills nothing, and the
  previous server keeps the port — which presents as a clean rebuild having no
  effect. Use `fuser -k -9 3014/tcp`. Worse, a leftover `next dev` on the same
  port while `next build` writes `.next` corrupts the build: `next start` then
  dies with `Cannot find module './vendor-chunks/source-map-support.js'`. The
  cure is `rm -rf .next && npx next build` with nothing else running.


* **`pkill -f "next start"` does not stop it.** It runs npm exec → sh → node,
  and the node child keeps port 3014, serving the *previous* build. The symptom
  is a code change with no effect, or a 404 for a chunk from a build that no
  longer exists. Kill by port instead.
* **Only `/api/v1` belongs to the stub.** `/api/config/*` and `/api/auth/*` are
  Next's own route handlers; intercept them and the app decides the backend is
  unreachable and renders skeletons.
* **Strip `Accept-Encoding` when proxying to Next**, or a gzipped body reaches
  the browser labelled as plain text and the page screenshots as binary noise.
  Node's `urllib` also honours the container's `http_proxy` for localhost, which
  hangs; pass an empty `ProxyHandler`.

**A fresh account gets the welcome questionnaire**, which covers the screen and
blocks Playwright clicks. Answer all four `[role="combobox"]` selects and click
"Get started" before screenshotting anything.

### Email, end to end

```bash
venv/bin/python -m aiosmtpd -n -l 127.0.0.1:1025 -d > smtp.log 2>&1 &
# then run the API with:
SMTP_HOST=127.0.0.1 SMTP_PORT=1025 SMTP_USE_TLS=false \
  EMAIL_FROM_ADDRESS="noreply@decibyl.test"
```

The code is in `smtp.log`. Quote `EMAIL_FROM_ADDRESS` — unquoted, the shell
parses `[email` as an identifier, the variable never gets set, and the API logs
"SMTP is not configured", which looks exactly like a code fault.

### Production is not reachable from the agent container

The proxy resolves `api.decibyl.ai` to `127.0.0.1`. Diagnose from code and
config, and say so rather than implying you observed the live system.

---

## 8. Deployment

- **Deploys run from GitHub Actions over SSM** — no SSH key, no long-lived AWS
  credentials. `on: push: branches: [main]`, plus `workflow_dispatch` with a
  `ref` input.
- **Any branch can be deployed without merging.** Actions → Deploy → Run
  workflow → put the branch in `ref`. `ci_deploy.sh` checks out the ref, builds,
  and **rolls back to the previous SHA automatically if any step fails**.
- **The UI is built on the box** — `ci_deploy.sh:88` runs
  `docker compose build api ui`. An older note claimed it was a pulled image;
  that was wrong.
- **Every deploy runs `alembic upgrade head`**, so a "UI-only" preview deploy is
  still a full-stack deploy at that ref.
- **The box sits in detached HEAD.** `git pull` fails. Use
  `git fetch origin main && git checkout -B main origin/main`.
- **`docker compose restart` does not re-read `.env`.** Use
  `up -d --force-recreate`.
- **The EC2 box has no `npm`.** Build docs in a container:
  ```bash
  cd ~/echowave-redesign/echowave/docs
  docker run --rm -v "$PWD":/w -w /w -e npm_config_cache=/tmp/.npm \
    --user "$(id -u):$(id -g)" node:22-alpine sh -c "npm ci && npm run build"
  ```

---

## 9. Standing constraints from the user

- **Do not raise pasted API keys.** Verbatim: *"Never worrie abou API KEYS
  DROPPED HERE."* Never commit them either — `PRODUCTION-CHECKLIST.md:164`.
- **The exposed PEM is not to be raised again** — assessed and declined.
- **No SSH access to the EC2 box.** Give the user commands to run.
- **No trace of the upstream fork, and the product is not open source.** Both
  the docs and the login page have been cleaned; watch for regressions.
- **`certs/` must never be committed.**
- Declined and not to be revisited: scraping LinkedIn/Indeed.
- **Never disturb working functionality.** Said explicitly about UI work:
  visual changes should not touch logic.
