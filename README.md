# Decibyl

Decibyl is where you hire an agent that remembers.

Two kinds of people, one machine behind both:

- **A person.** The warranty that runs out in March. The tailor you used before
  the last festival. Which Arun is the one from college. You tell it once, on
  WhatsApp, and you can ask it back months later.
- **A business.** Who rang while nobody picked up. What the supplier agreed to
  on the call. Why you chose the printer you chose, and who you chose them
  over. It answers the phone, reads the documents, keeps the promises it heard,
  and once a week says what it learned.

Voice was the first channel and is still the hardest, which is why so much of
this repository is telephony and speech pipelines. It is not the product. An
agent does its job on whatever channel the job needs — somebody calls, somebody
messages on WhatsApp or email or web chat, or nothing happens but the clock.

## What "remembers" means here

Two stores, and the difference between them is the whole design.

**Postgres is what is confirmed.** The account's own record: the contacts, the
documents, the numbers, what somebody typed in and meant.

**The graph is what was implied.** A call, a thread message or a document goes
in as an episode; what it implied about people, promises, decisions and dates
comes back out with time attached. Every answer from it is labelled `inferred`
until somebody corrects it — and a correction ("no, Arun is from college, not
work") writes a confirmed fact that outranks the inference from then on.

An agent that guesses and says so is useful. One that guesses and sounds
certain is a liability, so the labelling is enforced in code rather than asked
for in a prompt.

Memory is quiet by rule: at most one unprompted message a day per account, and
a week in which nothing happened is one line saying so.

**Personal memory and business memory never share a partition.** A personal
workspace is its own organization, which is the tenancy boundary the rest of
the platform already enforces — so what you tell Decibyl about your family is
not readable from your employer's account.

The graph is optional infrastructure. With `KNOWLEDGE_GRAPH_URL` unset, every
memory feature answers "no graph" quietly and everything else works exactly as
it did.

## Start here

- [Application overview and setup](echowave/README.md)
- [Development guide](echowave/DEVELOPING.md)
- [Contributor instructions](echowave/AGENTS.md) — read this before changing anything
- [Frontend](echowave/ui/) — Next.js dashboard
- [Backend](echowave/api/) — FastAPI services, voice runtime, memory graph
- [Python SDK](echowave/sdk/python/README.md) and [TypeScript SDK](echowave/sdk/typescript/README.md)
- [Documentation](echowave/docs/README.md)
- [Platform audit and improvement backlog, 7 September 2026](echowave/docs/audits/2026-09-07-platform-review.md)

The production application lives in [`echowave/`](echowave/). The top-level
`frontend/` and `backend/` directories are retained legacy preview scaffolding —
use the nested application's setup instructions and tests when contributing to
the live product.
