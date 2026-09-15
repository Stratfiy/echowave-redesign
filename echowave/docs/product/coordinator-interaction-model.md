# One office, named colleagues: the coordinator interaction model

**Status:** proposed, 15 September 2026. Answers KAN-129 (founder direction of 14 September: Decibyl is the builder and the coordinator; bots do the work; one conversation, no build/test mode switch).
**Owners:** CPO and CTO views below; the app agent owns sequencing.
**Decision asked for:** adopt the office model, do not build a mode switch, make P0 part of the launch gate.

---

## 1. The problem in one screen

Today a person who wants a bot to do something has four places to say so: the Home box (Decibyl), a bot's own chat, a channel with several bots, and the builder panel. Three of those can build or edit and three can run work, and nothing on screen tells you which you are in. The phone screenshots of 14 September show the result: an owner typed "build me a bot" into Home and Decibyl answered "I can't build a new bot myself, that needs to be set up in Decibyl directly." The product was in the room and said it was somewhere else.

The obvious fix, a Build / Work toggle like the Chat / Work switch in ChatGPT's mobile app, is the one we should not build. Section 2 says why.

## 2. Prior art, and what decides it

I looked at the products a mid-market buyer will have seen, and at one piece of interface theory that predicts which of them feel right.

| Product | How you address an agent | Build vs. work | What it teaches |
|---|---|---|---|
| **Slack, Agentforce agents** | Add an agent to a channel and @mention it like a teammate; it replies in the thread using your permissions. | Building happens elsewhere (Agentforce Builder). In Slack there is only work. | The addressee is the whole grammar. Nobody is taught a mode; they are taught a name. |
| **Agentforce Builder** | A preview panel beside the configuration. | Two preview modes, **Simulate** (mock data, no actions, no permission checks) and **Live Test** (real data, real actions). | Right for a Salesforce admin, who understands "mock data". Wrong for a clinic owner, who will run Live Test believing it is Simulate. Two modes with rules about what each can do is exactly the confusion we are trying to remove. |
| **Zapier Agents** | An agent can have another agent as a tool ("Call an agent"), with a recommended limit of about five, and related agents are grouped into pods. | Building is a separate editor; work is a run. | Delegation is a tool call with a result, not a chat thread. The caller waits for the callee. That is the shape our "delegate with wait" needs. |
| **Dust** | People and several agents share one conversation, in the app and in Slack; anyone in the channel sees the answer. | Agents are built in a separate editor. | One room for people and agents works at team scale, and the audience seeing the answer is a feature, not a leak. |
| **Lindy** | "Societies": agents delegate subtasks to one another by rules the user sets, with shared memory. | A separate builder per agent. | Automatic delegation without a person in the loop is the claim we are measured against. It is also the part buyers distrust, which is why every step in our model is a card a person can see. |
| **Grok Bot, Samvaad** (as pitched to us, not verified) | "Agents coordinate" and "build a company in days". | Not disclosed. | These are the claims a prospect will repeat to us. The honest answer is a demo of bots handing work to each other in one thread, with the cards showing who did what. |

The theory is Jef Raskin's rule from *The Humane Interface*: an interface is modal when the same gesture does different things depending on a state the user has to remember, and modes cause errors because the user can hold a false belief about that state and act on it. A Build / Work switch is a mode by that definition. It does not remove the confusion; it moves it to a toggle, and the first time someone sends "book Mrs Lakshmi for Tuesday" while the switch says Build, the bot rewrites itself instead of booking.

Every product in the table that feels right uses the same trick: **the thing you address decides what happens.** That is the whole finding.

## 3. The model: one office, named colleagues

Three roles, three rules, nothing else to learn.

**Roles**

- **Decibyl is the manager.** Not a bot on a number, never in front of a customer. It builds, edits, tests and hands work between bots, and it answers questions about the business from Company knowledge, the numbers and what every bot did. It already exists behind the Home box (`api/services/workflow/decibyl.py`).
- **Bots are colleagues with handles.** `@reception`, `@retention`. They work on their channels (a phone number, WhatsApp, email, a webhook trigger, a routine) and answer in the office when addressed.
- **Customers are never in the room.** They appear only as cards and reports: a call summary, an order confirmed, a decision the bot needs.

**Rules**

1. A message to nobody is to Decibyl.
2. A message to a handle is work for that bot. This is the existing channel mention path and does not change.
3. Decibyl never acts on a sentence. It proposes a card; you confirm; there is an undo window. This is the existing `actions.propose` / `actions.settle` pattern with its ten-second undo.

**What the person types, and what happens**

