# Care: older people and their families (stream `care`)

Phase 2 of `LAUNCH-PLAN.md`, founder request: Simple mode, medicine reminder
calls with family alerts, scam check, step-by-step tech help, and a family
circle with the older person's consent. Built on phase 1: the controls action
cards (`services/workflow/actions.py`) are the consent, member preferences
hold Simple mode, and the shell's components and motion tokens draw it.

Everything ships **off**, each part behind its own switch:

| Flag | Constant | What it turns on |
| --- | --- | --- |
| `care_simple_mode` | `CARE_SIMPLE_MODE_ENABLED` | Simple mode in preferences (needs `member_preferences`), the Settings card, the profile menu switch |
| `care_medicine_calls` | `CARE_MEDICINE_CALLS_ENABLED` | `/care/medicines`, the reminder card, the scheduler and sweep, Decibyl's `set_medicine_reminder` |
| `care_scam_check` | `CARE_SCAM_CHECK_ENABLED` | `/care/scam-check`, Decibyl's `check_for_scam` |
| `care_tech_help` | `CARE_TECH_HELP_ENABLED` | `/care/help`, Decibyl's `phone_help_start` / `phone_help_answer` |
| `care_family_circle` | `CARE_FAMILY_CIRCLE_ENABLED` | `/care/circle`, `/care/family`, the invite and share cards, family alerts |

All five honour per-organisation overrides from the staff console
(`per_organization=True`), so care can be tried on one workspace first. The
UI's `/care` page and the profile menu's Care entry appear only while at least
one is on.

## Screens

`/care` is the hub: a short list of large choices, only the parts switched on.
Choosing one shows that part alone with one way back (`?part=` in the address,
so the phone's Back works). "Say it instead" (the composer's dictation, words
land in the box to check) sits beside every box a person would type into. In
Simple mode "Talk to Decibyl" leads the hub.

* **Is this a scam?** -- message or call, paste or say it, one button. One
  sentence first, then why, what to do, "Decibyl will never ask you for an
  OTP, PIN or password", and that a list can miss a new trick.
* **Help with my phone** -- one step in large words, "Did that work?", Yes /
  No. No shows another way; no again stops kindly and says who was told.
* **My medicine reminders** -- the reminders and today's calls with "I took
  it"; "Add a reminder call" leads to the card. "Needs setup" replaces the
  form where no call could be placed; "Test mode" says calls are simulated.
* **My family** -- what the family calls you, who is in the circle and what
  each sees, change or remove; "Add someone" leads to the consent card.
* **People I look after** -- join with a code; then only what was shared.

## Simple mode

`member_preferences.simple_mode` (nullable boolean; NULL is off), saved through
`PUT /me/preferences` like the other preferences, so it follows the person to
every device and never changes anyone else. `SimpleModeProvider`
(`ui/src/lib/care/simpleMode.tsx`) reads it and sets `data-simple-mode="on"` on
`<html>`; `simple-mode.css` raises the root size to 19px (20px from 768px) and
every control to 56px, and calms movement. Fewer choices: in Simple mode the
profile menu is Care, Settings, "Switch back to the usual screen" and Sign out.
Switching back is one press from the menu, Settings or the hub.

While `care_simple_mode` is off: the API returns `simple_mode: null`, refuses
to save it (422), and the provider mounts nothing (no auth read, no request,
no attribute, whatever the device remembers).

## Family circle and consent

The older person owns their circle (`care_circles`, one per person per
workspace). Their personal space is the natural home; in a business workspace
only they can read or change it (every query joins on `person_user_id`).

* **Adding someone is a consent card** (`care_family_invite`) naming the
  person, their masked email and exactly what they will see. `only_user_id`
  on the card means only the older person can confirm it (a new, generic
  check in `actions.settle`). Confirming makes a one-time code (8 characters,
  no look-alike letters, valid 7 days, stored as a hash) and emails it when
  email is set up -- otherwise the card says to hand it over.
* **Joining** needs the code *and* being signed in as the invited email
  (verified, where verification is enforceable). The circle is found by the
  code's hash -- the only cross-workspace lookup -- then checked.
* **What family see** is computed from the active membership on every read
  (`circle.family_view`): a kind not shared is not queried at all. Shares:
  `medicine_alerts`, `medicine_schedule`, `scam_checks` (verdict and signs
  only), `help_requests`. Family never see a phone number or the words of a
  scam check.
* **Seeing more** is another card (`care_family_share`); until it is
  confirmed they see what they saw before. **Seeing less and removing** are
  immediate: stopping must never wait. Removing hides past alerts too.
* A declined or undone card shares nothing; Undo after it ran revokes it.

## Medicine reminder calls

