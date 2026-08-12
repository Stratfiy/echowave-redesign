# Next session — start here

Written at the end of the session of 12 Aug 2026. Read this before touching
anything; it exists so the next session does not re-derive what this one paid
for.

---

## 1. Where the code is

Everything below is on **`claude/product-deployment-readiness-2o8hbt`**, pushed,
**not merged**. `main` is at the merge of PR #36.

| Commit | What | Keep? |
|---|---|---|
| `c4fa90a` | App set in Poppins via `next/font` (self-hosted at build) | **Yes** |
| `e07eca9` | Logo assets in `ui/public/` recoloured; dark neutrals rotated off cool hues | **Yes** (logo half) |
| `a399553` | Flame rebrand — orange everywhere, glass canvas on the app shell | **The user rejected this look** |

**Decision still open:** the user said `a399553` "looks yukk". Ask whether to
restyle on top of it or restart the restyle from `main`. The Poppins and logo
commits are worth keeping either way.

### Merged and live already

PRs #31–#36. Notably:

- **#35** — docs rebrand + unpublished all self-hosting content. Live.
- **#36** — Sign in with Google. Live and configured; the backend returns a
  valid authorization URL. **Not yet confirmed working end to end** — the user
  never reported completing a sign-in.

---

## 2. The design direction that was agreed

The user pasted a full monday.com style reference and said *"Like this but the
orange"*. Read as: **monday.com's design language, with Decibyl orange in place
of monday's violet.** Not violet — the marketing site (decibyl.ai) is orange and
the app must not diverge from it again.

**Why the first attempt failed, so it is not repeated:** the marketing palette
was applied literally and at full strength — primary, panel gradients, chips and
glass glows all saturated `#F0431C`. On a landing page that orange appears in
three places against acres of white. Applied everywhere at once it stops reading
as a brand and starts reading as an alarm. monday.com's violet covers perhaps 5%
of their pixels. **One saturated accent, used sparingly, on a white canvas.**

### The plan

| | Target |
|---|---|
| Canvas | `#f5f6f8` floor, `#ffffff` cards. Remove the orange panel gradients entirely |
| Accent | `#F0431C` on primary CTAs and active states **only** |
| Pastels | Soft tints on feature/stat cards, derived warm to sit with orange. Surfaces only — never text, borders or icons |
| Type | Poppins (**already done**), light 300 for display with negative tracking |
| Shape | 160px pill buttons, 24px cards, 6px badges/inputs |
| Elevation | One soft shadow: `rgba(205,208,223,0.4) 0 2px 48px`. No glass glows |
| Dark theme | **Remove.** The user asked for light only |
| Semantics | Warning/critical need real distance from the accent again |

### Brand values (read from decibyl.ai's own stylesheet, not sampled)

```
#F0431C  primary      oklch(0.634 0.215 33.6)
#C42D0E  pressed      oklch(0.537 0.191 32.7)
#FF7A2F  gradient mid oklch(0.725 0.182 46.0)
#FFB627  amber        oklch(0.824 0.163 77.8)
#211814  ink (warm)   oklch(0.219 0.016 45.2)
#FFE4D6 / #FFF1DC     tints
```

---

## 3. Things that will cost time if you re-derive them

- **`ui/src/app/globals.css` is fully tokenised** — 170 custom properties.
  `--primary` etc. flip the whole shadcn surface from one place.
- **68 call sites still say `--brand-blue*`.** Those are now *aliases* onto
  correctly-named tokens. Do not rename them; it is a 68-file diff whose only
  purpose is renaming.
- **There is already a complete liquid-glass system** (`--glass-fill`,
  `--glass-edge`, `--glass-shadow-clay`, `.glass-canvas`, an animated wash).
  A first pass here added a *parallel* set of glass tokens which silently lost
  to it because they were declared earlier in the file. If glass survives the
  restyle, retune the existing system — do not add another.
- **`BrandLogo` does not use `icon.svg`.** It points at three files in
  `ui/public/`: `decibyl-mark.svg`, `decibyl-logo.svg`,
  `decibyl-logo-inverse.svg`. A `src/` sweep misses all three.
- **Danger vs brand collide.** `--destructive` was at hue 27.3, four degrees
  from the orange primary at 33.6 — a delete button and a save button the same
  colour. It was moved to a deep crimson. Keep that separation.
- **Only screenshots find colour bugs.** `tsc` and `next build` both passed
  while the header logo was blue on an orange page. Screenshot before claiming
  done. See §6 for how.

---

## 4. Deployment — the traps this session hit

