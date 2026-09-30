# Decibyl UI, end to end — the plan to remove the options

Date: 30 September 2026. Companion to `2026-09-30-cto-technical-plan.md`. Founder's brief: "the UI is fucked up, too many options". Builds on UI-1 (KAN-208), UI-2 (KAN-209), UI-3 (KAN-211), which already describe the destination; this document gives the measured starting point, the rules, the route map, and the order. Nothing here changes what the product can do; it changes how many places it can be done from.

Sources: `ui/src/components/layout/AppSidebar.tsx`, `navigation.ts`, `SectionTabs.tsx`, `app/workflow/[workflowId]/**`, `components/agent-builder/**`, `components/flow/**`, `lib/features.ts`, `lib/utils.ts:95-143`; `docs/product/coordinator-interaction-model.md`.

## 1. The diagnosis, measured

| Job | Ways to do it today | Should be |
|---|---|---|
| Create a bot | 8+: `/start`, sidebar "+", Create dialog (template / describe / blank), Home chips, Marketplace Hire, public `/agents/[slug]` Hire, `/roles/install`, `/workflow/squads/new`, Duplicate, Save as role | 1: a Build card in the Decibyl thread. Every other entrance opens that card prefilled |
| Test a bot | 9+: Test Audio, Test Chat, header Test, Phone call, `/start` Hear step, thread "Hear it / Try it", Message, `/talk/[token]`, triggers "Try it", marketplace demo, run-page web call | 3 verbs on one button: Hear it, Try it, Check it |
| Choose a model or voice | 5 live selectors (About-panel ModelRow with 16 option groups, two FallbackChains, voicemail LLMConfigSelector, composer vendor picker, create-wizard tier) plus 4 dead editors (ModelConfigurationV2, AIModelConfigurationV2Editor with the 1,750-line ServiceConfigurationForm, ModelSlotSelect, SimpleAgentEditor/FlowAgentEditor) | 1 section, "Brain & voice": a tier (Everyday / Natural / Premium) and a language. Vendor, own key, fallbacks and the 16 option groups sit under one "Advanced" disclosure |
| Integrations | 11 entry points: `/tools`, `/integrations`, `/integrations/dialer`, `/integrations/apps`, `/marketplace/{integrations,tools,skills}`, `/deploy/connect`, per-bot `/tools` and `/triggers`, Settings "Tool credentials" and "MCP Server", "Build a tool" | 2: the bot's "Apps & tools" section (what this bot may use) and Settings → Apps & connections (what the workspace has connected) |
| Put a bot on a phone number | 3 screens under 3 labels: `/verification` → `/numbers` → `/telephony-configurations/[id]` → PhoneNumberDialog; the editor never links to a number | 1 flow from the bot's "Channels" section: verify → pick a number → done |
| Put a bot on WhatsApp | No UI at all. WhatsApp exists as a Home chip, a description string, a connector icon and search keywords; the create wizard offers only "On the phone" or "In writing" | 1 flow in "Channels", same shape as the number |
| Navigate | 5 pinned rows + Channels + Bots + a Manage group that only exists when `shell` is on (otherwise Billing and Settings live in the profile dropdown) + 9 in-page tab strips (35 tabs) + a 9-item agent tab bar with a "Setup" dropdown | 7 homes, no in-page tab strips beyond one per home, no dropdown in the bot header |
| Name a destination | Six things with two names each: Decibyl/Home, Agents/Bots, Activity/Calls, Connect/Webhooks & triggers, Your tools/Apps & tools, Compliance/Privacy | One name each, the same in the sidebar, the tab, the page title and the search index |

Page weight where the options live: `settings/page.tsx` 1,852 lines (22 buttons, 21 fields, 11 sections), `billing/page.tsx` 1,388, `FirstAgentJourney.tsx` 1,348, `tools/[toolUuid]` 1,084, `run/[runId]` 1,000, `workflow/create` 992, `campaigns/[id]` 988.

Why it happened: each slice added its own entrance and its own tab because the shell had no rule about where things go. The office model (KAN-140) fixed the interaction rule (the addressee decides); the shell never caught up.

## 2. Five rules, so it does not happen again

