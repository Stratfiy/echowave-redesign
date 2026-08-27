# Environment keys to append

Everything built in this round that needs a value in production, in one place.

**Where.** `/home/ubuntu/echowave-redesign/echowave/.env` on the box. The whole
file is injected into the API container (`env_file` in `docker-compose.yaml`),
so a new name reaches the application without editing compose. `ci_deploy.sh`
deliberately never touches `.env` — configuration is the operator's, not the
repository's.

**After editing:**

```bash
cd /home/ubuntu/echowave-redesign/echowave
docker compose up -d --force-recreate api
```

There is no `worker` service — `docker compose up -d api worker` fails with
`no such service: worker`. The arq worker, the ARI manager and uvicorn all run
inside the `api` container under supervisord (see `api/Dockerfile`), so
restarting `api` restarts all of them.

`--force-recreate` because Compose compares the service definition, not the
*contents* of `env_file`: without it a changed `.env` can leave the old
container running with the old values, which reads as "the new key did not
work".

The schema migration (`e1f5b2c94a70`, the managed-markup history) needs no
separate step — `ci_deploy.sh` runs `alembic upgrade head` on every deploy.

Nothing here is a secret in this file. Fill the values in on the box.

---

## 1. Required — the managed tier does not work without these

```bash
# Encrypts every platform provider key at rest. No default on purpose: without
# it, storing a key raises rather than falling back to a value published in
# source. Generate once and never rotate without re-entering every key.
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
PLATFORM_CREDENTIAL_SECRET=

# The vendor keys the managed tier spends. Read at boot and written into the
# encrypted vault, after which they behave exactly like keys typed into
# /superadmin/provider-keys — rotatable, maskable, never read back.
#
# Naming is PLATFORM_KEY_<COMPONENT>_<PROVIDER>, split on the first underscore.
# Components are stt, llm, tts only. Embeddings and speech-to-speech have no
# slot of their own — both authenticate on the LLM credential, because the
# vendor issues one key for all three.
PLATFORM_KEY_LLM_GOOGLE=          # default/fast/lite/zen LLM tiers
PLATFORM_KEY_LLM_OPENAI=          # "accurate" tier, and all managed embeddings
PLATFORM_KEY_LLM_OPENAI_REALTIME= # speech-to-speech; same OpenAI key value
PLATFORM_KEY_STT_SARVAM=          # managed transcription
PLATFORM_KEY_TTS_SARVAM=          # managed voice
```

An empty variable means "not this one yet" and is skipped quietly. A key that
is set but cannot be stored is logged with its component and provider and never
its value, and does not stop the process booting — so check the API log after
the first restart rather than assuming.

Add `PLATFORM_KEY_STT_DEEPGRAM=` if you want Flux available on the managed
tier; the rate rows exist, the key does not.

## 2. Pricing

```bash
# What managed model usage is charged at, in basis points of what the vendor
# charges us. 14000 = 1.4x. 10000 would be at cost.
#
# This is now only the SEED. Once the markup is changed through
# /superadmin/billing (Managed model markup → send me a code), the database
# history wins and this variable is never read again. Set it anyway: an empty
# history falls back to it, so it is what a fresh box prices at.
MANAGED_PROVIDER_MARKUP_BPS=14000

# Free credit for a new account, in micro-dollars. 5000000 = $5.
# Lands as a `trial` ledger row, so given credit stays separable from bought
# credit in every revenue report. Set to 0 to switch it off.
SIGNUP_BONUS_MICROS_USD=5000000
```

The markup applies to **stt, llm, tts and telephony on our keys** —
`cost_engine.MARKED_UP_COMPONENTS`. Carriage was excluded once, on the belief
that its rate card row held the *sell* price; the rows never held that, so
carriage was being resold at cost. One rule now covers every vendor line: the
number in the rate card is what the vendor charges us, and the markup is what
we add.

**The platform fee is never marked up** — it is ours, and a margin on our own
margin is not a number anyone can defend. An account on its own key produces no
provider line to mark up at all; it pays an *uplifted platform rate* instead,
sized by which component it brought (`BYOK_TTS_UPLIFT_MICROS_USD`,
`BYOK_STT_UPLIFT_MICROS_USD`, gated by `BYOK_TIERED_FEE_ENABLED`). There is a
test holding that invariant (`test_provider_markup.py`), and another asserting
the quote applies the same uplift the invoice does
(`test_byok_quote_matches_invoice.py`).

The platform fee and the per-unit rates themselves are **not** environment
variables — they live in the rate card in the database and are edited at
`/superadmin/billing`.

## 3. Outbound mail — new requirement this round

