# Decibyl.ai — Product Overview

*(Rename in progress: the product is moving from "EchoWave" to "Decibyl.ai." This document and the rest of the docs in this pass use the new name. The underlying codebase still says EchoWave in most places — that rename is a separate, deliberate engineering pass, not yet started.)*

## What it is

Decibyl.ai is a voice AI agent platform. Teams design a phone rep as a visual conversation flow, connect it to a real phone line (or embed it on a website), and it handles calls — answering, qualifying, booking, transferring to a human when needed — with full transcripts, recordings, and quality scoring on every call.

## The problem

Businesses that live on the phone — clinics, service businesses, sales teams — either overstaff a call desk to avoid missing calls, or lose leads and appointments to voicemail and missed calls. Hiring, training, and scheduling a human team to cover this is slow, expensive, and inconsistent (quality varies rep to rep, day to day). Existing AI voice platforms (Vapi, Retell, Bolna) solve the automation problem but charge a blended rate that marks up whatever LLM/STT/TTS they route you through, and none of them are built for a technical, AI-coding-assistant-native workflow.

## The solution

- **Build the agent by describing it**, not by hand-writing IVR scripts — natural language in, a working conversation graph out, editable visually or directly by an AI coding assistant (Claude Code) via MCP.
- **BYOK pricing** — bring your own OpenAI/Deepgram/ElevenLabs (or other provider) keys. Decibyl.ai charges a flat platform fee on top ($0.02/minute), not a marked-up blended rate. You see exactly what you pay and to whom.
- **Full observability** — every call is transcribed, recorded, and automatically QA-scored — 100% coverage, not a 1-2% human-sampled audit.
- **Live integrations** — the agent can act mid-call: transfer, send DTMF, send an SMS, create a calendar event, send an email — not just talk.
- **Human handoff when it matters** — automation with a safety net, not an all-or-nothing replacement.

## Who it's for

**Primary go-to-market (near-term):** technical teams and agencies who already hold their own model-provider accounts and want infrastructure to build voice agents for their own use case or for clients — BYOK is a feature to this audience, not friction. This is also the audience already well-served by the product's MCP-native building and API/campaign tooling.

**Vertical focus in progress:** clinics and independent doctors in India — booking/reception automation, with Google Calendar as the current appointment-delivery mechanism (see Roadmap for the EMR-integration path).

**Not the current focus:** non-technical SMB self-serve (Vapi/Bolna's stronghold) — BYOK setup friction makes this a harder sell today without a managed-model tier, which is intentionally not being rebuilt yet (see below).

## Business model

- **Prepaid credits**, billed via Razorpay (India-based, GST-registered).
- **$0.02/minute platform fee**, metered per call, on top of the customer's own BYOK provider costs.
- **$10 trial credit** granted at signup (platform-fee only — cheap for us because we don't front inference cost, generous for the customer relative to competitors who must fund real inference cost out of their trial credit).
- No managed/bundled-model tier currently offered — this was deliberately cut (see Roadmap) to remove a dependency on the original open-source project's infrastructure; BYOK is the only path today.

## Competitive positioning

| | Decibyl.ai | Vapi | Retell | Bolna |
|---|---|---|---|---|
| Pricing model | Flat platform fee + BYOK, no markup | Blended rate | Blended rate | Blended rate, bulk plans |
| MCP-native building | Yes, first-class | Bolted on | Limited | Limited |
| BYOK-first | Yes | Optional | Optional | Optional |
| India billing (Razorpay, GST) | Yes | No | No | Partial |

The honest gap today: no managed-model "just start talking" onboarding path, which is a real onboarding-friction disadvantage against Vapi/Bolna for non-technical buyers. This is why the current GTM motion is agency/technical-buyer-led rather than SMB self-serve (see the GTM notes captured earlier in the founding conversation, not restated here).

## Current stage

Pre-launch. Core platform, billing, and two tool categories (Native: DTMF/SMS; Integration: Google Calendar/Gmail) are built. Payment collection (Razorpay checkout), compliance groundwork (DPDP), and the brand rename to Decibyl.ai are all in progress — see `ROADMAP.md`.
