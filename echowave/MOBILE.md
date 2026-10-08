# Mobile: the native Decibyl app for iOS and Android (stream `mobile`)

`mobile/` is an Expo app (React Native, TypeScript, Expo Router, SDK 57)
that talks to the same API as the web app with the same bearer token
(local auth, `/api/v1/auth/login`). Its API client is generated from
`ui/openapi.internal.json` (`npm run generate-client`); no endpoint is
written by hand. Builds and store submission go through EAS (`eas.json`);
`mobile/RELEASE.md` has the exact steps from the founder's accounts to
TestFlight and Play internal testing.

The product rules hold on the phone too (AGENTS.md): a missing app is a
connect chip in the thread, the thread carries its own next steps (follow-up
chips), everything works on Free with no phone number, and the app adds no
price, plan or positioning string.

## Switches

| Flag | Constant | Turns on |
| --- | --- | --- |
| `mobile_push` | `MOBILE_PUSH_ENABLED` | `/api/v1/me/mobile-push/*` (404 while off), Expo push to the app, and the app-only notices for replies, approvals and calls |

Per-workspace overrides from the staff console apply
(`features.require(..., per_organization=True)`). Everything else the app
shows follows the flags that already exist (`chat_shell`, `approval_dock`,
`today_list`, `today_reminders`, `daily_brief`, `identity_notifications`,
`privacy_center`, `settings_shell`, `member_preferences`, `care_simple_mode`,
`decibyl_voice`, `reply_feedback`, `connections_per_person`, ...): a screen
whose flag is off says "not switched on" instead of failing.

## Server: push to the app (`mobile_push`)

* **Table** `mobile_push_tokens` (migration `20261009mobile`, revises
  `20261008voice`): one Expo push token per install, owned by one person,
  with the workspace it registered in; unique on the token, so signing in as
  someone else on the same phone moves it.
* **Routes** (`api/routes/mobile_push.py`): `GET /me/mobile-push/devices`,
  `POST /me/mobile-push/tokens` (register; sent again on every app open),
  `POST /me/mobile-push/tokens/remove` (sign-out), `DELETE
  /me/mobile-push/devices/{id}`. Only the caller's own tokens.
* **Sending** (`api/services/identity/mobile_push.py`): Expo's push API
  (`EXPO_PUSH_URL`, optional `EXPO_ACCESS_TOKEN`), up to 100 per request;
  `DeviceNotRegistered` revokes the token like a 410 revokes a browser.
  Expo holds the APNs and FCM credentials; the server holds none.
* **One push channel, two kinds of device.** `notifications._push` now
  sends to browsers *and* phones, so every existing producer -- reminders and
  the brief (`today/delivery.py`), mail at the Decibyl address, "did it
  arrive?" -- reaches the app with no change, under the person's own topics,
  quiet hours, snoozes and private previews (generic lock-screen text by
  default). With the flag off the outcome is exactly what it was.
* **New producers, app only** (never email or WhatsApp): a Decibyl reply
  (`decibyl._answer`, topic `task_updates`), a card waiting for the person
  who asked (`actions.propose`, topic `approvals`; the notice opens the card,
  Confirm is only ever on the card), and the end of a call placed for them
  (`agent_timeline.record_call_ended` for `call_for_me` runs, which now carry
  `principal_user_id`). Each is claimed once per person and dedupe key.
* Push is "available" in notification settings and for today's reminders
  when `mobile_push` is on, even without browser VAPID keys.

Tests: `api/tests/test_mobile_push.py` (27: flag off, ownership, token
validation, moving a phone, sign-out, batching, revocation, the combined
channel, app-only notices, private previews, topic off, the card and call
producers, migration down/up).

## Screens

Native (React Native):

