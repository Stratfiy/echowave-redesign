# Decibyl - Project Overview

**Decibyl is an intelligent agent that grows and evolves with you.** That is
the founder's sentence and the anchor for every product string. It is one
personal assistant for life and work: talk in your language, give it a task,
and let it help you follow through. It learns your preferences and, with your
approval, gets better at your work over time. Agents — assigned jobs that
answer the phone, confirm orders, answer from your documents or run every
morning — are how it follows through, on whatever channel the job needs.

**Voice is one channel, not the product.** It was the first one and it is still
the hardest, which is why so much of this codebase is telephony and pipelines.
But `CallDirection` in `services/agent_templates/_base.py` has four values and
only two of them ring a phone:

| Direction | What starts it | Needs a number? |
|---|---|---|
| `inbound` | somebody calls | yes |
| `outbound` | a campaign dials | yes |
| `message` | somebody messages — WhatsApp, email, web chat, Slack | no |
| `scheduled` | nothing but the clock | no |

That file says it plainly: *"Named for calls because calls were all there was."*
`CALLING_DIRECTIONS` is the frozenset that decides which of the four are on a
phone, and the badge, the price, the validator and the hire flow all read it —
so anything that assumes "agent" means "call" is a bug waiting to happen.

**Why this matters for almost every decision in here.** The two halves have
different shapes, and code that treats the whole product as voice gets both
wrong:

- **Voice** is stateful, CPU-bound and latency-critical. A call cannot wait for
  a cold start, cannot be retried, and its worker is a uvicorn process holding
  a WebSocket.
- **Everything else** — chat, the builder, routines, WhatsApp, knowledge — is
  stateless, IO-bound and tolerant. It scales like an ordinary web app and
  cold-starts fine.

The same split runs through the money: `api/services/billing/` meters each
component in its own native unit — minutes, characters, tokens, months — rather
than in call-minutes, precisely because a routine run and a knowledge answer
have no minutes to bill.

## Project Structure

```
echowave/
├── api/              # Backend - FastAPI application
├── ui/               # Frontend - Next.js application
├── scripts/          # Helper scripts for local development
├── docs/             # Documentation (MDX, rendered by Astro Starlight)
├── pipecat/          # Pipecat framework (git submodule)
├── docker-compose.yaml       # Production/OSS deployment
├── docker-compose-local.yaml # Local development services
```

## Tech Stack

- **Backend**: Python with FastAPI
- **Frontend**: Next.js 15 with React 19, TypeScript, Tailwind CSS
- **Database**: PostgreSQL with SQLAlchemy (async)
- **Cache/Queue**: Redis with ARQ for background tasks
- **Storage**: MinIO (S3-compatible) for audio files

## Local Development

Contributor setup and service startup are documented in `docs/contribution/setup.mdx`.

## Environment Configuration

- `api/.env` - Backend environment variables. Source this when running repo-owned backend scripts against the dev DB (e.g. `python -m scripts.dump_docs_openapi`).
- `api/.env.test` - Test-only environment variables. Source this when running pytest so tests hit the test DB and never the dev/prod credentials in `api/.env`.
- `ui/.env` - Frontend environment variables

Typical invocation:

```bash
# Tests
source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/...

# Backend scripts
source venv/bin/activate && set -a && source api/.env && set +a && python -m scripts.dump_docs_openapi
```

## Product decisions that are already settled

Written down because they keep being re-litigated after a context reset, and
because re-deciding a settled thing wastes the founder's time twice: once
explaining it again, once undoing whatever was built instead.

**Never send anybody to another screen to finish something.** If a person is
in the chat and an app is not connected, the answer is a connect chip *in the
thread* — not "go to Marketplace → Tools". `connector_offer` exists for this
and `connected_tools.apps_block` tells the model so explicitly. The same rule
applies to every other dead end: name the wall, offer the way past it where
the person already is (`services/workflow/blocked.py`).

**The chat carries its own next steps.** Follow-up chips under a reply, the
way Grok does it — a person should be able to keep going by tapping, not by
composing. The home screen has them (`home_openers.py`, rendered by
`HomeAboveTheFold`); the thread is where they matter most.

**Every feature must work on the lowest tier with no phone number.** A feature
that only exists once somebody has bought a number is a feature most people
never see.

**No pricing shown to users; the positioning is fixed.** The founder decided
on 9 Oct 2026: no price, plan name, credit rate or upgrade prompt appears
anywhere a user can see it (staff and admin cost screens excepted, and the
checkout stays behind `free_mode`), and the positioning is "an intelligent
agent that grows and evolves with you". That includes what a model says
aloud. `ui/src/lib/pricing.ts` and `api/constants.py` (`PRICES_SHOWN`) hold
the switch; `pricingGuard.test.ts` and `test_prompts_quote_no_prices.py` fail
on new price copy in the UI and in the persona and builder prompts. Any new price or
plan copy still needs the founder, as do the public site copy and the bot
shelf.

**Verify against a running instance before saying something works.** Three
bugs in one afternoon were invisible from the source and obvious from one
API call against the real account: tools chosen alphabetically, a rate whose
numerator and denominator came from different populations, and a sync that
only fired from one screen. Comments asserting behaviour are not evidence —
two of those three contradicted a comment that said the opposite.
