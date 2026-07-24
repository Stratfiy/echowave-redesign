# EchoWave — Feature Documentation

**EchoWave** is an open-source, self-hostable voice AI agent platform — positioned as an alternative to Vapi and Retell. It lets teams design, test, and run production voice agents through a visual workflow builder, connect them to real phone lines, and let AI coding assistants (via MCP) help build and edit agents. It is a rebrand/redesign of the open-source project **Dograh** (`Stratfiy/echowave`, rebranded for **EchoWave by nAutomation Labs**).

This document catalogs the features present in this repository, which contains two things:

1. **`echowave/`** — the full product monorepo (backend API, Next.js dashboard, SDKs, docs, deployment configs). This is where nearly all product functionality lives.
2. **`frontend/` + `backend/`** — a lightweight scaffold (FastAPI + MongoDB + CRA) used only to preview the redesigned auth UI (`preview.html`) in this workspace; it is not the production app.

---

## 1. Voice Agent Builder

The core product surface: a visual, graph-based editor for designing what a voice agent says and does during a call (`echowave/ui/src/components/flow`, `echowave/api/schemas/workflow.py`).

- **Workflow graph model** — an agent ("workflow" in the API) is a directed graph of **nodes** (conversation stages) connected by **pathways/edges** (LLM-evaluated transition conditions), instead of one long prompt.
- **Node types**:
  | Node | Purpose |
  |---|---|
  | Start Call | Entry point for phone calls; configures the greeting |
  | Agent | LLM-driven conversation step — the core building block |
  | Global | Instructions applied across every node (tone, objection handling, fallback behavior) |
  | QA | Automated post-call quality analysis against defined criteria |
  | API Trigger | Endpoint for external systems (n8n, Zapier, custom backends) to start outbound runs |
  | Webhook | Fires an HTTP request when reached (CRM updates, notifications) |
  | End Call | Configures the agent's final message and terminates the call |
- **AI-generated starting graphs** — describe an "Inbound"/"Outbound" use case in natural language and an LLM scaffolds the initial workflow, ready to edit.
- **Versioning** — every update to a workflow definition is saved as a new version with full history; the latest version is always what runs live.
- **Test Agent panel** — **Test Audio** (real browser mic/speaker testing) and **Test Chat** (fast text-based iteration, including editing/replaying a prior turn to regenerate agent behavior from that point).

## 2. Conversation & Runtime Capabilities

