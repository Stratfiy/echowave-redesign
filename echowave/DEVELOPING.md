# Developing, testing, and what is left to roll out

The [README](README.md) is the product-level orientation and the Docker
quickstart. This file is the other half: running the stack from source, testing
it, the traps that cost hours, and a rollout gap analysis.

Written for a developer taking this forward — bug-fixing, testing, and deciding
what has to happen before it can be sold.

| For | Read |
|---|---|
| Architecture, layering, org-scoping rule | `AGENTS.md`, `api/AGENTS.md`, `ui/AGENTS.md` |
| What was built and why, subsystem by subsystem | `HANDOVER.md` |
| How a call is priced, what every dashboard number means | `DASHBOARD.md` |
| Running it: rates, payments, weekly checks | `OPERATIONS.md` |
| Going live, in order | `DEPLOY.md`, `docs/DEPLOY-GITHUB-ACTIONS.md` |
| The commercial case | `PRD.md` |
| Open problems and root causes | `KNOWN_ISSUES.md` |

`git log` is part of the documentation. Commit messages here are long on
purpose and explain why a decision was made, not what changed.

> The Docker path in the README is the fastest way to a running system. Use the
> from-source setup below when you need to attach a debugger, run the test
> suite, or work on the backend itself.

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

For **production**, `DEPLOY-ENV.md` is the full list: every key added in this
round, what breaks without it, and which settings are deliberately not
environment variables.

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

On a clean database, from a cold clone: **2975 passed, 41 failed, 4 skipped**
(12m23s).

**39 of the 41 come from six files**, none of which is a defect — they need a
toolchain or a provider SDK this environment does not install:

| File | Why |
|---|---|
| `test_ts_bridge.py` | Needs the TypeScript toolchain CI installs via `npm install` |
| `test_mcp_save_workflow.py` | Depends on the above |
| `test_pipecat_engine_tool_calls.py` | Provider SDKs absent |
| `test_telephony_routes.py` | Provider SDKs absent |
| `test_camb_tts_integration.py` | Provider SDK absent |
| `test_user_idle_handler.py` | Timing-sensitive |

Running those six alone reproduces exactly 39 failures, which is the quickest
way to confirm your environment rather than your change is at fault.

The remaining two vary by run and are Redis-dependent
(`test_from_number_pool_isolation.py`); they pass when Redis is up and fail
when it has died mid-run.

These were confirmed pre-existing by running the same files from a worktree on
`origin/main`. If you see roughly this set, keep going. Anything else is yours.

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

**3. Rate card gaps — narrower than they first look.** The price book is 33
rows dated `2026-08` and every rate checked against a vendor's published price
agrees, so the managed path and the common BYOK providers are correctly costed.
See `PRICING-REVIEW.md` for the audit, the rows still worth adding, and the two
things that actually need attention:

- **ElevenLabs multilingual v2 is unpriced** and falls through to the
  Flash/Turbo row at half its real cost. The only known case in the card where
  we bill less than the vendor charges.
- **`LLM_INPUT_SHARE = 0.7`** blends two-sided vendor pricing into the single
  rate the schema carries. It is an assumption, it is measurable from the
  Tokens screen, and every LLM margin figure depends on it.

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

**10. Agency tier and referrals.** See §6 and §7. The signup bonus and the two
staff tiers already exist; the agency tier and refer-a-friend do not.

**11. Admin-created accounts.** See §6.

---

## 6. The four-tier account model

The target shape, and how much of it exists.

| Tier | Who | Exists today? |
|---|---|---|
| **User** | A clinic or business running its own agents | Yes — this is the current account |
| **Agency** | Manages accounts it sets up, earns a flat commission we set | **No** |
| **Staff** | Reviews verifications and KYC, nothing further | Yes — `StaffRole.SUPPORT` |
| **Super admin** | You. Everything. | Yes — `StaffRole.SUPERADMIN` |

