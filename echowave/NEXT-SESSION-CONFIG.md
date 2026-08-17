# Next session — configuration handover

Written 17 Aug 2026, at the end of the session that fixed Google sign-in.
Read `NEXT-SESSION.md` for the standing engineering handover; this file is
specifically about **what you are about to plug in**, what it will do, and what
is not audited yet.

Production and `main` are both on `7f5ae49`.

---

## 1. Read this before you paste anything

**Two audits died before producing findings** — the money/Razorpay path and the
provider-keys/telephony path both hit a session limit. Those are exactly the two
things you are about to configure. Treat them as **un-audited**. The auth path
*was* audited and fixed; that work is live.

So the first task next session is to re-run those two audits, **before** the
end-to-end test, not after. If something is wrong in the money path you want to
know before a real payment, not during one.

---

## 2. Provider keys — the I/O contract

You asked specifically about this, and specifically about the **customer-facing**
page. The headline is in §2.1: the shape is wrong, and it is a UI problem rather
than a schema one, which makes it cheap to fix.

### 2.1 The actual complaint — one key, pasted three times

**Our page is component-first. Vapi's is provider-first. That is the whole
difference.**

Today `/provider-keys` renders three sections — Transcriber, Language model,
Voice — each with its own *Add key* button. So the customer's first decision is
*which slot am I filling*, and the provider is the second. A customer with one
Sarvam account holding one Sarvam key therefore meets that key three times, once
per slot, and reasonably concludes we are asking for three different things.

Vapi asks the opposite question first: *which vendor do you have an account
with*. One key per provider, entered once. Categories are how the list is
grouped for reading, not a thing you fill in one at a time.

**The good news: the backend already supports the right shape.** `PUT
/api/v1/provider-keys` takes `apply_to_all_components`, which stores one key
against every component that vendor serves in a single transaction, and the UI
already computes the "also serves" set from the registry
(`components_for_provider`). Sarvam and ElevenLabs each serve all three.

So the fix is an **inversion of the page, not a migration**:

- List **providers**, grouped by category for reading (Transcription / Model /
  Voice), the way Vapi groups them. Telephony is deliberately *not* one of these
  categories: carrier credentials live on a telephony configuration, which also
  models the KYC that comes with a phone number. Do not fold it in.
- One *Connect* action per provider. Ask for the key once.
- Derive the components from `components_for_provider` and send them; stop making
  the customer tell us something the registry already knows.
- Show state per provider — **Connected / Not connected**, with the masked last
  four and the label — rather than a key repeated across three sections.
- Keep the multi-component case honest: when a vendor serves several slots, say
  so on the card ("covers transcription, model and voice") rather than silently
  writing three rows.
- Keep the escape hatch. Two keys with one vendor on separate billing is a real
  case — it is why `apply_to_all_components` defaults false. Make it an
  "advanced: use a different key per component" affordance, not the default path.

The storage model stays `(organization_id, component, provider)`. That is fine
and should not change — it is what lets the escape hatch exist at all. Only the
question order changes.

### 2.2 There are two separate vaults

| | Customer BYOK | Platform (yours) |
|---|---|---|
| Route prefix | `/api/v1/provider-keys` | `/api/v1/platform-credentials` |
| Screen | `/provider-keys` | `/superadmin/provider-keys` |
| Scope | one organization | whole platform |
| Who | org ADMIN writes, any member reads | superuser only |
| Purpose | customer pays the vendor directly | you pay, customer is billed at your rate |

**The one you will use for your own keys is the superadmin one.** Keys you add
there are what makes an account "managed" — a customer with no key of their own
runs on yours and is metered against your rate card.

### 2.3 Input

`PUT /api/v1/provider-keys` (and the platform equivalent):

```json
{
  "component": "stt | llm | tts",
  "provider": "openai",
  "api_key": "sk-...",              // min 8 chars, write-only from here on
  "label": "optional, max 128 chars",
  "apply_to_all_components": false
}
```

`apply_to_all_components` is worth knowing about: Sarvam and ElevenLabs each
serve all three components on **one** vendor key. Set it true and one paste
covers STT, LLM and TTS in a single transaction. It is off by default because
holding two keys with one vendor on separate billing is a real case, and
silently overwriting the other one would be worse than the extra typing.

### 2.4 Output — there is no read path, by design

Every response returns:

```json
{
  "id": 12,
  "component": "stt",
  "provider": "sarvam",
  "masked_key": "••••4f2a",     // last four characters only
  "label": "Sarvam prod",
  "is_active": true,
  "updated_at": "2026-08-17T…"
}
```

**The key value can never be read back out of the API.** Not masked-with-a-
reveal-button — it is not returned at all. Storage is Fernet-encrypted, keyed by
`PLATFORM_CREDENTIAL_SECRET`. The reasoning: a customer needs to confirm *which*
key is installed, never to retrieve it, and a retrieval endpoint would turn one
stolen session into every key the account holds.

