# Decibyl for Windows and macOS

An Electron app that opens the Decibyl web app in its own window and adds what
a browser tab cannot:

- a **tray / menu-bar icon** (closing the window keeps Decibyl running there);
- **native notifications**, bridged from the web app's bell;
- a **global shortcut** (default `Ctrl/Cmd+Shift+Space`) for a quick ask window;
- **start at login**, off until the person turns it on;
- **`decibyl://` links** (`decibyl://chat/123`, `decibyl://today`, `decibyl://ask?q=...`);
- **auto-update** through `electron-updater`, stable or beta channel;
- **files from disk**: attach files or a whole folder, and an optional watched
  folder (for example, invoices) that the person picks;
- **"work on my computer"**: Claude's computer use, running on the person's
  machine, in the apps they allow, asking in Decibyl before anything that
  sends, pays, deletes or submits.

Everything new is behind two flags, both off by default
(`api/services/features.py`):

| Flag                   | Turns on                                                                |
| ---------------------- | ----------------------------------------------------------------------- |
| `desktop_app`          | Native notifications from the bell, attach a folder, the watched folder |
| `desktop_computer_use` | "Work on my computer" and the `/api/v1/desktop` routes (404 while off)  |

The app itself works with both off: it is then the web app in a window, with
the tray, shortcut, links and updates.

## Layout

```
desktop/
├── src/main/          main process: windows, tray, shortcut, links, updates, IPC
│   ├── main.ts        wiring (the only file that needs Electron to run)
│   ├── ipc.ts         the bridge: origin check per call, payload validation
│   ├── channels.ts    every IPC channel, who may call it, what it does
│   ├── computer.ts    one computer-use task at a time; Stop; the receipt
│   ├── config.ts      environments and settings (sanitized on every write)
│   ├── deeplink.ts    decibyl:// parsing (navigate only, never act)
│   ├── files.ts       attach files/folders; the watched folder
│   └── updater.ts     electron-updater
├── src/preload/       window.decibylDesktop (the whole surface a page gets)
├── src/computer-use/  the agent loop, policy, limits, receipt, model, drivers
├── src/renderer/      the app's own small pages: quick ask, working bar, settings
├── test/              vitest, with a fake computer-use driver
├── build/             icons, macOS entitlements
├── scripts/           package.mjs (signing-aware packaging), copy-renderer.mjs
└── electron-builder.yml
```

## Run in development

```bash
cd echowave/desktop
npm install          # downloads Electron; .npmrc sets legacy-peer-deps
npm test             # vitest: 70 tests, no Electron or display needed
npm run typecheck
npm run dev          # builds, then opens http://localhost:3000 (the ui dev server)
```

Which Decibyl opens, in order of precedence:

1. `DECIBYL_URL=https://...` (any https URL, or http on localhost);
2. `--env=production|staging|local` on the command line, or `DECIBYL_ENV`;
3. the choice in the app's Settings window (Decibyl, Staging, Local, another address).

| Name       | URL                        |
| ---------- | -------------------------- |
| production | https://app.decibyl.ai     |
| staging    | https://staging.decibyl.ai |
| local      | http://localhost:3000      |

To try "work on my computer" locally: run the api with
`DESKTOP_COMPUTER_USE_ENABLED=true` (and `DESKTOP_APP_ENABLED=true` for files),
sign in inside the app, open Settings from the tray, add the apps Decibyl may
use and paste a model key, then either tick "Work on my computer" in the quick
ask window or press the monitor button in Decibyl's composer.

## Build packages

```bash
npm run pack:win       # NSIS installer (x64 + arm64). Windows, or Linux with Wine 32+64-bit
npm run pack:mac       # .dmg + .zip (arm64 + x64). Needs a Mac
npm run pack:mac-zip   # .zip only. Runs on Linux too (unsigned)
npm run pack:dir       # unpacked Linux build, smoke test only (not shipped)
```

Output goes to `release/`. Without certificates the script says it is building
**unsigned** and carries on; nothing secret is read from the repository.

What a Linux container can and cannot build (checked while writing this):

| Package                                       | In a Linux container                                                                             |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Windows installer, unsigned                   | Yes, with `wine64` and `wine32:i386` installed                                                   |
| Windows installer, signed                     | Yes with a `.pfx` (`WIN_CSC_LINK`); an EV/HSM certificate needs Windows or Azure Trusted Signing |
| macOS `.zip`, unsigned                        | Yes (`pack:mac-zip`)                                                                             |
| macOS `.dmg`, or anything signed or notarised | **No.** `hdiutil`, `sips`, `codesign` and `notarytool` exist only on macOS: use a macOS runner   |