So two of four are built. `StaffRole` already splits exactly along the line you
described — SUPPORT reviews KYC documents and can do nothing else, SUPERADMIN
adds billing, platform keys and impersonation. Both are a nullable VARCHAR on
`UserModel`, chosen so a new tier needs no migration.

Inside an organization there is a second, separate axis: `OrganizationRole` —
`member` < `admin` < `owner`, with `ORGANIZATION_ROLE_RANK` for "at least this
role" checks. These are orthogonal on purpose: staff standing is about Decibyl,
organization role is about one account.

### What Agency needs

An agency is not a role on an existing organization — it is an organization
that *stands above others*. Two pieces:

1. **A parent link.** `organizations.managed_by_organization_id`, nullable. An
   agency's clients each keep their own account, balance and billing; the
   agency gets read access and management rights across them. This is the only
   schema change the tier needs.
2. **A commission rate.** `organizations.commission_bps` on the agency row, set
   by a super admin. Flat, as you described, and basis-points for the same
   reason every other rate here is: integers, no float drift.

Everything else follows from machinery that exists. Agency-scoped listing is
the existing org-scoped queries with the parent link in the `WHERE`. Commission
owed is a query over `payments`, which already records what settled, when and
for which account. Payouts are the only genuinely new service.

**The security question to settle first:** an agency user reading a client's
account is a deliberate hole in tenant isolation, which `api/AGENTS.md` treats
as a hard rule. It needs one explicit, tested seam — a helper that resolves
"organizations this user may act on" — rather than each query growing its own
exception. Get that wrong and it is a cross-tenant leak, not a bug.

### What is missing for all tiers: creating an account

`organization_members` can list, change a role and remove — it **cannot
create**. The only user-creating paths are self-serve signup and Google first
sign-in, and there is deliberately no invite endpoint (`AUTH_PROVIDER=stack`
expects Stack Auth to own invites). So no admin can make an account for anyone
today, and with `ENABLE_SIGNUP=false` that person cannot make their own either.

Smallest change that closes it:

1. `POST /organizations/members` — owner or admin only, takes email and role.
   Creates the user with a random password via the existing
   `create_user_with_email`, adds the membership, returns a one-time
   set-password link.
2. Gate on `AUTH_PROVIDER == "local"` the way `/auth/signup` is gated by
   `require_local_auth`. Under Stack Auth a second path would fork the identity
   source.
3. Send the link over the transport email verification already uses.
4. **Then make `member` mean something.** The rank exists but little enforces
   it. An operator who can run calls but not change billing or model
   configuration needs those checks written, and that is the larger half.

---

## 7. Referrals and the signup bonus

### Signup bonus — already built, already $5

`SIGNUP_BONUS_MICROS_USD` defaults to `5000000`, which is **$5.00**. Nothing to
do. It is worth knowing how it behaves:

- Denominated in **dollars** and converted at the FX in force at signup, so it
  does not quietly get cheaper as the rupee moves.
- Lands as `CreditLedgerKind.TRIAL`, not `TOPUP` — so revenue reporting can
  always separate given credit from bought credit, and the activation funnel's
  "paid" step (which filters on kind, not on sign) does not count it as a
  conversion.
- **No GST and no receipt voucher**: no money changed hands, and a tax document
  for a gift would misstate a supply that never happened.
- Granted once per organization, enforced by a partial unique index on the
  ledger rather than an application check — two requests racing during signup
  would otherwise both find no bonus and both grant one.

Set `SIGNUP_BONUS_MICROS_USD=0` to switch it off.

### Refer a friend — not built

Target: a referrer gets **20% of their friend's first purchase** as wallet
credit.

Everything needed exists in pieces:

