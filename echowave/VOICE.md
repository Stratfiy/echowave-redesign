# Voice: live voice with Decibyl, Call and Appointment (stream `voice`)

Phase 2 of `LAUNCH-PLAN.md`. Handoff 7 (mobile voice), 12, 15 F, 31.6, the
Call and Appointment runtime from 6, and screens 05 and 19 (with
`settings`). Built on phase 1: the task ledger and cards (`actions.py`),
operational quotas (voice minutes), member preferences, the event
catalogue, and the shell's `registerTalk` hook. Everything ships **off**.

| Flag | Constant | What it turns on |
| --- | --- | --- |
| `decibyl_voice` | `DECIBYL_VOICE_ENABLED` | Talk in Chat opens live voice with Decibyl (screen 05); `/voice/readiness`, `/voice/sessions/*`, `/ws/voice/{id}` |
| `voice_latency` | `VOICE_LATENCY_ENABLED` | Per-turn timings (`/voice/sessions/{id}/turns`), staff summary `/admin/voice/latency` |
| `call_for_me` | `CALL_FOR_ME_ENABLED` | Decibyl's `call_for_me` tool and the `place_call` card |
| `call_appointment` | `CALL_APPOINTMENT_ENABLED` | Booking policy, slots, booking, escalation; the appointments tool on calls; `/voice/appointments*` |
| `voice_language_settings` | `VOICE_LANGUAGE_SETTINGS_ENABLED` | Settings, Voice and language (screen 19); `/voice/catalogue`, `/voice/preferences`, `/voice/preview` |

All five honour per-organisation overrides from the staff console.

## Live voice (screen 05, handoff 21 "Live voice interaction")

**Depends on**: `chat_shell` (the composer's Talk button is part of screens
03-04) and `AGENT_BUILDER_ENABLED` with a model key (Decibyl's brain, as on
the text path). A spoken turn spends the person's voice minutes, not their
model turns: one allowance per conversation, never both.