- **The EC2 box has no `npm`.** Do not `apt install npm` (too old for Astro 7).
  Build in a container:
  ```bash
  cd ~/echowave-redesign/echowave/docs
  docker run --rm -v "$PWD":/w -w /w -e npm_config_cache=/tmp/.npm \
    --user "$(id -u):$(id -g)" node:22-alpine sh -c "npm ci && npm run build"
  ```
- **The box sits in detached HEAD.** `git pull` fails. Use
  `git fetch origin main && git checkout -B main origin/main`.
- **`docker compose restart` does not re-read `.env`.** Use
  `up -d --force-recreate`.
- **The UI is a pulled image**, not built on the box. UI changes only appear
  after the GitHub Actions deploy publishes a new image. A missing UI change is
  usually an unfinished deploy, not a bug — check
  `mcp__github__actions_list` on `deploy.yml` before debugging.
- **Docs are static files** at `./docs/dist`, bind-mounted into nginx. They need
  the container build above; no restart needed afterwards.

---

## 5. Open work, in the order it was prioritised

1. **The monday.com restyle** (§2). Largest, and the user is waiting on it.
2. **Verified test number** — user's own words: *"like bolna … they allow to add
   a number with OTP like a test number where user can test receiving calls"*.
   Designed but **not started**. Needs: own table (**not**
   `telephony_phone_numbers` — that is for rented numbers bound to inbound
   workflows), OTP with expiry + rate limiting + hashed storage, SMS delivery
   (`api/services/messaging/send.py` already sends via Plivo, live in prod), and
   a permission check on the outbound path. This unblocks trial calling while
   managed numbers are stuck behind Plivo KYC.
3. **DND scrubbing + calling hours** — the only P0 left in `STATUS.md`. TRAI /
   TCCCPR exposure; gates dialling anyone who has not asked to be called.
4. **Latency instrumentation.** `t_endpoint_fired_ms` is *always 0* — pipecat
   emits no VAD mark, so endpointing (often the largest chunk of perceived
   latency) is invisible. The network leg is invisible too; the timeline starts
   and ends inside our own process. Full analysis:
   https://claude.ai/code/artifact/705f8926-5654-44e8-bbc6-a90abd9a6212
5. **Analytics gaps** — activation funnel (signup → agent → first call → first
   top-up) is the highest-value one and is answerable in plain SQL. Then failure
   reasons (no first-class field today), account health, margin per account.
6. **Reconciliation check** between the credit ledger, the daily rollup and raw
   runs. Three independent paths to "what did this account use", and nothing
   asserts they agree.

### Blocked on third parties

- **Autopay** — Razorpay Subscriptions approval.
- **Managed numbers** — Plivo KYC verdict.

### Decided against

- **PostHog** — do not connect. The wired events are backend events already in
  Postgres; there is no frontend instrumentation, so it would not answer the
  funnel question anyway; and its default host is a US endpoint, which is a DPDP
  decision rather than a config toggle.

---

## 6. Verifying UI work

The test environment is rebuilt each session (container is ephemeral):

```bash
# api/.env.test — recreate if missing
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/decibyl
REDIS_URL=redis://localhost:6379/1
LOG_LEVEL=WARNING
ENVIRONMENT=test
```
`redis-server --daemonize yes` and
`su postgres -c "psql -c \"ALTER ROLE postgres WITH PASSWORD 'postgres'\""`.

Screenshots — Chromium is preinstalled, and the browser **cannot reach the
internet** through the proxy, so serve locally:

```bash
cd ui && npx next build && npx next start -p 3011
# then playwright-core with:
#   executablePath: /opt/pw-browsers/chromium-1208/chrome-linux64/chrome
#   args: ["--no-sandbox"]
```

**Caveat:** `/auth/login` renders only while its backend health check is still
failing fast. On a warm server it spins forever with no backend running. Either
screenshot immediately after starting the server, or stub the health endpoint.

---

## 7. Standing constraints from the user

- **Do not raise pasted API keys.** Verbatim: *"Never worrie abou API KEYS
  DROPPED HERE."* Never commit them either — `PRODUCTION-CHECKLIST.md:164`.
- **The exposed PEM is not to be raised again** — the user assessed it and
  declined rotation.
- **No SSH access to the EC2 box.** Give the user commands to run.
- **No trace of the upstream fork, and the product is not open source.** Both
  the docs and the login page have been cleaned; watch for regressions.
- **`certs/` must never be committed.**
- Declined and not to be revisited: scraping LinkedIn/Indeed.
- **Never disturb working functionality** — the user has said this explicitly
  about UI work. Visual changes should not touch logic.
