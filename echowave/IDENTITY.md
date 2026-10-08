# Identity: connections, channels, Decibyl identity and push (stream `identity`)

Phase 2 of `LAUNCH-PLAN.md`. Handoff sections 7, 15 D, 25, 31.7; screens 21
(with `settings`), 22, 23, 24. Builds on `controls` (action cards, task
ledger, outcome unknown, event catalogue, member preferences) and `shell`
(ConnectionRow, ActionPreview, SettingsSection, SaveBar, ErrorState).

## Switches

Everything here is off by default and each part honours per-workspace
overrides from the staff console. Off, its routes are 404s, its Settings
sections are not listed, and nothing is written.

| Flag | Constant | Turns on |
| --- | --- | --- |
| `identity_connections` | `IDENTITY_CONNECTIONS_ENABLED` | Settings -> Connections (screen 22): apps and channels per person, consent, disconnect cards, channel capability from verified traffic |
| `identity_email` | `IDENTITY_EMAIL_ENABLED` | Settings -> Decibyl identity -> Email (screen 23), the signed inbound and events webhooks, the virtual card interest row |
| `identity_phone` | `IDENTITY_PHONE_ENABLED` | Settings -> Decibyl identity -> Phone (screen 24) |
| `identity_notifications` | `IDENTITY_NOTIFICATIONS_ENABLED` | Settings -> Notifications (screen 21), web push, the manifest link |
| `identity_reconciliation` | `IDENTITY_RECONCILIATION_ENABLED` | Per-provider settling of `outcome_unknown` cards (ARQ, every 5 minutes); the card key sent to WhatsApp for its status webhooks |

**Not behind a switch:** an unknown WhatsApp sender is a stranger. A number
a workspace verified for test calls used to be answered as that
workspace's first member (their Gmail, memory and cards). Now any unlinked
sender gets the same "link me first" reply as every other channel, and
nothing they send is filed or handed to Decibyl
(`services/messaging/whatsapp_inbound.handle`).

## Cards private to their owner

Disconnecting an app, sending from a Decibyl address and requesting a
number send, delete or pay, so each is a controls action card
(`services/identity/cards.py`, kinds `disconnect_app`,
`send_identity_email`, `request_number`): exact preview, Confirm bound to
the payload version, run once, `outcome_unknown` on a lost answer.

* They are proposed only from Settings. The model's `propose_action`
  arguments reach `actions.resolve` unfiltered, so these kinds are refused
  unless a context variable set by `cards.propose` is present.
* They carry `private_to`, are written with the new
  `AgentEventVisibility.PRIVATE` (never returned by the shared timeline,
  which reads `always` and `on_request` only), and the lines under them
  are private too. `actions.settle` and `actions.revise` refuse anyone
  but the owner -- colleague or admin -- in the words used for a wrong
  tenant. The card is shown and approved on the page that asked for it.

## Connections (screen 22)

`services/identity/connections.py`, `routes/identity.py` `/me/connections*`.

* **Scope.** A person sees their own Composio tenant
  (`decibyl_org_{org}_user_{uid}`, with `connections_per_person` on) and
  the workspace's (`decibyl_org_{org}`). A colleague's tenant is never
  asked; `accounts_with_status` also drops any row whose `user_id` is not
  the tenant asked for.
* **States** (handoff 25): disconnected, authorizing, syncing, ready,
  limited, expired, error, revoked -- from Composio's account status and
  our consent record. Composio unconfigured is `needs_setup`; a failed
  listing is `error` with whatever could be read, never an empty list.
* **Consent before the sign-in.** `GET /me/connections/preview` returns the
  access lines; `POST /me/connections/start` stores them with the purpose
  and an in-app `return_to` (an outside address is dropped), emits
  `connection_started`, then mints the provider link. `complete` reads
  the provider: ready only when Composio says ACTIVE (`connection_ready`).
  A consent finished from another workspace is refused
  (`wrong_workspace`); another person's is not found.
* **Disconnect** is a card that deletes the account at Composio after
  checking it is in the caller's tenant, marks the consent revoked, drops
  the member-connection row and emits `connection_revoked`. Future tool
  use stops: `members.resolve` finds nothing. A refusal leaves it
  connected and says so; a lost answer is `outcome_unknown`.
* **Last success** is stamped by `connected_tools.execute` on each
  successful call.
* **Channels.** WhatsApp, Telegram, Slack, Teams: `disabled_by_policy`
  when the workspace's switch is off, `needs_setup` when keys are missing
  *or* no signature-verified message has arrived in 30 days
  (`channel_checks`, written by every inbound path after its own
  signature check), `available` otherwise. Each row says what the
  platform allows proactively and that messaging Decibyl is not access to
  other messages. Linking stays on the existing `/channel-links` flow
  (`DecibylAppsSection`).

## Decibyl email identity (screen 23)

`services/identity/email_identity.py`.

| State | Meaning |
| --- | --- |
| unallocated | No address |
| (checking) | `POST /me/email-identity/check` -- never stored |
| reserved | Name held (needs a verified account email) |
| provisioning | A check message was sent to the address |
| active | The check message arrived through the signed webhook |
| delivery_issue | A send bounced |
| suspended | A send drew a complaint; sending paused |
| released | A reservation given up before it was ever active |

* Names: 3-32 of `a-z0-9.-`; a reserved list (postmaster, support,
  decibyl...); collisions say "taken" and never whose. Two requests for
  one name: a partial unique index decides. One live address per person.
  A name once held is never given to anyone else.
