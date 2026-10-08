# Settings: the Settings shell (stream `settings`)

Phase 2 of `LAUNCH-PLAN.md`. Handoff sections 8, 24, 25 and 30; screens 15-19
and 25-27. Built on phase 1: the person's own settings live in `controls`'
`member_preferences` (same row, same revision), the shell's `user_onboarding`
answers are read into it, every delete goes through `controls`' action cards,
and the screens use the shell's `SettingsSection`, `SaveBar`, `ScopedSearch`,
`ActionPreview` and `ErrorState`. Settings stays in the profile menu.

| Flag | Constant | What it turns on |
| --- | --- | --- |
| `settings_shell` | `SETTINGS_SHELL_ENABLED` | Grouped Settings (Personal, Connections, Privacy, Advanced, then the workspace under its name) with everyday-word search; Account, Personalization, Voice and language; onboarding answers read in; the person's choices told to each Decibyl turn |
| `memory_manager` | `MEMORY_MANAGER_ENABLED` | Memory manager; memory starts off until chosen (enforced in the write path); temporary conversations |
| `saved_items` | `SAVED_ITEMS_ENABLED` | Saved items, scoped search, "Save" under Decibyl's replies |
| `privacy_center` | `PRIVACY_CENTER_ENABLED` | Privacy and security: MFA, effective retention, personal export, personal deletion through a card |
| `model_inheritance` | `MODEL_INHERITANCE_ENABLED` | Settings -> Models shows source, readiness, fallback, Sarvam and agent overrides; revision-checked saves (admins) |

All five honour per-organisation overrides. `settings_shell` needs
`member_preferences`; personal deletion needs `personal_space` (the card lives
there) and says "unavailable" without it.

## What each part does

* **Shell** (`ui/src/components/settings/sections.ts`, `ShellSettingsNav.tsx`):
  a 208px grouped list beside a 640px column; on a phone `/settings` is the
  grouped list and each section is its own page with Back. Search matches
  titles, blurbs and everyday words (mic, memory, email, dark mode, 2fa, gst,
  dnd...) and never offers a section the person cannot open. Admin-only
  sections (Developer, Tools and tracing) are hidden from members; the server
  still enforces every write.
* **Save contract** (`useProfileForm.ts`, `SettingsForm.tsx`): dirty -> saving
  -> saved; a rejection keeps the draft and the reason; a 409 shows both
  versions with Keep mine / Use what is saved; a failed load is an error with
  Retry. Leaving a dirty form warns (`UnsavedChangesProvider`).
* **Preferences** (`services/member_preferences.py`, `services/settings/profile.py`):
  new fields `preferred_name`, `explanation_language`, `response_length`,
  `custom_instructions` (refused over 1500, never cut), `memory_enabled`,
  `speaking_speed`, `captions`, `auto_detect_language`. Onboarding answers are
  read in once and only fill empty fields. Each turn gets "About this
  person" lines; instructions are quoted as preferences that grant nothing.
* **Memory** (`services/settings/memory.py`, `temporary.py`): yours and the
  workspace's, never a colleague's (scope in every query); provenance and a
  revision row per change (`memory_fact_revisions`); edits compare-and-swap
  on the value read; forget is a reversible card; share previews destination,
  member count and whether it moves or copies. With the manager on, a turn
  whose person has not opted in writes no facts, no graph episode, and the
  teach tool says "not saved". Temporary conversations (`tmp-` threads) write
  nothing and are purged by `purge_temporary_conversations` (ARQ, every 10
  minutes) after `TEMPORARY_CONVERSATION_HOURS`.
* **Saved items** (`services/settings/saved.py`): owned by a person, held in a
  workspace or personal space, private or shared with the workspace. Search
  runs in one scope; LIKE wildcards are escaped. Delete is a reversible card
  that touches only the saved copy.
* **Privacy** (`services/settings/privacy.py`): retention read from
  `services/privacy/retention` (what the purge enforces), marked "Managed by
  your workspace" for members. Export is built by `build_personal_export`
  (ARQ), downloadable for `PERSONAL_EXPORT_DAYS`, then expired (410). Deletion
  needs the authenticator code (MFA on) or the typed phrase, raises an
  irreversible card in the person's personal space, deletes store by store and
  records each outcome plus the exceptions that remain (sign-in account,
  workspace data, backups, and the knowledge graph where configured).
* **Models** (`services/settings/models.py`): readiness is `ready` only when the
  key it needs is held here; `needs_setup` / `missing_credential` otherwise.
  Fallback lists only configured backups. Each slot has a revision; a stale
  save is a 409 with what runs now.
* **Cards** (`services/settings/cards.py`, `actions.propose_prepared`): the same
  proposed -> armed -> running -> done life, version binding and run-once
  claim as Chat's cards; recorded on an unlisted thread, settled only by the
  owner (`/me/settings/cards/...`), they skip the workspace approval matrix
  (it is the person's own data) and say nothing on the shared thread.

## Migration

`20261008settings` (revises `202610071500shell`): nine nullable columns on
`member_preferences`; tables `memory_fact_revisions`, `saved_items`,
`personal_data_requests`, `temporary_conversations`. Additive; upgrade,
downgrade and upgrade again verified.

## Rollback

Every flag off restores today's behaviour: routes are 404s, Settings shows the
old grouped list and General page, Models uses the old endpoint, no memory
pause, no "About this person" lines. Rows stay. A temporary conversation
already started is still purged on time. Schema: `alembic downgrade
202610071500shell`.

## Not built here

Signed-in device list (shown as unavailable); changing the sign-in email
(needs an identity flow); automatic removal of a deleted person's knowledge
graph episodes (recorded as an exception); closing the sign-in account after
deletion (staff); daily brief and notification settings (screens 20-21, with
`today` and `identity`).