Practical consequence for you: **keep your keys in your own password manager.**
Once pasted here they are one-way. Rotation is re-paste, not read-modify-write.

### 2.5 The other three operations

- `GET ""` — list, masked. Also returns `encryption_configured` so the screen can
  say *why* saving fails rather than showing a generic error.
- `POST "/active"` — `{component, provider, is_active}`. Takes a provider out of
  service **without discarding the key**. This is the one to use while rotating
  at the vendor: agents fail over to managed instead of authenticating with a key
  that is being revoked mid-rotation.
- `DELETE ""` — `?component=&provider=` as query params, not a body.

### 2.6 ⚠️ The gap that will bite you tomorrow

**Nothing validates the key when you save it.** I checked — there is no live
call to the vendor anywhere in the save path. A typo'd, revoked, or
wrong-account key is accepted silently and fails for the first time **on a real
customer call**, where it surfaces as "the agent didn't answer" rather than as
"that key is wrong".

Vapi validates on add and that is the right behaviour. This is the top item on
the providers-page rebuild, and it should land **before** you hand Plivo test
numbers to clients.

Until it does: after adding each key, place one test call on that provider
before trusting it.

### 2.7 Supported providers, by component

Read out of the registry on `7f5ae49`:

- **STT** — assemblyai, azure_speech, cartesia, decibyl, deepgram, elevenlabs,
  gladia, google, huggingface, openai, sarvam, smallest, speaches, speechmatics
- **LLM** — aws_bedrock, decibyl, google, google_vertex, groq, huggingface,
  openai, openrouter, sarvam, speaches
- **TTS** — azure_speech, camb, cartesia, decibyl, deepgram, elevenlabs, google,
  inworld, openai, rime, rumik, sarvam, smallest, speaches, xai

Note `decibyl` is a real provider here (your own managed keys), not a
placeholder.

---

## 3. What you said you'd add — and what each needs

### Razorpay

Set on the box, then `docker compose up -d --force-recreate` (a plain `restart`
does **not** re-read `.env`):

```
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=
```

Without the webhook secret, top-ups are refused with a 503 — that is deliberate,
not a bug. Point the Razorpay webhook at `POST /api/v1/payments/razorpay/webhook`.

**Un-audited.** The code was verified end-to-end on 12 Aug by a previous session
(top-up → webhook → credit → voucher → email), but the audit that would have
re-checked it this session never ran.

### Provider API keys

Per §2 above. Add via `/superadmin/provider-keys` for platform-managed, and
test-call each one because nothing validates them on save.

### Plivo numbers for client testing

This is the one with the most moving parts, and the most likely to disappoint:

- `MANAGED_TELEPHONY_ENABLED` defaults **false**. Managed number provisioning is
  gated behind it and behind Plivo KYC.
- A test call from an account with **no telephony configuration** fails at
  `telephony_not_configured` (`routes/telephony.py`) *before* the verification
  gate. So handing a client a number is not by itself enough — the account they
  test from needs a telephony configuration, or platform origination via the
  managed path.
- **Call transfer raises `NotImplementedError` on Plivo** (also Vonage, Vobiz).
  Only Twilio and Telnyx implement it. If your test script includes "transfer to
  a human", it will fail on your actual carrier.

### DLT — you said you've applied and are waiting

Nothing to do until approval lands. When it does:

1. Set `VERIFICATION_CHANNEL=plivo_sms` (or `twilio_sms`).
2. **Match the code to the approved template, not the other way round.**
   `_body()` in the verification service is asserted character-for-character in a
   test. Operators match on the registered template and reject a message that
   differs by a full stop.
3. `REQUIRE_VERIFIED_TEST_NUMBER` can then be flipped true. The test asserting it
   is false (`test_verified_numbers.py::TestTheGateDefault`) **exists to be
   deleted** in that same change.

---

## 4. What more you need to provide — you asked

Beyond what you listed:

### Blocking, and cheap

1. **`UI_APP_URL`** (or `DECIBYL_APP_HOST`) must be set. It builds invitation
   links *and* partner referral links. Unset, it silently falls back to
   `http://localhost:3010` and both are dead on arrival. Verify:
   `docker compose exec api env | grep -E 'UI_APP_URL|DECIBYL_APP_HOST'`
2. **`PLATFORM_CREDENTIAL_SECRET`** must be set or **no key can be stored at
   all** — and any MFA operation 500s.
3. **Confirm Google sign-in now works.** The blocker was ours and is fixed, but
   also check: the authorised redirect URI is `https://<API-host>/api/v1/auth/
   google/callback` — the **API** host, not the app host — and the consent screen
   is **Published**, not in Testing.
