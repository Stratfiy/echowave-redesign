# The agent-coworker market, 30 September 2026, and what Decibyl does about it

CPO view for Nithish. Companion to `2026-09-30-cto-technical-plan.md` and `2026-09-30-ui-end-to-end-plan.md`. Four researchers read official pages, docs, pricing pages and dated press on 30 September; every claim below carries a source in §8, and "not verified" means exactly that. Products named by the founder: Muse (Meta), Dots (OpenAI), Grok Bot (xAI), Slack with Agentforce, Buzz (Block). Added because they matter to us: Dust, Lindy, Zapier Agents, Copilot Studio, Gemini Enterprise, and the Indian field (Sarvam, Bolna, Ringg, Trikon, Gupshup, Haptik/Interakt, Wati, Zoho, Freshworks, Outpero).

## 1. Executive summary

1. **The category Decibyl is building became mainstream this month.** Meta shipped Muse (8 Sep) and Muse for Small Business (29 Sep); OpenAI shipped Dots, "always-on agent coworkers", at DevDay (29 Sep); xAI shipped Grok Bot (11 Aug, enterprise 3 Sep); Block shipped Buzz (21 Jul). All four use the same grammar we chose in the office model: a named agent, addressed like a colleague, that keeps working after you close the app and comes back with a card when it needs approval. Our interaction model is validated. It is no longer a differentiator on its own.
2. **None of them run a business's customers on phone and WhatsApp in India.** Muse is US and Canada only with no India date, and its phone calling was pulled after Reuters found humans in a call centre making the calls. Dots have no WhatsApp and SMS is a US-only beta. Grok Bot has no telephony except a community template. Buzz is a developer tool with no telephony. The Indian voice players (Sarvam, Bolna, Ringg, Trikon, Outpero) are conversation-metered single-channel tools with no manager and no routines. **The open position is: a hired team of named agents that works your customers on phone, WhatsApp and email, coordinated by a manager, billed honestly in rupees.** Nobody occupies it today.
3. **The price anchor in India is set: ₹3.5 to ₹6 a minute all-in, free credits on signup, five minutes to first agent, no card.** Sarvam ₹3.5 (press), Gupshup ₹3.5, Trikon ₹5 with a 14-day no-card trial, Ringg ₹6, Outpero ₹3.5 to 7. Our plans must read at or under ₹5 a minute equivalent, or the 15-second pulse story does not get heard. This is the input BILL-1 was missing.
4. **The fights ahead are about trust and consent, and we are on the right side.** Amazon and Resy block Muse; MeitY has proposed mandatory human-in-the-loop for agentic payments; Meta's undisclosed human callers became a scandal; Buzz ships cleartext keys and membership-only permissions. A product whose whole UX is a confirmation card with a signed, exportable log is what regulators and buyers are about to ask for. Lead with it.
5. **Three moves for the launch**, all inside the current gate: (a) approval grant scopes so cards do not fatigue (once / this task / for N days / always, ask-first wins); (b) a free-to-start trial with an included allowance per bot and a hard-stop top-up card, priced against the ₹5 anchor; (c) the WhatsApp channel in the Build card (CH-0), because WhatsApp is where Muse will arrive in India and where our customers already are.

## 2. The landscape

Two axes decide the map. **Who the agent works for**: the owner's own tasks (personal or team productivity) versus the business's customers (calls, messages, collections, bookings). **How it is sold**: developer or enterprise-led versus self-serve for a small business.

```
                       works the OWNER's tasks                 works the business's CUSTOMERS
                 ┌──────────────────────────────────┬──────────────────────────────────────┐
 self-serve,     │ Muse (US only)  Dots ($100–500)  │  Trikon  Outpero  Ringg  Bolna       │
 small business  │ Grok Bot  Lindy  Zapier Agents   │  Sarvam voice agents  Gupshup voice  │
                 │ Zoho Zia (inside Zoho)           │  Interakt/Wati (WhatsApp only)       │
                 │                                  │  ★ DECIBYL: multi-channel + manager  │
                 ├──────────────────────────────────┼──────────────────────────────────────┤
 developer /     │ Buzz  Dust  Relevance AI         │  Skit (collections, 7 named agents)  │
 enterprise-led  │ Agentforce in Slack  Copilot     │  Yellow.ai  Haptik  Gnani  Sierra    │
                 │ Studio  Gemini Enterprise        │  Decagon                             │
                 └──────────────────────────────────┴──────────────────────────────────────┘
```

