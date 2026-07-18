# EchoWave Rebrand & Redesign — Handoff Notes

**From:** Dograh (open-source fork at `Stratfiy/echowave`)
**To:** EchoWave by nAutomation Labs
**Live target:** `echowave.nautomationlabs.com`

---

## What changed in this pass

### 1. Global rebrand
Every user-visible `Dograh / dograh / DOGRAH` string across the UI (`/ui`), root docs (`README.md`, `README.zh-CN.md`, `README.ja-JP.md`, `CONTRIBUTING.md`, `SECURITY.md`, `AGENTS.md`, `CLAUDE.md`), all docs pages (`/docs/**/*.md,*.mdx`) and top-level `docker-compose.yaml`, `docker-compose-local.yaml` was replaced with the matching-case `EchoWave / echowave / ECHOWAVE`.

Kept intentionally as-is (would break the running deployment otherwise):
- External URLs: `docs.dograh.com`, `services.dograh.com`, `app.dograh.com` — user does not own these hosts yet
- Python SDK package identifier `dograh_sdk` inside `/sdk/python`
- Anything under `/pipecat` (git submodule — external code)
- Existing env-var names (e.g. `DOGRAH_*`) — swapping them would break the live EC2 config

### 2. New brand assets (`/ui/public`)
Old PNG assets replaced with clean SVG:
- `echowave-logo.svg` (light-surface wordmark)
- `echowave-logo-inverse.svg` (dark-surface wordmark)
- `echowave-mark.svg` (square app-icon mark, blue gradient + waveform)

`BrandLogo.tsx` was rewritten to reference the new SVGs.

### 3. Redesigned auth experience (`/ui/src`)
Files touched:
- `src/components/auth/AuthShell.tsx` — completely redesigned two-column shell
- `src/components/auth/AuthEnterpriseCTA.tsx` — nAutomation Labs blue CTA
- `src/app/auth/signup/page.tsx` — new copy, data-testids, brand-blue submit
- `src/app/auth/login/LoginForm.tsx` — same treatment for sign-in
- `src/app/globals.css` — new brand tokens (`--brand-blue`, `--brand-panel`, chip/heading/body/card variables) for **both** light and dark modes
- `src/app/layout.tsx` — light theme is now the default, dark opts in
- new brand utility `bg-brand-panel` (light + dark gradients)

Design language:
- Left panel: gradient sky-blue in light mode, deep navy in dark mode, with soft radial glows
- Animated CSS waveform above the heading (voice-AI motif)
- Feature chips (`Speech-to-speech`, `MCP-native`, `BYOK · any model`, `Self-hostable`)
- Enterprise "Talk to Sales" card floats at the bottom, blue solid CTA (previously an outline "Enterprise Enquiry")
- Right form column has a floating theme toggle (top-right) so users can flip modes on the auth screen itself
- Form card is elevated with a soft brand-tinted shadow

### 4. In-container preview
`/app/frontend/public/preview.html` is a self-contained static HTML mirror of the redesigned auth pages, with:
- Tab switcher (Sign up / Sign in)
- Live dark/light toggle (top-right)
- Waveform + chips + enterprise card
- Google OAuth placeholder button

`/app/frontend/src/App.js` redirects `/` → `/preview.html` so the redesign is visible at the preview URL immediately.

**Preview URL:** open the app preview → auto-redirects to the redesign.

---

## How to ship this to your EC2

The redesigned Next.js source lives at `/app/echowave/ui`. The rest of the monorepo (api, docker-compose, pipecat submodule, etc.) is unchanged in structure — only branding strings and a couple of image assets were touched.

You have two clean paths:

### Path A — copy the whole `/app/echowave` folder onto EC2
```bash
# from your local machine
scp -r /app/echowave  user@your-ec2-host:/home/user/echowave-rebrand

# on EC2 (inside the folder)
docker compose down
docker compose up -d --build
```

### Path B — cherry-pick only the changed files
The high-signal changes are all inside:
- `ui/public/echowave-*.svg`  (new brand assets)
- `ui/public/echowave-*.png`  (renamed from `dograh-*.png`)
- `ui/src/components/BrandLogo.tsx`
- `ui/src/components/auth/AuthShell.tsx`
- `ui/src/components/auth/AuthEnterpriseCTA.tsx`
- `ui/src/app/auth/**` (signup/login)
- `ui/src/app/globals.css`
- `ui/src/app/layout.tsx`

Copy those over your EC2 checkout, then:
```bash
docker compose build ui
docker compose up -d ui
```

---

## What's NOT done (intentional / future work)

- **Custom docs site**: `docs.dograh.com` still points to the original org's docs. When you're ready, set up your own docs at `docs.echowave.nautomationlabs.com` and swap the URL constant in `ui/src/constants/documentation.ts`.
- **Marketing landing page**: this pass targets the app auth surface — not a marketing homepage. Bring the same brand system into a marketing site when needed.
- **Revenue / billing**: subscription + credits flow (Stripe) was deferred at your request ("keep it waiting for now"). The codebase already has a `billing` module and `usage` pages — we can layer subscription tiers + credits on top when you're ready.
- **Backend identifier rename**: Python package names like `dograh_sdk` and env vars `DOGRAH_*` are unchanged to avoid breaking the running EC2 stack. Rename these in a dedicated migration if you want a fully clean namespace.
- **Emails / transactional templates**: not audited in this pass — search `/api/**/*.html` and `/api/**/*.py` for any embedded "Dograh" strings in email bodies before you flip the branded outbound emails on.