Reminders only. `label` is the person's or family's own words ("BP tablet
after breakfast"); Decibyl reads it back and never suggests, changes or
explains a dose. Labels that ask for advice ("how much…", "should I…") are
refused; the card, the screen and the call script all say "it only reminds".

1. `POST /care/medicines` checks the label, times (1-6, `HH:MM`), phone (E.164;
   a bare Indian mobile gets +91), language (the person's own by default),
   and that every family member named shares medicine alerts -- refused, never
   dropped. Then `calls.readiness`: no outbound line means **needs setup**,
   said before anything is saved.
2. The card (`care_medicine_calls`) shows the masked number, the times and
   timezone, the language, that the call says it is Decibyl, and who is told.
   Only the person can confirm; the reminder is active only for the exact
   version confirmed (`approved_version`), and the row must still match.
3. `care_medicine_tick` (ARQ, every minute) writes one `care_dose_calls` row per
   due dose (unique per medicine and time, so two ticks or two workers make
   one row) and only the tick that inserted it places the call. Doses more
   than 10 minutes late are not rung.
4. The call is the platform's own outbound path (`telephony.outbound.
   dial_workflow`: concurrency slot, run, quota, dial) on a per-workspace
   reminder agent made once from `services/care/reminder_call.py` (not live;
   it answers nobody). The greeting names Decibyl, the medicine and asks if it
   was taken, in the person's language; the rules forbid dosing advice and any
   OTP, PIN or password. The workspace's do-not-call list applies. The calling
   window does not: the person asked, on the card, to be rung at these times
   on this number (founder to confirm, see below).
5. Outcomes: the finished call (`record_run_outcome`, from post-call
   processing; the run's workspace must own the dose), no outcome in
   `CARE_CALL_ANSWER_MINUTES` (`care_call_sweep`), or "I took it" in the app
   (which also stops a call not yet placed). Not taken, not answered, unclear
   or not placed tells the family members named on the reminder, once.
6. Pause is immediate; starting again is a new card. Undo after confirming
   pauses it.

`CARE_CALLS_FAKE` (`taken` | `not_taken` | `no_answer`) simulates outcomes, only
when `ENVIRONMENT` is local, dev or test; the status then says "Test mode:
calls are simulated and nobody is rung".

## Scam check

`services/care/scam.py`: a list of warning signs read in English, Hindi and
Hinglish (OTP or PIN requests -- not "never share your OTP" --, pay or scan to
receive, remote-access apps, digital arrest, bill cut-off tonight, KYC,
prizes, paid tasks, family emergency from a new number, sent by mistake,
secrecy, odd links). Strong signs alone, or enough hints together, make
"likely a scam"; a genuine OTP message is "be careful" with "never tell this
code to anyone". No outside service is called. Only the verdict and sign codes
are stored (`care_scam_checks`); a likely scam alerts family who share scam
checks.

## Tech help

`services/care/guides.py`: eight plain guides (bigger text, louder ring,
WhatsApp video call and photo, Wi-Fi, block a number, screenshot, torch). Each
step is one action with one alternative. A question with no matching guide
says so and offers the topics -- never a made-up answer. Answers carry the
step's version, so a double tap answers once. Stuck tells family who share
help requests.

## In Chat

While each flag is on, Decibyl is handed the matching tool and told its rule
(`services/care/tools.py`, added to `system_prompt` and `office_tools` like
the table and procurement tools). They call the same services as the screens;
a reminder from Chat is the same card.

## Tables (migration `20261008care`, revises `202610071500shell`)

`member_preferences.simple_mode`; `care_circles`, `care_circle_members`,
`care_medicines`, `care_dose_calls`, `care_alerts`, `care_scam_checks`,
`care_help_sessions`. Additive; every row carries `organization_id`.

## Needs a human or keys

* An outbound phone line in the workspace (telephony configuration) for real
  reminder calls; until then the status says needs setup.
* SMTP for emailing invitation codes; without it the card says to hand the
  code over.
* A native speaker's review of the non-English greetings in
  `reminder_call.GREETINGS`, and of the Hindi scam signs.
* The founder: the calling-window decision above, and the Care positioning
  line (`CARE_POSITIONING_LINE` in `ui/src/components/care/copy.ts`, null
  until written).
* Not built here: family alerts by WhatsApp or push (in-app only; `identity`
  owns channels and push), voice per call language (the `voice` stream's voice
  catalogue), and live Talk (the `voice` stream; Simple mode's "Talk to
  Decibyl" opens Chat).

## Rollback

Every part is off by default; turning a flag off restores today's behaviour:

* `care_simple_mode` off: `simple_mode` reads null and cannot be saved; no
  attribute is set, so nobody sees large text; the Settings card and menu
  items are gone.
* `care_medicine_calls` off: the routes are 404s, the tick and sweep do
  nothing, nothing rings; reminders stay stored.
* `care_scam_check`, `care_tech_help` off: routes are 404s, the Chat tools and
  rules are not offered.
* `care_family_circle` off: routes are 404s; no alert is written and the family
  view is empty for that workspace.

Schema: `alembic downgrade 202610071500shell` drops the seven tables and the
column.
