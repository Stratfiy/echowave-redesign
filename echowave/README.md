# Decibyl

Build production voice agents with a visual workflow builder — a real-time
speech pipeline with telephony and WebRTC, a drag-and-drop builder, and an MCP
surface so coding assistants can design and edit agents directly.

**This repository is private. Decibyl is a commercial product, not open source.**

<p align="center">
  <img src="docs/images/hero.gif" alt="Decibyl in action — build a workflow, launch a voice agent, talk to it" width="80%">
</p>

## Running it locally

Requires Docker, and Python 3.13 if you are working on the backend.

```bash
git submodule update --init --recursive
chmod +x scripts/start_docker.sh && ./scripts/start_docker.sh
```

First startup takes two to three minutes while images download. Then open
<http://localhost:3010>.

Contributor setup — virtualenv, `.env` templates, running the test suite — is in
[`docs/contribution/setup.mdx`](docs/contribution/setup.mdx). Common problems
are in [`docs/getting-started/troubleshooting.mdx`](docs/getting-started/troubleshooting.mdx).

## Your first agent

1. Open <http://localhost:3010>.
2. Pick **Inbound** or **Outbound**, name the agent, and describe the use case
   in a few words — *"screen insurance form submissions for purchase intent"*.
3. Click **Test Agent**.
4. **Test Audio** talks to it in the browser; **Test Chat** iterates faster in
   text, and lets you edit or replay a user turn and regenerate everything
   after it.

Browser testing needs no telephony and no verification. Phone numbers do — see
Verification in the app.

## Layout

| Path | What |
|---|---|
| `api/` | FastAPI backend — routes, services, pipeline, billing |
| `ui/` | Next.js 15 frontend |
| `docs/` | Mintlify documentation |
| `pipecat/` | Speech pipeline framework (git submodule) |
| `sdk/` | Python and TypeScript client SDKs |
| `deploy/` | Helm chart and deployment assets |

Each subtree has an `AGENTS.md` with the conventions that apply inside it.

## Reference documents

- **[`DEPLOY.md`](DEPLOY.md)** — going live: what to configure, in what order,
  and how to create the first admin account. Two steps have an ordering that
  only bites once.
- **[`PRIVACY.md`](PRIVACY.md)** — retention, erasure, export and access
  logging, what DPDP and GDPR each require, and which obligations code cannot
  discharge for you.
- **[`DASHBOARD.md`](DASHBOARD.md)** — how a call is priced, what every billing
  number means, and the invariants the money code holds. Read this before
  touching anything under `api/services/billing/`.
- **[`KNOWN_ISSUES.md`](KNOWN_ISSUES.md)** — open problems, each with what is
  wrong, why, and what fixing it involves.
- **[`CONTRIBUTING.md`](CONTRIBUTING.md)** — branching, tests, and where to
  report things.

## Pricing model

A platform fee plus provider costs passed through **at cost, with no markup** —
enforced by the schema rather than by convention: provider cost and platform fee
are separate rows and separate columns, and nothing anywhere stores a blended
number.

Time is billed in **15-second pulses** rather than whole minutes, so a
62-second call bills 75 seconds and not 120. Every price is set in the admin
dashboard under **Billing → Rate card**.

Accounts are **prepaid**: credit is bought up front from **Billing**, and usage
draws it down. Only a signature-verified Razorpay webhook credits an account —
never the browser reporting success. A run on an account with no credit is
refused, and each live call holds an estimate so concurrent calls cannot spend
the same rupee twice.

GST is added on top of the credit price and never enters the ledger, which stays
tax-exclusive end to end. Each payment produces a receipt voucher and each month
a tax invoice for actual usage; supply outside India is zero-rated under LUT.

## Support

- Security vulnerabilities: <security@decibyl.ai> — privately, please.
- Everything else: <support@decibyl.ai>.