- **Tools** — let the LLM take actions mid-call:
  - Built-in: **Call Transfer** (to phone number or SIP endpoint), **End Call**.
  - Custom: **HTTP API** tool (call any REST endpoint — CRM lookups, automations) and **MCP Tool** (invoke a remote MCP server's tools live during a conversation).
- **Knowledge Base** — upload PDF/DOCX/TXT/JSON documents an agent can reference during calls, with **Full Document** or **Chunked Search** (embedding-based) retrieval modes, attachable per node.
- **Pre-Call Data Fetch** — enrich call context with an HTTP call before the agent starts speaking.
- **Pre-recorded Audio** — mix real audio recordings in alongside LLM-generated TTS speech.
- **Template Variables** — reference `initial_context` (data passed in at call start) and `gathered_context` (data extracted during the call) inside prompts and payloads.
- **Interruption handling** — per-node toggle controlling whether a caller can barge in while the agent is speaking.
- **Human handoff / call transfer** on supported telephony providers.
- **Embeddable widgets** — add a voice agent to a website as a floating, inline, or headless widget (`echowave/api/routes/public_embed.py`, `workflow_embed.py`).

## 3. Telephony & Voice Infrastructure

- **Built-in telephony provider integrations**: Twilio, Vonage, Telnyx, Plivo, Vobiz, Cloudonix, and Asterisk ARI (`echowave/api/services/telephony`, `echowave/docs/integrations/telephony`).
- **Inbound and outbound calling**, including custom/BYO SIP trunk support.
- **WebRTC signaling** for browser-based test/voice calls (`routes/webrtc_signaling.py`, `routes/turn_credentials.py`).
- **Bring-your-own or platform-managed providers** for LLM, STT (transcription), and TTS (voice synthesis) — no API keys required to get started; EchoWave ships default managed models.
  - **LLM**: OpenAI, Google AI Studio, Google Vertex AI, Azure OpenAI, AWS Bedrock, Groq, OpenRouter, Hugging Face, MiniMax, Sarvam, or EchoWave-managed.
  - **STT**: Deepgram, OpenAI, Google, Azure Speech, AssemblyAI, Speechmatics, Cartesia, Gladia, Sarvam, Smallest AI, Hugging Face, ElevenLabs, EchoWave.
  - **TTS**: ElevenLabs, OpenAI, Google, Azure Speech, Deepgram, Cartesia, Smallest AI, MiniMax, Sarvam, Rime, Inworld, Camb.ai, xAI, EchoWave.
- **Speech-to-speech (realtime) model support** in addition to the STT→LLM→TTS pipeline (Azure/OpenAI/Gemini/Grok/Ultravox realtime wrappers).
- **Voicemail detection**, noise suppression (bundled `rnnoise` native module), and audio conversion utilities.
- Artifact storage for recordings via bundled **MinIO** or AWS/S3-compatible storage.

## 4. Campaigns (Bulk Outbound Calling)

Run a workflow against a large contact list automatically instead of triggering calls one-by-one (`echowave/api/services/campaign`, `docs/core-concepts/campaigns.mdx`).

- **CSV contact upload** — a `phone_number` column plus arbitrary extra columns, which become per-call `initial_context` for prompt personalization.
- **Scheduling & concurrency controls** — time-of-day windows, concurrency limits, retry rules.
- **Pause/resume** without losing progress; live progress tracking (processed, completed, failed, pending).
- **From-number pool isolation** for dialing at scale.

## 5. Calls, Runs & Observability

- **Runs** — every workflow execution produces a run record with transcript, recording, extracted/gathered context, and cost data.
- **Tracing** — full visibility into a run's STT/LLM/TTS behavior for debugging and prompt refinement.
- **QA analysis** — automated scoring of completed calls against custom criteria.
- **Reports & usage dashboards** — daily usage tables, organization usage/billing views (`ui/src/app/reports`, `ui/src/app/usage`, `api/services/reports`).
- **Webhooks** — deliver call results to external systems (CRM, Zapier, n8n) on run completion, with delivery tracking/retry (`api/tasks/webhook_delivery.py`).

## 6. Developer & Integration Surface

- **REST API** — full OpenAPI spec covering agents, runs, campaigns, API keys, telephony configs (`docs/api-reference`).
- **MCP Server** — EchoWave exposes its own MCP server so coding agents (Claude Code, Claude Desktop, Codex, Cursor) can list/inspect agents, fetch node schemas, create workflows, and search docs directly from a chat/IDE session, authenticated via API key.
- **SDKs** — official **Python** (`echowave-sdk` on PyPI) and **Node** (`@echowave/sdk` on npm) packages for programmatic agent creation and outbound call triggering.
- **API Keys & Service Keys** — API keys trigger voice agents; service keys authenticate to inference providers.
- **Environment variable & webhook configuration** reference for self-hosters (`docs/developer`).
- **Workflow Definition Schema** — documented JSON schema for programmatic graph generation.

## 7. Platform, Auth & Admin

- **Authentication** — signup/login flows, Google OAuth, organization-scoped accounts (`ui/src/app/auth`, `ui/src/lib/auth`).
- **Organizations** — organization-level configuration/preferences, model configuration defaults, quota service, usage billing (`api/db/organization_*`, `api/services/quota_service.py`).
- **Impersonation & Superadmin** — internal tooling for support/ops (`ui/src/app/impersonate`, `ui/src/app/superadmin`, `api/routes/superuser.py`).
- **Billing** — credits-based billing UI (`BuyCreditsControl.tsx`) and an underlying `billing`/`usage` module (subscription tiers deferred per `REBRAND_HANDOFF.md`).
- **Theming** — light/dark mode with a floating in-app theme switcher.
- **Telemetry** — anonymous usage analytics via PostHog, opt-out via `ENABLE_TELEMETRY=false`; Sentry error boundary for the UI.

## 8. Deployment

- **One-command Docker self-hosting** (`docker-compose.yaml`, `scripts/start_docker.sh`) — local or remote server, with HTTPS/custom-domain support.
- **AI-agent-assisted setup** — an official Claude Code / Codex plugin (`echowave-hq/echowave-plugins`) that detects the OS, picks a deploy path, and runs setup/verification for you.
- **Managed cloud offering** at `app.echowave.com` as an alternative to self-hosting.
- **Scaling & update guides** for production deployments (`docs/deployment`).

---

## This Repository's Redesign Work (Dograh → EchoWave)

Per `echowave/REBRAND_HANDOFF.md`, this pass rebranded the open-source **Dograh** fork into **EchoWave** and redesigned the auth surface:

- **Global rebrand** — `Dograh`/`dograh`/`DOGRAH` → `EchoWave`/`echowave`/`ECHOWAVE` across UI, root docs, and Docker Compose files. Intentionally left untouched: external `dograh.com` URLs not yet owned, the `dograh_sdk` Python package name, the `pipecat` submodule, and existing `DOGRAH_*` env vars (to avoid breaking the live deployment).
- **New brand assets** — SVG wordmark/inverse wordmark/app icon (`ui/public/echowave-*.svg`), with `BrandLogo.tsx` updated to use them.
- **Redesigned auth experience** (`ui/src/components/auth`, `ui/src/app/auth`) — a new two-column shell with an animated CSS waveform, gradient brand panel (light/dark variants), feature chips (*Speech-to-speech*, *MCP-native*, *BYOK · any model*, *Self-hostable*), a solid-CTA "Talk to Sales" card, and a floating theme toggle. Light theme is now the default.
- **In-container preview** — `frontend/public/preview.html` is a static HTML mirror of the redesigned sign-up/sign-in pages (tab switcher, live theme toggle) with `frontend/src/App.js` redirecting `/` to it, so the redesign is viewable without running the full `echowave/` stack.
- **Explicitly out of scope this pass**: a custom docs site under the new domain, a marketing landing page, Stripe billing/subscription tiers, and an audit of transactional email templates for lingering "Dograh" strings.

---

*Generated by walking the repository tree, `echowave/README.md`, `echowave/REBRAND_HANDOFF.md`, and the `echowave/docs/` documentation set (core-concepts, voice-agent, integrations, configurations, api-reference).*
