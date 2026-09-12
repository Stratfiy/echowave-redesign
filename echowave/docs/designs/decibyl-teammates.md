# Decibyl Teammates — the product, whole

Written 12 September 2026. This is the **vision document**: what the product is when it
is finished. It is deliberately separate from `humans-and-agents.md`, which is the next
ninety days. Both are true; conflating them is what made the office-hours session harder
than it needed to be.

Founder's description, verbatim, and the thing this document specifies:

> *"A product like Grok Bot where humans work with AI agents and chat with them in the
> knowledge, compliances and its memory compounds and gets smarter and does boring jobs.
> Also adding voice agents."*

---

## 1. The one-sentence product

**A workspace where a business's agents do its boring recurring work, and get measurably
better at it every week, because everything they learn is verified by a human before any
agent is allowed to say it.**

Voice is native, not a channel bolted on. The agent that rings a vendor about an
unanswered quote is the same agent, with the same memory, as the one that clears the NDR
queue.

## 2. The differentiator, stated precisely

Not "we have memory". Every agent product has memory.

> **Verified memory. Every fact an agent is permitted to state has a named human who
> approved it and a timestamp. What is approved grows every day.**

That is knowledge, compliance and compounding memory as **one system**, not three
features. It exists because Decibyl's agents speak to *somebody else's customer*, which
means an unverified claim is a legal event rather than a bad answer. No screen-agent
company will build this, because none of them take that risk.

It is already the enforced rule in the code
(`api/services/workflow/organisation_learning.py`): *"nothing learned here ever reaches an
agent's prompt... until a person confirms it."*

## 3. The compounding loop, which is the whole product

```
        ┌──────────────────────────────────────────────────────┐
        │                                                      │
   ┌────▼─────┐    ┌──────────┐    ┌───────────┐    ┌──────────┴───┐
   │ Agent    │───▶│ Facts &  │───▶│  Human    │───▶│  Verified    │
   │ does a   │    │ Gaps     │    │ confirms  │    │  knowledge   │
   │ job      │    │ (learned)│    │ or rejects│    │  the agents  │
   └──────────┘    └──────────┘    └───────────┘    │  run on      │
        ▲                │                          └──────────────┘
        │                │
        │          ┌─────▼──────┐
        │          │ Gaps = the │
        └──────────│  roadmap   │
                   └────────────┘
```

Six properties, and five of them already exist:

| Step | What happens | Status |
|---|---|---|
| 1. Agent does a job | Call, message, or a tool action | **Built** (voice, WhatsApp, Composio) |
| 2. Job emits facts and gaps | Structural, not an LLM pass, so it cannot hallucinate a gap and it works retrospectively on history | **Built** (`organisation_learning.py`) |
| 3. Everything arrives unbelieved | `status = learned`, never enters a prompt | **Built**, enforced in the write path |
| 4. A human confirms or rejects | One click, in the workspace | **API built** (`POST /organisation/memory/{fact_id}/status`), **no surface** |
| 5. Confirmed knowledge feeds the next run | Agents get better without a prompt edit | **Partially built** |
| 6. Gaps become the roadmap | *"Twelve callers asked about Saturday hours"* is a to-do, not a log line | **Built** (counted, not listed, deliberately) |

**Step 6 is underrated and should be a headline feature.** Most products tell you what an
agent did. This one tells the business what it does not know about itself. That is the
"gets smarter" claim made concrete and auditable.

## 4. The four layers

### Layer 1 — Memory and knowledge (the core)

| Component | Purpose | Status |
|---|---|---|
| `organisation_facts` | Instances: what is known about a subject | Built |
| `subject_type` | `contact` today; the schema comment names **`quote`** and **`shipment`** next | Built, one value used |
| Facts vs gaps | Same table, two sides of one question, by design | Built |
| `learned / confirmed / rejected` | The verification gate | Built |
| Knowledge base | Documents, chunking, OCR, staleness, pgvector retrieval | Built |
| Knowledge graph | Graphiti, episodes, ingest, per-tenant scoping | Built |
| **Ontology** | *What kinds of thing* this business deals in, and which are worth remembering | **Not built.** The model's own comment calls this "the half that comes next" |
| **Memory provenance UI** | Which human approved this, when, from which call | **Not built** |