**Distinct from dictation.** Dictate (the composer's existing `useDictation`)
records, transcribes and puts editable words in the box. Talk opens a live
conversation: `ui/src/components/voice/VoiceProvider.tsx` registers the
shell's Talk entry point while `decibyl_voice` is on.

**One brain.** `services/voice/pipeline.py` reuses the workflow pipeline's
parts -- `create_webrtc_transport`, `create_stt_service_with_backups` and
`create_tts_service_with_backups` (Sarvam for Indian languages), the
non-realtime turn strategies, Silero VAD and `RealtimeFeedbackObserver` for
captions -- and puts `DecibylVoiceBrain` where a workflow's LLM would be.
The brain hands each finished turn to `decibyl.answer`, the same function
the text path uses (context, memory, tools, cards), inside a `VoiceTurn`
context that streams each new piece of text to the voice, adds spoken-style
rules, and writes no chat draft. Signaling is the workflow tester's
`SignalingManager`, refactored into three overridable hooks
(`_authorize_start`, `_launch_pipeline`, `_on_websocket_closed`);
`VoiceSignalingManager` gates on the person's own live session and on
readiness, and never touches the workflow-run sender registry.

**States** (`ui/src/lib/voice/sessionState.ts`): idle, requesting
microphone, connecting, listening, processing, speaking, reconnecting; and
microphone blocked, no microphone, needs setup, daily limit reached,
connecting timeout, already live elsewhere, ended, failed. The microphone is
asked for before anything starts on the server.

**Session record** (`services/voice/sessions.py`, `voice_sessions`):

* one live session per person, held by a partial unique index (handoff 9's
  "1 live voice session");
* every move names the `state_version` it read; a stale one is `409` with the
  current session;
* the speech configuration is fixed at start; a reconnect that would run a
  different transcriber or voice refuses and asks for a new session
  ("model changes apply only to a new session");
* reconnect: the pipeline marks a dropped connection `reconnecting` and opens
  a gap on the server's clock; going live again closes it with `lost_ms` and
  `audio_lost: true` (nothing is buffered), and the screen says "Reconnected.
  About N seconds of audio was lost; anything you said then was not heard."
  Nothing said before the drop is replayed, so no task is duplicated;
* End always works whatever the version, and twice is once; ending settles
  voice minutes (the first minute was taken when audio connected) and emits
  `voice_session_ended` / `_failed`;
* a session nobody has heard from in `VOICE_SESSION_STALE_SECONDS` (90) is
  ended as `lost` by the worker (`sweep_stale_voice_sessions`, every minute)
  and before every start;
* private: every read and write is by organisation and person.

**Interruption** (handoff 12): the user aggregator's turn strategies raise
the pipeline's interruption; `DecibylVoiceBrain` cancels the turn in flight
and the voice's queue is cleared by pipecat. `HeardTracker`, after the output
transport, writes to the thread only what was actually played, marked
"(interrupted)" when cut off, so the next turn never assumes the person heard
it.

**Mute** disables the track (no audio is sent) and is recorded on the
session. **End** ends the session, closes the socket and peer connection and
stops every track, so the microphone is released.

**Approvals** never happen by voice: a card Decibyl proposes is on the
thread; the sheet says one is waiting and "Review it" minimises the sheet so
the exact card is in view (handoff 21: "Ambiguous or high-risk requests
return to a visible confirmation"). The voice rules tell the model a spoken
yes is not an approval.

**Honest states**: `/voice/readiness` and session start report
`needs_setup` naming the missing slot (transcriber, voice, assistant model)
without calling any provider; a language Sarvam cannot speak is typed and
captioned, and the readiness note says so.

## Latency (handoff 12)

`services/voice/latency.py`, `voice_turns`. Two clocks, never subtracted:

* the device measures `response_ms` (the person's input level falling to
  Decibyl's playback level rising) and `interruption_ms` (the person
  speaking over playback to playback stopping), on its own monotonic clock;
* the server measures `stt_final`, `brain_first_text` and `tts_first_audio`
  on its own monotonic clock.

Missing stages stay absent. Tool turns are flagged and reported apart (their
first audio may be filler). The staff summary gives p50/p95 and sample size
by language, channel and voice provider, with the proposed targets (800 ms /
1.5 s / 250 ms) shown as targets, and `small_sample` below 20.

Not done here (and why): the 300-turn annotated evaluation set and the 1/5/10
concurrency runs need real recordings, real keys and a staging box (phase 3).

## Call it for me

`services/voice/call_for_me.py`. Decibyl's `call_for_me` tool (offered only
while the flag is on, with a rule in its system prompt) proposes a
`place_call` card through `actions.propose`. The card shows the number
masked, who is called, the purpose, what may be shared, the exact opening
announcement, and "It cannot be undone". Confirm binds the payload version
(task ledger); the card runs once (compare-and-swap); it spends one outbound
message from the confirming person's allowance.

At run time, in `missed_call.place_callback`'s guard order: phone line and
helper set up, `dnd.assert_may_call` (do-not-disturb list and calling
window), then `dial_workflow(source="call_for_me")` with
`on_behalf_announcement = "Hello, this is Decibyl, an AI assistant calling on
behalf of <preferred name>."` in the run context. The engine
(`PipecatEngine.resolve_ai_disclosure`) speaks it before any greeting, even
where the agent's own AI line is switched off. A refusal fails the card with
its reason and dials nothing; any other error is `outcome_unknown` ("We are
checking whether this was delivered. Please do not send it again."), never
`failed`, with or without the ledger.

## Call and Appointment runtime

`services/voice/appointments.py`, `appointment_policies`, `appointments`.

* **Granted policy**: `booking` is `off` until an admin sets `suggest` (offer
  times, never confirm) or `book`. Saves carry a revision.
* **Slots**: the business's hours (the helper's own schedule, else the
  workspace's -- `agent_hours`), minus bookings, inside lead time and
  horizon. Hours nobody set are `needs_setup`, not "open all day".
* **Booking** needs name, callback number and reason; `known_caller`
  verification books only callers already in contacts; a per-workspace
  advisory lock makes overlap impossible even for two calls at once.
* **No private disclosure by caller ID**: the call's tool has no function
  that reads an existing booking.
* **Escalate**: a line on the team's Decibyl thread, and a transfer number
  when the policy has one.
* The tool is a built-in `appointments` tool row (new `tool_category`
  value), added to the workspace when an admin chooses the helper; attaching
  it to the helper's steps is the agent owner's choice in the editor.

A booking made by a caller on a call is the helper acting within the policy
an admin granted (handoff 6, "book within granted policy"): the policy is the
approval. Nothing a person asks Decibyl to do from Chat books, sends, pays or
deletes without a card.

## Voice and language settings (screen 19)

`/settings/voice`, listed in Settings under You while
`voice_language_settings` is on. Language (the member preference list, by
native name), the voices that speak it (from Sarvam's catalogue -- no
invented attributes such as gender), a cancellable preview from the shared
sample store (recorded on the platform key, never the account's; no key is
`needs_setup`), speed 0.5-2.0 with its number, captions, and the
microphone's state in this browser. One Save under the member preference
revision; a voice that cannot speak the language is refused, and a language
change clears an incompatible voice instead of substituting one.
`member_preferences` gains `voice_speed` and `captions`.

## Mobile (handoff 7)

The sheet is full screen below 768 px with controls above the safe area and
44 px targets; the minimised strip sits above the bottom navigation. Native
apps are outside this stream; the same authenticated APIs serve them.
Browser microphone support varies: the screen reports what the browser says
(`navigator.permissions`), and device testing is a human step.

## Migration

`20261008voice` (revises `202610071500shell`), additive: `voice_sessions`,
`voice_turns`, `appointment_policies`, `appointments`, two nullable columns
on `member_preferences`, and the `appointments` value of `tool_category`.
Downgrade drops exactly these (deleting any `appointments` tool rows first).

## Rollback

Turning a flag off restores today's behaviour at once:

* `decibyl_voice` off: Talk is unregistered and Chat says "Live voice is not
  available yet"; routes and the socket are 404s; sessions in flight are
  swept as lost.
* `voice_latency` off: nothing recorded; routes 404.
* `call_for_me` off: the tool is not offered; a stored card refuses to run.
* `call_appointment` off: the tool offers nothing on calls; routes 404.
* `voice_language_settings` off: the section is not listed; routes 404.

Schema: `alembic downgrade 202610071500shell`.
