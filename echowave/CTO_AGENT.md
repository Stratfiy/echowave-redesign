# CTO_AGENT.md: how the build team works

Read this, `WORKBOARD.md` and your own `personas/<you>.md` before every run.
`AGENTS.md` (loaded through `CLAUDE.md`) still holds the code rules; this file
holds the team rules. If the two disagree, `AGENTS.md` wins on code, this file
wins on process.

## The launch

- **Date:** 4 October 2026. Invite-only, 14-day trial (no Free), metered usage, customers' own model keys (BYOK).
- **Epic:** KAN-267. Every launch issue carries the label `oct4`.
- **Plan:** `docs/plans/2026-09-30-launch-4-october.md` on branch `claude/cto-technical-plan-2026-09-30` (PR #491).
- **Rule:** everything merges behind a flag that is off by default. A flag goes on only after the founder's check on staging passes. Anything not green by 3 Oct 20:00 goes on the dark list with a date.
- **Coordinator:** the main Claude session. It reads Jira, sets merge order, moves issues, and collects founder actions on KAN-279.
- **Founder:** Nithish. He tests on staging, does AWS, keys and vendor consoles, and merges. Nobody else merges.

## Who does what

| Persona | File | Running now |
|---|---|---|
| Platform Engineer (backend) | `personas/platform.md` | yes |
| AI Engineer | `personas/ai.md` | yes |
| Frontend Engineer | `personas/frontend.md` | yes |
| QA & Evals Engineer | `personas/qa.md` | yes |
| DevOps / SRE | `personas/devops.md` | yes |
| Product Planner, UX/UI Designer | the coordinator, during launch week | after 4 Oct |
| Security Engineer | folded into Platform for launch week | after 4 Oct |
| Growth Engineer, Internal Agents Builder | not yet | after 4 Oct |

## Rules for every persona

1. **One story at a time, end to end.** Claim an issue in your lane:
   - move it to In Progress;
   - comment `Claimed by <persona>`;
   - add a row to `WORKBOARD.md` → Now.

   The lead persona builds the whole story, API and UI both, unless the story is large enough to split. If it is, the coordinator splits it.
2. **Plans.** `oct4` issues are pre-approved: their plan is the issue description plus its comments, so start building. Post a plan and wait for "go" only when you will:
   - change `api/db/models.py` beyond one new column;
   - add a migration;
   - touch the approval path (`services/workflow/actions.py`, `approvals.py`);
   - touch auth (`services/auth/**`).

   Plan format: what the issue asks · what the code has (file:line) · the change · the tests · risks · the PRs.
3. **Hot files have one owner.** Every branch touches these, so only their owner edits them. Anyone else writes a Request in `WORKBOARD.md` → Requests.

   | File | Owner |
   |---|---|
   | `api/constants.py` flag block, `api/services/features.py`, `ui/src/lib/features.ts` | Platform (flags are registered in their own PR, like #494) |
   | `api/db/models.py`, `api/alembic/versions/**` | Platform, **one migration in flight at a time** |
   | `api/routes/main.py` (router registration) | Platform |
   | `ui/src/components/layout/navigation.ts`, `ui/src/client/**` (generated) | Frontend |
   | `.github/workflows/**`, `scripts/**`, `docker-compose.yaml` | DevOps |

4. **Branches.**
   - Name the branch after the issue: `KAN-<n>-<slug>`.
   - Start from `main`, except a branch with a migration. That one stacks on the branch holding the newest unmerged migration, so the migration chain stays linear. Say so in the PR.
   - PR title starts with the key. Open PRs as drafts.
   - Never force-push someone else's branch. Never merge.
5. **Tests first. Small PRs.**
   - Before pushing, run the suites for what you touched: `pytest` for the API, `npx tsc --noEmit` and `npx vitest run` for the UI.
   - Before pushing, also run `ruff check --select I,F401,F821 --fix` and `ruff format` on changed Python files, and `eslint --fix` on changed UI files.
   - If you add or change an API route, regenerate `docs/api-reference/openapi.json` (`python -m scripts.dump_docs_openapi`) and the UI client (`npm run generate-client`).
6. **No secrets anywhere.** Not in code, PRs, Jira or chat. Read them from the environment. Name the variable and ask the founder to set it.
7. **When a story is built:**
   - move the issue to **Testing**;
   - comment the PR link and 5–10 numbered staging steps written for the founder;
   - say which flag, and how to turn it on for one organisation: `FEATURE_ORG_OVERRIDES="<flag>:<org id>"`;
   - say what he should see.
8. **Human actions.** Anything only a human can do (AWS console, a vendor account, an API key, DNS, a merge, a template approval) goes in a comment starting with **`FOUNDER ACTION:`**, with exact clicks and the expected result. The coordinator collects these on KAN-279.
9. **End every run with five lines on your issue and in `WORKBOARD.md` → Log:** shipped · blocked · next · one number · risks.

## Merge order (the coordinator keeps this current in WORKBOARD.md)

DevOps → Security → Platform → AI → Frontend, and a parent PR before its stacked child. QA posts "CI green/red · journeys n/n" on a PR before the founder merges it. After a parent merges, delete its branch so GitHub moves the child PR onto `main`.

## Useful facts

- **Local test environment:** Postgres 16 with pgvector, and Redis on port 56379. Environment variables as in `.github/workflows/api-tests.yml`; `DATABASE_URL` points at `test_db`. If alembic complains about an unknown revision after switching branches, drop `test_db`.
- **Flags are env vars read at start.** A flip needs `docker compose up -d --force-recreate api` (see `docs/deployment/feature-flags.mdx`).
- **Secrets runbook:** `docs/deployment/secrets-rotation.mdx`.
- **Deploys:** every merge to `main` builds on the production box. On 2 and 3 Oct, merges are batched and deployed once at 16:00.
