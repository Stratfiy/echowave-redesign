---
name: diagnose-call
description: Work out why a Decibyl voice call went wrong, from the run record rather than from a transcript read by eye. Use when someone reports an agent looping, repeating a question, mishearing numbers, answering in the wrong language, talking after saying it would hang up, feeling slow, or cutting callers off — or when asked to check whether a live agent is healthy. Covers pulling a run, reading nodes_visited, identifying which model actually served the call, counting transcript scripts, and the known failure signatures with their fixes.
---

# Diagnosing a call (decibyl)

A voice agent fails quietly. It keeps talking, the recording is clean, nothing
errors, and the only evidence is a transcript nobody reads. Every real bug
found on this platform so far was found by the same six checks, not by
reasoning about the symptom.

**Run the checks first. Form a theory second.** The reported symptom is
usually a stage or two downstream of the cause: "it repeats itself" was a
model not emitting tool calls, "it reads digits wrong" was a word list missing
a script, "not patient" was three different things.

## The six checks

Run `scripts/diagnose_run.py` for all of them at once, or do them by hand.

| # | Check | What a bad answer looks like |
| - | ----- | ---------------------------- |
| 1 | `gathered_context.nodes_visited` | one node, on a multi-node workflow, with more than three caller turns |
| 2 | `usage_info.llm` keys | not the model you thought; a tier can resolve to anything |
| 3 | Scripts per caller line | one caller appearing in three alphabets |
| 4 | `rtf-latency-measured` | median over ~2s, or a long tail |
| 5 | `gathered_context.extracted_variables` | empty when the flow should have collected something |
| 6 | `pipeline_error` | usually null even on a broken call; a non-null one is a gift |

## Known signatures

Match the symptom to the check, not to the fix.

| Symptom | Check | Cause seen before |
| ------- | ----- | ----------------- |
| Repeats the same question; never progresses | 1 | Model not emitting node-transition tool calls. Every step change is a tool call. Seen with `reasoning_effort: minimal` on gpt-5 models. |
| Caller transcribed in several scripts in one call | 3 | Transcriber auto-detecting language per utterance. Pin it once the reply's own script has settled. |
| Numbers misread, digits dropped or reordered | transcript | Digit words spoken in an Indic script. `spoken_digits.py` must know that script in both registers, English-digits-in-local-script and the local numerals. |
| Says it will end the call, then keeps talking | transcript | No hang-up tool. `agent_can_end_call` is off by default. |
| Pauses mid-price, "Rs." then a gap | transcript | The sentence detector treats `Rs.` as an end of sentence. |
| First word slow | 4 | Text-to-speech aggregation grain or an unset vendor buffer. |
| Cuts the caller off / answers the next table | run config | `caller_environment`, and voice-detection thresholds. |

## Gotchas that cost time

- The workflow list is `GET /workflow/fetch`, **not** `/workflow/`. Per
  workflow it is `/workflow/fetch/{id}`.
- `transcript_url`, `recording_url` and `user_recording_url` are **storage
  keys**, not URLs. Sign them: `GET /s3/signed-url?key=...`.
- There are separate caller and agent audio tracks. The caller track is what
  the transcriber was actually given, which is often not what you assume.
- `GET /workflow/{id}/model-row` says what each slot **resolves to**. A slot
  configured `decibyl/accurate` can be serving anything; read this rather than
  the stored config.
- The live platform may be running older code than `main`. A merged fix is not
  a deployed fix.
- Latency events carry `latency_seconds`, not milliseconds, and the zero point
  is the caller genuinely falling silent, not the moment voice detection
  noticed.

## Rules for reading the result

**One call is one data point.** Two calls failing the same way is a pattern
worth acting on; one is a hypothesis. Say which you have.

**A short call is not a stuck call.** Most single-node runs are a caller who
said one thing and hung up. Weigh check 1 against the caller turn count before
calling anything broken.

**Check the model before blaming the prompt.** A tier is not a model, and the
model that served yesterday's call may not be the one serving today's.

**Report what is measured and what is inferred, separately.** The correlation
that survives is the one written down honestly enough to be argued with.

## Credentials

The script reads `DECIBYL_KEY` from the environment and never takes it as an
argument. Never write a key into a file, a commit, a PR body or a log line.

```bash
DECIBYL_KEY=... python3 scripts/diagnose_run.py 17 302
DECIBYL_KEY=... python3 scripts/diagnose_run.py 17          # last few runs
```