The top-right quadrant is where Decibyl sits and it has only single-channel, single-agent tools in it. The one product with a manager and a named roster working customers, Skit, is enterprise-led and collections-only.

## 3. Tiers

**Direct (the real fight in India, next 12 months):** Sarvam voice agents (self-serve since Jun, rebranded Aug, ₹100 free credits, agents that "work together", 22 languages), Trikon (₹5 a minute, one GST line item, 14-day trial), Ringg (₹6 a minute, voice + WhatsApp + browser, shared "context graph" across agents, $15M raised Aug), Outpero (the new "AI employee", ₹3.5 to 7 a minute, "built in 2 minutes"), Gupshup (self-serve voice from 4 Sep on top of the largest WhatsApp base). Interakt and Wati own WhatsApp for 50k+ SMBs and are adding agents.

**Adjacent (same grammar, different job):** Dots, Muse, Grok Bot, Lindy, Zapier Agents, Dust. They work the owner's inbox, calendar and apps. They will set customer expectations for how an agent should behave: named, persistent, asks before acting, shows a log. Muse on WhatsApp is the one to watch; assume it reaches India in 2027.

**Aspirational (where the money and the patterns are):** Agentforce in Slack (private draft → approve → post; Simulate vs Live Test), Copilot Studio (describe → suggested tools; per-agent credit cap), Sierra and Decagon (outcome pricing, and the honest finding that most customers choose per-conversation because "resolution" is arguable).

## 4. Benchmark matrix

Scale: ●● leads, ● present, ○ absent or not verified. No totals by design; read the columns.

| | Named agent, addressed as colleague | Manager coordinates agents | Confirm-before-act with scopes | Test vs live separated | Phone to customers | WhatsApp to customers | Email channel | Routines (scheduled work) | BYOK | Rupee prepaid + GST | Self-serve, minutes to first agent | India available |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Decibyl (honest)** | ●● | ●● | ● (every action, no scopes yet) | ●● (TEST stamp, Try/Hear/Check) | ●● | ● (backend, no UI: CH-0) | ●● | ●● | ● (voice only: BYOK-1) | ●● | ○ (not live; closed for studio) | ●● |
| Dots (OpenAI) | ●● | ● (teams of dots, enterprise pilot) | ●● (4 levels per category) | ○ (watch live only) | ○ (can call your dot; it cannot call out) | ○ | ● | ●● | ○ | ○ | ● (desktop only; $100–500 plans) | ● (gradual) |
| Muse (Meta) | ●● | ● (subagents internal) | ●● (Sentinel; one-time/task/time/perpetual grants) | ○ | ○ (pulled after human-caller incident) | ● (owner chats with Muse on WhatsApp) | ● | ●● (goals keep running) | ○ | ○ | ●● (free tier) | ○ |
| Grok Bot (xAI) | ●● | ●● (chief of staff + specialists, visible handoff) | ●● (Allow once / Always / Deny; ask-first wins) | ○ ("run once while watching") | ○ (community Bland template) | ○ | ● | ●● (teach by observation → routine) | ● (MCP) | ○ | ● ($30–300 plans) | ● (unverified) |
| Buzz (Block) | ●● (own keypair) | ○ | ○ (approval gates "being wired up") | ○ | ○ | ○ | ○ | ● (YAML workflows) | ●● (env vars, cleartext) | ○ | ○ (self-host, keys) | ●● (open source) |
| Agentforce in Slack | ●● | ● (subagents) | ●● (private draft → approve → post) | ●● (Simulate / Live Test) | ● (Agentforce voice, 30 credits) | ○ | ● | ● | ○ | ○ | ○ (Salesforce licences) | ● |
| Sarvam voice agents | ○ | ● ("agents work together") | ○ | ●● (simulated-user tests, versions) | ●● | ● (enterprise only) | ○ | ○ | ● (telephony only) | ●● (₹ credits, never expire) | ●● (<5 min, ₹100 free) | ●● |
| Trikon | ○ | ○ | ○ | ○ | ●● | ●● | ○ | ○ | ○ | ●● (one GST line) | ●● (14-day, no card) | ●● |
| Ringg | ○ | ● (context graph) | ○ | ○ | ●● | ●● | ○ | ○ | ○ | ●● | ●● | ●● |
| Outpero | ● ("AI employee") | ○ | ○ | ○ | ●● (outbound only) | ● (sync) | ○ | ○ | ○ | ●● | ●● (2 min) | ●● |
| Skit | ●● (7 named, incl. Manager) | ●● | ○ | ○ | ●● | ○ | ● | ● | ○ | ● (custom) | ○ (30–60 day pilots) | ●● |

