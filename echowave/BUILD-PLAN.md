# Build plan — pricing & billing feature queue

**Written 28 Aug 2026.** The sequenced version of `PRICING-DECISIONS.md §5` —
that file records *what was decided and why*; this one says *in what order,
with what tasks, and what "done" looks like*. Read the relevant `PRICING-DECISIONS.md`
section before starting any item here — it has the file/line references and
the reasoning; this file does not repeat them.

Nothing here is blocked on a decision that hasn't been made. Everything that
still needs a founder call (how much to raise the markup, whether 1.7x is
durable) is in `PRICING-DECISIONS.md §5` under "Needs your decision first,"
not here.

---

## Order, and why

```
Phase 1 (independent, ship first) ─┬─ 1a markup-override admin screen
                                    ├─ 1b model-cost roll-up display
                                    └─ 1c pricing-page "included" copy
Phase 2 (biggest, independent)     ─── configurable extraction library
Phase 3 (independent, needs one
         design decision inside)   ─── ingestion-time embedding billing
Phase 4 (blocked on you)           ─── markup increase, sized by measurement
```

Phases 1–3 have no dependency on each other and no dependency on Phase 4 —
they can run in any order, or in parallel across sessions. They're sequenced
this way because 1a/1b/1c are small and finish fast (momentum, and they close
out work already half-done), Phase 2 is the largest single piece of new
product surface, and Phase 3 closes the original "internal cost nobody bills"
finding this whole review started from. Phase 4 is last only because it's the
one genuinely waiting on you — the QA-cost measurement needs database access
I don't have.

---

## Phase 1 — Finish what's half-built

### 1a. Admin screen for the per-model markup override
**Backend done** (`api/services/billing/markup.py`, `PUT/GET/DELETE /admin/billing/rate-card/markup-overrides`, migration `ae88bf29885d`). Only the UI is missing.

- Add a section to `/superadmin/billing/rate-card` (or a new
  `/superadmin/billing/markup-overrides` screen, matching how the provider
  rate-card table already renders) listing active overrides: provider,
  component, model, multiple, effective-from, note.
- A form to set one: provider, component (dropdown: stt/llm/tts/telephony/embedding),
  model (optional, empty = provider-wide), markup in x (converted to bps),
  note. Client-side bound to `markup.MIN_OVERRIDE_MARKUP_BPS`/`MAX_OVERRIDE_MARKUP_BPS`
  (1.0x–2.0x) so a bad value is rejected before the round-trip.
