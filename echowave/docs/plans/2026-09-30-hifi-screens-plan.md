# Decibyl hi-fi screens: the plan behind the set

Date: 30 September 2026. Screens: `samples/2026-09-30-decibyl-screens.html` (ten screens, one shell, light and dark, phone width), also published as a private artifact. Sample 1 (`2026-09-30-my-decibyl-hifi.html`) is superseded by screen 1 here. This document is the checklist a reviewer uses: every main feature on the board, the screen it lives on, and the element that proves it.

## The system

One shell everywhere: rail (brand, seven homes, colleagues with faces and status, the trial meter) · main (title, one tab strip at most, the content) · inspector where a thing is selected. Type: Familjen Grotesk display, Schibsted Grotesk body, Martian Mono for handles, prices and counts. Colour: cool paper, green-black ink, teal for actions, haldi for "needs you", one red for never/failover. No gradients, no cards inside cards, nothing centred, numbers tabular. Charts: one hue, thin bars, today in haldi, axis in ink tokens, one chart per question. Faces: one SVG per colleague, state = what it is doing.

## Feature coverage, screen by screen

| # | Screen | Main features it must show | Board |
|---|---|---|---|
| 0 | Start | Invite accepted, trial days and starting credits; the first Build card already open with the business, the job and the channel; "or pick from the marketplace"; what happens next in one line | SIG-1 KAN-206, PLAN-1 KAN-255, S-2 KAN-190, KAN-140 |
| 1 | My Decibyl | Pending strip (needs you); Build card with Hear it · Try it · Check it and price before hiring, guardrails summarised on the card; approval card with the draft shown, "draft, not sent", Send · Edit · Not now, grant scopes Once · This task · 7 days · Always; composer that fits, addressee follows the handle, follow-up chips incl. Save as routine and Review; Thread · Graph switch; inspector: Pending for you, this week's outcomes and cost per outcome, Monday review card | UI-1 KAN-208, POL-2 KAN-259, OUT-1 KAN-253, REV-1 KAN-262, GV-1 KAN-215, G4 KAN-156 |
| 2 | Agents | Mine: one card per colleague with face, status chip, channel, outcomes and cost per outcome, budget used; New colleague; Hire: marketplace cards published by NAutomation Labs with version, hires, Check-it result, Try it first · Hire | UI-1, UI-4 KAN-260, MKT-1 KAN-256, OUT-1 |
| 3 | Bot page | Header: face, handle, live number and version, Chat · Canvas, Test ▾, Publish, "…"; chat edit by sentence → Publish card with the diff and the Check-it score, Hear the draft, rollback via Versions; Canvas as a view of the same definition; inspector, ten sections: Role & persona · Skills (shared chip, add from marketplace) · Channels (number live with ring rule, WhatsApp connect step, web widget) · Brain & voice (tier, BYOK line, Advanced) · Apps & tools · Knowledge & memory · Triggers & routines · Guardrails & approvals (Never / Asks first / Alone, budget, hours, Pause · Retire) · Quality (outcome definition, answered rate, p95, failovers, Check it) · Versions | UI-2 KAN-209, VOICE-1 KAN-261, CH-0 KAN-258, BYOK-1 KAN-254, POL-2, OUT-1, KAN-140 P1 |
| P | Project | Like a Claude project: the brief every colleague reads first, files scoped to it, people and agents collaborating in one thread (a hand-off card between two bots, with an Allow scope of "This project"), Decibyl reporting progress; inspector: Checklist (small internal list, items tick by person, by outcome, or at a goal), Files, Colleagues, Deliverables (a Show page), Activity here with spend and outcomes. Projects listed in the rail under Colleagues; Tasks stays the cross-workspace board with a Project filter | PRJ-1 (new), coordinator P2, OUT-1, ORG-1 light |
| 4 | Tasks | Mission line in the subtitle; one board with Project and Colleague filters; columns Waiting on you (blocked cards ask for the one thing), In progress with counts, Delegated (from → to, due), Done with outcomes and cost; every card names its colleague with a face | G11 KAN-186, ORG-1 light KAN-219, OUT-1, coordinator P2, task_board flag |
| 5 | Activity | Runs · Campaigns · Review · Analytics · Spend; four tiles (answered, first-turn p95 vs target, bookings with cost per booking, credits and days left); runs-per-day chart; runs table across kinds (call, WhatsApp, routine) with outcome chips incl. handed over and failover, latency and cost per run | VOICE-1, OBS-1 KAN-212, audit item 6, M-1 KAN-205 |
| 6 | Knowledge | Library · Contacts · Memory · Graph; contacts with consent and DNC; each item: source, stated or inferred, version or expiry, used by which colleagues, who approved; a guardrail-protected item (price list, never spoken); an inferred memory awaiting Confirm | UI-3 KAN-211, KNOW-1 KAN-210, MEM-1 KAN-197, KAN-46 |
| 7 | Team | Mission line; people and colleagues on one list with role, reports-to and budget; an operator (studio) with a session end time; approvals queue naming who may approve; operator sessions with Revoke | ACT-1 KAN-200, OPS-1 KAN-201, ORG-1 light, E-1 KAN-160 |
| 8 | Settings | Six sections; Billing & usage: trial tiles, "what a credit buys" rate card (read-only during trial), Your own keys with last use and health, "usage on your keys is recorded, never charged" | UI-3, PLAN-1, BYOK-1, BILL-1 deferred KAN-207 |
| 9 | Staff | Publish from an agent: kind, industry, needs (scrubbed), demo line required for a calling pack, price, preview of the public card, List · Save as draft, hired copies pinned; listings table with version, hires, Check-it; Invites (waitlist → invited → trials); Trials with Extend · Top up | MKT-1 KAN-256, S-2 KAN-190, PLAN-1, KAN-234 access |

Not a screen, by design: pricing and purchase (closed at launch), the org chart as a home (a Graph view instead), a separate builder (the Build card), a settings page per bot (the inspector).

## What each screen must still prove in code, not in the sample

- Screen 1: the addressee router (`mentions.resolve`), grant rules written and enforced before `actions.settle`, TEST stamping on Hear it / Try it / Check it runs.
- Screen 3: Chat and Canvas edit one definition; Check it runs on Publish by default; the number flow sets `is_platform_managed`; the BYOK line reads the vault on text paths.
- Screen 5: the latency numbers are org-scoped reads of the staff measurements; failover rows come from the ServiceSwitcher events.
- Screen 9: listings are rows, not code; a non-superuser gets 403; hired copies carry the version.

## Order

The screens land in the order of the UI plan §8: UI-0 (tokens from this set, dead code, names), then screens 1 and 2 (UI-1), screen 3 (UI-2 with CH-0 and VOICE-1), screens 5 and 4, screen 9 (MKT-1), screens 6, 7 and 8 (UI-3). Screen 0 rides SIG-1. Each PR attaches a screenshot at 1280 and 375 beside the matching screen here; the E2E journeys (ENG-1) use these screens' element names as selectors.