Where Decibyl leads: the three left columns together with the three channel columns. Nobody else has both halves. Where it trails: not live, no approval scopes, WhatsApp without a UI, BYOK on voice only, and a plan ladder that is closed.

## 5. Deep dives

**Dots (OpenAI).** A named, persistent agent with its own cloud computer, inheriting your ChatGPT memory, connected to 4,000 apps, messaged from Slack and Teams. The action model is the best in the field: per category (share / purchase / access) the owner picks "take action without asking", "if pre-approved", "ask", or "hand off", and "permission to draft does not authorise sending". First dot is bundled with Pro or Business Premium; extra dots are unpriced. Learn: the four-level rule and the draft-is-not-send principle. Avoid: unpublished pricing, desktop-only creation, memory that survives disconnecting an app with all-or-nothing reset.

**Muse (Meta).** Goal → plan → long-running task on a per-user VM, with a separate "Sentinel" agent as the sole permission authority, approvals shown outside the conversation with grant scopes (one-time, session, task, time-bounded, perpetual), single-use cards for purchases, and an exportable audit trail and memory. Free to 100M tokens a week; SMB tier free with limits, US and Canada only. The phone-calling feature was found to be humans in a call centre and was rolled back. Learn: scoped grants, approvals as their own surface, "understands what you sell and how your brand sounds" onboarding. Avoid: any undisclosed human in the loop; token-metered pricing an SMB cannot read.

**Agentforce in Slack.** The agent is a channel member; it drafts privately, the person approves, it posts in the thread. Builder has Simulate (no permissions, no actions, no data) and Live Test side by side. Pricing is three overlapping models (Flex credits at ~$0.10 an action, $2 a conversation, $5 a user) plus Salesforce licences. Learn: private draft → approve → post, and the Simulate/Live toggle in one pane. Avoid: three pricing units for one product.

**Sarvam and Trikon (the Indian direct tier).** Sarvam: self-serve since June, ₹100 free credits, versioned agents with simulated-user regression tests before go-live, 22 languages, ₹3.5 a minute in press, agents that route to each other, WhatsApp only for enterprise. Trikon: ₹5 a minute with STT, LLM, TTS and carriage bundled, one line on a GST invoice, DND and TRAI built in, 14-day trial without a card, "live in 5 minutes". Learn: Sarvam's test-before-publish harness (we have the eval runner; make it the default on Publish), Trikon's one-line invoice and no-card trial. Concede: raw per-minute price leadership to Sarvam's own stack; compete on the team, the channels and the honesty of the bill, not on the cheapest minute.

## 6. White space and threats

**White space, confirmed open.** The top-right quadrant: a self-serve, rupee-billed, named team of agents with a manager, working a business's customers on phone, WhatsApp and email, with routines, every action confirmed and logged. Three lines nobody in India claims: "Hire a team, not a tool"; "One GST invoice, honest 15-second billing, your own AI keys"; "Runs your routines, not just your conversations".