| Screen | Route | Server |
| --- | --- | --- |
| Sign in (with the authenticator step), sign up (invite code while `invite_only_signup` is on), verify email | `/sign-in`, `/sign-up`, `/verify-email` | `/auth/*` |
| Biometric unlock (optional, Settings) | `/unlock` | -- |
| Chat: threads, new chat | `/` | `/timeline/threads` |
| A thread: messages, turn status, follow-up chips, action cards (exact text, one confirm, undo window), connect chips (in-app browser), attachments (camera, photos as a one-page PDF, files), voice notes, Stop, Useful / Not quite, the approval dock | `/chat/[id]` | `/timeline*`, `/shell/chat/stop`, `/knowledge-base/*`, `/workflow-recordings/transcribe`, `/connectors/*`, `/reach/connections*`, `/feedback` |
| Today: approvals, due, brief, coming up, suggestions, end of day | `/today` | `/today*` |
| Approval (exact preview) | `/approvals/[id]` | `/today/approvals/{id}`, `/timeline/actions/settle` |
| Reminder: new (checked by the server first), detail | `/reminders/[id]` | `/today/reminders*`, `/today/resolve-date` |
| People: consent, synced contacts, search | `/people` | People API when it lands (below) |
| A person: phones, emails, brief, last interactions, ask Decibyl | `/person/[id]` | same |
| Talk to Decibyl (live voice, not dictation) | `/voice` | `/voice/*`, `/ws/voice/{id}`, `/turn/credentials` |
| Share into Decibyl | `/share` | `/timeline/message` |
| Settings | `/settings` | -- |
| Account (name, time zone) | `/settings/account` | `/me/settings/profile` |
| Language (Decibyl's language; app UI Hindi or English) | `/settings/language` | `/me/preferences` |
| Notifications (push to this phone, topics, quiet hours, lock-screen privacy, phones) | `/settings/notifications` | `/me/notifications`, `/me/mobile-push/*` |
| Privacy (download my data, delete my data through a card, stop syncing contacts) | `/settings/privacy` | `/me/privacy*`, `/me/settings/cards/*` |
| Simple mode, unlock, appearance | `/settings` | `/me/preferences` |

**Web views** (the web app's own screen, signed in, in an in-app web view;
listed in `mobile/src/lib/webScreens.ts` so each can be replaced natively
one by one): Personalization, Voice, Daily brief, Memory, Saved items,
Connected apps, Channels, Decibyl identity, Apps, Phone number, Models,
Skills, Knowledge, Developer, Advanced, Workspace, Team, Company,
Compliance, Agents, Activity, Care, Learning, Meetings, Follow-ups, Saved
reports, Trackers, Help. Any link or push to a web path with no native
screen also opens here, so nothing a link points at is missing.

The web view hands over the session the way the web's own sign-in does: its
first page is loaded with the web app as the base URL and POSTs the token to
`/api/auth/session` (same origin, httpOnly cookie), then goes to the screen.
The token never appears in an address.

## People (waits on the People stream)

The People server is being built in another stream (branch `claude/people`,
flag `people`) and has not landed, so there is no generated client for it.
The app is written against one interface (`mobile/src/lib/people/api.ts`)
and runs on `DevicePeopleApi` until then: contacts are read only after the
in-app consent and the phone's own permission, normalised (E.164 numbers,
lower-case emails), diffed against the last sync and kept **on the phone**;
nothing is sent. Briefs and interactions show "not yet" rather than
anything invented. Re-sync is incremental, on open and in the background
(`expo-background-task`, which replaced `expo-background-fetch` -- deprecated
in SDK 57).

When the People routes land: regenerate the client, implement
`serverPeopleApi.ts`, return it from `createPeopleApi` while `people` is on,
and bump `CONSENT_VERSION` so everyone is asked again before anything leaves
the phone.

## Deep links

`decibyl://` (the desktop app's scheme) and universal links / App Links on
`app.decibyl.ai`, both through `mobile/src/lib/links.ts`:
`decibyl://chat/<id>`, `decibyl://approvals/<id>`, `decibyl://reminders/<id>`,
`decibyl://today`, `decibyl://ask?q=...`, `decibyl://share?text=...`, and the
web paths `/overview?thread=`, `/tasks/approvals/<id>`,
`/tasks/reminders/<id>`, `/tasks`. Push notifications carry the web path, so
the same producers serve web and app. The `.well-known` files are templates
in `mobile/universal-links/` until the founder's Team ID and signing
fingerprint exist (RELEASE.md).

## Hindi and English

`mobile/src/lib/i18n/` (`en.ts`, `hi.ts`), checked for key and placeholder
parity by a test. The UI follows the person's language preference (Hindi
when it is Hindi, English otherwise) unless chosen in Settings; numbers use
Indian grouping; phone numbers are stored in E.164. The Hindi strings need a
native speaker's review before launch.

## Proof

* `cd mobile && npm test` -- 82 jest-expo tests: the generated client
  (bearer token at request time, errors as ApiError, 401 signs out), auth
  (MFA step, sign-up checks, session storage, biometric switch), approvals
  (a double tap is one request), contact sync (normalising, diffing,
  incremental send, no consent no read, failure keeps the snapshot), push
  registration (order of steps, never prompting on open, 404 is "off",
  sign-out), links, E.164, turn status, the photo PDF, i18n parity, web-view
  handoff.
* `npx tsc --noEmit` and `npx expo lint` clean.
* `npx expo prebuild --platform all` generates both native projects
  (permissions, App Links, associated domains, the share extension and app
  group).
* The web build walked against a local stack with every launch flag on:
  screenshots in the PR, scripts in `mobile/e2e/`.

## Needs a human or keys

* The founder's accounts and words: `mobile/RELEASE.md` section 1.
* A device build to verify live voice: `react-native-webrtc` is not yet
  marked as tested on React Native's New Architecture (expo-doctor says so);
  it runs through the interop layer. Try voice on a development build first.
* People: the server from the People stream (above).
* Hindi review by a native speaker.

## Rollback

`mobile_push` off: the routes 404, no token is read or written, no app
notice is sent, and push outcomes are what they were before. The app keeps
working without push. Schema: `alembic downgrade 20261008voice` drops
`mobile_push_tokens` (nothing else depends on it).
