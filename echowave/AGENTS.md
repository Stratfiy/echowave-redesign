# Decibyl - Project Overview

Decibyl is an **agent platform for Indian businesses**. A business hires a bot
for a job — answering the phone, confirming orders, chasing payments, answering
staff questions from its own documents, filing a reminder every morning — and
the bot does that job on whatever channel the job needs.

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
