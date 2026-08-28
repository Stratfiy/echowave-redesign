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

## Status — read this first, especially if you're a fresh session picking this up

**Goal: ship today, not "in a few days."** If this session hits its limit
mid-phase, the next agent should read this block, then the "Resume exactly
here" line, and continue — not re-read the whole conversation history.

**Founder decisions added 28 Aug, mid-build (see `PRICING-DECISIONS.md` for
the full record — this is the short version for resuming code):**
- **Meter everything internally; show customers a combined figure, not
  every line.** Applies beyond Phase 3 — also changes how Phase 1b's
  roll-up and the eventual extraction-library display should think about
  what's *shown* vs what's *tracked*. Full cost breakdown (STT/LLM/TTS/
  embedding/telephony, and ingestion-time embedding once Phase 3 ships)
  stays on `call_cost_items` and the internal unit-economics screen exactly
  as before — nothing about *measurement* changes. What changes is the
  customer-facing surface: combine into fewer buckets (e.g. one "Model
  cost" figure), the way Vapi/Bolna show "Agent Cost" rather than
  itemising STT+LLM+TTS+embedding separately. Reasoning given: competitors
  don't itemise this and customers react badly to a bill with many small
  lines.
- **Markup will rise to bring the effective price toward market**, sized
  using the internal-cost-vs-billed-cost analytics (see next point) rather
  than picked round. This is Phase 4, still blocked on the founder running
  the QA-cost measurement query — no code change needed until that number
  exists (the raise-markup mechanism, `markup.py`'s OTP flow, already
  works).
- **New, not-yet-scoped-into-a-phase item: internal-cost-vs-billed-cost
  analytics.** The founder wants a screen/report comparing what a call (or
  a feature) actually cost us against what was billed for it — explicitly
  framed as groundwork for turning a currently-absorbed feature into a
  priced one later, with real numbers to justify it. This is close to
  what `/superadmin/billing/unit-economics` already reports (revenue vs.
  provider cost, per component and per model) — check whether extending
  that screen covers it before building a new one. **Not yet slotted into
  a phase below — do that first if picking this up fresh**, most likely as
  an addition to Phase 1b (the roll-up work already touches this same
  cost-display surface) or as its own small Phase 1d.

### Resume exactly here

| Item | Status |
|---|---|
| Phase 1a — markup-override admin screen | **Done, committed, pushed** (`665431d`). |
| Phase 1b — model-cost roll-up display | **Done (narrowed scope), committed, pushed** (`340205a`). Full breakdown collapsed behind a "Show full breakdown" toggle in `CostPerMinuteBar`, default collapsed — the coloured bar/legend (Agent/Telephony/Platform/Features) was already the combined figure. **Not done**: adding embedding as a 5th estimable stack dimension in `estimate_cost_per_minute` / `CostStack` — bigger scope (new estimator params, a `default_units` assumption for embedding, new UI picker input), deliberately left as follow-up rather than half-built. Pick up only if a real product need for it shows up. |
| Phase 1c — pricing-page "included" copy | **Done (partial, correctly scoped), committed, pushed** (`43a1e9b`). Badge added on the Knowledge Base files page. QA/summary/sentiment has **no config screen to attach this to yet** — do it when Phase 2 ships one, not before. |
| Phase 1d (new) — internal-cost-vs-billed-cost analytics | **Done, committed, pushed** (`a5a0a89` backend, `8edc4bf` UI). `embedding_ingestion_costs` table (paired vendor cost + charge per document) + `billing_kpi_client.embedding_ingestion_totals` + a new `"embedding_ingestion"` block in `unit_economics_report`, now rendered as its own panel on `/superadmin/billing/unit-economics` (staff-only, labelled explicitly as never a customer-facing line). |
| Phase 2 — extraction library | **Done, committed, pushed** (`182c749`, `796967c`) — **scope narrowed from the original §2.1–2.4 design, see note below Phase 2's heading.** No new table, no new config screen, no new LLM call: extractions are a field on the existing QA node, rendered into the QA node's existing single LLM call. Config UI and result read-back both come free from existing generic components. |
| Phase 3 — ingestion-time embedding billing | **Done, committed, pushed** (`8785220`, plus the cost-row pairing in `a5a0a89`). |
| Phase 4 — markup increase | **Blocked on the founder's measurement query, as before.** |

**All of Phase 1 verified in this session:** `npx tsc --noEmit` clean, `npx next lint` clean, all 96 `npx vitest run` tests passing, and one full `npx next build` completed with exit 0 (run once, across the 1a commit — not re-run after every subsequent small change, but the pipeline is confirmed working).

**Phase 2 verified in this session:** `ruff check` clean, `ruff format --check` clean, `python3 -m py_compile` clean on every changed file. **Not run: live pytest** — this sandbox has no Postgres/Redis/pipecat, so `test_qa_extracted_data.py`'s new `TestExtractionKey`/`TestRenderExtractionInstructions`/`TestQAExtractionsOnTheNode` classes are unexecuted. Run the real suite (`source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_qa_extracted_data.py -v`) before calling Phase 2 done-done.

**Phase 3 and Phase 1d's first task verified in this session:** same discipline — `ruff check`/`ruff format --check`/`python3 -m py_compile` clean on every changed file, plus each new migration's position in the graph confirmed with a standalone AST-based head script (no live `alembic` — this sandbox is missing `alembic_postgresql_enum`/`pgvector`). **Not run: live pytest**, and **neither migration (`f4a2c8e91b73`, `b6d3f0a4c9e5`) has ever been applied to a real database** — run both for real before this ships. `api/tests/test_embedding_ingestion_billing.py` covers pricing, the per-model markup override, the balance-affordability gate, at-most-once-per-document idempotency, the paired cost row, and the KPI query's date-range summing — all unexecuted here.

**Phase 1d UI (`8edc4bf`) verified, fully:** `npx tsc --noEmit` clean, `npx next lint` clean (file-scoped and project-wide), all 96 `npx vitest run` tests passing, and `npx next build` completed with exit 0 — `/superadmin/billing/unit-economics` compiled at 10.6 kB (up from its prior size, confirming the new panel is in the bundle). Every frontend check available in this sandbox is green for Phase 1d.

**All four build-plan phases (1, 2, 3, 1d) are now done, committed, and pushed.** Only Phase 4 remains, and it is blocked on the founder, not on code. If you are a fresh session picking this up: there is no more unstarted work to pick up from this document — check with the founder for what's next, or re-verify (real `pytest`, real `alembic upgrade`, a from-scratch `next build`) what this sandbox could only compile-check.

**Phase 3 left one thing genuinely undone, not hidden:** `services/billing/embedding_ingestion.py` debits `credit_ledger` for real (kind `embedding_ingest`), so the money and the row exist and are queryable today. What's missing is a place an operator actually looks: `/superadmin/billing/unit-economics` (`db/billing_kpi_client.py`) is built entirely on `CallCostItemModel`, which only exists for a workflow-run receipt — ingestion has no run to attach one to, so this new cost is invisible on that screen even though it's real in the ledger. **This is Phase 1d's first concrete task**: extend `billing_kpi_client.py` (or add a sibling query) to fold `credit_ledger WHERE kind = 'embedding_ingest'` into the unit-economics report — grouped by org/day the way the rest of that screen already is. Small, well-scoped, and unblocked (no founder decision needed) — a good next pickup for a fresh session before Phase 4.

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

**Shipped, in a narrower shape than §2.1–2.4 below propose.** Read the
existing code before re-planning this: `QANodeData.qa_system_prompt`
was already operator-editable per-workflow, and the QA node already ran
exactly one LLM call per finished call regardless of what that prompt
asked for. That collapsed the "new table + new LLM call(s) + new config
screen" design below into three small changes instead:

- `api/services/workflow/dto.py` — new `ExtractionSpec` (name, prompt,
  `answer_type` free_text/predefined, `predefined_options`,
  `expected_format`), and `QANodeData.qa_extractions: List[ExtractionSpec]`
  using `ui_type=PropertyType.fixed_collection` — the same generic
  list-of-structured-objects widget already powering `BranchNodeData.rules`,
  so the "2.3 Config UI" add/edit/remove screen is free, zero new frontend
  code.
- `api/services/workflow/qa/analysis.py` — `render_extraction_instructions()`
  turns the configured extractions into a prompt fragment appended to the
  *existing* `qa_system_prompt`, in both `run_per_node_qa_analysis` and
  `_run_whole_call_qa_analysis`. Still exactly one LLM call — no batching
  decision to make, because there was never a second call to batch against.
  `_extraction_key()` sanitizes each name into the JSON key the instructions
  ask the model to use.
- Results land in the same place QA results already land — no new
  `extracted_data` column and no `workflow_run_extractions` table:
  `_extracted_data()` (previous commit `182c749`) already stops discarding
  any field outside the four reserved ones, so a configured extraction's
  answer just shows up under its key. Read-back is free too —
  `ContextDisplay` already renders `workflow_run.annotations` generically,
  the same place Bolna's "Execution payload" would show it.
- **Cost measurement:** no separate `record_addon_used` call was added, and
  none is needed — an extraction's cost isn't a new event, it's a few more
  output tokens on the QA LLM call that was already being measured (and,
  per the Phase 1c/PRICING-DECISIONS folding decision, already billed as
  part of the folded-in QA feature). "Free and tracked, not free and
  unmeasured" falls out of reusing the existing call rather than needing
  its own instrumentation.

None of §2.1–2.4 below shipped as written (no new `agent_extractions`
table, no per-extraction `model` picker, no `regex` format, no
`workflow_run_extractions` table). If a real need for any of those shows
up later — e.g. running an extraction on a different/cheaper model than
the QA call's own LLM config — treat it as new scope, not a gap in this
phase; the sections below are left as-is for that reference, not as a
remaining to-do.

Full detail on what Bolna ships is in `PRICING-DECISIONS.md §2.6a` — this
phase reproduces the same shape (name + prompt + answer type), not a
sentiment-only feature, just via a smaller implementation than first
scoped.

### 2.1 Schema (superseded — see narrower shape shipped above)
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

### 2.2 Runtime wiring (superseded — see narrower shape shipped above)
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

### 2.3 Config UI (superseded — see narrower shape shipped above)
- A new section in the agent builder (alongside where knowledge base and QA
  are already configured) listing an agent's extractions, with add/edit/
  remove.
- Extraction editor: name, prompt (textarea), answer type toggle, conditional
  fields (predefined-options list editor, or expected-format dropdown +
  regex field), model picker (defaulted to the cheapest tier).
- Read-back on the call detail / run page: show `extracted_data` next to the
  existing transcript/summary/QA-score display, the same place Bolna surfaces
  it in their "Execution payload."

### 2.4 Tests (superseded — see narrower shape shipped above; actual tests are in `test_qa_extracted_data.py`)
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

**Shipped** (`8785220`, `a5a0a89`): `services/billing/embedding_ingestion.py` +
`tasks/knowledge_base_processing.py` implement §3.1–3.2 below as designed —
balance checked before the vendor is called, the ledger debited for the real
vendor-reported token count afterward, at most once per document (DB-backed
by partial unique indexes, migrations `f4a2c8e91b73` + `b6d3f0a4c9e5`), never
a standalone customer-facing line. §3.2's last bullet ("its own row on the
unit-economics screen") is now backed by real data in the API response
(`embedding_ingestion_totals`, Phase 1d's first task) — the one thing still
missing is a UI card actually rendering it, see the Status section above.
§3.3's three tests are covered by `test_embedding_ingestion_billing.py`,
unexecuted in this sandbox like everything else Python this session (see the
verification discipline).

The one item that closes the *original* finding from this whole review:
document upload embeds every chunk on Decibyl's own key, for real vendor
cost, and bills nothing at all — not even as `uncosted`. Query-time embedding
(a KB search during a call) is already fixed (`PRICING-DECISIONS.md §2.5`);
this is the other half.

### 3.1 Decided: meter it for real, don't surface it as its own customer-facing line
Ingestion has no `workflow_run_id` — it's a background ARQ job
(`tasks/knowledge_base_processing.py`), not a call. So it cannot go through
`cost_engine.compute_call_cost`, which is built around a call receipt.

**The founder resolved the original (a)/(b) choice this section posed, and
combined them**, matching a principle stated for the whole pricing surface,
not just this one item: **meter everything internally; show customers a
combined figure, not every line** — competitors don't itemise this level of
detail and a bill with many small lines reads badly, but the cost still has
to be real and tracked, not invisible.

Concretely, that means:
- **Meter and debit for real** — this is still option (a) from the original
  framing: compute the cost of the embedding batch (tokens × the
  `CostComponent.EMBEDDING` rate rows added in the previous round), debit
  the credit ledger directly inside `knowledge_base_processing.py`, same
  shape as a number rental (`services/billing/rentals.py`). This is not
  optional — "meter everything" is the explicit instruction, and it is also
  what makes the internal-cost-vs-billed-cost analytics (new item, see
  Status section above) possible at all. Silently bundling it into the plan
  entitlement unmetered (the original option (b)) is now off the table.
- **Do not give it a standalone line on any customer-facing screen.** No
  "Embedding ingestion: ₹X" row on the knowledge-base or billing screen.
  Fold it into whatever combined bucket the Phase 1b roll-up settles on
  (most likely the same "Model cost" figure, or a "Knowledge base" bucket
  if that reads more naturally next to a document list) — the customer sees
  fewer, combined numbers; the ledger, the receipt debit, and the internal
  unit-economics screen keep the real, itemised figure underneath.
- **Internal visibility is not optional and does not get combined.**
  `/superadmin/billing/unit-economics` (or its extension for Phase 1d) must
  be able to show this cost on its own — combining is a customer-display
  decision only, never an internal-reporting one.

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
- Surface it **internally**: its own row on the unit-economics screen (or
  folded into the existing embedding cost-intensity row — confirm with
  whoever owns that screen's layout), so it's checkable against real spend.
  **Do not** add a customer-facing "embedding ingestion cost" line on the
  knowledge-base/document screen — per §3.1, it gets combined into whatever
  bucket the customer sees, not shown on its own.

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

**Historical — this was the proposed order before building started; the
"Status" section at the top of this file is the live one, read that first.**

**1a → 1c → 1b → Phase 2 → Phase 3**, in that order: the three Phase-1 items
finish fast and close out work that's sitting half-done, in ascending effort;
Phase 2 is the biggest single win and has no dependency on anything else;
Phase 3 closes the original finding but is smaller and can follow. Say the
word to start, or name a different order — none of these block each other.

As actually built: 1a → 1b (narrowed) → 1c (partial) → Phase 2 (narrowed) →
Phase 3. Remaining: Phase 1d (unblocked, small — see Status section), Phase
4 (blocked on the founder).
