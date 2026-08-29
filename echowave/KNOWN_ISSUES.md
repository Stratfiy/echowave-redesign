# Known Issues

Running log of open problems, so they can be worked through one at a time.
Each entry records what is wrong, why, and what fixing it involves.

Status legend: **OPEN** · **FIXED** · **DECISION NEEDED** (needs a product/ops
call, not a code change)

Last updated after the compliance and deployment pass.

> **Current test status: `api/tests` is fully green -- 4,442 passed, 10
> skipped, 0 failed, 0 collection errors** on **Python 3.13**, the version
> `api/pyproject.toml` pins and CI runs, with real Postgres + pgvector and real
> Redis.
>
> Two earlier figures in this line were wrong, and both were wrong in the
> direction of looking finished:
>
> * "1,911 passed" predated most of the suite -- collection now finds 4,452.
> * A later "4,436 passed, 5 failed" was measured on Python **3.11**, and all
>   five of those failures were artifacts of the wrong interpreter. They were
>   recorded here as pre-existing. They were not.
>
> **Run the suite on 3.13.** An older interpreter does not merely emit pydantic
> noise -- it fails and passes a different set of tests, so a result from one
> says little about the other. `scripts/setup_requirements.sh --dev` into a
> 3.13 venv is what CI does.

---

## Open

### 7. Top-level directory is still named `echowave/`

**Status:** DECISION NEEDED · **Severity: low**

The project root is `echowave-redesign/echowave/`. Renaming it to `decibyl/`
is cosmetic but high blast radius: it moves every path in git history, and CI
workflow `working-directory` values, deploy scripts, devcontainer mounts and
the `.gitmodules` submodule path all assume the current layout. Left as-is
deliberately.

---

## Fixed

### 28. The first reply paid for the LLM's TLS handshake

**FIXED.** Run 82, nine turns, one model throughout:

| turn | llm | prompt_tokens |
|---|---|---|
| 0 | **2,220ms** | 333 |
| 1 | 483ms | 373 |
| 8 | 496ms | 602 |

The slowest LLM stage of the call carried the **smallest** prompt, and the
stage got *faster* as the prompt grew. Cost that falls as input grows is not
generation cost. It is the first HTTPS request paying DNS, TCP and TLS to
`api.sarvam.ai`, with the caller listening to silence.

The speech legs do not have this problem, which is why it took a while to see:
Sarvam's STT and TTS both open their websockets in `start()`, during pipeline
setup, before anyone speaks. The LLM is plain HTTP over a pooled client, so it
connects on first use -- and first use is the caller's first question.

One token, out of band, fired as soon as the service is built so it overlaps
the remaining setup and the greeting. It lands on the same pooled client the
pipeline uses, so the handshake is already paid when the first real completion
runs. Backgrounded rather than awaited, and it swallows every exception: a
warm-up that fails must cost the latency it was trying to save and nothing
else.

Non-realtime only. A realtime service holds a websocket it opens in `start()`
and does not implement `run_inference` at all -- which is why `inference_llm`
exists beside it.

Expected: turn 0's LLM stage falls from ~2,200ms to the ~500-800ms the rest of
the call already runs at.

### 27. The 1,172ms turn wait was one hardcoded constant

**FIXED.** The question was "why can't we do anything about turn detection" and
the answer is that there was never anything physical to fix.

`turn_detect` measured **1171-1174ms across 40 turns of 15 calls, +/-1ms**. A
stage that constant is not measuring work -- no network round trip is stable to
a millisecond. It is `SpeechTimeoutUserTurnStopStrategy`'s second timer.

The strategy runs two timers after VAD silence and releases the turn when both
finish *and* a final transcript has arrived:

* `user_speech_timeout` -- the policy floor, 0.4s here.
* `stt_timeout` -- a safety net sized from the STT's **published P99
  time-to-final-segment**, delivered in `STTMetadataFrame`. It is
  short-circuited the moment the STT marks a transcript `finalized=True`.

    turn_detect = stop_secs + max(user_speech_timeout, ttfs_p99 - stop_secs)

`pipecat/services/stt_latency.py` publishes `SARVAM_TTFS_P99 = 1.17`. With the
recommended 0.2s VAD that is `0.2 + max(0.4, 0.97) = 1.17s` -- the measurement,
exactly. And **Sarvam's STT never sets `finalized`** (Deepgram does, via
`confirm_finalize`), so the short-circuit never fires and every turn pays the
published worst case whether or not the transcript arrived long before.

**This is the regression from forking.** Upstream Dograh defaults transcription
to Deepgram, published at 0.35: `0.2 + max(0.4, 0.15) = 0.6s`. Moving to Sarvam
added 570ms to every turn, silently, in a constant nobody had reason to read.
It is not model time and not network time.

`ttfs_p99_latency` is a constructor parameter and the factory never passed one,
so every call took the library default. It now passes
`SARVAM_STT_TTFS_P99_DEFAULT = 0.5`, overridable with `SARVAM_STT_TTFS_P99`
without a release.

**Lowering it cannot cut a caller off.** The turn also requires a final
transcript (`wait_for_transcript=True`), so the timer is a floor on the wait,
not a cap: set too low, the turn waits for the transcript instead. What it does
risk is the *tail* of a long utterance, where an STT emitting several final
segments still has one in flight. Raise it if transcripts start losing their
last few words.

0.5 is a starting point, not a measurement -- 1.17 is a P99 taken against
Sarvam from wherever the benchmark ran, and this deployment is in ap-south-1
alongside Sarvam's own API. The useful part is that with it in place
`turn_detect` stops being a constant and starts reporting how long Sarvam
actually takes, which is the number that should replace it.

**The proper fix is upstream of this**, and it is worth doing: teach
`SarvamSTTService` to mark its final transcript `finalized=True`. Then the
safety net short-circuits on every turn and the wait becomes however long
Sarvam really took, with no constant to tune at all. That is a submodule
change, which is why it is not in this one.

### 26. The fast transcriber was sitting in the codebase, unreachable