| You say | Who acts | What appears |
|---|---|---|
| "Create a receptionist for my clinic" | Decibyl runs the builder | **Build card:** name, handle `@reception`, what it will do, **Hear it** and **Try it** buttons, Confirm to create |
| "Edit @reception: be shorter on Tamil greetings" | Decibyl runs a builder turn scoped to that bot | **Edit card:** the diff, Confirm to apply, Check it offered first |
| "@reception, book Mrs Lakshmi for Tuesday 5 pm" | `@reception` works | Its reply, and an outcome card if something was booked |
| "Test @reception: call me as a patient asking for Saturday hours" | Decibyl places a TEST call or runs a scripted caller | **Result card:** what happened, graded, **Fix it** button |
| "When @frontdesk gets a demo lead, have @retention follow up in 3 days" | Decibyl proposes a delegation rule | **Task card:** who, when, what; Confirm to arm |
| "What happened this week?" | Decibyl answers | Prose from the timeline, as today |

No screen changes hands. The roster is on the left, the thread in the middle, the composer at the bottom. That is the screen we already have.

## 4. Testing without confusion

This is the part the founder raised directly: how does a bot know whether it is being tried or being used? The answer is that the bot does not decide and the person does not switch anything. **Every test is a labelled run**, started by a verb, stamped TEST, and kept out of customer analytics and business memory.

Three verbs, all from the same thread:

- **Hear it.** A real call, in the browser (the WebRTC path, `WorkflowRunMode.WEBRTC`) or to your verified phone. The only honest test of a voice job: latency, the voice, interruptions. Costs voice minutes like any call.
- **Try it.** Text with the bot inside a phone-shaped frame marked TEST, either you typing, or a simulated caller Decibyl plays from a one-line brief ("a patient asking for Saturday hours"). Cheap, repeatable, inside the builder allowance.
- **Check it.** A scripted caller runs the scenario and a judge grades the transcript (the existing eval runner and judge in `api/services/evals/`). Offered by default on every Edit card before the change lands on a live bot, and runnable on demand.

What makes this modeless: the run knows it is a test because it was created by a test verb, not because a switch was set. A TEST run writes its own row in the thread with a TEST stamp, is excluded from the overview numbers, never files an outcome against a real contact, and never teaches business memory. A live message to `@reception` cannot become a test by accident, and a test cannot become live.

## 5. CPO view

**Why this and not a toggle.** The buyer we are now pitching wants "AI co-workers", and the mental model that sells is an office: people you can name, a manager you can ask, work you can see. A toggle says "this is software with modes". Named colleagues say "this is a team".

**What the first ten minutes look like.** Sign up. Home says hello and asks what job you are hiring for. You type it. A Build card appears with a handle and a Hear it button. You hear it. You say what to change. A diff card appears; you confirm. You put it on your number. That is the site line: *"Tell Decibyl the job. Hear it. Put it on your number."*

**What we say to the coordination claim.** Not "our agents coordinate". Instead: "Ask @frontdesk to hand the lead to @retention and watch it happen in the thread." A demo in one thread, with cards showing who did what and when, beats a slide.

**What we do not build.** No separate builder screen for the common case; the panel stays for the graph. No agent-to-agent chatter the person cannot see. No "autonomous company". Every hand-off is a row.

**Risks.**
- Decibyl proposing when it should just answer (over-carding). Rule: a question gets prose; only a change gets a card.
- Handle discovery. New users do not know handles exist. The roster shows them, the composer completes them after `@`, and Decibyl names them in every Build card.
- Price surprise on Hear it. The card states the per-minute price before dialling, as the Test button does today.

## 6. CTO view

**Almost everything exists.** The inventory, by file:

| Piece | Where it is | State |
|---|---|---|
| Decibyl thread with workspace context | `services/workflow/decibyl.py`, `routes/agent_timeline.py` | Shipped |
| Handle grammar, never-guess resolution | `services/workflow/mentions.py` | Shipped |
| Bot-to-bot replies in a channel, two hops | `services/workflow/channel_reply.py` (`MAX_HOPS = 2`) | Shipped |
| Builder model client and settings | `services/agent_builder/` | Shipped |
| Propose / confirm / undo cards | `services/workflow/actions.py` (`UNDO_WINDOW_SECONDS = 10`) | Shipped |
| Self-edit with diff card | `services/workflow/self_edit.py` | Shipped |
| Text-chat runs with checkpoints | `services/workflow/text_chat_session_service.py` | Shipped |
| Eval runner and judge | `services/evals/runner.py`, `judge.py` | Shipped |
| Browser test call | `routes/webrtc_signaling.py` | Shipped |
| Routines, triggers | `routine_runner.py`, `bot_triggers.py` (KAN-137) | Shipped |
| Decision cards | `services/workflow/decisions.py` | Shipped |

**What is new.**