The markup confirmation code goes to a fixed address, `hello@decibyl.ai`. That
address is deliberately hardcoded (`services/billing/markup.py`) rather than
configurable: the second factor is access to the company inbox, and a
configurable destination is not a second factor.

Mail is off until these are set, and with mail off the markup cannot be changed
at all — the change stages, the send fails, and the UI says so rather than
leaving you waiting for an email that never comes.

```bash
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_USE_TLS=true          # true = STARTTLS on 587; false = implicit TLS on 465
EMAIL_FROM_ADDRESS=        # must be a sender the SMTP account is allowed to use
EMAIL_FROM_NAME=Decibyl
```

Make sure `hello@decibyl.ai` actually receives mail before relying on this.

## 4. Verified test numbers

The screen exists and the flow works; delivery does not, and that is the only
thing missing.

```bash
# log        writes the code to the log, refuses to run outside dev — current default
# plivo_sms  SMS on Decibyl's own Plivo account
# voice      call and read the code out (not wired)
VERIFICATION_CHANNEL=plivo_sms

# Sender number on Decibyl's own account. Must be the number registered against
# the DLT header, or the message is accepted by Plivo and never arrives.
PLATFORM_SMS_FROM_NUMBER=

PLATFORM_PLIVO_AUTH_ID=
PLATFORM_PLIVO_AUTH_TOKEN=

# Or Twilio instead. Having both is about carrier choice — it is NOT a way
# around DLT, which attaches to the sending entity and the Indian destination.
PLATFORM_TWILIO_ACCOUNT_SID=
PLATFORM_TWILIO_AUTH_TOKEN=

# Flip this to true in the SAME change that makes delivery work. With delivery
# on `log` and this true, nobody can verify a number and every test call is
# refused with no way forward.
REQUIRE_VERIFIED_TEST_NUMBER=false
```

**DLT registration is the blocker**, not configuration: SMS to Indian numbers
needs a Principal Entity, a registered header and a registered content
template. Unregistered traffic is blocked by the operator and the failure looks
like success.

Optional limits, all with sane defaults — set only to change them:
`VERIFICATION_CODE_TTL_MINUTES` (10), `VERIFICATION_MAX_ATTEMPTS` (5),
`VERIFICATION_MAX_SENDS` (5), `VERIFICATION_RESEND_COOLDOWN_SECONDS` (60).

## 5. Optional — tier overrides

Each managed tier can be moved without a release. Format is
`MANAGED_<COMPONENT>_<TIER>=provider:model`; provider and model move together,
because a model name is only meaningful to the vendor that serves it.

```bash
MANAGED_LLM_DEFAULT=google:gemini-2.5-flash
MANAGED_LLM_FAST=google:gemini-2.5-flash-lite
MANAGED_LLM_LITE=google:gemini-2.5-flash-lite
MANAGED_LLM_ACCURATE=openai:gpt-4o
MANAGED_LLM_ZEN=google:gemini-2.5-flash-lite
MANAGED_STT_DEFAULT=sarvam:saarika:v2.5
MANAGED_TTS_DEFAULT=sarvam:bulbul:v2
MANAGED_REALTIME_DEFAULT=openai_realtime:gpt-realtime-2
MANAGED_EMBEDDINGS_DEFAULT=openai:text-embedding-3-small
```

Those are the current defaults, so setting them changes nothing. They are worth
writing into `.env` anyway — the file then records what customers are actually
running on, instead of it being knowable only by reading source.

**`fast`, `lite` and `zen` currently resolve to the same model.** Three tier
names, one model behind them. Either give them distinct models or collapse them
in the UI before launch; offering a customer a choice that is not one is worse
than offering fewer options.

## 6. Follow-caller-language

Built and tested, off by default.

```bash
# After two consecutive turns in a new language, the agent's voice follows.
# Needs an STT in multilingual mode to do anything; Deepgram already defaults
# to multi. Realtime speech-to-speech ignores it entirely — those models hear
# the caller directly.
FOLLOW_CALLER_LANGUAGE=true
```

## 7. The Model Proxy Service — what happens if you never set this

`MPS_API_URL` was absent from this file entirely, so a deployment silently
inherited `https://services.decibyl.ai` and depended on a host nobody had
decided to depend on. It is not a secret and there is nothing to fill in for
most installs; it is here because *not* setting it has consequences, and those
consequences should be a choice.