**FIXED (opt-in).** Found by reading upstream (`dograh-hq/dograh`) after the
report that "initially it was so smooth". It was: upstream defaults to
**OpenAI + ElevenLabs + Deepgram**. This fork moved all three legs to Sarvam
for DPDP residency, Indian-language coverage and ~8x cheaper tokens. Those are
good reasons and nothing here argues with them -- but the latency cost was
never written down, and "it used to be smooth" is that cost being noticed.

Worth recording that this fork is *ahead* of upstream in two places, so the
comparison is not one-way: upstream has no TTS token-streaming at all
(`TextAggregationMode.TOKEN` appears zero times there) and still runs pipecat's
0.6s `user_speech_timeout` default where this uses 0.4. Upstream also carries
the same `.get("turn_stop_strategy")` bug fixed in #23 -- inherited, not
introduced.

The portable idea is upstream's managed STT: it routes to **Deepgram Flux**
whenever the language allows, and Flux emits its own turn boundaries. Nothing
waits on silence at all, so the ~1,172ms endpointing stage of #24 does not
shrink, it disappears.

**That entire path already exists here** -- `decibyl_stt_uses_flux_language`,
the Flux branch in `create_stt_service`, `stt_uses_external_turns`, and rate
rows for both Flux models. It was simply unreachable, because `("stt",
"default")` pins `sarvam/saaras:v3` and the branch that would pick Flux never
runs.

Added as a second tier rather than a new default, and the reason is a language
limit, not caution. Flux multilingual covers de/en/es/fr/hi/it/ja/nl/pt/ru:
Hindi yes; Telugu, Tamil, Kannada, Bengali and Marathi no. A caller answering
in one of those transcribes as nothing, which is a worse call than a slow one
-- and the QA transcript that started this whole investigation ends with the
caller saying "థ్యాంక్ యూ", in Telugu. So:

| tier | model | endpointing | languages |
|---|---|---|---|
| `default` | `sarvam/saaras:v3` | ~1,172ms | 22 Indian, code-mixed |
| `instant` | `deepgram/flux-general-multi` | **none** | 10, Hindi the only Indic one |

An agent that knows its callers speak English or Hindi can buy that second
back. An agent serving the 22-language case cannot, and keeps the default.

**It needs a Deepgram platform key.** Without one `managed_resolution` logs and
leaves the section alone, so an agent choosing the tier keeps what it had
rather than failing -- quiet, but not broken.

**A loose end from #25, closed here.** `platform_models` is what the customer's
picker sells, and `model_catalogue` offers a model only when it is in that
table, has a platform key, and has a rate row. The tier change in #25 moved
what calls resolve to without the catalogue following, so
`sarvam-105b-conversations` ran on every managed call while not being on sale.
Resolution never reads that table -- which is why the calls worked -- but the
picker does. Migration `a3f7c21e9b04` seeds both it and the Flux STT entry,
idempotently.

### 25. The voice agent was running Sarvam's *reasoning* model

**FIXED.** This is the long pause. Reported as "there is a long pause", and
`call_turn_metrics` on run 77 put a number on it: `latency_ms` 7,554, of which
`t_llm_first_token_ms - t_stt_final_ms` was **6,045**.

`sarvam-105b` is a reasoning model. It emits `reasoning_content` --
chain-of-thought -- before a single word of the answer, on every request, with
the caller listening to silence throughout. It cannot be told not to: Sarvam
accepts only `low`/`medium`/`high` for `reasoning_effort` (`none` is a 400), and
measured against a 600-token cap both the default and `low` spent the *entire*
budget thinking and returned no content at all.

`sarvam-105b-conversations` is the same generation without the reasoning step.
Measured on the same key, same prompt:

| | reasoning tokens | first content | tool calls |
|---|---|---|---|
| `sarvam-105b` | 299 | never (capped) | yes |
| `sarvam-105b-conversations` | 0 | **0.24s** | yes |

Changed in four places, and the tier is the one that mattered: a managed
account resolves through `managed_tiers`, so a default on the configuration
schema would never have reached it. The run log says which path is live --
`Managed llm resolved to sarvam/<model> on a platform key`.

**Streaming was turned back on with it, and that was wrong — reverted.** `SarvamLLMService.get_chat_completions` forces
`stream=False` and returns the whole reply as one chunk, added to stop Sarvam's
streaming deltas losing leading whitespace. On the reasoning model that concern
was real; on the conversational model it does not reproduce -- a three-sentence
reply streamed as 60 deltas and 48 correctly separated words. It also made
time-to-first-token equal time-to-*whole-response*, and silently cancelled two
optimisations that are still configured: the TTS runs in
`TextAggregationMode.TOKEN` so it can synthesise while the LLM is still talking,
and issue #22's `min_buffer_size` tuning is for the same incremental text.
Neither has anything to stream when the LLM speaks once, at the end. Usage
survives the change -- Sarvam returns `CompletionUsage` inside the stream
despite the parent stripping `stream_options`.

**None of which mattered, because the override was not really about
whitespace.** The same method stamps an `index` onto every tool call, which
OpenAI-style streaming aggregation needs to assemble a call from its deltas and
which Sarvam does not supply. Its commit title says both halves: "Preserve
spaces *and tool calls*". Every node transition is a tool call, so restoring
streaming did not risk joined words -- it stopped transitions resolving, and
the agent fell silent on the first turn that should have moved it to another
node. Runs 78, 79 and 80 each recorded exactly one turn where runs 75 and 76
recorded five.

It was missed because the two halves were verified separately: tool calls
against a non-streaming call, streaming against a call with no tools. Neither
test exercised the combination the pipeline actually runs. `stream=False` is
restored, `DecibylSarvamLLMService` now overrides nothing but the model
allow-list, and a test asserts it stays that way.

The latency cost is real and now paid deliberately: time-to-first-token is
time-to-whole-response, and the TTS's token aggregation and `min_buffer_size`
tuning have nothing to stream. Reclaiming it means assembling the tool-call
deltas with an index of our own, not removing the guard. The model change is
the larger half of the win regardless -- 6,045ms to ~1,000ms -- and it is
independent of this.