4. **SMTP/Resend confirmed live**, or invitations only work via the copy-link
   shown once on `/settings`.

### Decisions only you can make

5. **GST: does a prepaid top-up get a tax invoice, or a receipt voucher with the
   tax invoice on consumption?** This changes which emails we send and is a
   genuine legal question. **Ask a GST practitioner** — I could not resolve it
   from public sources.
6. **Auto-recharge default amount ≤ ₹15,000.** RBI caps recurring debits without
   additional-factor auth at that. Above it, every top-up needs AFA and
   unattended recharge breaks. Confirm the current threshold with Razorpay — the
   framework was revised in 2026 and reporting is inconsistent.
7. **RBI requires a pre-debit notification ≥24h before every auto-recharge**,
   with a per-transaction opt-out. This is mandatory, not a courtesy, and it does
   not exist yet.

### Things I'd want before real customers

8. A **superuser account** for yourself — `scripts/grant_superuser.py` is the
   only way in on a Docker install.
9. **Seed the rate card** if not already done:
   `docker compose exec api python -m scripts.seed_provider_rates`. Without it
   every call reports ₹0 provider cost and 100% margin.
10. **A monitored alert on the ARQ worker.** Costing runs there. If it dies,
    calls complete normally and are never costed — free calls, silently,
    indefinitely. A liveness check exists; make sure it pages someone.

---

## 5. Known-broken and known-missing, as of `7f5ae49`

Not blockers for a test, but do not be surprised:

| Thing | State |
|---|---|
| `/provider-keys` shape | **Component-first** — the same vendor key must be entered once per slot. Should be provider-first, like Vapi. See §2.1 |
| Provider key validation on save | **Missing** — a dead key is accepted and fails on a live call |
| Call transfer on Plivo | `NotImplementedError` |
| Knowledge-base document upload | Broken — calls MPS, which does not resolve |
| `stt:openai` rate | May still be missing → undercosted calls |
| Cached LLM tokens | Billed at full rate; unit-economics screen overstates our cost |
| Mid-call balance enforcement | Absent — a customer who raises their own max duration can outrun their balance |
| Partner statement email | Not built — partners see statements on screen only |
| Statement generation | A button, not a cron. Deliberate: a month must settle first |
| `/auth/login` `?next=` | Ignored, so an invitee lands on the dashboard and must reopen the invite link |
| "Decibyl Tokens" on `/usage` | Legacy credit currency, not LLM tokens. Confusing name, discloses nothing |
| `format.sh` | Reaches into the `pipecat` submodule and dirties it. Revert, don't commit |

---

## 6. Suggested order for next session

1. Re-run the **money** and **provider-keys/telephony** audits (they never ran).
2. Fix whatever they find.
3. **Rebuild `/provider-keys` provider-first** (§2.1) **and validate the key on
   save** (§2.6). One piece of work, not two — both change the same dialog, and
   the validation call is what makes a *Connect* button honest. This should land
   **before** clients get test numbers, because both failures currently surface
   as "the agent didn't answer".
5. Notifications + OTP, built on Resend. The competitor research is done and the
   headline is: your low-balance ladder already beats Vapi, Retell, Bland and
   Synthflow — none of them documents a low-balance email at all. The real gaps
   are payment receipts, security alerts, the RBI pre-debit notice, and a weekly
   usage summary.

---

## 7. Environment notes for whoever picks this up

The container is ephemeral and was restarted mid-session. To get a working test
environment back:

```bash
cd /home/user/echowave-redesign/echowave
python3.13 -m venv .venv && source .venv/bin/activate   # 3.12/3.13 ONLY
./scripts/setup_requirements.sh --dev                    # must run INSIDE the venv
apt-get install -y postgresql-16-pgvector                 # server extension, not the wheel
pg_ctlcluster 16 main start && redis-server --daemonize yes
cd api/mcp_server/ts_validator && npm install             # or 6 mcp tests fail as "bridge_error"
```

Tests:

```bash
cd api && source ../.venv/bin/activate && \
export PYTHONPATH=/home/user/echowave-redesign/echowave \
  DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/test_db \
  REDIS_URL=redis://localhost:6379/0 ENABLE_AWS_S3=false \
  MINIO_PUBLIC_ENDPOINT=http://localhost:9000 DEPLOYMENT_MODE=oss ENVIRONMENT=test && \
pytest tests/ -q
```

Baseline on `7f5ae49`: **3372 passed, 10 skipped, 0 failed.** Anything else is
a regression you introduced or an environment step missed above.

Deploys fire automatically on push to `main`, or Actions → Deploy → Run workflow
with a `ref` to deploy any branch without merging. Production is not reachable
from the agent container — diagnose from code, and say so rather than implying
you observed the live system.
