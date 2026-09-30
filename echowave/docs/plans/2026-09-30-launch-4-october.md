# Launch: 4 October 2026

Decided by the founder on the evening of 30 September. Four days. Everything on the self-serve gate is wanted; here is how that becomes true rather than late.

## The rule

Every story is built on its own branch by its own builder session, in parallel, and merged **behind its flag**. Harsha verifies on a scratch workspace and writes the result on the issue. A flag turns on the morning of 4 October only with a passing check on the issue. Anything not green by 20:00 on 3 October **ships dark**, is listed on the LAUNCH epic with its turn-on date, and the launch proceeds. Nothing reaches an invited user without a check.

## What an invited user sees on 4 October, at minimum

Invite code → verify → the Start screen with one Build card → Hear it → a bot on a number (from a pre-provisioned pool, see W7) or on web chat → the My Decibyl thread with cards and grant scopes → the trial meter → Settings with the rate card and their own keys. Guardrails on the bot page. Projects if W5 lands. WhatsApp if the Meta prerequisites are met (W7); otherwise "coming this month" on the card.

## Workstreams, owners, and what "done" means for each

| W | Story | Board | Builder | Done on 4 Oct means |
|---|---|---|---|---|
| 1 | Rotate the two exposed keys | KAN-233 | Nithish | Old keys refused |
| 1 | Security fixes (TLS, error masking, WS token) | KAN-245 | Claude | Merged, on by default |
| 1 | Images off the production box | KAN-241 | Claude | Only if green by 2 Oct; otherwise after launch, and no deploys during business hours on 4 Oct |
| 1 | Monitoring and on-call | LAUNCH-5 | Nithish + Claude | Sentry alerts to a phone, uptime check on /health, one named person on call each day 4–6 Oct |
| 2 | UI-0 cut and rename, tokens | KAN-257 | Claude | Merged; shell on for all; dead code gone |
| 2 | UI-1 seven homes, Projects in rail | KAN-208 | Claude | Behind `shell`; redirects table test green |
| 2 | UI-4 faces | KAN-260 | Claude | Component in roster and header |
| 3 | UI-2 bot page, ten sections | KAN-209 | Claude | Behind flag; Chat/Canvas; Publish with diff |
| 3 | VOICE-1 number flow + corpus | KAN-261 | Claude + Harsha | Flow from the bot page; corpus run on the default stack; live test call answered |
| 3 | CH-0 WhatsApp connect | KAN-258 | Claude | Flow built; on only if W7 prerequisites hold |
| 4 | PLAN-1 trial replaces Free | KAN-255 | Claude | Invited accounts land on `trial`; zero pauses with a card |
| 4 | BYOK-1 keys on text paths | KAN-254 | Claude | Decibyl turn on the account's key; card on failure |
| 4 | POL-2 grant scopes, pause, retire | KAN-259 | Claude | Scopes on cards; ask-first wins; audit row with rule id |
| 4 | OUT-1 per-bot budget + outcome definition | KAN-253 | Claude | Budget on the bot page; stop pauses |
| 5 | PRJ-1 Projects | KAN-266 | Claude | Brief, files, members, checklist on the channel |
| 5 | MKT-1 publish from the superadmin org | KAN-256 | Claude | One pack published from the UI, hireable |
| 5 | ORG-1 light: mission line, goal per bot | KAN-219 | Claude | Mission in Team and Tasks subtitle |
| 6 | Verification pass of the Testing set | KAN-222, KAN-223 | Harsha | Each issue closed or reopened with evidence |
| 6 | E2E journeys | KAN-240 | Claude | Signup → bot → Try it → top-up journey green in CI; the rest after |
| 7 | Invites: waitlist, codes, first 20 names | LAUNCH-1 | Nithish | Codes issued; emails ready |
| 7 | Number pool: 10 numbers on the platform account, KYC cleared | LAUNCH-2 | Nithish | A trial account can be given a number without its own KYC |
| 7 | WhatsApp prerequisites: Meta business verification, BSP/Tech Provider status, 3 pre-approved templates | LAUNCH-3 | Nithish | Or CH-0 stays dark with a date |
| 7 | Support and legal: working support link (Chatwoot DNS or a WhatsApp number), privacy notice, AUP, recording disclosure copy | LAUNCH-4 | Nithish | Links resolve; copy in place |
| 7 | Minimal staging box | KAN-204 | Nithish | A second compose stack answering /health, for Harsha's pass |

## Day plan

- **1 Oct (T-3):** every branch open with a draft PR; W7 started; first merges: KAN-245, KAN-255, KAN-257. Flags for the launch cohort listed on the LAUNCH epic.
- **2 Oct (T-2):** UI-1, UI-2, UI-4, VOICE-1 flow, POL-2, BYOK-1, OUT-1, PRJ-1, MKT-1, ORG-1 light merged behind flags; deploy by 18:00; Harsha starts the Testing pass on staging or a scratch workspace.
- **3 Oct (T-1):** fixes only, no new scope; each story's live check written on its issue by 20:00; the flag list finalised; dark list written.
- **4 Oct (T-0), 08:00:** flags on; smoke by Harsha on a fresh invited account (the first-ten-minutes journey, timed); invites go out; on-call all day; no deploys after 10:00 unless a blocker.

## What I am not promising

That all fifteen stories are verified by 3 October. That WhatsApp is on if Meta's verification is not done. That images are built off the box before launch. The launch is still a launch if the dark list is short and honest.
