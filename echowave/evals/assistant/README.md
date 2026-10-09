# Assistant seed scenarios

45 scenarios that say what state a conversation with Decibyl must leave
behind, in English, Tamil, Hindi and code-mixed Tamil-English and Hinglish.
They grade **outcomes, not prose**: the card that was proposed and what is on
it, the reminder schedule and its full local date, the fact that was
corrected and since when, the follow-ups a meeting produced, and what was
sent or dialled (which, without a Confirm, is nothing).

Written for Stage 1 of the research and intelligence plan. Nothing here has
been run against a model, paid or otherwise.

| File | Scenarios | Holds Decibyl to |
|---|---|---|
| `scenarios/reminders.yaml` | 15 | Deterministic dates in the person's zone ("tomorrow" at 00:10, "parso", Tamil and Hindi time words); 09:00-21:00 with no care-style exemption; the 5-a-day cap; no under-18s; a reminder still works with no phone line; snooze moves the task, not the delivery |
| `scenarios/memory.yaml` | 10 | A correction is confirmed and outranks what was inferred; a change "from next Monday" does not rewrite today's answer; personal memory stays personal; learned facts stay out of prompts |
| `scenarios/meetings.yaml` | 9 | Owner, action and due date for each commitment; none invented; a later correction in the same meeting wins; conditional and unowned items marked as such; an OTP said aloud is stored nowhere |
| `scenarios/tools.yaml` | 11 | Cards wait for Confirm; a typed "yes" is not one; a failed tool is said, not papered over; do-not-call honoured; voice approvals read-out only; injected instructions are data; a connect chip, not "go to Settings" |

`target: current` scenarios can be checked against `main` today.
`target: stage2` scenarios name the stores the reminder-call contract adds
(`docs/plans/reminder-calls.md`) or time-bound facts, and become runnable as
Stage 2 lands. 27 are `current`, 18 `stage2`.

**Review status.** The author checked every date, weekday and expected
value against the stated `now` and zone. The Tamil and Hindi lines have not
yet had a native speaker's review; that is required before the set is used
as a gate (CARE.md asks the same of the call greetings).

## Shape

```yaml
- id: rem-ta-01                 # category-language-nn, stable
  category: reminders           # the file it is in
  language: ta                  # en | ta | hi | ta-en | hi-en
  target: stage2                # current | stage2
  context:
    now: "2026-10-09T18:00:00+05:30"   # always with an offset
    person: {timezone: Asia/Kolkata, language: ta, phone_confirmed: true}
    workspace: {outbound_line: true}
    setup: [{store: facts, row: {...}}]  # state the run needs first
    transcript: |                        # meetings: the input
  turns: ["..."]                # lines said in one fresh thread
  on_call: ["..."]              # lines said on a call instead
  expect:
    tools: {called: [correct_memory]}
    state:                      # required: the final state
      - store: cards
        count: 1
        fields: {kind: reminder_call, local_date: "2026-10-10"}
    recall:                     # what memory answers on a given date
      - {as_of: "2026-10-12", key: opening time, value: "10:00"}
  why: why this is the right outcome
```

Stores: `cards`, `chips`, `reply`, `outbound` (anything sent or dialled),
`tasks`, `facts`, `personal_facts`, `remembered_block` (what reaches a
voice prompt), `follow_ups`, `done_callbacks`, `done_calls`,
`reminder_call_schedules`, `reminder_call_occurrences`,
`reminder_call_dispatches`, `any`.

Operators on a store: `count` (exact), `fields` (some row matches all;
`x_any` is one-of, `x_contains` a case-blind substring, `x_contains_any`),
`fields_contain` (substring match on rendered fields), `none` (no row
matches), `none_text` / `none_pattern` (not in any text), `script` (the
reply's script).

`python -m evals.assistant.scenarios` loads and checks every file (no model
calls); `api/tests/test_assistant_eval_seed.py` runs the same check in CI.

## How they would be run

The runner is not built in this stage. The plan, reusing what exists:

1. **Setup** through the same seams `evals/decibyl` uses (`api.py`,
   `local_stack.py`): a fresh thread on a test account, the `context.setup`
   rows written through the services that own them, preferences set and put
   back afterwards, the clock pinned to `context.now`.
2. **Drive** the turns through the HTTP path a person uses. `on_call` lines
   go through the call's text path (the pipeline's text-chat mode), not
   audio. Meetings feed `context.transcript` to `services/meetings`.
3. **Fakes at the edge.** The carrier is never reached: `dial_workflow` is
   replaced (as in `api/tests/test_call_when_done.py`), as are email and
   WhatsApp sends. `outbound` is what those fakes recorded.
4. **Read the state** from the database and the fakes, and apply the
   operators. A scenario passes only if every assertion holds. No judge
   model: these are deterministic by design. (Prose quality is
   `evals/decibyl`'s job.)
5. **Model.** Locally, `evals/decibyl/fake.py`'s scripted model drives the
   plumbing at no cost. A real run uses staging's configured model and costs
   money, so like `evals/decibyl` nothing schedules it; run it by hand or
   from the manual workflow, with an estimate first.

A scenario whose account lacks what it needs (no line, a flag off) is
skipped with the reason, never scored.