**Threats, in order.**
1. **Sarvam** adds a manager layer and WhatsApp self-serve. It has the stack, the price and 325M minutes of proof. Twelve months.
2. **Gupshup or Interakt** put a named agent on the WhatsApp base they already own. Distribution beats product for the first 10k SMBs.
3. **Muse for Small Business on WhatsApp reaches India** in 2027, free. It works the owner, not the customers, but the owner will compare.
4. **Regulation**: MeitY's human-in-the-loop for agentic payments. A threat to autonomous-first products; an asset for us if the card and the audit row are already the product.
5. **Our own**: we are not live, and the price book has not been decided while five rivals publish rupee rates.

## 7. Recommendations, tied to the board

| # | Move | Why (evidence) | Board | Gate |
|---|---|---|---|---|
| 1 | **Approval grant scopes**: on every card, "Once · This task · For 7 days · Always", per action category; "Ask first" always wins over an "Always" | Dots, Muse and Grok Bot all shipped scopes; without them cards fatigue and owners switch bots to autonomous | new POL-2 (KAN-259), extends POL-1 KAN-198 | Self-serve |
| 2 | **Price against the ₹5 anchor** with an included allowance per bot per month and a hard-stop top-up card; one unit (credits) with a printed rate card per action type | Indian anchor ₹3.5–6; Slackbot bundles a quota; Copilot disables at 125% overage; Agentforce's three units confuse | BILL-1 KAN-207 (input recorded) | Self-serve, week 1 |
| 3 | **WhatsApp in the Build card** | Muse arrives on WhatsApp; Interakt/Wati/Gupshup own the base; our backend prices it and no customer can switch it on | CH-0 KAN-258 | Self-serve |
| 4 | **Check it before Publish, by default**, with the result on the Publish card | Sarvam ships simulated-user tests before go-live; Agentforce ships Simulate; ours exists (eval runner) and is optional | P1 of the office model, KAN-140 comment | Self-serve |
| 5 | **Private draft → confirm → thread** for anything a bot sends to a customer; "draft is not send" in copy and permissions | Agentforce, Dots | POL-2 (KAN-259) | Self-serve |
| 6 | **"Save this as a routine" from any finished card**; teach-by-doing | Grok Bot, Slackbot AI-skills, Lindy | G4 exists (KAN-156); add the entry point in UI-2 | Self-serve |
| 7 | **Per-bot ledger of actions + credits, exportable**; per-bot spend and send caps | Muse's audit trail and exportable memory; Buzz's signed log; Dust's spend checkpoint | OUT-1 KAN-253, E-1 audit log | Self-serve → pilots |
| 8 | **Business-context onboarding**: what you sell, how you sound, what customers ask, before the first Build card | Muse for Small Business | SIG-1 KAN-206 | Self-serve |
| 9 | **Disclosure rule**: no human ever completes a "bot" call or message without saying so on the card and in the call | Meta's incident | PRIVACY.md + AUP (H1 exists) | Now |
| 10 | **BYOK on every surface** | Bolna has BYOK in USD; Zoho inside Zoho; nobody with rupees + GST + BYOK | BYOK-1 KAN-254 | Self-serve |
| 11 | **Positioning copy**: adopt the three unclaimed lines; drop "voice AI platform" | §6 | COPY-1 KAN-213, M1 KAN-74 | Self-serve |
| 12 | **Do not** build outcome pricing as the default; offer it later for one measurable outcome (booked appointment, promise to pay) | Sierra unpublished; Decagon: most choose per-conversation | OUT-1 note | Later |

Brand balance check: the plan keeps the product's weight on trust and honest billing (the moat), then on the team model (the story), then on the agent loop itself (table stakes). Move 2 is the only one that touches price and it is the founder's call.

## 8. Questions for the founder

