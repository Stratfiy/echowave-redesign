# Agents: the five launch helpers and the builder (stream `agents`)

Phase 2 of `LAUNCH-PLAN.md`. Handoff section 6 and 31 item 5, screen 06, and
the founder's requests for trading summaries by interest, "who owes me", and
"ask Decibyl to build or do anything". Builds on `controls` (action cards,
task ledger, quotas, personal space, event catalogue) and `shell` (Chat,
composer, shared components). Everything ships **off**.

| Flag | Constant | What it turns on |
| --- | --- | --- |
| `launch_helpers` | `LAUNCH_HELPERS_ENABLED` | The helper picker in Chat (screen 06), `GET /helpers`, a `helper` on `POST /timeline/message` |
| `research_reports` | `RESEARCH_REPORTS_ENABLED` | `save_report`, "Save as report" under replies, `/saved-reports`, export |
| `follow_up_ledger` | `FOLLOW_UP_LEDGER_ENABLED` | `track_commitment`, `who_owes_me`, `follow_up_commitment`, `/follow-ups` |
| `trading_summaries` | `TRADING_SUMMARIES_ENABLED` | What a person follows, a summary from Research, the advice guard |
| `describe_builder` | `DESCRIBE_BUILDER_ENABLED` | "Build something" in the picker, trackers, `/start` opens the builder in Chat |

All five honour per-organisation overrides from the staff console
(`features.require(..., per_organization=True)`), so a workspace can try them
first.

## 1. Helpers are configurations, not stacks (handoff 6)