Both live in `api/services/pipecat/sarvam_llm.py` as a local subclass rather
than a submodule patch, the same pattern as `DecibylGoogleLLMService` and
`minimax_tts.py`. Upstream's `_validate_model` raises on any name outside its
allow-list, so the model is not merely unset -- it is unselectable until that
set is widened.

**The billing trap this walked into.** `provider_from_processor` derives the
rate-card provider from the processor's class name. `DecibylSarvamLLMService#0`
strips to `decibylsarvam`, which has no rate on file -- and an unpriced provider
does not fail, it costs the call at zero and reports margin at 100%, exactly
the silent miscosting that function's docstring was written about. Aliased to
`sarvam` alongside the existing `googlevertex` and `elevenlabsrealtime`
entries, with a regression case in `test_billing_costing.py`.

**Not fixed, and worth knowing.** `sarvam-30b` is retired (Sarvam 400s it), but
pipecat still defaults `SarvamLLMService` to it and lists it in
`_SUPPORTED_MODELS`. Our factory always passes a model explicitly so nothing
here hits it; a caller that did not would get a dead model.

### 24. Turn-taking: the 600ms that turned out to be 1,172ms, and cost 139ms

**FIXED, and the original estimate was wrong twice over.** Issue #23 fixed a real bug --
`DEFAULT_TURN_STOP_STRATEGY` genuinely could not reach the pipeline -- but the
600ms attached to it was derived from reading code rather than measuring.

`call_turn_metrics` says the endpointing wait is **~1,172ms**, and it is
strikingly constant: 1171-1174 across 40 turns and 15 calls, under both
strategies. `t_user_stopped_ms` is hardcoded to 0, so that column is pipecat's
`user_turn_secs` -- "from when the user actually stopped speaking to when the
turn was released ... includes VAD silence detection, STT finalization, and any
turn analyzer wait". It is dominated by **Sarvam STT finalization**, not by
`user_speech_timeout`, which is why changing the stop strategy barely moves it
(run 77, on `turn_analyzer`, measured 1,312ms).

Re-measured once issue #25 took the LLM stage from 6,045ms to 1,079ms and the
noise floor dropped. `turn_analyzer` is a consistent **+139ms**:

    transcription    1171-1174ms, across 40 turns of 15 calls
    turn_analyzer    1311-1312ms, runs 77 and 78

Two samples, both within a millisecond of each other, against a baseline
sampled forty times. Not noise. `DEFAULT_TURN_STOP_STRATEGY` is therefore
`"transcription"` -- chosen on measurement, against the reasoning in its own
comment, which is left in place because it is sound and may well hold on a
different STT.

The plumbing fix from #23 stays: reading the default through the constant is
correct regardless of which value it holds, and it is what made this
measurable at all. The tests now assert that the unconfigured path agrees with
`DEFAULT_TURN_STOP_STRATEGY` rather than naming a strategy, so the default can
move again on evidence without a test rewrite.

The real gain on this stage is not in either strategy. ~1,172ms of it is
Sarvam STT finalisation -- the turn is not released until the final transcript
lands -- so an STT that emits its own turn boundaries (Deepgram Flux, Cartesia
ink-2) removes the wait rather than tuning it. That is the next move on this
stage, and it is now the largest single slice of a turn at 38%.

### 23. The `turn_analyzer` default never reached the pipeline

**FIXED.** Reported from a live QA call as "there is a long pause". The run's
transcript shows a steady-state reply latency of ~1.8-2.0s once the call is
warm, on an agent that was never configured for the slow path and never chose
it.

`DEFAULT_TURN_STOP_STRATEGY` was flipped to `turn_analyzer` in
`schemas/workflow_configurations.py`, and `api/Dockerfile` was changed to
install pipecat's `local-smart-turn-v3` extra so the setting could actually
run. Neither reached production, because `run_pipeline.py` branched on

```python
if run_configs.get("turn_stop_strategy") == "turn_analyzer":
```

with **no default**, and never imported the constant. Two facts make that fall
through for essentially every workflow:

- `run_configs` is the stored JSON, not a validated
  `WorkflowConfigurationDefaults`, so a Pydantic default cannot apply to it.
- `create_workflow` persists `workflow_configurations or {}` and the create
  route passes nothing, so **every workflow is born with `{}`**. The key only
  appears once a human opens the settings dialog and saves.

So every unconfigured agent took the silence-timeout path and paid the VAD's
0.2s plus `user_speech_timeout`'s 0.4s — **600ms of dead air on every turn**,
which is exactly the cost the default was flipped to remove. The smart-turn
model sat in the image, installed and unused.

Now resolved through `DEFAULT_TURN_STOP_STRATEGY`, using `or` rather than a
`.get()` default so an explicit `null` from an older client is treated as
unset rather than as a choice. `transcription` remains selectable for
workflows whose callers pause mid-sentence.

Two things worth recording, because both read like reasons not to ship this
and neither is:

- **The dependency is already proven present.** `LocalSmartTurnAnalyzerV3` is
  imported at module scope in `run_pipeline.py`, so an image lacking the extra
  could not import the module at all and would fail every call today. Calls
  run, therefore the extra is installed.
- **Inference does not land on the shared event loop.** `analyze_end_of_turn`
  dispatches through `run_in_executor`, and the ONNX session is pinned to
  `inter_op_num_threads=1` / `intra_op_num_threads=1`. This matters because
  `FASTAPI_WORKERS` is 1 on the Hostinger deploy and the API workers are also
  the media workers (see `INFRASTRUCTURE.md`).

`test_user_turn_stop_timing.py` covered the configured cases but never
`build_stop({})` — the one every production workflow actually hits. It does
now, along with the explicit-null case and an assertion that the pipeline reads
the same constant the schema declares.

### 24. Tool rows were re-read from the database on every node transition

**FIXED.** Found while tracing the same slow call. A node transition composes
the node's function schemas (`compose_functions_for_node` →
`CustomToolManager.get_tool_schemas`) and then registers its handlers
(`register_handlers`), and both called `db_client.get_tools_by_uuids` for the
same rows — two round trips per transition, inside the tool call the caller is
sitting in silence waiting for.

