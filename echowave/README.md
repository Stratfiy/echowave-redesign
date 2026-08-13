# Decibyl

A voice AI platform: build a conversational agent as a graph, run it over
telephony or WebRTC, and bill the calls it makes.

This file is the **entry point** — how to run it, how to test it, what state it
is actually in, and what is left before it can be sold. The detail lives in the
documents it points at; nothing here is repeated from them.

| For | Read |
|---|---|
| Architecture, layering, org-scoping rule | `AGENTS.md`, `api/AGENTS.md`, `ui/AGENTS.md` |
| How a call is priced, what every dashboard number means | `DASHBOARD.md` |
| Engineering handover, subsystem by subsystem | `HANDOVER.md` |
| Deploying, environment variables, the EC2 runbook | `DEPLOY.md`, `docs/DEPLOY-GITHUB-ACTIONS.md` |
| Open problems and root causes | `KNOWN_ISSUES.md` |
| Data protection | `PRIVACY.md`, `compliance/` |
| Managed telephony and KYC | `MANAGED_TELEPHONY.md` |

`git log` is part of the documentation. Commit messages here are long on
purpose and explain why a decision was made, not what changed.

---

## 1. Running it locally

The recipe below is the one that works from a cold clone. It is written out
because several steps are non-obvious and cost hours to rediscover.

### Services

Postgres 16 with `pgvector`, and Redis. Both must be running before anything
else. In a fresh container they sometimes need starting by hand, and they can
die under memory pressure mid-session — a wall of `ConnectionRefusedError` in a
test run is almost always this and not your change:

```bash
sudo service postgresql start
sudo service redis-server start
pg_isready && redis-cli ping
```

### Python

```bash
python3 -m venv venv
./venv/bin/pip install -r api/requirements.txt -r api/requirements.dev.txt
```

The `pipecat/` submodule is a **fork** and is imported from source — it is not
the PyPI package. If imports of `pipecat.*` fail, the submodule is missing:
`git submodule update --init --recursive`.

### Environment

Two files, and using the wrong one is the classic mistake:

- `api/.env` — dev. Source it for anything that touches the dev database.
- `api/.env.test` — tests. Source it for pytest so tests never reach dev credentials.

Neither is committed. Minimum for local work:

```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/decibyl
REDIS_URL=redis://localhost:6379/1
ENVIRONMENT=dev
AUTH_PROVIDER=local
PLATFORM_CREDENTIAL_SECRET=<Fernet.generate_key()>
```

> **Quoting matters.** `EMAIL_FROM_ADDRESS=noreply@example.com` unquoted makes
> the shell parse `[email` as an identifier and the value silently never
> reaches the process. The app then logs "SMTP is not configured" and it looks
> like a code fault. Quote anything containing `@`.

`PLATFORM_CREDENTIAL_SECRET` is a Fernet key. Without it, provider keys cannot
be stored at all — the Provider Keys screen shows a red banner and every "Add
key" button is disabled.

### Running

```bash
# API
set -a && source api/.env && set +a
./venv/bin/python -m uvicorn api.app:app --port 8000 --reload

# UI (separate shell)
cd ui && npm install && npm run dev
```

`ui/.env.local` should point at whichever port the API is on:

```
BACKEND_URL=http://127.0.0.1:8000
NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:8000
```

### Getting a staff login

There is no seeded admin. Sign up, then promote:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"a-long-password","name":"You"}'

set -a && source api/.env && set +a
./venv/bin/python -m scripts.grant_superuser you@example.com
```

Email domains are validated: `.local` and other reserved TLDs are rejected.
Use `example.com`.

---

## 2. Testing

```bash
set -a && source api/.env.test && set +a
./venv/bin/python -m pytest api/tests/ -q
```

**Run against a scratch database.** The suite rolls each test back, but rows
committed by earlier work persist and several tests assert on absolute counts —
they will fail against a database that has been used:

```bash
sudo -u postgres psql -c "CREATE DATABASE decibyl_verify"
DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/decibyl_verify" \
  ./venv/bin/python -m pytest api/tests/ -q