* Inbound: `POST /public/email-identity/inbound` with `{recipient,
  raw_base64}` (raw MIME), HMAC-SHA256 over `timestamp.body` with
  `EMAIL_IDENTITY_WEBHOOK_SECRET`, five-minute window. Size limit
  (`EMAIL_IDENTITY_MAX_INBOUND_BYTES`), Message-ID dedupe, thread key from
  References / In-Reply-To, attachments listed with a **file-type check
  only (not an antivirus scan)** and dangerous types blocked. Mail for an
  address nobody owns is quarantined and shown to nobody. The owner is
  notified (topic `mail`).
* Outbound: a card, only from the owner's own active address, only when
  `EMAIL_IDENTITY_OUTBOUND_VERIFIED` says SPF/DKIM/DMARC are done. The send
  row is written before SMTP; a refusal fails the card, anything else is
  `outcome_unknown`. Events webhook (`delivered`, `bounce`, `complaint`).
* Not a mailbox: no IMAP, and the screen says so.
* Virtual card: a "coming soon" row with `PUT /me/card-interest`
  (yes/no only). No issuance, no card details.

## Phone and verification (screen 24)

`services/identity/phone.py`. One lifecycle derived from what exists:
not_requested, pending_verification, rejected (with the carrier's or
reviewer's reason as the next step), eligible, provisioning (a number with
no recorded test), active (incoming call **and** handover recorded by an
admin, audited), suspended, released. Verification, numbers, autopay and
helpers are read separately; a failed one is named and is never "none";
a request needs the helper list to have loaded. Chat never waits on any of
it (`chat_needs_number: false`).

**Number payment flow.** Five plain steps (rented monthly from a carrier;
verification first; cost and payer shown on the card; rent until release;
release stops rent). Who pays is open: until `NUMBER_PAYMENT_POLICY` is
`sponsored` or `provider_autopay`, requesting is unavailable with that
reason. With `provider_autopay`, an authorised mandate is required. A
request is an admin's card that calls the existing
`telephony.provisioning.provision` with the helper assigned.

## Notifications and web push (screen 21)

`services/identity/notifications.py`, `services/identity/push.py`,
`ui/public/sw.js`, `ui/src/lib/webPush.ts`.

* Preferences per person (`notification_preferences`, revisioned like
  member preferences; a stale save is 409). Channels: push, email, the
  linked channel. The in-app bell is workspace-wide, so personal notices
  are not put there and the screen says why.
* Topics: reminders (requested; keep their time through quiet hours),
  approvals, task updates, mail, suggestions (off until chosen, capped by
  `NOTIFY_SUGGESTION_DAILY_CAP`). Snooze up to 30 days.
* `notify()` claims one row per (person, dedupe key, channel) before
  sending, so a retried job notifies once; keeps lock-screen text generic
  while private previews are on (default).
* Push: VAPID keys from the operator (`VAPID_PUBLIC_KEY`,
  `VAPID_PRIVATE_KEY`; missing is "needs setup"). Subscriptions only to
  browsers' push services (an allowlist, so the server cannot be made to
  call an arbitrary address). 404/410 from the push service marks the
  device revoked. The browser is asked only after "Enable push"; the
  service worker is registered only then; the manifest is linked only
  while the flag is on (iPhones need the site on the Home Screen).
* Wired producers: mail at the Decibyl address; reconciliation results
  and "did it arrive?". Today's brief and reminders call
  `notifications.notify` from stream `today`.

## Reconciliation of unknown outcomes

`services/identity/reconcile.py`, `tasks/identity.py`
(`reconcile_unknown_outcomes`, minute 3 of every 5). Only ever from
`outcome_unknown`; never sends again.

| Provider | Evidence |
| --- | --- |
| `identity_email` | Our send record (accepted / delivered / bounced / failed), else never sent |
| `whatsapp` | Meta status webhooks matched on `biz_opaque_callback_data` = the card's key (sent only while this flag is on) |
| `composio_accounts` (disconnect) | Whether the account is still listed |
| `carrier` (number) | Whether the number is on the account with a carrier id |
| anything else | After 30 minutes the approver is asked "Did it arrive?"; `POST /me/outcomes/{id}` settles it as theirs |

Looks back 7 days; a card handed to a person with no provider to ask is
not re-read.

## Migration

`20261008identity` (revises `202610071500shell`): eleven new tables,
nothing changed in an existing one -- `connection_consents`,
`channel_checks`, `email_identities`, `email_identity_messages`,
`email_identity_sends`, `card_interest`, `notification_preferences`,
`push_subscriptions`, `notification_deliveries`, `delivery_receipts`,
`number_readiness`. `alembic downgrade 202610071500shell` drops them.

## Needs keys or a human

* Composio API key (connections show "needs setup" without it).
* Inbound mail provider posting raw MIME to the webhook with
  `EMAIL_IDENTITY_WEBHOOK_SECRET`; MX for the domain; SPF/DKIM/DMARC then
  `EMAIL_IDENTITY_OUTBOUND_VERIFIED=true`; SMTP for the check message.
* VAPID keys for push; testing on real Android, desktop and an iPhone with
  the site on the Home Screen.
* Founder: who pays for numbers (`NUMBER_PAYMENT_POLICY`), the monthly
  amount and sponsorship terms (placeholders), the suggestion cap.
* WhatsApp, Telegram, Slack, Teams app registrations; a channel turns
  "available" only after its first verified message.

## Rollback

Turn the flag off; the previous behaviour returns at once (routes 404,
sections hidden, nothing written, no card key sent to WhatsApp, the sweep
does nothing). Rows stay. The stranger fix stays on by design. Schema:
`alembic downgrade 202610071500shell`.
