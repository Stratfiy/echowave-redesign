# WORKBOARD

The shared whiteboard for the build team. Short lines only. The coordinator
tidies it daily; Jira (project KAN) is the record, this is the view.

## Merge queue (in order; founder merges)

| # | PR | Issue | Base | State |
|---|---|---|---|---|
| 1 | #494 | KAN-275 launch flags | main | ready for review |
| 2 | #493 | KAN-245 security fixes | main | ready for review |
| 3 | #495 | KAN-273 invite codes | #494 | draft, tests green |
| 4 | #496 | KAN-255 trial + notices | #495 | draft, tests green |
| 5 | #498 | KAN-254 BYOK for Decibyl | #494 | draft, tests green |
| 6 | #497 | KAN-257 UI-0 part 1 | main | draft, UI 993/993 |
| 7 | #499 | KAN-277 Decibyl in your apps | #496 | **WIP**: Teams, Slack routes, tests, settings UI left |

Migration chain (keep it linear): `e8b4d6f2a1c9` → `202609302200kan273` →
`202609302300kan255` → `202610010100kan277`. The next migration stacks on
`202610010100kan277`, on branch `KAN-277-decibyl-in-your-apps` or its successor.

## Now (one row per persona)

| Persona | Issue | Branch | Since |
|---|---|---|---|
| Platform | KAN-277 finish (Teams, Slack routes, tests) | KAN-277-decibyl-in-your-apps | |
| AI | KAN-261 voice check set | | |
| Frontend | KAN-257 parts 2–3 | KAN-257-ui-cut-and-rename | |
| QA | Test scripts on every Testing issue, then KAN-240 | | |
| DevOps | KAN-276 script half, KAN-272 /health probes | | |

## Next (per lane, in order)

- **Platform:** KAN-277 → KAN-274 NUM-1 → KAN-259 POL-2 (pause, scopes) → KAN-253 OUT-1 (turn on budgets) → KAN-256 MKT-1 → KAN-266 PRJ-1 (tables)
- **AI:** KAN-261 → KAN-266 (project brief in context) → pack evals for launch packs → KAN-219 ORG-1 light
- **Frontend:** KAN-257 → "Decibyl in your apps" settings card (KAN-277) → KAN-208 UI-1 → KAN-260 UI-4 faces → KAN-209 UI-2 → KAN-258 CH-0 card
- **QA:** scripts for KAN-245/273/255/254/257/275 → KAN-240 journeys → nightly full suite
- **DevOps:** KAN-276 → KAN-272 → KAN-204 staging (founder executes) → KAN-241 (after launch)

## Requests (to a hot-file owner)

| From | To | Ask | Status |
|---|---|---|---|

## Founder actions (collected; details on each issue)

- KAN-233: rotate the SerpApi key, revoke the `dcb_wHit…` key, rotate the Refero token
- KAN-270: start Meta business verification
- KAN-278: Telegram bot (BotFather), Slack app, Azure Bot for Teams; secrets in `.env`
- KAN-204: staging box; KAN-272: Sentry DSN + uptime ping
- KAN-276: GitHub ruleset on `main` (required checks, up-to-date branches)
- Merge #494, then #493

## Log (newest first; five lines per run)