```

### Measured state, August 2026

On a clean database: **2933 passed, 40 failed, 4 skipped.**

All 40 failures were verified as pre-existing by running the same files from a
worktree on `origin/main` — that baseline produced 41. They are environmental,
not defects:

| File | Why |
|---|---|
| `test_ts_bridge.py` (26) | Needs the TypeScript toolchain CI installs via `npm install` |
| `test_mcp_save_workflow.py` (6) | Depends on the above |
| `test_pipecat_engine_tool_calls.py` (3) | Provider SDKs absent |
| `test_telephony_routes.py` (3) | Provider SDKs absent |
| `test_camb_tts_integration.py`, `test_user_idle_handler.py` | Provider SDK / timing |

If you see roughly this set, you are fine. Anything else is yours.

`KNOWN_ISSUES.md` records "1,911 passed" from an earlier pass; the suite has
grown since.

### Front end

```bash
cd ui
npx tsc --noEmit      # must be silent
npm run lint          # must be clean
```

After changing any API route shape, regenerate the typed client or the UI will
compile against a stale contract:

```bash
npm run generate-client            # needs the API running
OPENAPI_FILE=/tmp/openapi.json npm run generate-client   # from a dumped spec
```

The second form exists because a running backend is not always reachable, and
because routes that are not deployed yet cannot be generated against a live
server. Dump the spec with:

```bash
./venv/bin/python -c "from api.app import app; import json; \
  json.dump(app.openapi(), open('/tmp/openapi.json','w'))"