`CustomToolManager` now holds the rows for the life of the call and fetches
only uuids it has not seen. Safe because a run is pinned to one published
workflow definition version, so the tools a node may use cannot change
underneath it. Uuids that return no row are remembered as misses, so a node
pointing at a deleted tool does not reissue the same empty query on every
transition for the rest of the call.

Not a 600ms item — tens of milliseconds per transition — but it lands at the
worst possible moment, and the fix has no behavioural surface.

### 22. Sarvam's TTS buffer was set below the value Sarvam accepts

**FIXED.** Reported from a live test as "the call ends after I pick up", on
Plivo, with run #70 recording `pipeline_error` at 0s duration.

`min_buffer_size` and `max_chunk_length` are sent to Sarvam in the config
message at websocket connect, and Sarvam validates them. Its API reference
gives `min_buffer_size` as **30-200** (default 50). The first-byte latency work
shipped it at **20**, under the floor. A refused config becomes a fatal
`ErrorFrame`, `on_pipeline_error` fires, and the call is ended immediately —
so the failure is not "the voice sounds wrong", it is a call that is answered
and dies before the first word. `max_chunk_length` at 80 is inside its own
50-500 range and is unchanged.

Now 30: the documented floor, and still a large cut from the default 50. The
token-level aggregation is where most of the latency win came from anyway, and
it is untouched.

Sarvam's Pipecat integration guide separately suggests 15-25, contradicting
their own API reference. **Worth resolving with them** — if 20 is genuinely
accepted, this is not the cause and the run log will say so. Until then the
reference is the number to hold: too high costs a little first-byte latency,
too low costs every call.

`test_sarvam_service_factory.py` now asserts both values sit inside the
documented ranges, so the next latency pass cannot quietly go under again.

**How to confirm on a live box**, since the cause is recorded either way:

* The run's own timeline — open run #70 in the UI; a fatal pipeline error is
  rendered as a "Fatal Pipeline Error" notice carrying the provider's message.
* Or the API log: `Pipeline error for workflow run 70: ...`.

### 21. Telnyx calls were answered and then dropped

**FIXED.** Reported from a live test as "the call ends after I pick up".

The media socket gained a capability token (`services/telephony/
stream_capability.py`): the carrier's URL now carries one, and
`/api/v1/telephony/ws/...` refuses the handshake when none is presented (the
route closes with 4401 *before* accepting, so the upgrade itself is rejected).
That change moved seven call sites onto a single builder — five markup stream
elements and two inbound routes.

There were eight. Telnyx streams **inline with the dial request** rather than
from a markup response, so its URL was built inside `initiate_call` and was
not found by looking at webhook handlers. Every Telnyx outbound call therefore
went out with a token-less URL: the call was placed, it rang, the callee
answered, Telnyx opened the media socket, and we refused it. The call died at
the exact moment audio should have started, and nothing in the call's own
record said why — the only trace was a warning line in the API log.

Telnyx **inbound** was never affected: that URL comes from the route, which
already used the builder.

Two changes, plus a guard:

* `initiate_call` now calls `stream_capability.stream_url(...)`, and refuses to
  dial at all if it has no `workflow_id` / `organization_id` /
  `workflow_run_id` to mint against, rather than dialling a URL naming
  `/ws/None/None/None`.
* `stream_url` no longer returns a token-less URL when the socket requires a
  token. `mint` returns `None` when Redis is unreachable, and the old fallback
  called that "a call that still connects rather than a call that cannot be
  placed" — which stopped being true the moment the socket started requiring a
  token. It was the same failure as above, reachable by a Redis blip on any
  provider. It now raises `StreamCapabilityUnavailable`, so the error lands
  where the caller can report it and names the real cause. With
  `TELEPHONY_WS_REQUIRE_TOKEN=false` — the incident escape hatch — a token-less
  URL genuinely connects, and that is still what comes back.
* `tests/test_media_socket_is_authenticated.py` asserts that no module outside
  the builder spells the socket path. Seven out of eight the first time says
  grepping for it by hand is not a check worth relying on.

### 20. The model screen offered three tabs that were never three options

**FIXED.** Full write-up in `MODEL-SELECTION-REDESIGN.md`, including the
competitor comparison. Summary:

The screen asked you to pick one of "Speech to Speech", "Decibyl" or "BYOK".
Two of those are ways of paying for inference and one is an architecture, so
they were never comparable — they were exclusive only because a single stored
`mode` field had been made to carry both meanings.

What that cost:

* **A mixed stack was unrepresentable.** "Your Indic transcriber, my OpenAI
  contract" is what most Indian accounts want, and there was no way to say it.
* **Managed speech-to-speech was impossible**, because realtime lived inside
  the BYOK branch.
* **Nothing said that picking Speech to Speech meant bringing your own key.**
  That fact was in the shape of the JSON, not in any label.
* The screen had grown a warning strip explaining that switching tabs did not
  switch your account over — a reliable sign the model underneath is wrong.

Now two questions, asked separately. Architecture is the only top-level choice.
Whose key each model runs on is a property of the slot: pick `decibyl` in any
provider dropdown and that one model is managed, so half a stack can be managed
and half your own. There is deliberately no `mode` field in v3 — managed-ness is
derived, because a stored summary of the slots would eventually disagree with
them and the summary is what people would trust.

**The change was small, which is the part worth remembering.** The config types
were already discriminated unions including the Decibyl variants, and
`managed_resolution` already rewrote each section independently. One validator
(`_reject_decibyl_provider`) and one dropdown filter (`_byok_provider_schemas`)
were the only things forbidding mixed stacks. The rest is a vault to get keys
out of the configuration JSON.

Keys now live in `organization_provider_credentials` — Fernet-encrypted,
org-scoped, write-only — instead of inline as plaintext inside the model config.
That is what makes "store a key" and "choose a model" two separate jobs; before,
a key could only be entered while choosing a model, and switching a slot's
provider discarded the key you had just pasted.