`api/services/helpers/catalogue.py` holds each helper as data: job, completion
evidence, acceptance boundary, example, permissions in plain words,
instructions, a tool allowlist, the connected-app toolkits it may use, the
`propose_action` kinds it may propose, skills, task templates and voice
(inherits the person's). There is one runtime: `decibyl.answer(...,
helper=key)`.

* **Instructions** join the system prompt for the turn (`turn.instructions`).
* **Tools narrow, never widen.** `decibyl.tools_for` intersects what Decibyl
  already holds with the helper's allowlist (`turn.narrow`). A helper can only
  ever hold fewer tools than Automatic. A call outside the list -- including a
  `propose_action` kind outside its own -- is refused at dispatch
  (`turn.refusal`), not just hidden.
* **Every card says who asked.** `actions.propose` stamps `helper` on the
  payload beside the organisation and member; it is not part of the approved
  version.
* **Background work keeps the helper.** A turn handed to the board
  (`decibyl_tasks.hand_off`) carries the helper in its continuation.
* **Allowlist guard.** `test_launch_helpers.py` fails if a name in any
  allowlist is not a tool Decibyl can hold, so a rename cannot silently shrink
  a helper (`api/AGENTS.md`, "Silent Absence").

| Helper | Tools (beyond recall, files, connect card) | Apps | Acceptance boundary, as built |
| --- | --- | --- | --- |
| Inbox | `track_commitment` | Gmail, Outlook | Cites message/thread ids and times; names unconnected accounts as not read; email content is data; a send is a card |
| Research | `web_search`, `web_fetch`, `search_records`, `save_report`, `trading_interests` | none | Source vs inference vs dated kept apart; conflicts and unreadable sources kept; export is the stored rendering |
| Follow-up | `track_commitment`, `who_owes_me`, `follow_up_commitment`, `schedule_routine`, `create_task`, `read_board`, `search_records` | Gmail, Outlook, WhatsApp, Slack | Tracked only after a confirmed card; follow-up is a `run_tool` card (run once, cancel before it fires, edit needs a new confirm) |
| Learning Guide | `web_search`, `web_fetch`, `schedule_routine`, `correct_memory` | none | One goal, practice, specific feedback, next review offered; no promise of results. Saved progress belongs to stream `learning` |
| Call and Appointment | `propose_action` (`create_bot`, `return_missed_call` only), `test_bot`, `search_records` | Google Calendar, Outlook, Calendly | Books only through calendar cards; no disclosure from caller ID; hands over when unsure. The call runtime is stream `voice` |
| Build something | `propose_action` (`create_bot`), `build_bot_from_spec`, `schedule_routine`, `create_tracker`, `add_to_tracker`, `read_tracker`, `create_task`, `install_from_repository` | none | Agent, routine or tracker as one card; a one-off task is just done |

## 2. Capability states (screen 06; design "Capability state")

`services/helpers/states.py` decides, from local readings only (tool rows,
phone-number rows, the search key's credential row, the workspace's switches)
-- opening the picker never calls an outside service:

* `available`; `needs_setup` with the next step (`connect` puts a connect card
  in the same conversation and the draft stays; `number` points at phone
  numbers; `operator` is said, not offered); `disabled_by_policy` ("Turned off
  by your workspace", set by an admin with `PUT /helpers/{key}/workspace`);
  `unavailable` with the reason.
* A reading that fails is `unavailable` ("Could not check this just now"),
  never `needs_setup`.
* Things that work and things that do not yet are said as notes beside an
  available state (Learning Guide's saved progress; Follow-up's sending).
* The server enforces it: `POST /timeline/message` with a helper that is not
  available is a `409` with the reason.
* Admins see each helper's tools, skills, inherited model and voice
  ("Advanced"), and a link to the existing agent settings.

## 3. Saved research reports

`saved_reports`. Findings carry `basis` (`source` | `inference`), the source
numbers they rest on and an optional `as_of`; a `source` finding must cite a
listed source; sources must be http(s) links; conflicts and unreadable
sources are kept. `body` is the one rendering and `content_hash` its SHA-256:
`GET /helpers/reports/{id}` shows its parts and `/export` returns `body`
byte for byte (Markdown, or the same text in an HTML `<pre>`), with the hash
in `X-Content-Hash`. "Save as report" under a reply keeps the reply as shown,
with every link in it as a source -- no finding is invented from the prose.
Private to the saver until they share it with the workspace.

## 4. Follow-up and "who owes me"

`commitments`. From a conversation, a commitment is a `track_commitment` card;
confirming it creates the row once (unique on the approving card, so the same
Confirm from two channels tracks it once). A person adding one themselves is
the approval. `who_owes_me` totals open amounts per currency (minor units,
never floats) and counts overdue. A follow-up is `follow_up_commitment`: an
ordinary `run_tool` card on a connected app's send tool, carrying the
commitment id, linked on the row; its delivery state is read live from the
card (`awaiting_approval`, `scheduled`, `sending`, `sent`, `failed`,
`cancelled`, `outcome_unknown`), so it cannot disagree with it. A second
follow-up while one waits is refused. Settle/cancel/share name the revision
read (409 with the stored row otherwise) and only the owner may.

## 5. Trading summaries by interest (information only)

`research_interests`, one row per person, revisioned. `POST
/helpers/research/trading-summary` asks Research in the person's thread with
their list (a turn like any other; it counts against their turns). Research
replies that are trading turns, and saved trading summaries, pass
`helpers/guard.py`: sentences that tell the reader to buy, sell, hold, set a
stop-loss or follow "my target" are removed (reporting what an analyst said is
kept), a line says a recommendation was removed, and the information-only
notice is always present. The guard is a blocklist of advice shapes on
purpose: a miss leaves advice a reviewer can see and add.

## 6. The describe-it builder

"Build something" decides agent (`create_bot` / `build_bot_from_spec`),
routine (`schedule_routine`) or tracker (`create_tracker`), or just does a
one-off task. Trackers (`trackers`, `tracker_entries`) are created by card;
rows are added from chat or `/trackers/{id}` (organising, not sending). With
the flag on, `/start` -- every old "Build an agent" link -- opens Chat with the
builder chosen; a template link becomes words in the box. Off, `/start` is the
old journey unchanged.

## 7. Scope and sharing

Every new table has `organization_id`; person-owned rows add `owner_user_id`
and `visibility` (`private` default, `workspace` to share). Filters are in
the query (`helpers/sharing.py`); only owners change rows; nothing crosses
workspaces. Interests are the person's alone. In a personal space the owner
is the only member, so everything there is theirs.

## Placeholders and what needs a human

* No new price, plan or positioning string. Helper copy is the handoff's
  own wording (section 6, screen 06). The trading notice ("Information only,
  not investment advice. Decide with a registered adviser.") is a safety line;
  **the founder should confirm its wording** (and whether a regulatory
  disclaimer is needed) before the flag is on for everyone.
* Inbox needs app connections (`COMPOSIO_API_KEY`) and a person's Gmail or
  Outlook; Research needs a search key (Serper, platform credential); Call and
  Appointment needs a number after KYC (stream `identity`) and the call
  runtime (stream `voice`); Learning Guide's saved progress is stream
  `learning`; Follow-up's sends need a connected sending app.

## Rollback

Each flag off restores today's behaviour at once:

* `launch_helpers` off: no picker; `GET /helpers` is a 404; a `helper` on a
  message is refused (old clients never send one); turns run as Automatic.
* `research_reports` off: no `save_report` tool or rule, no Save button,
  report routes 404.
* `follow_up_ledger` off: no commitment tools; routes 404. Cards already
  proposed still settle as cards.
* `trading_summaries` off: no interests tool, routes 404, no guard.
* `describe_builder` off: no builder, tracker routes 404, `/start` is the
  old journey.

Schema: `alembic downgrade 202610071500shell` drops the six tables (additive
migration `20261008agents`; nothing else depends on it).
