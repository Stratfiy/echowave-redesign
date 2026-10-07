# Learning: the Learning Guide's data, lessons and progress (stream `learning`)

Phase 2 of `LAUNCH-PLAN.md`. Handoff 6 ("Learning Guide data"), 23
("Learning and personal guidance", "Saved records"), 15 E (learning
progress), and screens 13-14. Builds on `controls` (action cards, quotas,
member preferences, event catalogue, reply feedback) and `shell` (Chat,
shared components, motion tokens). Everything ships **off**.

| Flag | Constant | What it turns on |
| --- | --- | --- |
| `learning` | `LEARNING_ENABLED` | `/api/v1/learning/*`; the lesson inside Chat (screen 13); `/learning` and `/learning/{goal}` (screen 14). Honours per-organisation overrides. |
| `learning_today` | `LEARNING_TODAY_ENABLED` | `GET /learning/reviews` and the Learning section at the top of Today (`/tasks`). Needs `learning` too. |

`LEARNING_TEACHER` (not a flag): `model` (default) writes and marks with the
platform's model through the builder's client; `fake` is a fixed, offline
sample teacher for tests and local runs, labelled "Sample teacher" on screen.
Anything else is treated as `model`.

## Who owns what

* **This stream**: the learning record (tables below), lessons, practice,
  marking, progress, reviews, suggestions, export, deletion, and screens 13-14.
* **`agents`**: the Learning Guide's configuration (instructions, tools,
  voice, the helper picker). It reads the record through
  `api/services/learning/guide.py`: `context_for(org, user)` (a few lines of
  evidence: goals, marked answers, what needs another attempt, due reviews,
  resume link) and `resume_link(goal_id)` (`/overview?learn=<goal>`). Writes
  go through the same service calls the screens use; the agent never marks
  practice itself.
* **`today`**: Today's layout. `LearningToday` is a self-contained section it
  can place; `GET /learning/reviews` and `GET /learning/suggestions` are the
  data. Review reminder *delivery* (the routine editor, S10) is `today`'s;
  the goal stores the person's opt-in (`review_reminders`).

## Data (handoff 6)

Migration `20261008learning` (revises `202610071500shell`), six new tables,
nothing changed in an existing one.

| Handoff field | Where |
| --- | --- |
| Learning goal | `learning_goals.title`, `studying_for` (course or exam), `material` (pasted notes) |
| Preferred explanation language | `learner_profiles.explanation_language` (default for new goals, else the member preference), `learning_goals.explanation_language` |
| Baseline | `learning_goals.baseline_question`, `baseline_answer`, `baseline_level`, `baseline_feedback` |
| Lesson objective | `learning_lessons.objective`, `explanation`, `source_kind` (`material` / `general`), `sources` |
| Rubric | `learning_exercises.rubric` |
| Attempts and feedback | `learning_attempts.answer`, `outcome`, `rubric_results`, `feedback`, `idempotency_key` |
| Next review date | `learning_skills.next_review_at` |

Learner profile: explanation language, `adult` / `student` /
`course_learner`, what they are studying for, and `adult_confirmed_at`.
Adults first (handoff 6): starting needs "I am 18 or older"; there is no
child flow.

## Rules kept (`api/services/learning/core.py`)

* **The learner's own.** Every read and write names the organisation and the
  person. A colleague in the same workspace, a workspace admin, or the same
  person in another workspace gets "not found". No route takes a user id.
* **Progress is evaluated practice.** Only a marked attempt moves a skill:
  `Not practised yet` -> `Practised` / `Needs another attempt`. Reading,
  chatting, Next and time spent change nothing. No percentages, streaks or
  fluency labels. No practice is "No practice yet", not zero.
* **Once.** `POST .../attempts` needs an `Idempotency-Key`; the same key
  returns the first marking with `replayed: true` and changes nothing.
* **Marking.** Against the rubric, criterion by criterion; "passed" is
  derived (every criterion met), never the model's word. A criterion the
  marker skipped is not met. A cited source must be a line actually in the
  person's notes.
* **Reviews.** After a pass: 1, 3, 7, 14, 30, 60 days; after a miss: 1 day.
* **Suggestions** (at most three, from marked practice only): stuck (missed
  twice in a row -> "Try a smaller step"), improving (passed after a miss ->
  next step), review due. Nothing without practice.
* **Ask before saving sensitive details.** Goal, course and notes are checked
  (`sensitive.py`: health, mental health, disability, religion or caste,
  sexuality, money or legal trouble); a match is a `409 confirm_sensitive`
  naming the category, and nothing is saved until the person says yes.
* **Honest states.** No model key: `GET /learning/status` says
  `needs_setup`, and lesson writes are `503 needs_setup`. Quota (operational
  quotas): each lesson, placement and marking spends one `model_turns`;
  over the limit is `429` with the controls sentence.
* **Delete is a card.** `POST .../deletion` proposes the controls action
  `delete_learning_goal` (irreversible, version-bound with the task ledger,
  run once by the ARQ worker after the undo window). Only the learner's own
  Confirm runs it; the card on the shared thread carries no title.
  Learning writes no memory facts and no tasks, so nothing derived survives.
* **Export**: `GET .../export`, the whole record as JSON.
* **Events** (catalogue, codes only): `learning_goal_started`,
  `learning_practice_evaluated` (`outcome`, `exercise_kind`),
  `learning_goal_deleted`.
* **Was this useful?** On a practised lesson (`reply_feedback`, subject kind
  `lesson`).

## Screens

* **13, in Chat** (`ui/src/components/learning/LearningSession.tsx`): the
  "Teach me something" starter (kept among the three while `learning` is
  on), a "Continue learning" resume link, and `/overview?learn=<goal>`
  (`new`, and `&review=<skill>`) open it in the Chat column; the composer
  and thread list step aside. Profile -> goal -> one baseline question ->
  lesson (says "From your notes" or "General explanation") -> practice ->
  specific feedback per criterion -> Try again / Next exercise / Try a
  smaller step. Dictate fills the answer box for editing first. A dropped
  submit keeps the answer and retries with the same key.
* **14** (`/learning/{goal}`, `LearningProgress.tsx`): 760px; skill rows open
  to rubric and attempts; recent exercises; one next step; Continue practice;
  Edit goal (save contract, conflict shows what is saved); Export; Delete
  through `ActionPreview`. A failed refresh keeps the page, labelled stale.
* **Today** (`LearningToday.tsx`): due reviews and suggestions; draws nothing
  when there is nothing.

## Not built here

* Review reminder messages (opt-in is stored; delivery is the `today`
  stream's routine editor).
* The Learning Guide agent configuration and helper picker (`agents`).
* Learning in voice (`voice`): the same routes serve it.
* Retention period for learning records: none is set; records live until the
  person deletes them. A number needs the founder (see the PR).

## Rollback

* `learning` off: every `/learning` route is a 404; Chat's starter fills the
  box as before; `/learning` pages say "not available"; Today shows nothing.
  Rows stay.
* `learning_today` off: Today shows nothing; `/learning/reviews` is a 404.
* Schema: `alembic downgrade 202610071500shell` drops the six tables.