~~Still open from this work: the per-agent model override.~~ **Done — this
line was stale and cost a later session real time.** The per-agent override is
fully built in `ui/src/app/workflow/[workflowId]/settings/page.tsx`: a
"Give this agent its own models" switch, the shared
`AIModelConfigurationV2Editor` bound to
`workflow_configurations.model_configuration_v2_override`, and a graceful
path when the org has no `organization_v2` configuration yet (an explanatory
message and a link to `/model-configurations`, rather than a hidden section).

Worth noting for anyone auditing coverage the same way: this feature is a
**field on `workflow_configurations`**, saved through the ordinary workflow
update endpoint — not an endpoint of its own. Any "which backend features
have no UI" scan driven by unused generated-client operations is structurally
blind to it, and to anything else shaped like it.

### 19. The two silent billing killers had no signal

**FIXED.** `PRODUCTION-CHECKLIST.md` §2 reproduced both against a real
deployment, and the only thing standing between either one and an operator was
a log line nobody was reading.

With `SUPPLIER_LEGAL_NAME` and `SUPPLIER_GSTIN` unset, a signature-verified
payment was credited to the ledger and issued **zero** tax documents.
`issue_receipt_voucher()` returning `None` rather than raising is correct — a
real payment must not be rolled back over a missing environment variable — so
the fix is not to make it throw. It is to make the consequence visible. With an
empty price book the second one has the same shape: every call bills its
platform fee, provider cost reads zero, and the dashboard reports 100% margin
rather than an error.

`GET /admin/billing/readiness` now answers both, in the shape
`/privacy/readiness` already used. The split that carries the weight is
configuration versus evidence:

* **Configuration** — is the supplier identity set, is there a webhook secret,
  is there a price book. Cheap, and worth little alone.
* **Evidence** — *are there captured payments carrying no receipt voucher*, and
  *are there costed calls that used a provider we hold no rate for*. These are
  the ones that matter, because setting the supplier identity today does
  nothing about the payments already taken without one. Those are an accrued
  liability and only a query finds them.

The headline check is designed to read zero missing vouchers. Any other value
is an incident, not a statistic.

A fresh install reports `unknown` rather than `ready` for every evidence check.
Reporting a pass because nothing has happened yet is the specific dishonesty
the module exists to avoid, and it is why one live ₹10 top-up remains on the
pre-launch checklist — it is the only thing that proves payment, credit and
document issuance work end to end.

The `Check`/`Readiness` vocabulary moved to `api/services/readiness.py` and is
shared with the privacy assessment, which re-exports it so its own callers and
tests are unaffected. Two independent status vocabularies that both meant
"ready" would have drifted.

### 18. A dead background worker was invisible

**FIXED.** One container runs uvicorn, the ARQ workers, the ARI manager and the
campaign orchestrator (`start_services_docker.sh`). When the ARQ worker dies
the API keeps answering 200 on every endpoint while completed calls stop being
costed, the rollups the entire dashboard reads stop refreshing, monthly tax
invoices stop being issued, and the nightly purge and backup stop running.

The dashboard does not go blank, which is the problem — it serves the last
figures it had. A quiet morning and a dead worker look identical.

The absence of work cannot be the signal, because a night with no calls
produces no costing either. So the worker now states positively that it is
alive: a one-minute ARQ cron writes a timestamp to Redis, and
`GET /health/workers` reports its age. Behind the devops secret, like
`/health/active-calls`.

Two design points worth keeping:

* **The key's TTL is a day, far longer than the five-minute staleness
  threshold.** An expiring key would answer "the worker is gone" and destroy
  the more useful answer — *when* it stopped, which is what lines the failure
  up against a deploy or an OOM kill.
* **`alive` is tri-state.** `null` means no heartbeat on record or Redis
  unreachable, and is deliberately not folded into `false`. A deployment whose
  worker was never started and one whose worker died an hour ago need different
  responses, and collapsing them sends an operator hunting a process that never
  existed.

The billing readiness check consumes the same signal, because a dead worker is
precisely what makes every billing number on the dashboard stale.

### 17. The campaign report had no aggregate, and no Language column

**FIXED.** `GET /campaign/{id}/report` streams a row per call and always did.
What it could not answer is what tender §10 asks for: connection rate,
completion rate, retry statistics, language distribution, daily progress.

`GET /campaign/{id}/summary` now does. The arithmetic is division; the content
is the denominators, so they are fixed in one module and match the admin
dashboard's campaign query exactly — `/admin/billing/campaigns` and this
endpoint cannot disagree about the same campaign.

The choice that changes the answer: **completion rate is over connected calls,
not over attempts.** A call nobody picked up cannot complete a conversation, so
putting it in the denominator reports the agent as failing at something it
never got the chance to attempt. Connection rate keeps attempts as its
denominator, and reach against the supplied contact list is reported separately
again — a reach target is written against the list you were given, not the
dials you chose to make.

A rate with nothing in its denominator is `null`, never `0.0`, consistent with
the rest of the codebase (HANDOVER.md §6): a campaign that has not dialled has
measured nothing, and 0% reads as total failure.

Daily progress buckets by **IST** calendar day. In UTC the boundary sits 5h30m
off an operator's own day, so a call at 01:00 IST would be filed under
yesterday.

Separately, the per-run CSV now carries a **Language** column.
`workflow_runs.language` was populated all along and the report query simply
did not select it, so language distribution — which the tender explicitly
requires — was underivable from the export a customer is handed.

### 16. Recordings defaulted to a US region

**FIXED.** `S3_REGION` defaulted to `us-east-1` in `api/constants.py`, in
`docker-compose.yaml`, and across the Helm values. That bucket holds call
recordings and transcripts of conversations with people in India, and the
region is where that personal data comes to rest — so every deployment that
never set the variable put it in Virginia.

Now `ap-south-1` (Mumbai) in all four places. The safe location should be what
you get by not thinking about it; a deployment whose data subjects are
elsewhere can still override deliberately.

This does **not** discharge the `ap-south-1` migration on the pre-tender
checklist — an existing deployment's data does not move because a default
changed. It stops the next one from starting out wrong.