`.github/workflows/desktop.yml` runs the tests on every change to `desktop/`
and, when started by hand, builds the Windows installer on `windows-latest` and
the macOS packages on `macos-latest`, signed when the secrets below are set.

## Signing: what the founder must provide

Nothing below is in the repository. Each value goes into the CI secrets (or the
shell of whoever builds a release) under exactly these names.

### macOS: Apple Developer ID + notarisation

1. An **Apple Developer Program** membership for the company (USD 99/year), so
   the app is signed as Decibyl and not as a person.
2. A **Developer ID Application** certificate, exported from Keychain as a
   `.p12` with a password:
    - `CSC_LINK`: the `.p12`, base64-encoded (or an https URL to it);
    - `CSC_KEY_PASSWORD`: its password.
3. **Notarisation** credentials, one of:
    - an App Store Connect API key (preferred): `APPLE_API_KEY` (path to the
      `.p8` file, written from a secret at build time), `APPLE_API_KEY_ID`,
      `APPLE_API_ISSUER`; or
    - `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`.

Without 2 the package is unsigned and Gatekeeper blocks it on other Macs.
With 2 but not 3 it is signed but not notarised, which macOS also blocks.

### Windows: a code-signing certificate

One of:

- an **OV or EV code-signing certificate** from a CA (DigiCert, Sectigo, ...).
  Since 2023 these keys must live on a hardware token or cloud HSM, so in
  practice: the CA's cloud signing service, or
- **Azure Trusted Signing** (cheapest for a small company): an Azure
  subscription, a Trusted Signing account and certificate profile, and
  `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` plus the
  endpoint, account and profile names (add `win.azureSignOptions` to
  `electron-builder.yml` when the account exists).

For a `.pfx` that can be exported: `WIN_CSC_LINK` (base64 or URL) and
`WIN_CSC_KEY_PASSWORD`. Unsigned installers work, but SmartScreen warns
until the certificate has built reputation.

### Updates

- `DECIBYL_UPDATE_URL`: where packages and `latest.yml` / `latest-mac.yml`
  are uploaded (for example `https://downloads.decibyl.ai/desktop`, an S3
  bucket behind CloudFront). Required to publish; packaging without it uses
  that default so the built app knows where to look.
