# Prompt caching and compression: measure first

Roadmap item 10. This half **measures**; it does not optimise. Caching
already existed before it, and nobody could tell whether it worked:

- `services/workflow/pipecat_engine_context_composer.py` keeps the blocks that
  are the same for a whole call above the values that change;
- `services/pipecat/service_factory.py` turns Anthropic's prompt caching on for
  every pipeline (pipecat's adapter marks the last two user turns);
- `services/agent_builder/client.py` marks Anthropic's system block with
  `cache_control`;
- cached-token counts were already recorded on run receipts
  (`usage_info["llm"]`, `call_cost_items`) and on direct calls (`model_usage`),
  and priced (`default_rates.CACHED_INPUT_SHARE`, `CACHE_WRITE_SHARE`).

What was missing was *which prompt* each call sent, so a cache that never hits
and one that always hits looked the same. No saving is claimed anywhere until
the numbers below say so.

## What is measured

One row per model call in `llm_call_usage`, through either door:

| Door | Where it is recorded | Feature |
|---|---|---|
| Builder client (Decibyl, the builder, triggers, huddle, ...) | `agent_builder/client.py` → `cache_metrics.record_direct` | the open `model_usage.scope` (`decibyl`, `huddle`, `builder`, ...) |
| Fallback brain on Bedrock | `aws_gateway/fallback.py` | same, marked as a retry |
| Pipeline inference (calls, agent text chats, routines) | `PipelineMetricsAggregator` buffers, `cache_metrics.record_run` writes when the run (or text turn) ends | `agent_call`, `agent_chat`, `routine` |
| In-call background summary (`run_inference`, never on the receipt) | `pipecat_engine_context_summarizer` → aggregator side call | `summary` |

Each row carries:

- usage in **one shape** (`billing/llm_usage.normalise`): uncached input,
  output, cache read, cache write, reasoning. Every vendor payload the code
  receives is read by the same function — Anthropic, Bedrock Converse, OpenAI
  Chat Completions and Responses, Gemini (REST and SDK names), and the
  pipeline's own `LLMTokenUsage`. What `model_usage` and `usage_info` store is
  unchanged (`NormalisedUsage.as_usage_fields`), so no bill moves;
- four hashes of the **prompt prefix** (`billing/cache_metrics.prompt_hashes`):
  `system_hash`, `tools_hash` (tool schemas in the order sent, keys sorted,
  no whitespace), `prefix_hash` (both: what a vendor's prefix cache matches)
  and `prompt_fingerprint` (the prompt *version*: the same, with the per-call
  values — the clock line, the caller block, the known-values block —
  masked);
- the **conversation** it belongs to: `run:<id>` for a pipeline run,
  `thread:<id>` for a Decibyl thread (from `agent_timeline.current_thread`, or
  an explicit `cache_metrics.conversation(...)`);
- whether it was a **retry** (after a 429, on another vendor after
  out-of-credit, or the fallback brain) and whether it ran on the account's
  own key (counted, never priced).

The provider/model **capability matrix** (`billing/cache_capabilities.py`) is
data: for every LLM and realtime vendor the registry can route to, and every
model it lists, whether the vendor caches, how (automatic prefix or explicit
`cache_control`), its documented minimum, whether our request path asks for
it, and the read/write multipliers **from billing's own tables**. Anything not
known is `"unknown"` — never a guess, and never billing's 1.0x default.

## How to read the report

Staff only: `GET /api/v1/admin/staff/operations/caching` (`operations.read`,
hidden from the public API) and **Operations → Prompt caching** in the staff
console. Seven days by default.

- **Hit rate** = cache-read tokens ÷ *cacheable* input, where cacheable input
  is the whole prompt of every call long enough for its vendor to cache
  (vendor minimum unknown → every call counts). `raw_hit_rate` is the same
  over all input. A dash, not 0, when nothing could have hit.
- **Cold / warm**: warm calls read something from the cache. Split by
  position — the first call of a conversation is expected to be cold; a cold
  *later* call is the cache failing.
- **Cost** is what the vendor charges us, priced against the rate book for the
  window with `usage.llm_split_items`, cache writes included. A model with no
  rate is listed as unpriced, never free.
- **Cost per successful outcome** exists only where an outcome does: an
  `agent_call` that completed, a `routine` that delivered. Every call of the
  task counts — tool rounds, retries, the background summary — and the cost
  of the tasks that failed is divided over the ones that succeeded. Decibyl
  threads and agent text chats have no outcome yet and show none.
- **Top fingerprints by spend**: where caching would matter most.
- **Prefix breakers**: inside one conversation, every change of `prefix_hash`
  between consecutive calls, labelled `system`, `tools` or `both`.
  **Volatile prefixes**: one prompt version sent with a different prefix in
  different conversations — something per-call sits in the prefix.

## Prefix breakers found in the code

| # | Where | What breaks | Status |
|---|---|---|---|
| 1 | Agent prompts (composer) | The clock line (to the minute) is the **first** block, so no two calls share a prefix and automatic-prefix vendors (OpenAI, Google, DeepSeek) can never reuse the operator's prompt across calls | Fixed behind `cache_v2`: the line moves after the per-bot fixed blocks, before the caller / steps / known values. Off by default: it changes what the model reads first |
| 2 | Decibyl via the builder client on Claude | One breakpoint, on the system block. The turn's context (team, memory, knowledge, the clock) rides in the latest user message, and every tool round of the turn resends it **uncached** | Fixed behind `cache_v2`: a second breakpoint on the last block of the conversation. Same content; the cost is a write premium on single-round turns, hence the flag |
| 3 | Agent prompts on Claude | The system prompt is one block, recomposed at each node transition (node prompt, known values, tools change), and pipecat's markers sit on user turns only — so every transition is a full miss | Measured (`system`/`both` breaks). Not changed: splitting the system prompt or moving known values into messages changes what the model sees — later work |
| 4 | Decibyl deferred tools | Loading a connected app's schema mid-turn changes the tool list; tools precede the system prompt in Claude's prefix, so the system cache entry is lost | Measured (`tools` breaks). Not changed |
| 5 | Decibyl's system prompt | Carries per-organisation rules and the person's own settings block, so it is shared per person, not "every thread in every account" as the client's comment says | Measured (fingerprints per feature). Not changed |
| 6 | Tool order and serialisation | Checked: connected tools are read ordered by name, a node's custom tools in the node's own order, Decibyl's own built in a fixed order, schemas from code or JSON with stable key order | No fix needed; `test_decibyls_own_tools_serialise_identically_turn_to_turn` holds it |
| 7 | In-call summary | Its tokens never reached the receipt | Now measured as `summary` in the task's cost; billing unchanged |

`cache_v2` lives in `api/constants.py` (`CACHE_V2_ENABLED`),
`services/features.py` and `ui/src/lib/features.ts`. With it off every prompt
and request is byte-for-byte what it was (tested).

## The gate for any later work

No optimisation below ships on by default, and `cache_v2` is not switched on
anywhere, until **comparable tasks show reduced cost per successful outcome
with no quality regression**:

- *comparable*: the same feature and prompt fingerprint, the same vendor and
  model, over a window long enough that each arm has enough tasks with an
  outcome to compare;
- *reduced cost per successful outcome*: from this report, with retries and
  summaries counted — not a higher hit rate, and not a lower cost per call;
- *no quality regression*: the success rate itself does not fall, and the
  existing quality signals (QA, evals, feedback) for that feature do not
  move the wrong way.

Turning `cache_v2` on for a few organisations through the staff console's
per-organisation switch is the first experiment this gate is for.

## The staged R3 plan — not implemented now

Each stage is its own change, behind its own flag, judged by the gate above.

1. **Selective retrieval.** Send the knowledge, memory and team sections a
   turn needs rather than all of them, chosen by the question; the context is
   most of a Decibyl turn's input.
2. **Compact tool outputs.** Trim tool results (connected-app reads, search,
   knowledge passages) to the fields the model uses, with the full result kept
   for the person and the audit trail.
3. **Shadow-mode compression of long transcripts.** Compress long call and
   chat transcripts in shadow first — run beside the real prompt, compare,
   never sent — preserving every number, date, negation and tool payload
   verbatim; only after shadow results pass the gate does a compressed
   transcript reach a model.
4. **Provider-aware routing.** Use the capability matrix to route work whose
   prefix is stable to vendors whose cache is cheap and automatic, and to
   split Claude prompts into cacheable blocks (finding 3) where that is where
   the work runs.
