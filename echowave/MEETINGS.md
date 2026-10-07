# Meetings: meeting capture and record (stream `meetings`)

Phase 2 of `LAUNCH-PLAN.md`. Handoff sections 23 ("Meeting capture and
preparation", "Saved records") and 31 item 6; screens 11 (Meeting capture) and
12 (Meeting record and actions). Built on phase 1: the controls action cards
(`services/workflow/actions.py`), the task ledger, the event catalogue, the
operational quotas, and the shell's `registerMeetingMode` hook.

Everything ships **off** behind one switch:

| Flag | Constant | What it turns on |
| --- | --- | --- |
| `meeting_capture` | `MEETING_CAPTURE_ENABLED` | `/api/v1/meetings/*`, the pages under `/meetings`, and Attach -> Meeting mode in Chat. Honours per-workspace overrides from the staff console. |

Two operational limits, environment variables, not prices: `MEETINGS_MAX_UPLOAD_MB`
(50) and `MEETINGS_MAX_MINUTES` (120). Both are proposed defaults for the
founder to set.

## What a person can do

1. **Start** from Chat (Attach -> Meeting mode) or `/meetings/new`. Three ways
   in, each shown as available or needs setup *before* anything is captured:
   * **Record this meeting** -- this device's microphone, and nothing else. The
     screen says it cannot record another app's audio or a phone call.
   * **Upload a recording** -- a file the person already has.
   * **Paste notes** -- no audio, no consent needed.
2. **Consent and source.** Audio needs the person to confirm everyone agreed to
   being recorded; the server refuses an audio meeting without it (422). The
   stated source, language and consent sit above the timer.
3. **Capture.** A large timer that follows real capture (pauses and
   interruptions stop it), an input meter that follows the actual amplitude
   (M7; a static value with reduced motion), Pause/Resume and Stop fixed above
   the home indicator on a phone, and the live transcript. A small, collapsed
   "Possible actions so far" strip appears only when a line sounds like a
   commitment (English and Hindi cues); nothing is suggested to act on until
   after the meeting.
4. **Record** (screen 12): Summary, Decisions and Transcript tabs; suggested
   actions beside them at 1280 px (320 px column) or below on a phone. Every
   decision and action shows its source excerpt and jumps to it in the
   transcript; an action shows owner, task, the time *with its full date*,
   confidence, and what is missing. Rename, correct any transcript part (the
   original is kept), update the summary from the corrected transcript, export
   as Markdown, delete, and Return to chat (the conversation it came from).
5. **Act**, one card at a time: Review puts a suggestion on its own action
   card; Approve confirms that exact version; a short undo window; then exactly
   one task on the workspace's task board. Edit before approving withdraws the
   waiting card. "Take back" cancels the task while nobody has started it.

## Honest states

| Situation | What shows |
| --- | --- |
| No Sarvam key (workspace STT on Sarvam, or the platform's Sarvam STT key) | Record and Upload: "Transcription needs setup ..." before capture; the server refuses to start one (409). Notes still work. |
| No text model for this workspace | "The summary needs setup ... Transcripts still work." The transcript stands on its own. |
| No ffmpeg on the server | Upload needs setup. |
| Microphone refused / browser cannot record | Said in words with the other two ways in; nothing is created. |
| Microphone muted or ended mid-meeting | "Interrupted", the clock stops, Reconnect microphone; the stretch becomes a gap with its length and reason. |
| A part that never arrived (network) | Retried with backoff, waits while offline; if lost, a "This part never arrived" gap. |
| A part Sarvam could not transcribe | "Not transcribed: ..." with Try again; its audio is kept for the retry. |
| Any gap or failed part | The meeting is **Partial**, with the count of each; never "Ready". |
| No words at all | **Failed**, with why. |
| Daily model-turn limit (operational quotas) | The summary says the limit and when it resets; the transcript stands. |
| Card outcome unknown | "We are checking whether this was added. Please do not add it again." |

## Design decisions

* **Sarvam, always, in the language spoken.** Meetings reuse
  `SarvamTranscriptionService` (the batch STT integration). The workspace's own
  Sarvam key is used when its STT is Sarvam, else the platform's Sarvam STT
  key; never MPS. The person picks the language (native names; `unknown` asks
  Sarvam to detect) and it is sent as `xx-IN`.
* **Short self-contained segments.** Sarvam's synchronous endpoint takes about
  30 s, a live transcript needs words during the meeting, and a dropped
  connection should lose one piece. The browser rotates a fresh MediaRecorder
  every 25 s (`lib/meetings/recorder.ts`); uploads are cut with ffmpeg in the
  worker. Each piece is converted to 16 kHz mono WAV before Sarvam.
* **Audio is not kept.** A segment's audio is deleted the moment its words are
  stored (recording retention is off by default, handoff 24). A failed segment
  keeps its audio only so it can be retried; deleting the meeting removes it.
* **Times are never invented.** The reader returns the words said ("by
  Friday") and a resolved time only when one was said, resolved in the
  person's timezone (member preferences, else Asia/Kolkata). The UI shows the
  full date and "a suggestion until you confirm".
* **Every item is traceable.** The reader must quote the transcript; the quote
  is looked for, and an item whose quote is not there is kept and flagged
  ("These words were not found in the transcript"), not silently dropped.
* **One card kind, internal.** `actions.MEETING_FOLLOW_UP` is in
  `INTERNAL_ACTIONS`, not in the model's `propose_action` enum: a model cannot
  propose one. Its payload is built from the stored suggestion, never from the
  request. Its effect line says nothing is sent to anyone; an email or an
  invitation would be its own card with its own preview.
* **Exactly one task per suggestion.** The card's run creates the task through
  the task ledger with the idempotency key `meeting:<id>:item:<id>` (or reuses
  the linked task when the ledger is off), so a retried job, a second tab or a
  double run never makes a second task.

## Privacy and scoping

* A meeting belongs to one person in one workspace: every read and write
  filters on `organization_id` **and** `owner_user_id`. A colleague in the same
  workspace gets 404 for the record, export, deletion preview and actions; the
  list shows only the person's own meetings. In the personal space the
  workspace is theirs alone.
* The cards live on the meeting's own hidden thread (`mtg-<id>`), never in a
  shared conversation; the chat timeline refuses any `mtg-` thread (404, even
  to the owner), no result line is posted to Decibyl's shared thread, and
  `actions.settle` refuses a meeting card to anyone but its owner, so the
  generic `/timeline/actions/settle` route cannot be used to confirm one.
* Analytics (event catalogue): `capture_started` (audio source, language),
  `capture_failed` (audio source, reason code), `meeting_processed` (status,
  reading status), `action_confirmed` (card state), plus the cards' own
  `approval_*` and `task_*`. No transcript, excerpt, title or name ever goes to
  analytics.
* Deleting a meeting withdraws any waiting card, scrubs the meeting's words
  (the excerpt) from every card it ever had, and lets the person cancel the
  tasks nobody has started; it says that nothing from the meeting was saved to
  memory (this stream writes none).

## API (`/api/v1/meetings`, tag `meetings`)

| Route | What |
| --- | --- |
| `GET /capabilities` | Sources, transcription, summary: available / needs setup with reason; languages; limits; the "cannot record another app" and retention lines |
| `GET /` , `POST /` | The person's meetings; start one (`source`, `consent_confirmed`, `language`, `title`, `participants`, `origin_thread_id`, `notes`) |
| `GET /{id}` | The record (transcript, breaks, decisions, actions with their cards, possible actions) |
| `POST /{id}/segments` | One live segment (idempotent per `seq`) |
| `POST /{id}/upload` | The recording for an upload meeting (size checked before storing) |
| `POST /{id}/pause`, `/resume`, `/gaps`, `/stop` | Capture moves; `stop` records any segment that never arrived as a gap |
| `POST /{id}/retry`, `/read` | Retry failed parts; write the summary again |
| `PATCH /{id}`, `PUT /{id}/transcript/{seq}` | Rename; correct a part |
| `GET /{id}/export` | Markdown |
| `GET /{id}/deletion`, `DELETE /{id}?cancel_tasks=` | What deleting does; delete |
| `PUT /{id}/actions/{item}`, `POST .../review`, `POST .../settle` | Edit a suggestion; put it on a card; confirm / decline / undo |

Worker jobs: `transcribe_meeting_segment` (each live segment) and
`finish_meeting` (after Stop, upload or notes: split, transcribe, decide the
state, read).

## Not built here (and why)

* **Preparation brief before an upcoming event** and its optional one-day
  reminder (handoff 23, first half): needs calendar events and event-linked
  reminders, which are the `today` stream's (screens 07-10) and `identity`'s
  (connected calendars). The record keeps everything that brief would read.
* **Online meeting capture through an integration** (Zoom, Meet, Teams): no
  verified supported integration exists, so none is offered or implied.
* **Disputed speaker** (screen 12 state): Sarvam's synchronous endpoint
  returns no speaker labels; the reader lowers confidence when it is unclear
  who spoke, and owner is editable. Diarisation is a follow-up when a
  diarising endpoint is chosen.
* **Scoped search over meeting records** (handoff 23 "Saved records"): the
  search surface is the `settings` stream's (screen 15); records are listed at
  `/meetings` and linked from the conversation they came from.
* **Transcription minutes as an operational quota**: `quotas.KINDS` has no
  transcription allowance; reading spends one model turn. Proposed to
  `controls` / the founder; meetings are capped by `MEETINGS_MAX_MINUTES`.

## Rollback

* `meeting_capture` off: every `/meetings` route is a 404, the pages say
  "Meeting mode is not available yet", and Chat's Attach menu shows Meeting
  mode as "Not available yet" -- exactly today's behaviour. Rows stay.
* The only change outside this stream's files that runs with the flag off:
  the chat timeline refuses `mtg-` thread ids (no client mints them), and
  `actions.py` knows one more internal card kind (unreachable without a
  meeting).
* Schema: `alembic downgrade 202610071500shell` drops the four tables
  (additive migration `20261008meetings`; nothing else depends on them).