| Piece | What to use |
|---|---|
| Attribution | New nullable `organizations.referred_by_organization_id`, captured at signup from a code |
| The credit | A new `CreditLedgerKind.REFERRAL` — a fourth kind beside topup/adjustment/trial |
| "First purchase" | `payments` already records order, settlement and amount per account |
| Idempotency | The partial unique index pattern the signup bonus already uses |
| Reporting | The activation funnel fixes its cohort at signup and asks "ever", so referred-vs-organic conversion is a filter on an existing query |

**Four decisions before code:**

1. **20% of what — gross or net?** `payments` stores both `gross_paise` (what
   the customer was charged, tax included) and `amount_paise` (net of GST,
   what reaches the ledger). Paying 20% of gross means paying commission on
   tax you remitted to the government. Use **net**.
2. **When does it vest?** On settlement, or after a hold? A refunded or
   charged-back first payment leaves credit already granted. A short hold is
   the cheap answer; instant is the marketable one.
3. **Is the credit spendable or withdrawable?** Spendable is a ledger row and
   nothing else. Withdrawable is a payout obligation, a KYC question and a
   different regulatory posture. **Strongly prefer spendable.**
4. **Is it capped?** Uncapped, one referred enterprise account funds the
   referrer indefinitely. A per-referral ceiling costs one line.

**Why `REFERRAL` must be its own ledger kind**, not `ADJUSTMENT`: the funnel,
the revenue reports and the "how much of this balance was paid for" question
all key on kind. Folding referral credit into adjustments makes staff-granted
credit and earned credit indistinguishable forever.

### How this relates to the agency tier

They are different mechanisms and should stay separate. A **referral** is a
one-time reward for introducing an account. An **agency commission** is an
ongoing share of revenue from accounts it manages. Same money, different
lifecycle — one is a ledger row, the other is a recurring computation and
eventually a payout run. Building them as one thing means the simple case
carries the complexity of the hard one.

---

## 8. Test numbers

`/verified-numbers` exists and does most of this: a customer adds a number,
receives a code, confirms it, and can hold several. The backend is
`api/services/telephony/verified_numbers.py` with 32 tests.

Two things are not what you may expect:

1. **Delivery is off.** `VERIFICATION_CHANNEL=log` writes codes to the log
   rather than sending them, and `REQUIRE_VERIFIED_TEST_NUMBER=false` means
   nothing enforces verification. Both deliberate — enabling the requirement
   while the channel is `log` would refuse every test call.
2. **These are the customer's numbers, not ours.** If what you want is
   *Decibyl provides 2–3 numbers a new account can call to try the product
   without owning a number*, that does not exist. It is a different feature:
   a small pool of platform-owned numbers, an inbound route to a demo agent,
   and a per-account call cap so the pool is not someone's free telephony.
   Worth confirming which of the two you meant.

---
## 9. Checklist

Everything above, as one list. Ticked means built and verified in this repo —
not that it is switched on in production.

**Done and merged**

- [x] Signup bonus of $5 (`SIGNUP_BONUS_MICROS_USD=5000000`), as trial credit, once per org, no GST
- [x] Staff tier that only reviews verifications (`StaffRole.SUPPORT`)
- [x] Super admin tier (`StaffRole.SUPERADMIN`)
- [x] Customer registers and verifies their own test numbers (`/verified-numbers`)
- [x] Managed tiers as a customer-facing choice, vendor-agnostic and env-overridable
- [x] Simple/advanced model picker per slot ("Decibyl provides it" vs "My own key")
- [x] Price book accurate as of 2026-08, with `_inr()` and `_blend()` handling rupee vendors and two-sided token pricing
- [x] Managed markup at 1.4× (`MANAGED_PROVIDER_MARKUP_BPS=14000`)
- [x] Markup editable without a deploy, effective-dated, behind a code to `hello@decibyl.ai`
- [x] BYOK earns the platform fee and nothing else — locked by test, not by convention
- [x] ElevenLabs multilingual v2 rate row, Azure speech-to-speech row, Flux STT rows
- [x] One vendor key covers every slot it can authenticate, on both the customer and the staff vault
- [x] Model picker tabs show each slot's current setting instead of only its name

