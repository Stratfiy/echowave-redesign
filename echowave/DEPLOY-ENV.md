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
docker compose up -d api worker
```

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

The markup applies to **stt, llm and tts on our keys only**. Telephony and the
platform fee are never marked up, and an account on its own key produces no
provider line to mark up at all — BYOK earns the platform fee and nothing else.
There is a test holding that invariant (`test_provider_markup.py`).

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