1. **One spine.** The Decibyl thread is where building, editing, testing and asking happen. A screen exists only for what a thread cannot show: a list, a graph, a ledger, a form with many fields.
2. **One way per job.** Every job in §1 has exactly one entrance. Other places may *link* to it (prefilled), never re-implement it. A PR that adds a second entrance is refused in review.
3. **Simple by default, Advanced by disclosure.** A business owner sees a tier, a language, a channel and a price. Vendor names, keys, fallbacks, turn-taking and the rest sit behind one "Advanced" toggle per section, remembered per user. Nothing is removed; it is folded.
4. **One name per thing.** Sidebar label = page title = tab label = search entry. The table in §5 is the dictionary.
5. **Flags hide, they do not fork.** A flag may hide a section; it may not create a second navigation. `shell` becomes the default and its off-state is deleted.

## 3. The seven homes and where everything goes

| Home | What it is | Absorbs (today's routes) |
|---|---|---|
| **My Decibyl** | The thread. Above the fold: what needs you (approvals, questions, blocked), then the composer with follow-up chips | `/overview`, `/start` (first-run is the same thread with a Build card open), `/requests` |
| **Tasks** | Board or list of work bots are doing, by person and bot | `/tasks`, `/schedules`, `/deliverables`, `/contacts` (as a tab), `/campaigns/*` (a campaign is a task with a list) |
| **Agents** | Directory of bots and channels; each bot opens its page (§4) | `/workflow`, `/workflow/archived`, `/workflow/squads/new` (a squad is created from a Build card), `/channels/[id]`, `/model-configurations`, `/roles/install`, `/marketplace/*` as the "Hire" tab |
| **Knowledge** | Library, templates, persona, memory (mine / shared), graph | `/files`, `/recordings` (audio clips), `/overview/memory`, `/deliverables` documents |
| **Activity** | Every run: calls, chats, routines, triggers; review, analytics, spend | `/usage`, `/reports`, `/review`, `/analytics`, `/analytics/spend`, `/missed-calls`, `/workflow/[id]/runs`, `/workflow/[id]/run/[runId]`, `/workflow/[id]/analytics` |
| **Team** (managers and admins) | People, roles, approvals queue, operator sessions | `/settings` Team card, `/settings` Approvals card, `/partner` |
| **Settings** | Six sections (§6) | `/billing`, `/privacy`, `/do-not-call`, `/api-keys`, `/deploy/*`, `/integrations/*`, `/tools`, `/numbers`, `/verification`, `/verified-numbers`, `/telephony-configurations/*`, `/settings` remaining cards |

Public, outside the shell: `/agents`, `/agents/[slug]`, `/talk/[token]`, `/trust`, auth.

Every old route gets a redirect (UI-1 test 2). The nine tab strips collapse to at most one strip per home: Tasks (Board / Contacts / Campaigns), Agents (Mine / Hire), Knowledge (Library / Memory / Graph), Activity (Runs / Review / Analytics / Spend). Marketplace, Telephony, Billing, Developer, Compliance and Bots strips are gone; their content is inside Agents → Hire and Settings.

## 4. The bot page (UI-2, made concrete)

One page per bot, `/agents/[id]`. Header: name, handle (`@reception`), status (draft / live / paused), **Test ▾** (Hear it · Try it · Check it), **Publish** (shows the diff; rollback lives in Version history). No "Setup" dropdown, no More menu; Duplicate, Download, Copy UUID and Save as role go to a "…" at the far right, superusers also see "Publish to marketplace" there (MKT-1).

Body: **Chat** (the bot's thread; edits by sentence produce diff cards) or **Canvas** (the graph), one switch, same definition. On the right, the **inspector**, nine sections in this order, each collapsed to a one-line summary:

| Section | Simple (default) | Advanced (disclosure) | Replaces |
|---|---|---|---|
| Role & persona | Job, greeting, tone, language(s) | Full prompt, variables, notices, voicemail behaviour | settings Advanced → general, notices, voicemail, variables |
| Channels | Phone number (verify → pick → assign, one flow), WhatsApp (connect, one flow; **new**), Web widget, Email address, Webhook | Carrier, SIP, caller-ID pools, test numbers | `/verification`, `/numbers`, `/telephony-configurations/*`, `/verified-numbers`, `/deploy/*`, Share tab |
| Brain & voice | Tier (Everyday / Natural / Premium) · language · price per minute or per reply | Vendor and model per slot, own key (BYOK-1), fallback chains, the 16 option groups (turn-taking, denoising, interruption, keypad, fillers, compaction…) | About-panel ModelRow + SlotSettings, FallbackChain ×2, voicemail LLMConfigSelector, composer vendor picker, `/model-configurations` |
| Apps & tools | Toggle which connected apps this bot may use; connect a missing one in place | Custom HTTP tools, MCP, per-tool timeouts, secure-form fields | `/workflow/[id]/tools`, `/tools/*`, Tool credentials, MCP Server |
| Knowledge & memory | Which documents and sections; whether it learns | Retrieval settings, citation style, memory scope | DocumentSelector, memory settings |
| Triggers & routines | "When X, do Y" sentences and schedules | Webhook URLs, payload mapping, email-in address | `/workflow/[id]/triggers`, Schedules |
| Permissions & approvals | What it may do alone, what needs a yes | Approval matrix rows for this bot, spend cap | Settings → Approvals, `SCRIPT_EXTERNAL_SPEND_CAP` |
| Quality | Outcome definition (OUT-1), QA on/off, last Check it result | Eval suites, golden sets, readiness checklist, rate card, recordings retention for this bot | settings Quality tab, `/workflow/[id]/evals` |
| Versions | Live version, draft, last publish | Full history, rollback, download | Version history, Back to Draft |

Count on the default view: 9 section summaries, 3 header actions. Today: 9 tab destinations, 22 buttons and 21 fields on Settings alone, plus 16 option groups in the About panel.

Deleted outright (dead, never imported): `ModelConfigurationV2`, `AIModelConfigurationV2Editor`, `ServiceConfigurationForm`, `ModelSlotSelect`, `SimpleAgentEditor`, `FlowAgentEditor`, `ChangeByChat`, `ConfigurationsDialog`, `RecordingsDialog`, `VoicemailDetectionDialog`, `DictionaryDialog`. Retired after migration: `settings/page.tsx`, `AgentTabs`, the Setup dropdown, `/workflow/create` (its three steps become the Build card), `FirstAgentJourney` (its four steps become the first-run Build card with Hear it inline).

## 5. The dictionary (one name per thing)

| Thing | Name everywhere | Not |
|---|---|---|
| The manager thread | My Decibyl | Home, Overview, Decibyl chat |
| A bot | Agent | Bot, Workflow, Role (Role is the marketplace listing only) |
| Runs of any kind | Activity | Calls, Usage |
| Where documents live | Knowledge | Files, Documents |
| Apps the workspace connected | Apps & connections | Tools, Your tools, Integrations, Providers, Connect |
| Data protection | Privacy | Compliance |
| Outbound list work | Campaign (inside Tasks) | Dialer |
| A shared listing | Marketplace (inside Agents → Hire) | Shelf, Packs, Templates in customer-facing copy |

COPY-1 (KAN-213) owns the words on the site; this table is the app's.

## 6. Settings (UI-3, unchanged, listed for completeness)

Members & roles (with operator sessions) · Apps & connections (Mine / Workspace) · Billing & usage (plan, credits, minutes, alerts, spend limit, packs, invoices) · Voice & numbers (numbers, KYC, DND, carriers) · Privacy (retention, disclosures, exports, erasure) · Developers (API keys, webhooks, SDKs, MCP).

## 7. The first ten minutes, on the new shell

1. Sign up, verify. Land on My Decibyl with one Build card already open: "What job are you hiring for?" and the industry chips.
2. Type the job. The card fills: name, handle, what it will do, price per minute. **Hear it** in the card (browser call). **Try it** in a phone frame.
3. Say what to change. A diff card. Confirm.
4. In the card: "Put it on WhatsApp" or "Put it on a number". One flow each, inside the card.
5. Done. The bot appears under Agents; its first runs appear under Activity.

Target (from the coordinator model): sign-up to first Hear it under 15 minutes median; 80% of bots tried or checked before going live; zero vendor names seen unless Advanced is opened.

## 8. Order of work, inside the self-serve gate

| Step | Scope | Board | Size |
|---|---|---|---|
| UI-0 Cut and rename | Delete the 11 dead components; `shell` on by default and its off-state removed; one name per destination (§5) with redirects; collapse the 9 tab strips to the 4 in §3; remove the Setup dropdown by moving its items into the header "…"; Billing and Settings into the sidebar for everyone. No new screens. | new KAN-257 | 1 week |
| UI-1 Shell | Seven homes, redirects, Pending-for-you strip, directory, ⌘K | KAN-208 | 1.5 weeks |
| UI-2 Bot page | Chat/Canvas + nine-section inspector; Test ▾ with three verbs; Publish with diff; delete settings page, create wizard and first-agent journey after migration | KAN-209 | 3 weeks |
| CH-0 WhatsApp connect | The one missing flow: connect a WhatsApp number to a bot from Channels (WABA onboarding via the existing `public_whatsapp` routes) | new KAN-258 | 1 week, parallel |
| UI-3 Settings | Six sections; role × section table test | KAN-211 | 1 week |
| MKT-1 Publish | Superadmin marketplace screen and "Publish to marketplace" in the bot "…" | KAN-256 | parallel, backend-heavy |

UI-0 starts now because it removes options without waiting for the new shell and it is where the E2E journeys (ENG-1) get their stable selectors. UI-1 and UI-2 follow; UI-3 last because Settings is the least visited.

Evidence for done, per step: the five E2E journeys green; the option counts in §1 re-measured and written on the issue; five test users, four of five find each destination unaided (UI-1 test 7).

## 9. Component library decision (founder question, 30 September)

The app already runs on shadcn/ui (Radix + Tailwind), Recharts, lucide and react-hook-form. The generic look comes from the information architecture in §1, not from the components, so no library swap.

| Library | Decision | Where |
|---|---|---|
| shadcn/ui | Keep; it is the design system. Every UI PR uses `components/ui` first | Whole app |
| Tremor | Adopt, narrowly: charts, KPI tiles and tables on the Activity home and superadmin billing; Tailwind/Radix-based, so no second theme | Activity, superadmin |
| 21st.dev | Source of individual components (Try-it phone frame, diff card), copied in and made ours; never a dependency | Bot page |
| Magic UI, Aceternity | Public site only (separate repo); nothing animated in the app | decibyl.ai |
| React Aria | No; Radix already covers it | |
| MUI, Chakra, Mantine, HeroUI | No; each is a second design system | |

Missing today: a written token set. The app uses ivory, ink, coral, Inter and Outfit by convention; the docs site and the public site each drifted. UI-0 (KAN-257) adds `ui/src/styles/tokens.css` and a one-page `ui/DESIGN.md` extracted from what the app already uses, so UI-1 and UI-2 start from one palette, one spacing scale and one set of surface rules (no cards inside cards, dense and quiet for daily use).

## 10. Agent faces and people avatars (founder input, 30 September)

A hired agent is a colleague with a handle. It needs a face that is the same everywhere: the roster, the thread, the About panel, the public marketplace card, the WhatsApp profile. Today `BotAvatar` is static artwork behind the `shell` flag.

**Decision.** Port the Bloub avatar (github.com/jeremy-prt/bloub, MIT, Vue 3, 1.7k stars) to a React component in `components/ui/AgentFace.tsx`: one SVG shape morphing between states, two eyes with gaze drift and blinking, no animation library. Bloub gives 8 bodies × 12 colours × 16 rest expressions, so every handle in a workspace gets a distinct, deterministic face from a hash of its id; the owner can re-roll or pick. Alternatively the React `bot-avatars` package (18 3D shapes) if a 3D look is wanted; recommendation is the flat SVG, because it renders in a 24 px roster row and in an email signature without WebGL.

**States mapped to what the agent is doing**, so the face is also a status indicator: idle · listening (a call or message is coming in) · thinking (a run in progress) · speaking (TTS or a reply streaming) · waiting (a card needs the owner) · blocked (key exhausted, number down) · asleep (paused). The same states drive the roster dot and the header chip, one source of truth.

**People.** Members get a deterministic avatar from their id with zero image storage (the Blobatar idea), replacing the initials fallback; a real photo still overrides.

Board: UI-4 (new). Lands with UI-1 (roster) and UI-2 (bot page header). Also becomes the marketplace card's picture in MKT-1.

## 11. Hi-fi sample 1: My Decibyl (30 September)

`samples/2026-09-30-my-decibyl-hifi.html`, also published as a private artifact. One screen, the first ten minutes: the roster with animated faces (states: speaking, waiting, idle), the Build card with Hear it · Try it · Check it and a price per minute before hiring, an approval card from `@accounts` with the draft shown and the grant scopes Once · This task · 7 days · Always, the trial meter (invite-only, days left, credits), the composer whose addressee changes when a line starts with a handle, follow-up chips, and the inspector with the nine sections collapsed and BYOK shown in Brain & voice.

Type and surface, on the founder's instruction to avoid common faces and generated-looking design: Familjen Grotesk for display, Schibsted Grotesk for body, Martian Mono for handles, prices and counts; a cool paper ground with a green-black ink, one teal accent for actions and one haldi for "needs you"; no cream, no coral, no gradients, no cards inside cards, nothing centred. These become the tokens in UI-0 item 7 once the founder confirms the direction; Refero references are pulled in the next session (the server is registered) for the bot page and Activity samples.