The migration of the deployment that already exists is `MIGRATE-TO-MUMBAI.md`,
and it is verified rather than declared: `scripts/verify_region_migration.py`
asks S3 where each bucket actually is, rather than reading back the variable
that says where it should be. Until that is green on the box, this issue is
half-fixed — right for every future deployment, and unchanged for the data
already sitting in Virginia.

### 15. `usage/daily-breakdown` returned 400 for an unconfigured account

**FIXED.** The guard was right — an account with no `price_per_second_usd` has
nothing to break down — but a 400 made a correct guard render as a broken
dashboard tile on the first screen a new customer sees.

Now an empty series with `pricing_configured: false`. The flag is the point:
without it a caller cannot distinguish *not priced yet* from *priced, but
nobody has called*, and would render the same empty chart for a configuration
problem and for a normal Sunday. `total_cost_usd` stays `null` rather than
`0.0` — there is no price, so there is no cost, as distinct from a cost that
was measured and came to nothing.

### 13. Nothing backed up the database

**FIXED.** There was no automated backup of Postgres anywhere — no `pg_dump`, no
WAL archiving, no volume snapshot, nothing in `docker-compose.yaml` and nothing
in any deploy script. `DEPLOY.md` told the operator to "take a backup, and check
you can restore it", which is an instruction to a human, not an implementation.

The credit ledger is the only record of what every customer has paid, and it
cannot be reconstructed: Razorpay knows what was charged but not what was
consumed, reserved or adjusted, and the tax invoices issued against it become
unreproducible — a GST problem on top of a customer-trust one.

Found while writing `compliance/DPA-TEMPLATE.md`, where the security annex has
to state the backup position to a customer either way.

Now a nightly `run_database_backup` job: `pg_dump` in custom format, Fernet
encrypted before it leaves the process, uploaded, then read back and size-checked
before the local file goes. Old dumps are pruned on `BACKUP_RETENTION_DAYS`,
because a dump holds every phone number in the system and ages under the same
obligation as the data inside it. The privacy readiness check reports the age of
the newest object, so "is there a backup" is answered by the object store rather
than by the presence of the code.

Unlike the other scheduled jobs it re-raises after logging, so a failure reaches
Sentry rather than leaving the readiness check reporting the last good one.

The restore has been rehearsed end to end — dump, encrypt, decrypt, restore into
a scratch database, verify — and `scripts/rehearse_restore.sh` repeats it on
demand against a database it creates and drops itself, never the live one.

Re-run it after any schema change, secret rotation or storage migration. Those
are what silently break a working backup, and the failure is only visible on the
day it cannot be fixed.

---

### 12. `scripts/format.sh` reformatted the documentation, and its result depended on where you ran it

**Status:** FIXED

Two problems in the same place, both of which made the CI format-drift check
fail on a clean checkout.

Ruff formats Python code blocks inside Markdown as of 0.14, and `format.sh`
passes it the whole `api` tree. Every run reflowed the annotated snippets in
`AGENTS.md` and the service READMEs, where comment columns are aligned to be
read rather than executed — so the docs were a moving target and the drift
check failed on files nobody had touched. `extend-exclude = ["*.md"]` in
`api/pyproject.toml` stops it; Python is still formatted.

Adding that section made `api/pyproject.toml` ruff's configuration root, which
exposed the second problem: with no config file anywhere, ruff had been
resolving isort's first-party packages against the *current directory*, so
`ruff check api` from the repo root and `ruff check .` from `api/` disagreed
about whether `from api.x import y` was first-party. `src = [".."]` settles it
for both. `.` is deliberately not on that path — with it, the `api/alembic/`
package makes the third-party `alembic` distribution look first-party and every
migration's import block gets reordered instead.

### 11. The recordings bucket was published to the world

**FIXED.** `MinioFileSystem.__init__` applied a `Principal: {"AWS": "*"}` policy
granting `GetObject`, `PutObject` **and** `DeleteObject` on every
initialisation, and `aget_signed_url` returned a bare bucket path that only
worked *because* of it. So call recordings and transcripts — recordings of real
conversations with real customers — were readable by anyone who could reach the
endpoint and could guess a URL, and writable and deletable by them too.

Access is now by presigned URL, which carries its own expiring signature and
needs no bucket policy at all. Reads and uploads are both signed. Because a
presigned URL is signed for a specific host, and the internal endpoint
(`minio:9000`) differs from the public one, there are two SDK clients — one
bound to each, each signing for its own audience. That mismatch is the reason
the original code gave for not signing in the first place.

The anonymous policy survives behind `MINIO_PUBLIC_BUCKET=true` for a local
stack, off by default and logging a warning when on.

KYC documents were never exposed this way — `api/services/kyc/documents.py`
talks to MinIO directly and sets no policy, deliberately.

---

### 10. Schema drift between models and the database

**FIXED.** `alembic check` failed, and the real cost was worse than a failing
check: every `--autogenerate` run proposed **dropping
`workflow_definitions.call_disposition_codes`**, a NOT NULL column holding data
on every published version of every workflow. Anyone generating a migration had
to know to delete that line by hand, and the billing migration
(`810aaefd657d`) records having done exactly that.

Resolved in both directions deliberately rather than by accepting whatever
autogenerate suggested:

* `call_disposition_codes` existed in the database but not on the model, so it
  is now declared on the model. The data is the reason.
* `idx_queued_runs_campaign_state_optimized` was declared on the model but never
  created — a partial index on the campaign dispatcher's hot query. Created in
  `c8f31a604be7`.
* Several `server_default`s were set by migrations but not declared on the
  models, which was drift introduced by this billing work. Now declared on both
  sides. No DDL: the database was already correct.

`alembic check` now reports no operations, and a database built from an empty
schema by replaying every migration matches the models exactly — verified.

---

### 4. Sentry organization slug still said `echowave`

**FIXED.** `ui/next.config.ts` hardcoded `org: "echowave"`, a live external
identifier the rebrand deliberately left alone because renaming it without a
matching Sentry org breaks stack traces rather than fixing anything.

