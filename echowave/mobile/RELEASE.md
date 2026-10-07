# Releasing the Decibyl app: from accounts to TestFlight and Play internal testing

Everything in the repository is ready to build. What is missing is the
company's own accounts, keys and words, which only the founder can supply.
Nothing below is in the repository, and nothing secret ever should be:
credentials live in EAS (Expo's build service) or in the store consoles.

Builds run on EAS in the cloud, so no Mac is needed for iOS.

## 1. What the founder supplies

| # | Item | Where it goes |
| --- | --- | --- |
| 1 | **Apple Developer Program** membership for the company (USD 99/year) | Signs the iOS app as Decibyl |
| 2 | **App Store Connect app record**: name, bundle id, SKU, primary language | `submit.production.ios.ascAppId` in `eas.json` |
| 3 | **Google Play Console** account for the company (USD 25 once) and an app created in it | Play internal testing |
| 4 | **Expo account** (organisation) and an **EAS access token** | `eas init`; `EXPO_TOKEN` for CI builds |
| 5 | **APNs key** (`.p8`, its Key ID, the Team ID) | Uploaded to EAS: Expo sends iOS push with it |
| 6 | **FCM**: a Firebase project with the Android app, its `google-services.json`, and an FCM V1 service-account key | EAS env file + EAS credentials: Expo sends Android push with it |
| 7 | **Confirm the app name and bundle id**. Defaults: name `Decibyl`, iOS bundle id and Android package `ai.decibyl.app` (share extension `ai.decibyl.app.ShareExtension`, app group `group.ai.decibyl.app`) | `eas.json` -> `build.base.env` |
| 8 | **Store listing**: description, keywords, support and marketing URLs, the privacy policy URL | App Store Connect, Play Console |
| 9 | **Icons and screenshots**: 1024x1024 app icon; Android adaptive icon foreground/background/monochrome (432x432); a splash mark; phone screenshots | `mobile/assets/` (replace the Expo placeholders, same file names) |
| 10 | **Privacy labels**: Apple App Privacy answers and the Play Data safety form (draft below), and the confirmed wording of the permission texts (iOS contacts text below) | App Store Connect, Play Console, `app.config.ts` |
| 11 | A **Play Console service account** JSON with release rights (for `eas submit`) | `mobile/secrets/play-service-account.json` (git-ignored) or EAS |

Pricing, positioning and the listing copy are the founder's (AGENTS.md: no
new price, plan or positioning string without asking). The app contains
none.

## 2. One-time setup (about an hour once the accounts exist)

All commands from `echowave/mobile/`.

```bash
npm ci
npx eas-cli@latest login                 # the company's Expo account
npx eas-cli@latest init                  # creates the EAS project; prints its id
```

1. Put the project id where the build reads it:
   `npx eas-cli@latest env:create --name EAS_PROJECT_ID --value <id> --environment production --environment preview --environment development`
   (Expo push tokens are issued per project; without it push says "unsupported".)
2. If the name or bundle id differ from the defaults, change
   `DECIBYL_APP_NAME`, `DECIBYL_IOS_BUNDLE_ID` and `DECIBYL_ANDROID_PACKAGE`
   in `eas.json` (`build.base.env`) and the two files in
   `universal-links/`. Nothing else needs to change.
