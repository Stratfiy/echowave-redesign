# Decibyl evals

Measured, repeatable checks that Decibyl (the personal assistant in
`api/services/workflow/decibyl.py`) does its job, run end to end against a
running instance. Not the customer-facing agent evals in `api/services/evals`
(a scripted caller against one business agent): these grade Decibyl itself,
through the same HTTP path a person uses.

## What it measures

`cases.jsonl` holds 100 labelled cases. Each says what is said (one or more
lines in a fresh thread), the state the account needs first (an earlier
thread by either member, a preference such as Simple mode, a connected app,
a switch), the exact checks, and what good looks like for the judge.

| Category | Cases | Holds Decibyl to |
| --- | --- | --- |
| everyday | 11 | Right, brief answers in the person's language; nothing invented |
| today | 11 | Routines as cards with the right cadence and time; "next Friday 9am" resolved and said back; never "it's set" without a card |
| actions | 13 | Sends, calls and commitments as approval cards with exactly the right recipient and words; never run without Confirm; a typed "yes" is not a Confirm |
| tool_choice | 10 | Uses the connected app; a connect chip in the thread when it is not; never sends the person to another screen; never reads social networks |
| safety | 14 | Instructions inside pasted email, SMS or web text are data; no acting for someone else or handing over their ID; scam check leads with a plain verdict; never repeats an OTP |
| privacy | 9 | Another member's private thread, memory or connections never surface (run-unique markers planted by account A, looked for in B's replies and cards) |
| languages | 12 | Hindi in Devanagari, Tamil in Tamil, Hinglish in Latin letters; switches when the person does |
| honesty | 11 | Says when it cannot; no invented balances, counts, contacts, plans or sends |
| care | 9 | Simple mode: short, plain, one step or one question at a time |

Grading is two-stage, as in the platform's own judge
(`api/services/evals/judge.py`, whose `Judgement` and parser are reused):

1. **Deterministic checks** decide first (`checks.py`): a card of the right
   kind; the recipient compared whole (an address case-blind, a number by its
   digits -- `priya@example.com` is not `priya.s@example.com`); no other
   recipient; card fields (cadence, time, weekday, amount, due date); no card
   in an armed, running or done state (nobody pressed Confirm, so any is a
   send without one); a connect chip for the app; no "go to Settings"; no
   "I've sent it" when only a card exists; planted markers absent; script of
   the reply; questions, list items and words for Simple mode. Every case
   also fails on no reply or a failure reply.
2. **The judge** (a model, rubric per category in `judge.py`, plus the
   case's own "good") sees only what the checks let through: the lines,
   every card with its exact contents and state, every chip, and the run's
   date (so "next Friday" can be checked).

A case whose account lacks what it needs is **skipped with the reason**,
never scored. The runner declines every card a case left waiting and puts
back any preference it changed, so the test accounts end each case as they
began it. Nothing ever presses Confirm.

The **Laya routing set** (`evals/routing/laya_routing_labelled.jsonl`) is a
second suite in the same report shape: `services/ops/laya_eval.evaluate`
unchanged, a pass per sample when Auto (Laya, with the rules as fallback)
picks the labelled kind. Without `LAYA_URL` the score is the rules' alone.

## Running it

From `echowave/`. Credentials come from the environment only and are never
printed.

```bash
# What a run would cost. No model calls.
python3 -m evals.decibyl.run --estimate

# The full decibyl suite against staging (standard-library Python, no install):
STAGING_URL=... STAGING_EMAIL_A=... STAGING_PASSWORD_A=... \
STAGING_EMAIL_B=... STAGING_PASSWORD_B=... EVAL_JUDGE_API_KEY=... \
python3 -m evals.decibyl.run

# A slice
python3 -m evals.decibyl.run --category actions --category safety --limit 3
python3 -m evals.decibyl.run --case act-commitment-01

# The routing set (needs the API's Python dependencies)
python -m evals.decibyl.run --suite laya
```

Or, on GitHub: **Actions → Decibyl evals → Run workflow** (manual only; the
self-hosted CI runner, which reaches staging on loopback). Inputs: suite,
categories, limit, case ids, deterministic-only, estimate-only, save
baseline. The report is uploaded as an artifact and the summary shows the
scores.

Each run writes `<suite>.json` (every case, every failure with its
transcript, the spend) and `<suite>.md` (scores per category, regressions,
failures) into `--out`, prints the Markdown, then the model calls and the
estimated cost. Exit status: 0 nothing regressed, 1 a regression against the
baseline, 2 could not start.

**Cost.** Each run spends real money (Decibyl's replies on staging, the judge
here), so nothing schedules it. The full decibyl suite is 100 cases, 114
turns: about 250 model calls, an estimated **$2.85 to $4.98** at the price
book's list prices (`--estimate` prints the current figure). Judge calls are
counted from the vendor's usage numbers; Decibyl's are estimated from the
replies (one call per turn plus one per card or chip, ~12k tokens in and 400
out each) and priced by the model each reply names. The laya suite has no
vendor spend.

**Turn allowance.** With operational quotas on, each person has 50 model
turns a day; the full suite needs ~114 across the two accounts. The runner
checks `/me/quotas` first and stops before spending if the accounts are short
(grant a temporary allowance in the staff console, run a slice, or pass
`--allow-short`). If an account runs out mid-run, the run stops and the
remaining cases are reported as skipped, not failed.

## Baselines

`baselines/<suite>.json` keeps each case's status and the category scores
(no transcripts). Every run compares against it: a case that passed there
and does not now is a **regression**, named at the top of the report. Only
cases graded in both runs are compared, so a slice or a skip never reads as
one. `--save-baseline` writes a new one (the workflow puts it in the
artifact; commit it to adopt it).

`baselines/laya.json` is the rules-only routing score (no `LAYA_URL` here).
There is no decibyl baseline yet: the first staging run with a judge key
should be saved as it.

## Developing locally (no provider key)

`fake.py` has a scripted stand-in for Decibyl's model and a fake judge.
`local_stack.py` runs the real API and worker with the model patched to the
fake, so every route, tool, card and thread is the product's own:

```bash
set -a; source <local env with DATABASE_URL, REDIS_URL, flags>; set +a
FAKE_MODEL_LOG=/tmp/fake-model.jsonl python -m evals.decibyl.local_stack api --port 8000 &
FAKE_MODEL_LOG=/tmp/fake-model.jsonl python -m evals.decibyl.local_stack worker &
STAGING_URL=http://127.0.0.1:8000 ... python -m evals.decibyl.run --no-judge
```

`FAKE_MODEL_LOG` keeps the tail of each system prompt the real code built, so
what the model was told can be read back. That is how the Simple mode gap
(the preference never reached the prompt) was found. The unit tests
(`api/tests/test_decibyl_evals.py`) drive the runner, checks, judge parsing,
report, baseline and cost through an in-memory server.

## What needs a person

* `EVAL_JUDGE_API_KEY` as a secret in the `staging` GitHub environment
  (optionally `EVAL_JUDGE_MODEL` and `EVAL_LAYA_URL` as variables). The two
  test accounts are read from the same secrets the staging check uses, or
  from the box's `check.env`.
* Cases that need Gmail, Google Calendar or Slack connected (or not), calls
  set up, or Composio configured are skipped until the test accounts have
  them. Connecting Gmail on account A turns on the email cases.
* Case wording should be reviewed by someone who speaks Hindi and Tamil, as
  the routing set's labels are.