### Layer 2 — Agents (jobs, one at a time)

- An agent is **a job with a KPI**, not a job title. Vendor quote follow-up. COD
  confirmation. NDR recovery. Missed-call callback.
- Six role-shaped templates already ship in `api/services/agent_templates/catalogue.py`.
- Each agent carries: a target, a measured rate, and a cost per successful outcome.
- **The vertical is three config objects, never a forked agent:** a connector (which
  system of record), a guardrail (what it may say), an outcome schema (what done means).
  The call flow is identical across verticals and is the expensive part.
- **Growth rule:** a new agent is built only after someone outside the founding team has
  sold or deployed that job twice from a generic template.

### Layer 3 — The workspace (where humans and agents meet)

This is the layer with the least code and the most product value.

| Component | What it is | Status |
|---|---|---|
| **Named agent list with status lines** | *"NDR Recovery · 8:50pm · 41 confirmed, 6 need you"* | **Not built.** The single highest-value missing screen |
| **Chat with an agent** | Ask it what it did, why, and tell it it was wrong | **Partially** — `agent_builder` chats to *build*, not to *review* |
| **The queue** | Jobs the agent could not finish, with full context, one-click resolve, job resumes | **Not built** |
| **Memory review** | Confirm or reject what the agents learned this week | **API built, no surface** |
| **Gaps board** | What the business cannot answer, counted | **API built, no surface** |
| **KPI per agent** | Target, actual, cost per successful outcome | **Half built** — `workflow_outcomes` has outcome-rate and readiness; `cost_by_outcome` still lacks its successful-outcome denominator |
| Permissions and roles | Who handles which accounts | Built |

**Scope test for this layer:** *would a COO look at this screen?* Then it belongs. *Would
a COO decide this?* Then a human decides and the product surfaces it.

### Layer 4 — Channels and actions

| Channel | Status | Notes |
|---|---|---|
| Voice, inbound and outbound | Built | 7 carriers, warm transfer with briefing, per-turn latency capture |
| WhatsApp | Built, unsurfaced | 88% gross margin against voice's 35-65% |
| Email | Built | `api/services/messaging/email.py` |
| Web chat / embed | Built | Needs the credit gate (audit F5) |
| Tool actions | Built | Composio, 1,540 toolkits |
| **Cross-app triggers** | **Not built, and rented rather than built** | Composio integration is actions-only. n8n, Make or the customer's tool fires the job |

**The rule for non-voice work:** build the screen work a conversation requires, refuse the
screen work it does not. Read the NDR list, place the call, write the outcome back: build.
Post a file from Drive to Meta: refuse, that is a four-node n8n workflow.

## 5. How a customer experiences it

**Week 1.** They connect Shopify and Shiprocket, pick *COD confirmation* from the
catalogue, and give it a number. The agent starts calling. Because the learning pass is
structural rather than an LLM pass, their *existing* call history is read too, so the
memory is not empty on day one.

**Week 2.** The workspace shows: 1,950 calls, 412 orders confirmed, RTO down from 24% to
19%. It also shows 31 things the agent learned and 7 gaps. *"Fourteen buyers asked whether
they could change the delivery slot. No agent can do that."*

**Week 3.** The ops person spends four minutes confirming 24 facts and rejecting 7. The
agent stops asking questions it now knows the answer to. Someone connects the slot-change
API, and the gap closes.

**Week 8.** RTO is 16%. The agent handles slot changes. A second agent is hired from the
catalogue for vendor follow-up, and it starts with the business's confirmed knowledge
rather than from nothing.