1. Price: do we anchor at ₹5 a minute all-in on the default Indic stack, with premium voices outside the flat price, as the costing audit recommends?
2. Trial: 14 days without a card (Trikon) or credits-limited without a clock (Sarvam)? My recommendation: 14 days plus an included allowance, because a clock creates the conversion moment.
3. Which single line leads the site: "Hire a team, not a tool" or "One invoice, honest billing, your keys"?
4. Sarvam is the sharpest threat. Do we also *use* Sarvam (we do, for STT/TTS) and say so, or keep the stack unnamed?

## 9. Sources and verification notes

Official and dated unless marked. **OpenAI Dots**: openai.com/index/introducing-dots (29 Sep 2026); TechCrunch, VentureBeat, 9to5Google, Bloomberg (29 Sep); Business Today (30 Sep); Help Center wording via search snippets only (openai.com returned 403 to the fetcher). **Grok Bot**: x.ai/news/introducing-grok-bot (11 Aug), grok-bot-more-plans (26 Aug), grok-bot-for-enterprise (3 Sep); docs.x.ai/grok-bot/bots and /approvals-security-and-privacy; marketplace dial-bot page. **Muse**: about.fb.com newsroom (8 Sep, 29 Sep); research.meta.ai security post (8 Sep); muse.ai/platform, /business; TechCrunch (8, 23, 27, 29 Sep); CNN (23, 28 Sep); Reuters via BNN Bloomberg and 404 Media (22 Sep); MediaNama (Sep); Techdirt (24 Sep); pricing tiers via TechCrunch and eesel (secondary). **Buzz**: block.xyz post and github.com/block/buzz README and SECURITY.md (21 Jul onward); TechCrunch (21 Jul); Euronews (23 Jul); star-history (30 Sep). **Slack/Agentforce**: slack.com help and blog (5 Mar 2025, 15 Apr 2026, 17 Jun 2026); Trailhead Agentforce Builder; salesforce.com/agentforce/pricing; per-resolution $2 is third-party only. **Dust, Lindy, Zapier, Relevance, Copilot Studio, Gemini Enterprise**: their docs and pricing pages, 30 Sep; Zapier and Gemini pricing pages 404, figures third-party. **India**: inc42 (2 Jun, 2 Aug); docs.sarvam.ai changelog and pricing; bolna.ai; ringg.ai (26 Aug); trikon.tech; gupshup.ai and PR Newswire (4 Sep); haptik.ai, interakt.shop, wati.io, zoho.com/zia/agents, freshworks.com, outpero.com, skit.ai, gnani.ai (30 Sep). Not verified: Sarvam ₹3.5 on an official page; Skit and Gnani rates; Agent8Work beyond a snippet; Lindy "Societies"; MCP in Dots; Indian-language support in Dots or Grok Bot; a Meta-published skill-authoring doc.

## 10. Addendum: OpenBot by CopilotKit (founder input, 30 September)

github.com/CopilotKit/openbot, MIT, alpha, 5.8k stars. "The AI assistant your company can actually own." A self-hosted agent platform: each agent is an isolated Docker "computer" with its own browser, workspace and profile (optional gVisor); a gateway evaluates a CEL policy, writes an audit entry, then routes browser, file, shell and MCP actions; agents are declared in `agents.yaml` or a web UI; standing routines with a 15-minute floor; AG-UI as the agent-to-UI protocol; durable threads via CopilotKit's hosted Intelligence service; React/Vite + Hono + Postgres/pgvector. No Slack, WhatsApp or voice channels; single-user by default; not a SaaS.

**Where it sits.** Bottom-left of the map with Buzz and Dust: developer-led, self-hosted, works the owner's tasks. Not a competitor for an Indian SMB; a strong reference architecture, because it is the same shape as ours: per-bot isolation, a policy gate, an audit row per action, routines, packs as declarations.

