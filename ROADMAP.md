# Decibyl.ai — Roadmap

Status as of this pass. "Shipped" means built and code-complete; none of it has been verified against a live production environment yet (no live DB/Redis/payment gateway available in the build environment) — real-environment verification is its own near-term item, called out below.

## Shipped

**Independence from the original open-source project (Dograh)**
- Killed the live dependency on the original project's managed-model backend (no more auto-provisioned trial keys, no default phone-home URL).
- Hid the managed-model tab in the UI so no org can newly opt into that dependency.
- Renamed leaking internal secrets/env vars.
- *Known gap, not yet fixed*: the knowledge-base embeddings feature has a parallel dependency on the same original backend, found but not yet cut.

**Billing & metering**
- Built from scratch — none of this existed before (it all lived on the original project's servers). Local prepaid credit ledger, per-minute rating engine on call completion, minimum-balance gate before a call can start, $10 signup trial grant.

**Tools — Native**
- DTMF (real audio tones played into a live call) and SMS (via Twilio) — closes the gap against Vapi's default tool set.

**Tools — Integration**
- Google OAuth sign-in flow, encrypted token storage with auto-refresh, two live in-call actions: Create Calendar Event, Send Email.

**Product/marketing docs** — this pass: `PRODUCT.md`, `FEATURES.md`, this roadmap.

## In progress / near-term

- **Razorpay checkout integration** — the credit ledger can receive purchase entries, but nothing triggers one yet. Paused mid-build at the user's request; ready to resume.
- **Frontend client regeneration** (`npm run generate-client`) — needed before the new backend routes/schemas are fully type-safe on the frontend; currently bridged with a documented cast.
- **Tool edit page** — creating a Native/Integration tool works end-to-end; editing one after creation isn't wired yet.
- **Real-environment verification** — migration, OAuth round-trip, DTMF audio generation, SMS send all need to run against a real Postgres/Redis/Google/Twilio setup, not just compile-checked.
- **WhatsApp notification tool** — scoped, not built. Needs a registered WhatsApp Business sender + one approved message template (external prerequisite, not engineering) before the tool itself (small, same shape as SMS) gets built.

## Planned / mid-term

- **DPDP compliance build-out**: call-recording consent disclosure, data processing agreement template for clinic customers, retention/deletion policy, breach-response runbook. Concrete, buildable now; not yet started.
- **ABDM integration path**: sandbox registration, ABHA/HIP milestones, FHIR R4 data exchange, Safe-to-Host security certification. This is the real path to writing appointments into whatever EMR a clinic uses, instead of a one-off vendor deal — a multi-month project, sequenced after there's real clinic traction to justify it.
- **Brand rename execution**: EchoWave → Decibyl.ai across the codebase, docs, domains, and any live deployment. Scoped the same way the original Dograh→EchoWave cut was — not started; explicitly on hold per current instruction.
- **Deeper native/integration tool coverage**: more Google actions, and providers beyond Google, based on what customers actually ask for.

## Later / conditional (don't build until triggered)

- **ISO 27001 certification** — only once a specific enterprise/hospital-chain deal requires it.
- **SOC 2** — only if a US customer or investor asks for it by name; lower priority than ISO 27001 given the current India-first focus.
- **Managed-model tier** (fronting inference cost ourselves, Vapi/Bolna-style "just start talking" onboarding) — deliberately not rebuilt after being cut for independence; revisit only once revenue funds it and BYOK friction is confirmed to be costing real SMB deals.
- **Direct EMR partnerships** (Practo, HealthPlix, or smaller players like TatvaCare/Eka Care) — a business-development conversation, realistic only once there's clinic volume to bring to the table.