1. **A router.** One function that resolves the addressee of a line on Decibyl's thread: no handle, or "Decibyl", goes to the manager; a leading handle goes to that bot through the existing channel path; "Decibyl, verb @handle …" goes to the manager with the bot as the subject. Built on `mentions.resolve`, which already refuses to guess. About a day.
2. **Eight Decibyl tools that only propose cards.** `create_bot`, `edit_bot` (a builder turn scoped by handle, producing a diff), `hear_it` (start a WEBRTC or verified-number TEST call), `try_it` (open a TEST text session), `check_it` (enqueue an eval case from a brief), `place_call`, `delegate` (a task row), `set_routine`. Each writes a card and nothing else; `actions.settle` does the doing. None of them touch a live bot without a confirm.

   **Plus the workspace's connected apps** (shipped after P1). Decibyl is given every Composio tool the organisation has connected, under an `app_` prefix so none can shadow its own. The office rule decides what happens when it calls one: a **read** (fetch, list, search, find — by the verb in the tool slug) runs in the turn and feeds the answer, because it changes nothing; anything else is a **write** and becomes a `run_tool` card that a person confirms, with the same undo window, and `actions.settle` runs it. An unknown verb is treated as a write — a needless confirm is noticed, a mail that went out is not. A reply may take up to four tool rounds ("find the lead, then draft the mail") and is then made to answer, so a loop of reads cannot run on credit; tools are re-offered only after a round of pure reads, and a round that produced a card ends the tool phase, so the model says it has proposed and cannot propose twice. One credit a call, the same as a bot pays. Custom HTTP and MCP tools are not yet reachable from the thread.
3. **TEST stamping.** A `test` flag on the workflow run and its annotations, honoured by costing (billed, but labelled), overview (excluded), outcomes (never filed against a contact) and organisation memory (never learned). One column, four checks, one guard test each.
4. **Delegation with wait.** A task row: from-bot, to-bot, brief, due, status. The to-bot runs as a triggered text turn with the brief as its message; its deliverable is posted back to the from-bot's thread as a message, which the from-bot answers through the normal path. This replaces nothing; the two-hop reply chain stays for quick questions. It is the Zapier "call an agent" shape with the wait made visible as a card.
5. **Try it frame.** A phone-shaped container over the existing text-chat session, with a TEST stamp and a "you or a simulated caller" switch; the simulated caller is the eval runner's `caller_prompt` driven live.

**What needs nothing new.** Billing: a TEST call is a call, a Try is a text reply, a Check is an eval run, all already priced. Audit: every confirm is an audit row via `actions.settle`. Tenancy: every tool resolves the bot with `organization_id` from the user, never from the text.

**Where it can go wrong.** Decibyl calling a tool when it should answer; mitigated by the tool descriptions and a guard test on the "What happened this week?" opener. The router mis-reading a sentence that contains a handle mid-sentence; mitigated by the leading-handle rule and by showing the addressee above the composer as you type.

## 7. Phasing

- **P0, before the launch gate.** Router; Build and Edit cards; Try it frame; Hear it from the thread; TEST stamping. This alone ends the confusion in the screenshots and makes the site line true.
- **P1, launch month.** Check it; Result cards with a Fix it button that opens a scoped Edit card; scoped edits with diffs on a live bot.
- **P2.** Delegation with wait; routines set from the thread; desktop actions as cards (KAN-129 items 6 to 9 land here as routines on our own workspace).

## 8. How we will know

| Measure | Target |
|---|---|
| Sign-up to first Hear it | under 15 minutes, median |
| Bots tried or checked before going live | over 80% |
| Mis-addressed messages (a line to a bot that Decibyl answered, or the reverse, judged from the thread) | under 5% by week two |
| Build cards discarded without a follow-up sentence | low; a high rate means Decibyl is proposing when it should ask |

## 9. Decision

Adopt the office model. Do not build a Build / Work switch. Put P0 on the launch gate. The Home box becomes the manager, not a second chat.

## Sources

- Slack, "Work with AI agents in Slack" and "Use Agentforce in Slack" (help centre); "Agentforce Is Here: Turn AI Agents into Teammates" (Slack blog).
- Salesforce Trailhead, "Explore the New Agentforce Builder" (Simulate and Live Test preview modes); Agentforce Developer Guide, "Preview and Debug an Agent".
- Zapier Help, "Call an agent from another agent"; Zapier blog, "Zapier Agents: AI Teams That Work Together".
- Dust docs, "Collaboration" and "Slack workflows"; Dust blog, "How teams use Slack AI agents (2026)".
- Lindy, "Societies" as described on lindy.ai and third-party reviews (2026).
- Jef Raskin, *The Humane Interface* (2000), chapter 3, on modes and habituation.
