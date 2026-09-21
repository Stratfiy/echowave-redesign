# EchoWave — Product Requirements Document

## Problem Statement (verbatim)
> https://echowave.nautomationlabs.com/auth/signup — This is what I'm building.
> https://nautomationlabs.com/ — This is my website.
> https://github.com/Stratfiy/echowave — This is the code... I want to rebrand and reshape this open-source code and make a revenue generating product. Can you build it?

## User Choices (session 1)
- **Core function:** AI voice/audio generation platform (voice AI agents)
- **Brand:** Keep the name "EchoWave", rebrand from the underlying open-source "Dograh" project
- **Revenue model (future):** Both subscription tiers (Free / Pro / Enterprise) via Stripe **and** pay-per-use credits
- **Build approach:** Adapt the existing GitHub fork (not build from scratch); keep the original Docker-based stack because it "is working completely fine" on EC2
- **AI integration:** Claude Sonnet 4.6 via Emergent LLM Key (deferred; not wired in this pass)
- **Payments:** Deferred (`keep it waiting for now, just redesign the code and rename`)
- **UI direction:** Match nAutomation Labs light/blue aesthetic **with a dark-mode toggle**
- **Preview format:** Simple HTML preview only (don't run the full stack in the container)

## Architecture (as-inherited)
- **Frontend:** Next.js 16 + React 19 + Tailwind v4 (in `/app/echowave/ui`)
- **Backend:** FastAPI + Python 3.13 + Postgres + Redis + MinIO (in `/app/echowave/api`)
- **Realtime:** LiveKit + pipecat submodule
- **Orchestration:** Docker Compose (not runnable inside the Emergent Kubernetes preview pod)
- **Preview environment:** Static HTML mirror of the redesigned auth pages at `/app/frontend/public/preview.html`, served by the standard React frontend at `/preview.html`

## What's Been Implemented
### Session 1 — 2026-01 (Rebrand & Redesign)
- Cloned `Stratfiy/echowave` into `/app/echowave` (depth 1, no submodule)
- Global find/replace `Dograh → EchoWave / dograh → echowave / DOGRAH → ECHOWAVE` across:
  - Entire `/ui` folder (TS/TSX/JSON/CSS/MD/SVG)
  - Root docs: `README*.md`, `CONTRIBUTING.md`, `SECURITY.md`, `AGENTS.md`, `CLAUDE.md`
  - `/docs/**/*.md,*.mdx`
  - `docker-compose.yaml`, `docker-compose-local.yaml`
  - Selective backend brand strings (`api/app.py` FastAPI title & description, `api/sdk_expose.py` docstring)
- New brand assets: `echowave-logo.svg`, `echowave-logo-inverse.svg`, `echowave-mark.svg` in `/ui/public`
- Renamed legacy PNGs: `dograh-logo{,-inverse,-mark}.png → echowave-*.png`
- Rewrote `BrandLogo.tsx` to reference the new SVGs
- Redesigned the auth experience:
  - New two-column shell (`AuthShell.tsx`) with left brand panel + right form card
  - Animated CSS waveform, feature chips, floating enterprise CTA
  - Dark/light mode toggle floating on the form column
  - Solid brand-blue primary CTAs replacing the previous warm-outline style
  - `AuthEnterpriseCTA` promoted from outline "Enterprise Enquiry" → solid "Talk to Sales →"
- Rewrote `globals.css` `:root` + `.dark` blocks with a full brand token set (`--brand-blue`, `--brand-panel`, `--brand-heading`, `--brand-body`, `--brand-chip*`, `--brand-card`) so both themes read as coherent brand variants
- Default theme flipped to **light** (dark opts in) in `layout.tsx`; app metadata title updated
- Simple HTML preview built at `/app/frontend/public/preview.html` with signup/login tab switcher + dark toggle
- `/app/frontend/src/App.js` redirects `/` → `/preview.html`
- Handoff doc written at `/app/echowave/REBRAND_HANDOFF.md` (deployment path to EC2)

## Prioritized Backlog
### P0 — Ship the redesign
- [x] Rebrand strings + assets (this session)
- [x] Redesign auth pages (this session)
- [x] Static preview (this session)
- [ ] User pulls `/app/echowave/ui` onto EC2 and rebuilds Docker Compose

### P1 — Reach the revenue goal
- [ ] Stripe integration (subscription tiers Free / Pro / Enterprise)
- [ ] Credits / pay-per-use ledger on top of existing `/api/billing` module
- [ ] Pricing page + upgrade flow inside the app shell
- [ ] Usage-metering wiring (calls/min, tokens, characters TTS) → charged against credits

### P2 — Brand & content
- [ ] Rebrand app pages beyond auth (dashboard header, sidebar, footer, empty-states — quick copy sweep)
- [ ] Move docs from `docs.dograh.com` → `docs.echowave.nautomationlabs.com`
- [ ] Marketing landing at `echowave.nautomationlabs.com/` (currently 404s / redirects to auth)
- [ ] Rename Python `dograh_sdk` package + `DOGRAH_*` env vars for a fully clean namespace (breaking change — do behind a release)
- [ ] Audit outbound transactional emails (`/api/**` templates) for legacy "Dograh" copy

## User Personas
- **Voice-AI product builder** — technical PM / founder wiring a voice bot for support, sales, or lead qualification. Cares about BYOK, MCP, self-hosting, low per-minute cost.
- **Enterprise buyer** — regulated industry (fintech, healthcare, gov) that needs on-prem or VPC deployment. Enters via the "Talk to Sales" CTA.
- **Developer / integrator** — uses the Python or Node SDK to programmatically create workflows and place outbound calls.
