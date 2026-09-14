# Developer docs and public API: what the market does, what we ship

Date: 14 September 2026. Companion to the pricing study. Internal.

## 1. What was wrong

- **The reference was prose about an API, not the API.** Forty-four pages
  carried an `openapi: POST /api/v1/…` line in their frontmatter and nothing
  rendered it. No arguments table, no response, no errors; each page was a
  paragraph. Every one of those lines still pointed at a live operation, so
  the pages were not wrong, they were empty.
- **The document described the staff console.** The public OpenAPI file and
  the live `/openapi.json` carried the 66 operations under `/admin` and
  `/superuser`: markup changes, provider keys, KYC review, impersonation.
  Enforced, never callable by a customer, and fully described to one. Fixed
  in #261: public and internal documents, guard tests.
- **Three themes for one product.** The app is ivory with a coral accent
  and Inter; the public site is a violet system with Outfit; the docs were
  Mintlify blue with a green search box left over from the template.
- **Self-hosting copy on a hosted product.** "your-decibyl-instance" in
  the reference overview and the API trigger page; a Vapi comparison in the
  MCP page.

## 2. What the market does (September 2026)

| | Slack | Vapi | Retell | Bland | ElevenLabs Agents | Bolna |
|---|---|---|---|---|---|---|
| Reference organised by | method family, `object.verb` (`chat.postMessage`) | resource (assistants, calls, phone numbers, tools, files, squads, workflows, webhooks) | resource (agent, call, phone number, knowledge base, voice, LLM, batch) | resource (calls, pathways, agents, numbers, voices, tools, knowledge, SMS, web agents) | four pillars: build, integrate, operate, monitor | quick start, agent setup, API reference, templates, concepts |
| Method page | facts box (token, scopes, rate tier), arguments table, example, response, errors, warnings, tester | generated from spec, SDK tabs | spec block, body params with required flags, 201 and error codes, JS and Python samples | grouped parameter tables (basic, model, dispatch, knowledge, audio, voicemail, analysis, post-call, advanced), response, every error code | capability tables by goal, SDK tabs | REST reference plus `llms.txt` |
| Quickstart | app manifest, first call | "first voice agent in 5 minutes", dashboard-first | dashboard-first | dashboard-first | "first agent in 5 minutes" | first agent |
| Agent-readable | `docs.slack.dev` markdown | `.md` suffix on any page, `llms.txt`, MCP server | | | | `llms.txt` |
| Events | Events API, per-event pages | server URL webhooks | webhooks | webhooks | webhooks | webhooks |

What to take from each: Slack's **method family and page anatomy** (the
thing you asked for); Bland's **grouped parameter tables** for the one big
method we have (start a call); Vapi's **agent-readable docs** (`llms.txt`,
`.md` suffix, MCP), which we already half have through the docs MCP tools;
ElevenLabs's four verbs as the **guide** structure, not the reference.

## 3. How we plan the API

**One rule: the API is the product's objects, named the way the app names
them.** The app says bots, calls, channels, numbers, knowledge, campaigns,
contacts, team. The routes say `workflow`, `workflow-runs`, `folder`,
`telephony`. The reference maps the second onto the first; the routes do
not change in this pass (the UI client and the SDKs depend on them), and a
`v2` path alias is a later, separate decision.

Method families, in Slack's `object.verb` style, drawn only from the public
document (#261):

| Family | Methods | Routes behind them |
|---|---|---|
| `bots` | create, createFromTemplate, list, get, update, archive, validate, publish, restoreVersion | `/workflow/*` |
| `calls` | start, startTest, list, get, artifacts | `/public/agent/*`, `/workflow/{id}/runs*` |
| `campaigns` | create, list, get, update, start, pause, resume, redial, progress, runs, report | `/campaign/*` |
| `contacts` | lists.create, lists.list, lists.update, lists.delete, import, list, delete, doNotCall.add, doNotCall.list, doNotCall.remove | `/contact-lists/*`, `/do-not-call/*` |
| `numbers` | search, provision, release, list, verified.start, verified.confirm, missed.list | `/managed-numbers/*`, `/verified-numbers/*`, `/missed-calls` |
| `channels` | list, create, rename, delete, timeline, post, decide, settleAction, provideSecret | `/folder/*`, `/timeline/*` |
| `knowledge` | uploadUrl, process, list, get, delete, translate, search, usage | `/knowledge-base/*` |
| `tools` | create, list, get, update, delete, refreshMcp, connectors.list, connectors.connect, connectors.accounts | `/tools/*`, `/connectors/*` |
| `credentials` | create, list, get, update, delete | `/credentials/*` |
| `keys` | create, list, archive, reactivate | `/user/api-keys/*` |
| `team` | home, status, members.* | `/team/*`, `/organizations/{id}/members/*` |
| `billing` | balance, plan, payments, topup, autoTopup, documents | `/billing/*` |
| `marketplace` | templates.list, templates.get, templates.hire, packs.list, packs.get | `/agent-templates/*`, `/packs/*` |
| `events` | the webhook payloads, one page per event | `developer/webhooks` |

Not in the reference, on purpose: carrier callbacks under `/telephony/*`
(they are ours to receive, not a customer's to call), the embed session
routes (documented under the widget guide), `/health`, and anything
organisation-internal that only the app calls.

Every method page carries the same anatomy: **facts** (method and path,
auth, who may call it, rate limit), **arguments** (path, query, body with
type, required, default), **request** example, **response** with an example
built from the schema, **errors** as the spec lists them. All of it read
from `api-reference/openapi.json` at build time, so a page cannot fall
behind the code: the drift check already fails the build when that file
falls behind the app.

## 4. What ships, in order

1. **Theme.** Ivory, ink, coral, Inter and Outfit, from the app's tokens.
   The green search box goes.
2. **`ApiMethod`.** An Astro component and a remark step: any page whose
   frontmatter names an operation renders the anatomy above from the spec.
3. **Reference regenerated.** `scripts/generate-reference.mjs` reads a
   families map and the spec, writes one page per method and the Reference
   tab of `docs.json`. Hand-written prose on an existing page is kept above
   the generated anatomy.
4. **Copy.** Hosted-product wording everywhere; the comparison line goes.
5. **Agent-readable.** `llms.txt` at the site root listing every page, and
   a `.md` twin of each page, the way Vapi does it.

Later, separately: a `v2` alias that renames routes to the object names,
and an API tester on each method page.