- Upload credentials for that bucket in the release job (for example
  `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, if `publish` is switched to
  the `s3` provider).

## Release

1. Bump `version` in `package.json`.
2. On a Mac with the Apple secrets: `npm run release:mac`.
   On Windows (or CI) with the Windows certificate: `npm run release:win`.
   Both upload to `DECIBYL_UPDATE_URL`.
3. Installed apps check on start and every six hours, download in the
   background, and install when the person quits Decibyl. A beta build
   (`0.2.0-beta.1`) reaches only people who chose the beta channel.
4. To hold a release back, do not upload its `latest*.yml`; to roll back,
   upload the previous version's files with a higher version number.
   `DECIBYL_DISABLE_UPDATES=1` turns updating off on managed computers.

## The safety model of "work on my computer"

**Where it runs.** The agent loop runs in this app, on the person's computer.
Screenshots and actions never leave the machine except in the model call,
which goes straight from the computer to the Claude API (or to a relay named in
settings that forwards and keeps nothing). The Decibyl backend never receives
a screenshot: it receives the text of a step that needs approval, and the
receipt. Screenshots are held in memory for the model call only; they are not
written to disk, not in events, not in the receipt.

**The model call.** Claude's computer toolset (`computer_toolset_20260801`):
one `tools` entry with no name and no display size; each action comes back as
a `tool_use` named after the action, with `toolset_name: "computer"`, and every
result echoes it. Screenshots are scaled to at most 1920 px on the long edge
and coordinates scaled back. Adaptive thinking, effort `high`, prompt caching,
and server-side fallback on a safety refusal; a refusal that stands ends the
task and says so.

**Before every action**, in this order:

1. **Stop pressed?** Nothing more runs. Stop is on the always-visible bar
   (kept out of screenshots), in the tray, and `Ctrl/Cmd+Shift+.`. It aborts
   the model call in flight, marks the rest of a batch "not executed", and
   takes a waiting approval card off the thread.
2. **Limits.** Per task: actions (default 40, at most 200), minutes of work
   (default 10, at most 60; time waiting for a person is not counted) and
   model cost (default US$2, at most US$20, from the usage each response
   reports). Hitting one ends the task and the receipt says which.
3. **Never touch.** System security dialogs (macOS authentication sheets,
   Keychain, System Settings; Windows UAC, credential prompts, Windows
   Security), password managers (1Password, Bitwarden, LastPass, KeePass...)
   and Decibyl itself. No screenshot is taken while one is in front and no key
   goes to it, whatever the person's list says; they cannot be added to it.
4. **Only the apps the person picked.** Default deny. The model is told the
   name of an app it may not use, and the bar shows it, so a refusal is
   visible, never silent. `open_app` switches only between listed apps.
5. **Passwords.** Typing needs a focused field the OS says is _not_ a
   password field (macOS `AXSecureTextField`, Windows UI Automation
   `IsPassword`). If the OS will not say, Decibyl does not type. In a
   password field only Tab and Escape go through, to move away.
6. **Send, pay, delete, submit.** The model must call `request_approval` with
   the exact detail before such a step, and the app also catches what it
   recognises on its own: a click on a control labelled Send / Pay / Delete /
   Submit / Place order (Windows reads labels through UI Automation), Enter in
   a single-line field, Cmd/Ctrl+Enter, Delete in Finder or Explorer, text that
   ends in a newline. The step is held, and a card appears in the person's
   Decibyl thread: one sentence, the exact detail, which app, "once". It is the
   same contract as every card in `api/services/workflow/actions.py`: Confirm,
   the undo window, then **released** (not done). The app claims it with a
   compare-and-swap that checks the SHA-256 fingerprint of the exact step
   (app, action, real-screen coordinates, text); a second claim, a retried
   request or a step that differs at all gets nothing. The rest of that batch
   is not run, because it was planned for the screen before. Only the person
   whose computer it is can answer the card.

    With the task ledger on (stream `controls`), the card is bound to a
    payload version like every other card: Confirm names the version on
    screen and `actions.run` re-checks it before releasing. A step the
    computer claimed and never reported is swept to `outcome_unknown`, and so
    is one that broke part-way (the app reports "not known" rather than
    "failed"); neither is ever run again. A released step no computer took
    within ten minutes is cancelled with a line saying nothing was done. A
    `send` step spends the confirming person's outbound-messages quota, as
    every send does; the model calls run on the person's own key and use no
    server quota.

**The receipt.** Every task leaves one: each step with its app, what it did,
its outcome and the card it ran under; totals for actions, time, cost and
approvals; and why it ended. Saved on the computer (`receipts/` in the app's
data folder) and posted as text to the person's Decibyl thread.

**What it does not do yet.** On macOS, labels under a click are not read
(hit-testing the accessibility tree needs a native helper), so for clicks the
model's own `request_approval` is the gate there; keys and typing are caught
on both systems. Linux is not supported for computer use.

**Permissions macOS will ask for:** Screen Recording (screenshots),
Accessibility (mouse and keyboard), Automation of System Events (which app is
in front, password fields). Windows asks for nothing extra.

## The IPC bridge

A page gets `window.decibylDesktop` and nothing else (context isolation,
sandbox, no Node). Main checks every call:

- the **web** surface is the configured web origin exactly; any other origin,
  including a page the web app navigated to, gets "This page may not use
  that";
- the **local** surface is the app's own pages from its own folder;
- each channel names the surfaces it serves (the web app cannot change
  settings, the model key or the allowed apps; Stop is open to every page);
- each payload is validated before a handler runs, and a failure is a reply,
  never a crash in main.

## Tests

```bash
npm test
```

| File                          | What it holds                                                                                                                                                                                                          |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test/agent.test.ts`          | the loop with a fake driver: the toolset request, scaling, allowed apps, never-touch, passwords, approval gating and run-once, Stop (before a call, mid-batch, while a card waits), step/time/cost limits, the receipt |
| `test/ipc.test.ts`            | origins, surfaces, payload validation, the watched folder never set by path, Stop from every page                                                                                                                      |
| `test/policy.test.ts`         | never-touch list, default deny, what counts as consequential, the password rule                                                                                                                                        |
| `test/computer.test.ts`       | refusals before starting, the bar, the receipt, Stop                                                                                                                                                                   |
| `test/approvals-http.test.ts` | the card over HTTP: text only, waiting, claim once, timeout                                                                                                                                                            |
| `test/main-modules.test.ts`   | environments, settings sanitizing, `decibyl://`, files and the watched folder                                                                                                                                          |

The backend half is `api/tests/test_desktop_steps.py`; the web app's half is in
`ui/src/lib/__tests__/desktop.test.ts` and the ActionCard and ChannelComposer
tests.

## Rollback

- Turn `desktop_computer_use` off: the routes 404, the app refuses to start a
  task ("not switched on for your workspace"), and the composer button goes.
- Turn `desktop_app` off: no native notifications, no folder attach, the
  watched folder stops.
- The app is self-contained in `desktop/`; the backend change is one internal
  card kind (`desktop_step`) and one router, both inert while the flag is off.