```bash
# The Model Proxy Service. Defaults to https://services.decibyl.ai — Decibyl's
# own hosted service, which is right for the managed product and wrong for a
# self-hosted install that cannot reach it.
MPS_API_URL=

# Which backend converts and chunks an uploaded knowledge base document:
#   local  (default) — in-process, no network call, works with nothing else up
#   mps              — delegate, and surface the outage if MPS is down
#   auto             — MPS when MPS_API_URL is set, local on any failure
KB_DOCUMENT_PROCESSOR=local
```

**What still calls MPS, and what happens without it:**

| Feature | Without a reachable MPS |
| --- | --- |
| Knowledge base ingestion | Works. Converted and chunked in-process. |
| Recording transcription | Works, if the account has its own STT key. Uses the same provider its live calls use. |
| Agent generation | Works. Falls back to building the workflow locally. |
| Voice picker | Works. Migrated to a local catalogue. |
| Service keys | Not applicable — see below. |

Nothing here fails silently any more. The first two rows are the ones that
changed: both had no fallback, so an unreachable MPS meant a customer uploaded
a policy document or a recording and got a transport error.

**Service keys are the exception, and they are not a gap.** A service key is a
credential *for* MPS — the `decibyl` provider's API key is one of these, and it
is validated against MPS's own usage endpoint. A deployment that does not use
MPS has nothing to issue them against and no use for them. The screen now says
so, with a 503 and an explanation, rather than reporting a failure.

So the remaining question is narrower than it looks: **do you sell the
Decibyl-managed model tier?** If yes, `MPS_API_URL` must point at a running
service and `DECIBYL_MPS_SECRET_KEY` must be set. If no — every account brings
its own provider keys — you can leave both unset and nothing is missing.

## 8. Durability — the recovery point, and where the backups live

None of these are required, and leaving all of them unset is a position rather
than a default. Billing and privacy readiness both report which one you are in.

```bash
# Set when the database is managed Postgres with point-in-time recovery. With
# no WAL archiving the recovery point is "since last night's dump" — a failure
# at 17:00 loses that day's calls, costings and top-ups, and the ledger is the
# only record of what customers paid against invoices already issued.
DATABASE_PITR_ENABLED=false

# Records a deliberate decision to live with that gap, in hours. Readiness then
# reports the accepted figure instead of an open finding. Set it only when
# somebody has actually weighed it.
ACCEPTED_RECOVERY_POINT_HOURS=

# An hourly dump of the money tables only — credit_ledger, payments,
# tax_documents, provider_rates, organizations — between the nightly dumps of
# everything. About 25KB an hour on a small deployment, and a read, so nothing
# about it can affect the primary.
#
# This narrows the *irreplaceable* part of the gap from 24 hours to one. Calls
# can be re-made and documents re-uploaded; the ledger cannot be reconstructed
# from anywhere, because Razorpay knows what was charged and nothing about what
# was spent, reserved or adjusted. It is NOT point-in-time recovery and does
# not close the finding above — readiness keeps reporting the real number.
LEDGER_SNAPSHOT_ENABLED=true
LEDGER_SNAPSHOT_RETENTION_DAYS=3

# A second copy of each nightly dump, somewhere the first one's failure cannot
# reach. Backups are otherwise written to a prefix in the same bucket, under
# the same credentials, in the same account as the call recordings — which is a
# backup against hardware failure and nothing else.
#
# Worth configuring only if it is genuinely elsewhere: a write-only identity,
# in a different account, into a bucket with object lock enabled for at least
# BACKUP_RETENTION_DAYS. A mirror sharing the primary's credentials is reported
# as partial rather than as protection.
BACKUP_MIRROR_BUCKET=
BACKUP_MIRROR_REGION=
BACKUP_MIRROR_ACCESS_KEY_ID=
BACKUP_MIRROR_SECRET_ACCESS_KEY=
```

---

## Not environment variables

Worth stating, because they are the three things most likely to be looked for
here:

- **Endpointing / `user_speech_timeout`** — per workflow, in the agent's own
  settings, default 0.4s, bounded 0.15–3.0. Deliberately not global: the right
  value differs between a clinic booking line and an outbound survey.
- **Per-unit rates and the platform fee** — the rate card, at
  `/superadmin/billing`. Effective-dated, so changing one never rewrites what a
  past call was charged.
- **The markup, after the first change through the UI** — the
  `managed_markup_history` table. `MANAGED_PROVIDER_MARKUP_BPS` seeds an empty
  history and is then ignored.

## Still unbuilt

Not oversights — nothing to configure yet, listed so the absence is not
mistaken for a missing key: the agency tier and its commission rate, the
referral wallet credit, admin-creates-account, and per-minute realtime pricing
for Ultravox and Grok (both meter by minute; we record tokens, so they are
deliberately unpriced rather than wrongly priced).