**What to take.**
1. **AG-UI** as the wire protocol between our runtime and the thread. We stream agent events over a bespoke WebSocket (`routes/agent_stream.py`) and render cards from our own event kinds. Adopting AG-UI's event vocabulary for the text paths would let any AG-UI client (and CopilotKit's React components) render a Decibyl thread, and would make the SDK (ENG-10) smaller. Evaluate in UI-2; not a rewrite.
2. **CEL policy in the gateway**, one expression language for the approval matrix and grant scopes, evaluated before every action, with the rule that allowed or blocked it written on the audit row. This is POL-2's mechanism; borrow the shape, keep our matrix.
3. **Per-agent computer, not per-job.** Our sandbox starts one container per script run (SBX-1). OpenBot gives each agent a persistent workspace and browser profile, which is what "log in once, then the bot works the portal every morning" needs (the desktop-companion and procurement stories). Phase 3 option, after INFRA-B.
4. **`agents.yaml` packaging** ≈ our `packs/*/SKILL.md`. Publishing (MKT-1) should keep the declaration file as the unit so a pack can be exported to, or imported from, an OpenBot-style deployment.

**What to leave.** The hosted Intelligence dependency (our threads are ours), single-user mode, the 15-minute routine floor (ours run on triggers and the clock), and the absence of channels: the channels are the product for us.

**What it means.** Self-hostable "own your coworkers" is now free and credible for developers. For an SMB that is not a purchase decision, but it sets a floor: governance (policy, audit, isolation) is table stakes, not a premium tier. Ours is already built; make it visible on the trust page and in the Activity log.

## 11. Addendum: Paperclip (founder input, 30 September)

github.com/paperclipai/paperclip, MIT, Paperclip Labs. "The open-source app everyone uses to manage agents at work." Open-sourced 15 January, launched 2 March 2026; about 95k stars and 16k forks by 30 September, self-hosted only, no paid cloud, no disclosed funding. It sits above agent runtimes (Claude Code, Codex, OpenClaw, Cursor, any CLI or webhook: "if it can receive a heartbeat, it's hired") and gives them a company: a mission and goals, an org chart with roles, titles, reporting lines and permissions for humans and agents alike, a monthly budget per agent with a hard stop at 100%, tickets linked to goals and checked out atomically, heartbeats (scheduled or event wake-ups), board-level approval gates for hires and strategy, cost tracking by company, agent, project, goal and provider, and an immutable audit trail of decisions and tool calls. A CEO agent drafts a hiring plan and delegates down the chart. No channels: no phone, WhatsApp, email or customers.

**Where it sits.** Bottom-left with OpenBot and Buzz: developer-led, self-hosted, works the owner's company. It is the most direct external validation of the office model: named agents, a manager, work as tickets, approval gates, per-agent budgets, an audit row per decision. It is also the vocabulary developers now expect.

**What it confirms and what to take.**
1. **Per-agent monthly budget with a hard stop** is the governance primitive users understand. Ours exists as a script spend cap and workspace budgets; make it per bot, visible on the bot page, and the thing that pauses with a card. Lands in OUT-1 (consumables ledger) and POL-2.
2. **Goals that trace to a mission, and every ticket carrying its ancestry.** ORG-1 (KAN-219, "org chart and goals") was Phase 3; Paperclip shows it is table stakes for anyone who has seen an agent company. Pull the light version forward: a workspace mission line, a goal per bot, and the outcome definition (OUT-1) counted against it.
3. **Heartbeats** are our routines and triggers. Same primitive; keep the name "routine" for owners, expose "heartbeat" only in the API.
4. **Tickets checked out atomically** so two agents never do the same work: our task board (G11) plus delegation-with-wait (coordinator model P2) should adopt the checkout rule.
5. **Humans and agents on one org chart with the same permission model**: this is what Team (UI-1) and the approval matrix already imply; say it plainly in the UI.

**What to leave.** The zero-human-company framing (our buyer wants a receptionist, not a CEO), and a chart as the front door (ours is the thread; the chart is a view).

**What it means.** The "company of agents" mental model has 95k stars of mindshare among developers. For an Indian SMB owner it becomes real only when the agents answer the phone. Decibyl's line, "hire a team, not a tool", is the SMB-facing version of exactly this, with the channels Paperclip does not have.