Now `SENTRY_ORG` and `SENTRY_PROJECT`, so a deployment sets its own and one
that sets neither uploads no source maps. Errors are reported either way; only
the readability of the trace depends on it.

---

### 8. Runs are not gated on any balance

**FIXED.** Removing MPS billing had taken the prepaid-credit check out of
`authorize_workflow_run_start`, so there was no spend ceiling at all. Both
halves of prepaid now exist: credit is bought through
`api/services/billing/payments.py`, and `api/services/billing/reservations.py`
refuses a run on an unfunded account.

A balance check alone would not have been enough, and that is worth recording
because it is the non-obvious part. A call's cost is unknown until it ends, so
two calls starting in the same instant both read the same balance, both find it
sufficient, and both proceed — an account with 10 rupees could start fifty
concurrent calls, each of which passed the check. Concurrency is what the
product sells, so that was the normal case rather than an edge one.

So a call holds an estimate before it starts, as an ordinary negative ledger
row taken under a per-organization `SELECT ... FOR UPDATE`. The lock is
load-bearing: with it removed, the concurrency test in
`api/tests/test_billing_reservations.py` allows 8 of 8 simultaneous starts on a
balance covering 2. The hold is released at costing and replaced by the real
charge, so an account is billed for what it used and never for the estimate,
and a cron sweeps holds stranded by a worker that died mid-call.

The tenant-isolation checks in that function were preserved throughout, and the
credit check deliberately runs *after* them — a security check must not be
reachable around, and consulting a balance before proving the caller owns the
workflow would leak whether an unrelated account has credit.

`test_quota_service.py::test_authorization_module_exposes_no_external_credit_gating`
still guards the thing that actually mattered: the check reads our own paise
ledger, and the external billing service that used to sit on the critical path
of every call does not come back.

Enforcement is on by default and can be disabled with
`BALANCE_ENFORCEMENT_ENABLED=false`. See DASHBOARD.md.

---

### 5. Links pointed at upstream community infrastructure Decibyl does not own

**FIXED.** ~40 references across READMEs, docs, `CONTRIBUTING.md`, `SECURITY.md`,
the issue template and the Helm chart pointed at a `decibyl-hq` org, a Slack
invite whose token was upstream's, a Trendshift badge for upstream's repo id,
and a `decibyl-plugins` repo that does not exist.

Resolved by removal rather than repointing, because Decibyl is not open source
and most of these were OSS-community artifacts with no equivalent to point at:

* **`curl | bash` installers** (22×) fetched `raw.githubusercontent.com/decibyl-hq/...`
  and 404'd for anyone following the docs. Anyone deploying has a clone, so they
  now run the script that is already there. The `ghcr.io/decibyl-hq` registry
  option went with them.
* **Slack invite, Trendshift badge, GitHub Discussions, the plugins repo** —
  deleted outright.
* **Issue tracker and security advisory form** — a private repo has neither.
  Now `support@decibyl.ai` and `security@decibyl.ai`.
* **The fork-and-PR contributor flow** described working against a public
  upstream. Replaced with direct clone and branch.
* **`README.md` claimed "100% open source" and BSD 2-Clause** throughout, which
  contradicts the product. Rewritten as a private-repo README.
* **`README.zh-CN.md` and `README.ja-JP.md`** were translations of that
  positioning. Removed rather than left contradicting the English one — they
  need a translator, not a find-and-replace, if they come back.

Now `security@decibyl.ai` and `support@decibyl.ai`, confirmed as the owned
domain. The remaining `decibyl.com` references are inherited docs and marketing
URLs (`docs.decibyl.com`, `www.decibyl.com/privacy-policy`,
`api-leads.decibyl.com`) pointing at hosts nobody here owns — a separate
decision, since repointing them at `.ai` would produce the same number of broken
links.

### 1. Test suite could not run from a fresh clone — `pipecat` missing

**FIXED.** `.gitmodules` did not exist anywhere in the repository (verified on
`main` too) and nothing was tracked under `pipecat/`, yet
`scripts/setup_requirements.sh`, `scripts/format.sh` and both CI workflows all
expected a pipecat submodule. This single missing declaration caused **107 of
the 130 collection errors and 39 of the 51 failures.**

Restored with the exact URL and pin upstream uses, rather than a guess:

```
[submodule "echowave/pipecat"]
    path = echowave/pipecat
    url = https://github.com/dograh-hq/pipecat.git
```

pinned at `aadd1d5dd606d2871b082e6f2ca1ad1eee53785b` — the `pipecat` gitlink
recorded at upstream tag `dograh-v1.42.0`, the release matching this repo's own
version (`1.42.0`, consistent across `.release-please-manifest.json`,
`api/pyproject.toml` and `ui/package.json`). The path is `echowave/pipecat`
because the project sits one level below the git root.

This also independently confirmed the pipecat revert done during the billing
removal: the pinned commit ships `src/pipecat/services/dograh/` exporting
`DograhLLMService`, `DograhSTTService`/`DograhSTTSettings`,
`DograhTTSService`/`DograhTTSSettings` and `DograhFluxSTTService` — exactly the
six symbols reverted. Had the rebrand's `pipecat.services.decibyl` been left in
place, every one would have failed to import at startup.

### 2. Test failures — all environmental

**FIXED.** Root causes, in order of impact: the missing pipecat submodule
(issue #1); no Postgres/Redis running; missing `ts_validator` npm deps (CI
installs these in a dedicated step); a locally-corrupted `alembic` install
mixing files from two versions; and **Python 3.11 vs the `>=3.13` this project
requires** (`api/pyproject.toml`), which produced every remaining
`pydantic.errors.PydanticUserError: Please use typing_extensions.TypedDict`
error.

Progression while fixing these:

| | failed | passed | collection errors |
|---|---:|---:|---:|
| starting point | 51 | 328 | 130 |
| + pipecat submodule | 51 | 365 | 120 |
| + full dependency set | 41 | 1061 | 63 |
| + Postgres, Redis, ts_validator npm | 6 | 1096 | 61 |
| + clean alembic reinstall | 6 | 1142 | 20 |
| **+ Python 3.13 (correct version)** | **0** | **1206** | **0** |