```

### Screenshot verification

Charts and layout regress in ways type-checking cannot catch. Chromium is
preinstalled at `/opt/pw-browsers/chromium`; pass it as `executablePath` and
launch with `--no-sandbox`. Authenticate by setting two cookies:
`decibyl_auth_token` and `decibyl_auth_user`.

> **Recharts and `fullPage: true` do not mix.** A full-page screenshot resizes
> the viewport, `ResponsiveContainer` re-renders, and the entry animation
> restarts — you capture an empty chart with correct axes and no bars. Use a
> tall viewport and a normal screenshot instead. This looks exactly like a
> rendering bug and is not one.

---

## 3. What the system is made of

```
api/          FastAPI. routes/ → services/ → db/ clients. 40 routers under /api/v1.
ui/           Next.js 15 App Router, React 19, Tailwind v4, shadcn/ui, Recharts. 57 pages.
pipecat/      Forked real-time voice framework (submodule). Imported from source.
docs/         Astro Starlight documentation site.
```

The parts worth knowing before you touch anything:

**The call pipeline** (`api/services/pipecat/`) assembles a graph of processors
per call: transcriber → language model → voice, or a single speech-to-speech
model. Turn boundaries are decided by a *user turn stop strategy*, and which
one you get depends on the STT model — see §5.

**Billing** (`api/services/billing/`) computes a per-call receipt from measured
usage times a rate card. Provider costs and the platform fee are separate line
items and are never summed into one stored number. Every line stores both
`cost_paise` (charged) and `provider_cost_paise` (our cost).

**Managed vs BYOK** (`api/services/configuration/`) — a customer either brings
an API key per component, or picks `decibyl` and we supply it. Managed choices
are *tiers* (`fast`, `accurate`), and `managed_tiers.py` maps tiers to real
vendors, overridable by environment variable so moving a tier is a restart, not
a release.

**Org scoping is a security rule, not a style.** Every org-scoped read or write
filters by `organization_id`. See `api/AGENTS.md`.

---

## 4. Things that will confuse you

Collected from real debugging sessions. Each cost more than an hour.

**The dev port may not be your backend.** In some environments an agent proxy
resolves outbound hosts locally, so `127.0.0.1:8000` can answer with the
*deployed* OpenAPI spec rather than your process. If generated client types do
not match code you just wrote, check what is actually serving that port.

**Tailwind v4 `@theme inline` does not emit `--font-sans`.** A font set only
through the theme block silently falls back. Set `html { font-family: ... }`
explicitly, and put the font variables on `<html>` — not `<body>` — if that is
where they are consumed.

**The generated API client never throws.** It resolves to `{ data, error }`. A
`try/catch` catches network failures only; a 4xx slips through if you check
`response.data` alone. Always check `response.error` and render it through
`detailFromError`.

**`or 0` on a nullable metric column is a bug waiting to happen.** An
unmeasured stage and an instantaneous one are opposite facts that render
identically. The codebase uses `None` deliberately in these places.

---

## 5. What is left before rollout

Ordered by what blocks revenue first. Items marked **verify** could not be
checked from a development container and need confirming against production.

### Blocking

**1. Managed models are switched off in the UI.** Every slot on the Models
screen shows "Decibyl provides it — COMING SOON, not available yet". This is
not a stub: `managed_availability()` enables a slot only when a *platform* key
exists for the provider that slot's `default` tier resolves to. Store platform
keys for **Google** (language model), **Sarvam** (transcriber and voice) and
**OpenAI** (speech-to-speech, embeddings) under superadmin → Provider Keys and
it lights up with no code change. Until then the entire no-key offering — the
simplest thing a new customer can buy — is dark. **verify** whether production
already has these.

**2. Three of five managed tiers are the same model.**

```
default  → gemini-2.5-flash
fast     → gemini-2.5-flash-lite
lite     → gemini-2.5-flash-lite      ← same
zen      → gemini-2.5-flash-lite      ← same
accurate → gpt-4o
```

A picker offering `fast`, `lite` and `zen` as distinct choices where all three
are identical is worse than offering one. Needs a product decision: collapse to
three real tiers, or point them at genuinely different models. One line each in
`managed_tiers.py`, or an environment variable per tier. Collapsing is
customer-visible — stored configurations naming `zen` must keep resolving.

**3. Rate card coverage.** 27 provider/component pairs have no rate row. The
managed default path is priced (Google LLM, Sarvam STT/TTS), but the cost
estimator on the Models screen reported *"No rate on file for stt:deepgram,
llm:openai, tts:elevenlabs — the real cost will be higher than shown"* for a
default BYOK stack. Uncosted usage is surfaced rather than silently zeroed, but
a receipt that omits a component understates cost and overstates margin. Needs
a per-model audit, not just per-provider — rates resolve model-first with a
provider-wide fallback.

**4. No way for an admin to create an account.** `organization_members` can
list, change a role and remove — it cannot create. The only user-creating paths
are self-serve signup and Google first sign-in, and there is deliberately no
invite endpoint (`AUTH_PROVIDER=stack` expects Stack Auth to own invites). See
§6.

### Important

**5. Phone verification is off.** `VERIFICATION_CHANNEL=log` — codes are
written to the log, not sent. `REQUIRE_VERIFIED_TEST_NUMBER=false`, so nothing
enforces verification. Both are deliberate: enabling the requirement while the
channel is `log` would refuse every test call. Flip the channel first
(`twilio_sms` / `plivo_sms`), confirm delivery, then the requirement. Indian
A2P SMS needs DLT registration before either works.

**6. Latency instrumentation needs real data.** Endpointing is measured as of
`f4ff7bd` and deployed, but has never been validated against production traffic
— the numbers in this session's charts were synthetic. First real question to
ask it: what is p50 endpointing per language, and how far behind is the
non-Flux path.

**7. Languages outside Flux stay slow.** Tamil, Telugu, Kannada, Marathi and
Bengali are not supported by `flux-general-multi`, so those workflows fall back
to Nova-3 and pay the full VAD silence wait — roughly half a second per turn
that Hindi and English no longer pay. That gap is Deepgram's, but it is ours to
explain to a customer.

**8. Python formatting has drifted.** `scripts/format.sh` reformats about 50
files that nobody touched in the current work. The `pre-pr-drift-check`
workflow runs it and fails on any difference, so **the first pull request
raised from a branch will fail CI** regardless of its content. Fix as a
formatting-only commit so it never mixes with real changes.

**9. Failure reasons are not a first-class field.** `queued_runs.refusal_reason`
exists but is not generalised, so "why did this call fail" is not answerable
across the fleet.

### Not built

**10. Referral and partnership.** Nothing exists — see §7.

**11. Agent accounts.** See §6.

---

## 6. Agent accounts

What exists today:

- `OrganizationRole` — `member` < `admin` < `owner`, with
  `ORGANIZATION_ROLE_RANK` for "at least this role" checks.
- `organization_memberships` — a user's standing in one organization.
- Routes to **list**, **change role** and **remove** a member.

What is missing is the ability to *create* one. An owner cannot make an account
for a colleague; that person must sign themselves up and then be promoted, and
in a deployment with `ENABLE_SIGNUP=false` they cannot even do that.

The smallest change that closes it, staying inside the existing model:

1. `POST /organizations/members` — owner or admin only, takes an email and a
   role. Creates the user with a random password, adds the membership, and
   returns a one-time set-password link. Reuse `create_user_with_email`.
2. Gate it on `AUTH_PROVIDER == "local"`, the same way `/auth/signup` is gated
   by `require_local_auth`. Under Stack Auth, invites belong to Stack Auth and
   a second path would fork the identity source.
3. Send the link over the existing email transport — the one email verification
   already uses, so no new infrastructure.
4. Decide what `member` may actually do. The rank exists but little enforces it
   today; an "agent" who can run calls but not change billing or model
   configuration needs those checks written, and that is the larger half of
   the work.

Worth settling first: is "agent" a *third organization role* (a human operator
with narrow permissions), or a *distinct account type* with its own screens?
The role system will carry the first cheaply. The second is a bigger build.

---

## 7. Partnership tiers and referrals

Nothing is built. Sketching where it would attach, so the shape is not invented
twice:

**What already helps.** `OrganizationModel.account_type` is a free-text
VARCHAR chosen specifically so a new tier needs no migration — the same
convention as `StaffRole`. A `partner` account type costs nothing structurally.
`CreditLedgerKind` already separates `topup`, `adjustment` and `trial`, so
partner-funded credit can be its own kind and stay out of the activation
funnel's "paid" step, which filters on kind rather than on sign.

**What has to be decided before code.** Three questions, in order:

1. **What does a partner earn — commission on revenue, or a discounted rate
   card?** These are different systems. Commission is a payable computed from
   settled payments and needs its own ledger and payout run. A discount is an
   account-level rate override, and `organization_rate_history` plus the
   existing platform-rate resolver already support exactly that. The discount
   route is dramatically cheaper and reuses machinery that is tested.
2. **Who owns the customer relationship** — does the partner's client have
   their own account with their own balance, or does everything bill to the
   partner? "Own account, partner sees it" needs a parent/child link on
   organizations. "Bills to the partner" needs nothing new beyond a shared
   account and roles.
3. **Is attribution one-time or perpetual?** A referral credited once is a
   ledger entry on first top-up and nothing more. A perpetual share is a
   recurring computation over every payment, forever, and has to survive a
   partner leaving.

**Where each piece would live.** Attribution is a nullable
`referred_by_organization_id` on `organizations`, captured at signup from a
code. The activation funnel already fixes its cohort at signup and asks "ever",
so partner-sourced conversion is a filter on the existing query rather than a
new one. Payouts are a new service under `api/services/billing/`, keyed off
`payments` — which already records what settled, when, and for which account.

My recommendation: start with a `partner` account type, a referral code
captured at signup, and a **discounted rate card** rather than commission.
That is a rate override, an attribution column and a filter on a query that
already exists — no payout run, no second ledger, no money leaving the system.
Commission can be layered on later against the same attribution data if
partners ask for cash rather than margin.