3. **iOS credentials.** `npx eas-cli@latest credentials -p ios`, signed in
   with the Apple account (Admin or App Manager):
   - let EAS create the distribution certificate and provisioning profiles
     for the app **and** the share extension (`ai.decibyl.app.ShareExtension`);
     EAS registers the app group `group.ai.decibyl.app` and the Associated
     Domains capability from `app.config.ts`;
   - *Push Notifications* -> upload the APNs `.p8` with its Key ID (EAS
     attaches it to the project; Expo's push service uses it).
4. **Android push.** In Firebase, add an Android app with package
   `ai.decibyl.app`, download `google-services.json`, then
   `npx eas-cli@latest env:create --name GOOGLE_SERVICES_JSON --type file --value ./google-services.json --environment production --environment preview`.
   Create a Google service-account key with the *Firebase Cloud Messaging
   API Admin* role and upload it: `npx eas-cli@latest credentials -p android`
   -> *Google Service Account* -> *Push Notifications (FCM V1)*.
5. **App Store Connect id.** Copy the app's Apple ID (a number) from App
   Store Connect -> App Information into `eas.json`
   (`submit.production.ios.ascAppId`).
6. **Universal links / App Links** (a link to app.decibyl.ai opens the app):
   - `universal-links/apple-app-site-association`: replace `APPLE_TEAM_ID`;
   - `universal-links/assetlinks.json`: put the Play App Signing SHA-256
     (Play Console -> Setup -> App signing) and the EAS upload key SHA-256
     (`eas credentials -p android`);
   - serve both at `https://app.decibyl.ai/.well-known/` with
     `Content-Type: application/json` and no redirect (for example as
     `ui/public/.well-known/` files). Until they are served, `decibyl://`
     links and push still open the right screen; only https links open the
     browser instead of the app.
7. **Server.** Turn on `mobile_push` on staging first
   (`MOBILE_PUSH_ENABLED=true`, or per workspace from the staff console),
   with `identity_notifications` on. `EXPO_ACCESS_TOKEN` is needed only if
   "enhanced push security" is turned on for the Expo project.

## 3. TestFlight (iOS)

```bash
npx eas-cli@latest build -p ios --profile production      # signed .ipa, build number auto-incremented
npx eas-cli@latest submit -p ios --profile production --latest
```

Then in App Store Connect -> TestFlight: answer the export-compliance
question (the app uses only standard HTTPS; `usesNonExemptEncryption` is
false), add internal testers (up to 100 App Store Connect users, no review),
and they install from the TestFlight app. External testers need a short Beta
App Review and the test information (a demo account; sign-up is
invite-only, so give the reviewer an invite code).

For testing against staging instead, build the `preview` profile
(`EXPO_PUBLIC_API_URL=https://staging.decibyl.ai`) and share it through
EAS internal distribution (`eas device:create` registers each tester's
iPhone), or submit a `preview` build to TestFlight the same way.

## 4. Play internal testing (Android)

1. In the Play Console create the app (name, default language, app, free),
   and complete *App content*: privacy policy URL, ads (none), app access
   (a demo account and an invite code), content rating, target audience,
   news app (no), **Data safety** (draft below).
2. Build:
   ```bash
   npx eas-cli@latest build -p android --profile production   # signed .aab
   ```
3. **The first upload is manual** (Google requires it before the API can
   submit): download the `.aab` from the EAS build page, Play Console ->
   Testing -> Internal testing -> Create release -> upload, accept Play App
   Signing, add release notes, roll out. Add testers (an email list) and
   share the opt-in link.
4. From then on:
   ```bash
   npx eas-cli@latest submit -p android --profile production --latest   # track: internal, status: draft
   ```
   and roll the draft out to internal testing in the console.

## 5. Store words to confirm

**iOS contacts permission text** (shown by iOS after the in-app consent
screen; in `app.config.ts`):

> Decibyl reads the names, phone numbers and email addresses in your
> contacts, after you agree in the app, so it can tell you who someone is
> and keep a short brief on each person. You can stop at any time in
> Settings.

The other permission texts (microphone, camera, photos, Face ID) are in the
same file.

**Draft privacy answers** (to confirm against the final People server and
the privacy notice; `compliance/PRIVACY-NOTICE-FACTS.md` has the facts):

| Data | Collected | Why | Linked to the person | Shared with third parties |
| --- | --- | --- | --- | --- |
| Name, email address | Yes (account) | App functionality, account | Yes | No |
| Messages and files the person sends | Yes | App functionality | Yes | Model and transcription providers process them (sub-processors, `GET /api/v1/privacy/subprocessors`) |
| Voice notes and live voice audio | Yes, when used | App functionality (transcription, conversation) | Yes | Speech providers (sub-processors) |
| Photos and files the person picks | Only the ones they send | App functionality | Yes | As messages |
| Contacts (names, phone numbers, emails) | **Today: no** -- kept on the phone (`DevicePeopleApi`). Once the People server is on: yes, only after in-app consent | App functionality (People) | Yes | No |
| Device push token | Yes, when push is on | Notifications | Yes | Expo, Apple, Google (delivery only) |
| Crash and diagnostics | Not in this app version | -- | -- | -- |

Data is encrypted in transit; people can request deletion in the app
(Settings -> Privacy -> Delete my data) and download their data there.

## 6. Every release after the first

1. Bump `version` in `app.config.ts` when the store version should change
   (build numbers increment automatically).
2. `eas build -p all --profile production`, then `eas submit` for each.
3. JavaScript-only fixes can later ship as EAS Updates on the `production`
   channel (the builds already carry `runtimeVersion: appVersion`); that
   needs `expo-updates` added first, which is not in this version.