### 3. Pre-existing `ruff format` drift in `cloudonix/provider.py`

**FIXED.** Committed the formatting so `pre-pr-drift-check` passes. Confirmed
pre-existing by diffing formatter output at `main` versus the rebrand commit.

Note: with pipecat installed, ruff correctly classifies its imports as
first-party, so the spurious isort churn that previously appeared across ~20
unrelated test files no longer happens.

**Still worth doing:** ruff is not pinned (`api/requirements.dev.txt` has no
ruff entry and there is no `[tool.ruff]` config), so CI installs whichever
version is current and formatting will drift again on the next ruff release.

### 6. Brand PNGs carried stale `dograh` metadata

**FIXED.** Stripped the XMP metadata from `ui/public/decibyl-logo.png`,
`decibyl-logo-inverse.png` and `decibyl-mark.png`, verified pixel-identical
before and after. (These files are not referenced anywhere in the app — only
the SVGs are — so they are legacy assets and could simply be deleted instead.)

### 6b. The login/app background watermark still said "dograh"

**FIXED.** `ui/public/brand-imprint-{light,dark}.svg` — the giant faded wordmark
behind the auth pages and the app surface (`--brand-imprint` in `globals.css`) —
were a single traced `<path>` spelling "dograh". Because the letters are vector
outlines and not `<text>`, neither `grep` nor a DOM text query found them; it
only surfaced in a screenshot. Note the CSS comment already *claimed* the asset
was the "decibyl" wordmark, so the file and its documentation disagreed.

Regenerated from the app's own typeface: Geist (the `next/font` subset the UI
already ships) instantiated at weight 700, glyph outlines for "decibyl" laid out
by advance width and emitted as one path — so the asset still needs no font at
render time. Same fills as before (`#000` @ 1.8% light, `#fff` @ 0.9% dark).
Verified by screenshot, both inline and through the `background-image` path CSS
actually uses.

### 9. `openapi.json` needed verification by the real generator

**FIXED — verified.** The endpoint removals were originally applied to the spec
by pruning it programmatically, because `scripts/dump_docs_openapi.py` imports
`api.app`, which needs pipecat. With the submodule restored and a Python 3.13
venv, the real generator now runs, and its output is **semantically identical**
to the hand-pruned spec: same 129 paths, same 243 schemas, equal when compared
as parsed JSON. The only byte difference was key ordering inside a
discriminator mapping; the generator's ordering is now committed, since
`pre-pr-drift-check` compares bytes.

### F1. Rebrand broke `pipecat` imports — voice pipeline would not start

**FIXED.** The rebrand rewrote `pipecat.services.dograh` to
`pipecat.services.decibyl` along with the class names imported from it. Those
modules live in the pipecat submodule — external code this repo does not own —
so the imports would have raised `ModuleNotFoundError` at startup and taken
down the entire voice pipeline. Reverted in
`api/services/pipecat/service_factory.py`,
`api/tests/test_decibyl_managed_correlation.py`,
`api/tests/test_camb_tts_integration.py` and
`api/tests/test_decibyl_stt_service_factory.py`.

This needed care: `DecibylLLMService` / `DecibylSTTService` /
`DecibylTTSService` exist **twice** — once in pipecat and once as our own
config-registry classes with identical names. Only the pipecat ones were
reverted; ours (`DecibylGoogleLLMService`, `DecibylGoogleVertexLLMService`,
`DecibylGeminiJSONSchemaAdapter`) stay renamed. Confirmed correct against the
actual pinned pipecat commit — see issue #1.

### F2. Rebrand left `ruff format` drift

**FIXED.** The longer "Decibyl" identifiers pushed several lines past the
formatter's width. Confirmed by diffing formatter output at `main` versus the
rebrand commit.

---

## Running the test suite

The project requires **Python 3.13** (`api/pyproject.toml`:
`requires-python = ">=3.13,<3.14"`). Running on an older interpreter produces
a wave of pydantic `TypedDict` errors that look like code bugs but are not.

```bash
python3.13 -m venv .venv && source .venv/bin/activate

# Order matters: api requirements first, pipecat (with extras) last, so
# pipecat's pinned extras win. Installing pipecat first lets tuner-pipecat-sdk
# pull pipecat-ai from PyPI, which shadows the submodule and reintroduces
# "No module named 'pipecat.services.dograh'".
pip install -r api/requirements.txt -r api/requirements.dev.txt pytest pytest-asyncio
git submodule update --init --recursive
pip install -e "./pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,\
sarvam,soundfile,silero,webrtc,speechmatics,openrouter,camb,mcp,inworld,smallest]"

# ts_validator needs its own npm deps or ~22 MCP tests fail
(cd api/mcp_server/ts_validator && npm install)

# Not reached by the requirements files above, and each one fails tests by
# itself: camb-sdk backs pipecat.services.camb.tts (13 tests), pipecat-rumik
# backs the Rumik voice (14), and NLTK's punkt_tab tokenizer is loaded at
# runtime by the pipeline rather than at import, so its absence surfaces as
# ErrorFrames mid-test rather than a missing module.
pip install camb-sdk pipecat-rumik
python -c "import nltk; nltk.download('punkt_tab')"

# Services. Postgres needs the pgvector extension: a migration runs
# CREATE EXTENSION vector.
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/decibyl_test"
export REDIS_URL="redis://127.0.0.1:6379/0"
export ENABLE_AWS_S3=false MINIO_PUBLIC_ENDPOINT=http://localhost:9000 DEPLOYMENT_MODE=oss

python -m pytest api/tests -q
```

`api/conftest.py` normally loads `api/.env.test`, which is gitignored and not
present in a fresh clone — hence the manual exports above.

A handful of `ERROR [asyncio] Task was destroyed but it is pending!` lines in
the output are log noise from torn-down pipeline tasks, not test errors; pytest
reports them separately from its pass/fail counts.
