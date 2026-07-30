# Contributing to Decibyl

Decibyl is a voice agent platform: a drag-and-drop workflow builder on top of a
real-time speech pipeline, with telephony and WebRTC.

Decibyl is **not open source**. This repository is private, so there is no fork
step and no public issue tracker — clone it directly and branch.

## Getting set up

Setup lives in [`docs/contribution/setup.mdx`](docs/contribution/setup.mdx): the
pipecat submodule, the Python virtualenv, the `.env` templates and the local
service stack.

Two things catch people out:

- **The project requires Python 3.13** (`api/pyproject.toml`). An older
  interpreter produces a wave of pydantic `TypedDict` errors that look like code
  bugs and are not.
- **`pipecat` is a git submodule.** Without `git submodule update --init
  --recursive`, most of the test suite fails to collect.

## Where things live

| Area | Path | Notes |
|---|---|---|
| Backend | `api/` | FastAPI. See `api/AGENTS.md`. |
| Frontend | `ui/` | Next.js 15. See `ui/AGENTS.md`. |
| Docs | `docs/` | Mintlify. |
| Pricing and billing | `api/services/billing/` | Read `DASHBOARD.md` first. |

The `AGENTS.md` file in each subtree carries the conventions that matter there.
Two are worth reading before a first change, because both guard against mistakes
that are easy to make and hard to catch in review: the organization-scoping
rules in `api/AGENTS.md` (skipping one is a tenant-isolation bug, not a style
issue) and the API error handling rules in `ui/AGENTS.md` (the generated client
does not throw on HTTP errors).

## Making a change

1. Branch from `main`.
2. Run the tests:
   ```bash
   set -a && source api/.env.test && set +a && python -m pytest api/tests -q
   ```
3. Push to `origin` and open a pull request against `main`.

For anything touching money, read [`DASHBOARD.md`](DASHBOARD.md) first. The
billing code holds invariants — integer paise, round once at the line item, the
invoice total defined as the sum of its own line items — that are asserted by
tests rather than left to review. A change that breaks one fails loudly, which
is the intent.

## Reporting problems

- **Security vulnerabilities**: email <security@decibyl.ai> privately. Please
  do not open an issue.
- **Everything else**: email <support@decibyl.ai>.

Open problems are tracked in [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) with what is
wrong, why, and what fixing it involves.