**That last sentence is the compounding, and it is the reason to buy the second agent from
you rather than from anyone else.**

## 6. Why this is defensible against Grok Bot and Grok Voice

x.ai now ships both halves: an agent teammate workspace and a voice agent platform. The
positioning overlaps almost exactly. Four things do not port:

1. **Verified memory with provenance.** Their agent works on your screen; a wrong belief
   costs you a redo. Ours speaks to your customer; a wrong belief is a complaint. The
   human-approval gate is a product requirement here and an unnecessary friction there.
2. **The licensed half.** Indian DIDs, carrier KYC, DLT registration, TRAI conduct, GST
   invoicing, a prepaid ledger that reconciles to the paise. A model layer will not apply
   for a telecom reseller arrangement.
3. **The outcome receipt.** *"RTO 24% to 16%, 312 orders saved, ₹56,000 recovered"* is a
   number from a metered ledger. "Comes back with finished work" is a claim.
4. **Server-side and unattended.** A desktop agent needs a logged-in human and a machine.
   The NDR file lands at 2am.

**And Grok Voice is a supplier, not a rival.** The service factory already swaps STT, TTS
and LLM per slot. Adding Grok Voice as a managed tier is a rate-card row and an adapter,
and TTS is where the margin leaks today. Treat a cheap good voice model as a COGS win.

**What is not defensible:** being cheapest. `COMPETITIVE-BUNDLES-2026.md` positions
Decibyl as cheapest on every row. A frontier lab with "at low cost" in its subhead ends
that. Lead with the receipt, not the rate.

## 7. What is actually missing

Roughly 30% of the product, concentrated in one layer.

**Must build**
1. The workspace shell: named agents with status lines, and a chat per agent about its work
2. The queue: jobs needing a human, with context, resolve, resume
3. Surfaces for two APIs that already exist: memory review and the gaps board
4. KPI per agent, closing audit finding #8 by giving `cost_by_outcome` its
   successful-outcome denominator
5. The ontology: subject types beyond `contact`, starting with `quote` and `shipment`
6. Non-call jobs feeding the learning loop, which today only reads calls

**Must not build**
- Cross-app trigger and polling infrastructure. Rent it
- A connector catalogue. Composio is the catalogue
- An orchestrator. n8n, Make or the customer's tool
- Agents that fail the four-noes test
- Generic chat, documents, notes, projects, a marketplace

## 8. Build order

Vision above, sequence here, and the sequence is constrained by five people and ₹5 lakh.

| Phase | Build | Proves |
|---|---|---|
| **0. Unblock** | Audit F21, F39, F38. Meter agent-builder tokens. Run `measure.sql` §1 and §6 | A generated client authenticates and a price can be quoted |
| **1. One job, one queue** | NDR/COD agent live at one brand. The queue. Memory review surface | A stranger configures it; someone resolves tasks daily |
| **2. The receipt** | KPI per agent, cost per successful outcome, the weekly number | A customer sees RTO fall and renews |
| **3. The shell** | Named agent list, chat-per-agent, gaps board | It feels like a teammate, not a dashboard |
| **4. The second agent** | Vendor follow-up, on confirmed knowledge from agent one | **Compounding is proven, and the catalogue starts paying for itself** |
| **5. Ontology** | `quote` and `shipment` subject types | Memory generalises past calls |

**Phase 4 is the moment the company becomes what this document describes.** Everything
before it is one good agent. Everything after it is a platform that gets smarter.

## 9. What is unproven

- Nobody outside the founding team has configured an agent. The whole model rests on it
- No customer has paid
- Whether an ops person will do the weekly memory review, or whether unreviewed facts pile
  up and the compounding stalls. **This is the central product risk and it is untested**
- Whether verified memory is a buying reason or merely a sleeping-well reason
- TTS characters per minute is unmeasured, and it moves every price by up to 58%