- A clear/remove action per row, calling the `DELETE` route.
- No OTP step in the UI — the backend deliberately doesn't require one for
  this (see `ManagedMarkupOverrideModel`'s docstring); don't add one on the
  frontend that the backend doesn't ask for.

**Effort:** ~0.5 day. **Done when:** an operator can see, set, and clear a
per-model override without touching the API directly, and the change is
reflected on the next call costed against that model.

### 1b. Model-cost roll-up display
**Prerequisite (embedding pricing) is done.** This is presentation-only.

- `POST /cost-estimate/per-minute` (or wherever `CostPerMinuteBar` sources its
  data — confirm in `api/routes/cost_estimate.py`) already returns STT+LLM+TTS
  as separate lines. Add the embedding line to the same response (it's priced
  now — `CostComponent.EMBEDDING` resolves through the same estimator path as
  everything else once a rate exists, which it does).
- On the frontend, sum STT+LLM+TTS+embedding into one "Model cost: ₹X/min"
  figure in `CostPerMinuteBar.tsx`, replacing (or collapsing under a
  disclosure) the current per-line display. Telephony and the platform fee
  stay separate — the roll-up is specifically "model cost," not the whole bar.
- **Do not** change what `call_cost_items` stores — the roll-up is a display
  sum, the receipt keeps itemised lines (this was explicit in the founder's
  clarification: "bundle means only for calculation").

**Effort:** ~0.5–1 day (mostly the estimator response shape + one component).
**Done when:** the model-configuration screen shows one model-cost figure
instead of three, and the underlying receipt for a real call still itemises
STT/LLM/TTS/embedding separately on `/usage`.

### 1c. Pricing-page / model-cost display: say KB/summary/sentiment are included
Small copy + UI change, but real — this was the concern that prompted the
absorb decision in the first place ("the more it becomes optional and
add-on, the user doesn't prefer it and won't even know").

- Wherever the model-configuration or pricing screen lists what a plan/rate
  includes, add a line: knowledge-base retrieval, call summary and sentiment
  analysis included at no extra charge. Mirror how Vapi's own pricing page
  states this (§2.6a of `PRICING-DECISIONS.md`) — plainly, not buried.
- No backend change. Just don't let this slip — an absorbed feature nobody
  knows exists earns nothing in goodwill or differentiation either.

**Effort:** ~0.25 day (copy + placement). **Done when:** a prospective
customer reading the pricing/model screen can tell these are included without
asking support.

---

## Phase 2 — Configurable extraction library (the Bolna-shaped feature)

The largest item in the queue. Full detail on what Bolna ships is in
`PRICING-DECISIONS.md §2.6a` — this phase reproduces the same shape (name +
prompt + answer type + model), not a sentiment-only feature.

### 2.1 Schema
New table, e.g. `agent_extractions`:

| Column | Notes |
|---|---|
| `id` | PK |
| `workflow_id` | FK — extractions are per-agent, like nodes |
| `name` | operator-facing label |
| `prompt` | free-text extraction instructions, may reference call variables the way existing prompts do (check how `{{name}}`-style substitution already works elsewhere in this codebase — `services/workflow/` likely has a var-substitution helper already; reuse it, don't reinvent) |
| `answer_type` | enum: `free_text` \| `predefined` |
| `predefined_options` | JSON array, used only when `answer_type = predefined` (this is how sentiment becomes positive/neutral/negative) |
| `expected_format` | enum: `text` \| `timestamp` \| `numeric` \| `boolean` \| `email` \| `regex`; `regex_pattern` column alongside it |
| `model` | provider/model to run this extraction on — default to the cheapest configured LLM tier, mirroring Bolna's `gpt-4.1-mini` default (see the cost note below) |
| `enabled` | soft toggle, don't delete a configured extraction a customer might re-enable |
| timestamps, `created_by` |  |

Migration via `./scripts/makemigrate.sh`, following the effective-dating-free
pattern of an ordinary config table (this isn't a rate — no history table
needed, a normal `updated_at` is enough since past calls store their own
result, not a live reference to the config row).

### 2.2 Runtime wiring
- Extend `services/workflow/qa/analysis.py` (where `_run_whole_call_qa_analysis`
  already runs one LLM pass per finished call) to also run each enabled
  extraction for the workflow, in the same pass if the prompt design allows
  batching (cheaper — one LLM call scoring multiple extractions beats one
  call per extraction), or as separate calls if reliability suffers from
  batching. Decide by testing both against a handful of real transcripts
  before committing to one shape.
- Store results on the run — either a new `extracted_data` JSON column on
  `workflow_runs` (mirroring Bolna's `extracted_data` key exactly, which also
  makes the eventual API response format familiar to anyone who's used
  Bolna) or a new `workflow_run_extractions` table if you want per-extraction
  rows queryable independently. Start with the JSON column — simpler, and
  matches how `usage_info` already works in this codebase.
- **Cost measurement, not just cost avoidance:** call `record_addon_used`
  (or a new sibling function) for extraction usage even though it's free —
  the whole point flagged in `PRICING-DECISIONS.md §2.6` is that "free and
  unmeasured" and "free and tracked" are different, and the second is what
  lets a future markup or pricing decision be sized against real volume
  instead of a guess.

### 2.3 Config UI
- A new section in the agent builder (alongside where knowledge base and QA
  are already configured) listing an agent's extractions, with add/edit/
  remove.
- Extraction editor: name, prompt (textarea), answer type toggle, conditional
  fields (predefined-options list editor, or expected-format dropdown +
  regex field), model picker (defaulted to the cheapest tier).
- Read-back on the call detail / run page: show `extracted_data` next to the
  existing transcript/summary/QA-score display, the same place Bolna surfaces
  it in their "Execution payload."

### 2.4 Tests
- Unit tests for the extraction runtime (batched vs per-extraction LLM call,
  predefined-option validation, regex-format validation) mirroring the
  existing `qa/analysis.py` test patterns.
- A migration test confirming the new table/column round-trips.
- Do **not** add this to `test_every_service_is_priced.py`'s scan — that
  file is about STT/LLM/TTS pipeline services specifically; extraction is a
  QA-runtime concern, priced (or not) the same way `call_qa` already is.

**Effort:** ~3–4 days (schema + runtime + one config screen + read-back +
tests). The largest single item in this plan — worth confirming scope (batched
vs per-extraction LLM calls, JSON column vs new table) before starting, since
both are cheap to decide now and expensive to change after data exists in
either shape.

**Done when:** an operator can define a named extraction with a prompt and
answer type on an agent, it runs once per finished call, the result shows up
next to the transcript, and it's measured (even though free) the same way
every other feature in this codebase is.

---

## Phase 3 — Ingestion-time embedding billing

The one item that closes the *original* finding from this whole review:
document upload embeds every chunk on Decibyl's own key, for real vendor
cost, and bills nothing at all — not even as `uncosted`. Query-time embedding
(a KB search during a call) is already fixed (`PRICING-DECISIONS.md §2.5`);
this is the other half.

### 3.1 The design decision to make before writing code
Ingestion has no `workflow_run_id` — it's a background ARQ job
(`tasks/knowledge_base_processing.py`), not a call. So it cannot go through
`cost_engine.compute_call_cost`, which is built around a call receipt. Two
shapes, pick one:

- **(a) Direct credit-ledger debit at upload time** — closest to how a
  number rental works (`services/billing/rentals.py`): compute the cost of
  the embedding batch (tokens × the same `CostComponent.EMBEDDING` rate rows
  from Phase-2-of-the-last-round), debit the ledger directly inside
  `knowledge_base_processing.py`, write a ledger row with a clear kind/note
  (`"embedding_ingest"` or similar) so it's auditable on `/billing` the same
  way a rental charge is.
- **(b) Bundle it into the plan entitlement, unmetered** — the knowledge-base
  *byte* allowance already gates ingestion (`subscription_plans.knowledge_base_allowance_for`);
  a plan could simply be priced assuming embedding cost is included in what
  the KB entitlement already costs to grant, with no separate ledger line.
  Cheaper to build (nothing new), but re-introduces exactly the "absorbed,
  unmeasured" pattern flagged as a real gap in Phase 2 — the actual per-org
  embedding cost stays invisible.

**Recommendation:** (a). It's a small amount of code reusing rate rows that
already exist, and it's the only shape that makes ingestion cost visible on
the unit-economics screen the way every other cost in this codebase is
required to be. (b) is faster but repeats the mistake this whole review
started by finding.

### 3.2 Implementation (assuming 3.1 → option a)
- In `tasks/knowledge_base_processing.py`, after `_embed_texts_in_batches`
  returns, sum the token usage across the batch (same `last_usage_tokens`
  capture already added to `OpenAIEmbeddingService`/`AzureOpenAIEmbeddingService`
  in the last round — accumulate it across calls instead of reading the
  single last value, since ingestion embeds in batches).
- Resolve the rate the same way `costing.py` does for a call line: 
  `resolve_provider_rate(component=EMBEDDING, provider=..., model=...)`,
  apply the managed markup via `resolve_markup_bps`/`resolve_markup_override_bps`
  — same functions Phase 2 of the last round built, just called from a task
  instead of `cost_workflow_run`.
- Debit `CreditLedgerModel` directly (see how `rentals.py` or
  `signup_bonus.py` writes a ledger row outside the call-costing path, for
  the pattern to copy) with a new `CreditLedgerKind` member (e.g.
  `EMBEDDING_INGEST`) so it's distinguishable from a call debit or a rental
  on the ledger and on `/billing`.
- Balance check before embedding starts, mirroring the reservation logic's
  spirit (`reservations.py`) but scaled down — ingestion is not concurrent
  the way calls are, so a simple pre-check (does the org have enough balance
  for the estimated cost of this document) is proportionate; the full
  hold-then-release reservation machinery built for calls would be
  over-engineering here.
- Surface it: a line on the document/knowledge-base screen showing what
  ingesting a document cost, and on the unit-economics screen as its own
  cost line (or folded into the existing embedding cost-intensity row —
  confirm with whoever owns that screen's layout).

### 3.3 Tests
- A test that a document upload debits the ledger by the expected amount for
  a known token count and rate.
- A test that an org with insufficient balance is refused before the vendor
  call is made (money-losing direction: don't call the vendor and then find
  out you can't charge for it).
- A test that the per-model markup override from the previous round applies
  here too, if one is set for the embedding model in use — same resolver,
  should be free, but assert it rather than assume it.

**Effort:** ~1.5–2 days (mechanism is well-precedented by rentals.py, most
of the time is the balance-check-before-vendor-call ordering and tests).

**Done when:** uploading a document debits the account for what it actually
cost to embed, visibly, and an account without enough balance is refused
before the vendor is ever called.

---

## Phase 4 — Markup increase (blocked on you)

Not a build item — a number, and it needs input only you can provide.

1. **You run the QA/summary/sentiment-cost measurement** — the query against
   `usage_info["llm"]` filtered to the `QAAnalysis` processor, described in
   `PRICING-DECISIONS.md §2.6`. I don't have database access in this session.
2. Once that number exists, I can compute what markup increase breaks even on
   what's being absorbed (per §2.6's arithmetic note: a markup increase
   recovers the *average* absorbed cost across the book, not the actual cost
   of the actual call — worth having the real distribution, not just a mean,
   before picking a number).
3. Apply via the existing OTP-confirmed flow (`markup.py:start_change` →
   emailed code → `confirm_change`) — no new code needed, this mechanism
   already exists and is tested.
4. Once you confirm the new value is durable (not just today's live
   setting), bump the `MANAGED_PROVIDER_MARKUP_BPS` env-var fallback default
   to match, per the standing item in `PRICING-DECISIONS.md §2.9`.

**Effort:** ~0 dev days once the number is in hand — this is a config
action through an already-built flow, not new code.

---

## What I'd start on first, if you don't specify

**1a → 1c → 1b → Phase 2 → Phase 3**, in that order: the three Phase-1 items
finish fast and close out work that's sitting half-done, in ascending effort;
Phase 2 is the biggest single win and has no dependency on anything else;
Phase 3 closes the original finding but is smaller and can follow. Say the
word to start, or name a different order — none of these block each other.