**One setting each — no code**

- [ ] **Turn managed models on.** Store platform keys for Google (LLM), Sarvam
      (transcriber, voice) and OpenAI (speech-to-speech, embeddings) at
      `/superadmin/provider-keys`. Until then the entire no-key offering is dark.
- [ ] **Turn on OTP delivery.** `VERIFICATION_CHANNEL`, then
      `REQUIRE_VERIFIED_TEST_NUMBER`. Blocked on DLT registration, not on us.
- [ ] **SMTP.** Now load-bearing: without it the markup can be staged but never
      applied, and no invoice, receipt or low-balance warning is delivered.

**Small, contained**

- [ ] **Formatting-only commit.** `black` reformats **103** files nobody touched.
      `pre-pr-drift-check` fails on any difference, so the first PR raised from
      any branch fails CI regardless of content. Do it alone, never mixed in.
- [ ] Collapse or differentiate `fast` / `lite` / `zen` — all three resolve to
      `gemini-2.5-flash-lite`. Collapsing is customer-visible: stored configs
      naming `zen` must keep resolving.
- [ ] Measure `LLM_INPUT_SHARE = 0.7` against real traffic. Every LLM margin
      figure depends on it and it is currently an assumption.
- [ ] Validate endpointing against production traffic. Instrumented and
      deployed, never checked — p50 per language, and how far behind the
      non-Flux path runs.
- [ ] Reconcile the README's "at cost, with no markup" with the 1.4× on managed.
      True for BYOK, not for managed (`PRICING-REVIEW.md` §4).

**Known limits to decide about, not bugs**

- [ ] **Google cannot serve managed speech.** Cloud STT and TTS authenticate
      with a service-account JSON; the key vault stores API-key strings only, so
      there is nowhere to put one. Gemini (key-based) is fine, which is why the
      default LLM tier works. Pointing `MANAGED_STT_DEFAULT` at Google would
      fail at dial time. Needs a `credentials` path in the vault, or Google stays
      LLM-only on the managed tier.
- [ ] **Languages outside Flux stay slow.** Tamil, Telugu, Kannada, Marathi and
      Bengali fall back to Nova-3 and pay the full VAD silence wait — roughly
      half a second per turn Hindi and English no longer pay. Deepgram's gap,
      ours to explain.
- [ ] **Ultravox and Grok bill per minute**, we record tokens. Deliberately
      unpriced rather than wrongly priced; needs a per-minute costing path
      before either is offered.

**Real features, in dependency order**

- [ ] `POST /organizations/members` — admin creates an account
- [ ] Enforce `OrganizationRole` so `member` actually means something
- [ ] Referral: attribution column, `CreditLedgerKind.REFERRAL`, 20% of first **net** payment
- [ ] Agency tier: `managed_by_organization_id`, `commission_bps`, and one tested "orgs I may act on" seam
- [ ] Agency commission reporting and payout run
- [ ] Failure reasons as a first-class field — `queued_runs.refusal_reason` exists
      but is not generalised, so "why did this call fail" is unanswerable fleet-wide

**Operational gaps that will bite**

- [ ] **Refunds.** `payments` records settlement; nothing reverses one. A
      chargeback today leaves credit granted with no way to claw it back — and
      referral credit makes that worse.
- [ ] **Dunning past low-balance.** What happens to a *live* call when credit
      runs out mid-conversation needs a decided answer, not an emergent one.
- [ ] **Platform key rotation.** Managed inference runs on our keys. There is no
      rotation path, and revoking one silently breaks every managed account at
      once. The one-key fan-out makes rotation easier, not safer.
- [ ] **Spend velocity ceiling.** Concurrency is capped; spend is not. A stolen
      card plus a loop is an unbounded vendor bill.
- [ ] **Backup restore rehearsal.** `scripts/rehearse_restore.sh` exists — has it
      ever been run against production-shaped data?
