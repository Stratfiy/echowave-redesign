# Managed Telephony / Plivo Subaccount — Audit

Read-only audit. No code was changed. Line references are against `claude/pricing-correctness` at `e874a78`.

**Headline:** managed telephony does not exist as a product path. What exists is a KYC document-collection flow with a manual carrier step, and a phone-number table that records numbers a human typed in. There is no provisioning, no subaccount, no release, no reconciliation, and — the expensive one — **no recurring billing of any kind**. A customer who rents a number and never calls is billed ₹0 while we pay the carrier every month.

Also worth saying plainly before the detail: **nothing can put an organization onto the managed path today.** `is_platform_managed` has exactly one writer (`db_client.set_platform_managed`, `api/db/telephony_configuration_client.py:222`) and **zero callers** — no route, no admin UI, no script. The only way to flip it is a hand-written SQL `UPDATE`.

---

## 1. What `MANAGED_TELEPHONY_ENABLED` actually gates

Defined at `api/constants.py:70`, default `false`. Every consumer in the codebase:

| Location | What it does |
|---|---|
| `api/routes/kyc.py:58` | Sets `verification_open` in the KYC GET response, so the UI shows "coming soon" instead of an upload form |
| `api/services/kyc/service.py:204` | `submit()` raises `ValueError` — refuses to accept a KYC submission |

That is the entire blast radius. It is a **door on the document-upload flow**, nothing more.

It does **not** gate:
- number provisioning (none exists to gate)
- `is_platform_managed` on a telephony configuration
- outbound or inbound calling
- bring-your-own-carrier configuration, which works today regardless

The calling gate is a separate, independent mechanism: `kyc_service.assert_configuration_may_place_calls()` fires only when `configuration.is_platform_managed` is true, wired at `api/routes/telephony.py:140` and `api/routes/campaign.py:572`. Since nothing ever sets that flag, **this gate is currently dead code in production** — correct, tested (`test_kyc_gate.py`), and unreachable.

### Plivo subaccount code

There is none. Grepping `subaccount|sub_account|create_subaccount` across the repo returns **two comments and no code**:

- `api/constants.py:62` — a comment explaining the ISV model the flag anticipates
- `api/services/kyc/carrier.py:82` — a docstring noting Plivo supports subaccounts per end customer

`PlivoCarrier.submit()` and `PlivoCarrier.check()` (`carrier.py:91`, `:106`) both `raise CarrierNotConfigured` with an honest message saying the API shape has not been confirmed. `DEFAULT_CARRIER = ManualCarrier.name` (`carrier.py:154`).

- **Can we create a subaccount per organization today?** No.
- **Is `subaccount_auth_id` stored on the org?** No. There is no such column anywhere. The nearest home is the `credentials` JSON on `telephony_configurations`, which already carries `auth_id` / `auth_token` / `application_id` for the BYO case (`api/services/telephony/providers/plivo/config.py`) — a subaccount's credential pair would fit there without a migration, but nothing writes it.

---

## 2. Number lifecycle

| Item | Status | Evidence |
|---|---|---|
| Search available Plivo India numbers by city/type | **ABSENT** | No call to Plivo's `/PhoneNumber/` or `/AvailablePhoneNumber/` endpoints exists anywhere in `api/` |
| Purchase a number and assign it to an org | **ABSENT** | `POST /telephony-configs/{id}/phone-numbers` (`api/routes/organization.py:830`) records an address a human typed. It never contacts the carrier to acquire anything |
| Bind number to the right Plivo Application | **PARTIAL** | `configure_inbound` (`plivo/provider.py:413`) POSTs `answer_url` onto `self.application_id`. Its own docstring, lines 422-425: *"Linking the number to `self.application_id` (in the Plivo console, or via the Account Phone Number API) is the operator's responsibility — we only update the application's webhook here."* So the app gets the right URL; the number→app link is a manual console step |
| List an org's numbers in the dashboard | **BUILT** | `GET /telephony-configs/{id}/phone-numbers` (`organization.py:815`), UI at `ui/src/app/telephony-configurations/[configId]` |
| Release a number on churn / downgrade | **ABSENT** | `DELETE .../phone-numbers/{id}` (`organization.py:992`) deletes the DB row. No carrier call. There is no `release_number` on the provider protocol (`api/services/telephony/base.py`) |
| Provisioning fails halfway | **N/A → but adjacent bug** | Nothing provisions, so nothing half-fails. However `create_phone_number` commits the row **first**, then calls `_sync_inbound_for_phone_number` (`organization.py:900-903`), which returns `provider_sync.ok=false` on failure and **does not roll back**. The number sits in the DB looking configured while inbound is dead. Same pattern on update (`:963-968`) |

---

## 3. Inbound routing

**BUILT, and it's the strongest part of this subsystem.** Fully DB-backed, nothing hardcoded or env-driven.

The path, at `api/routes/telephony.py:746` (`POST /api/v1/telephony/inbound/run`):

1. Detect provider from webhook shape → normalize.
2. `db_client.find_inbound_route_by_account(provider, account_id_field, account_id, to_number)` → returns `(config, phone_row)`. The `account_id` comes from the webhook: for Plivo, `webhook_data["AuthID"] or webhook_data["ParentAuthID"]` (`plivo/provider.py:379`).
3. **Org** = `config.organization_id`. **Workflow** = `phone_row.inbound_workflow_id` (`telephony.py:825`).
4. Verify webhook signature against *that config's* credentials — so a spoofed webhook can't borrow another org's routing.

The `(provider, account_id, address_normalized)` tuple must be globally unique, and that is enforced at write time: `create_phone_number` calls `find_inbound_routing_conflict` and returns **409** if any other config — in this org *or another* — already claims the combination (`organization.py:846-876`). That is a genuine tenant-isolation guard, not decoration.

**No workflow attached:** the call is rejected with `TelephonyError.WORKFLOW_NOT_FOUND` and a `logger.warning` (`telephony.py:816-823`). The caller hears the provider's validation-error response. **Nothing alerts the customer** — a number with no agent attached fails silently from their point of view until someone reads the logs.

**One wiring detail that matters for the subaccount model:** routing keys on the account id we stored in the config's credentials. Under an ISV setup, Plivo sends the *subaccount's* `AuthID`. If a managed config stores the **parent** auth id, no route matches and every inbound call to that org is rejected. This is not a bug today (nothing is managed) but it is the thing that will break on the first managed call if the subaccount credentials aren't stored per-org. The `or ParentAuthID` fallback does not save you — it only fires when `AuthID` is absent.

---

## 4. Recurring billing — the one that loses money

**ABSENT. Completely. There is no recurring charge mechanism of any kind in this codebase.**

I looked for it four ways and found nothing:

- **Cost components** (`api/enums.py:210`): `STT`, `LLM`, `TTS`, `TELEPHONY`, `PLATFORM`. That is the closed set.
- **Rate units** (`api/enums.py:230`): `MINUTE`, `1k_chars`, `1k_tokens`. There is no per-month unit, so a rental cannot be expressed as a rate even if you wanted to.
- **Telephony rates** (`api/services/billing/default_rates.py:361`): Plivo ₹0.60/min, Twilio ₹1.20/min — **carriage only**. The comment there already flags that the tender model's ₹0.25/min assumption is wrong. It says nothing about rental because rental has no representation.
- **ARQ cron jobs** (`api/tasks/arq.py:78`): backup, billing rollup, campaign batch, credit-reservation sweep, data retention, FX refresh, heartbeat, KYC carrier poll, webhook sweep, monthly tax invoices. **None charges a fee.** `issue_monthly_tax_invoices` is monthly but it *documents* charges already in the ledger; it originates nothing.
- **Grep for `subscription|recurring|monthly|rental|plan`**: the only structural hit is `organization_usage_cycles` (`alembic/versions/2159d4ac431a`), which tracks `period_start`, `period_end`, `used_decibyl_tokens` — a **usage quota counter, not a billing cycle**. It charges nothing.

### Answers to the three questions

**If a customer rents a number for 30 days and makes zero calls, do we bill anything?**
**No. ₹0.** Billing is exclusively per-call, written to `CallCostItemModel` when a workflow run completes. Zero calls means zero rows means zero revenue. Meanwhile the carrier invoices us for the DID every month regardless. Every idle managed number is a pure, silent loss, and it compounds: the customer with the worst usage costs us the most in absolute terms relative to what they pay.

**Is number rental recorded as our cost?**
**No.** It appears in no ledger, no cost item, no rate table, no dashboard. This has a second-order effect worth stating: **every margin number the platform reports is overstated for managed numbers**, because a real fixed cost is invisible to the cost engine. You would look at a healthy per-call margin and be losing money on the account.

**Is there a scheduled job charging monthly fees?**
**No.** And to be precise about how far away it is — there is no ledger kind for it, no rate unit that can express it, no charge model, no proration logic, no dunning, and no cron. This is not a gap to patch; it's a subsystem that was never started.

---

## 5. KYC

This is the part that is genuinely built, and built well.

| Item | Status | Evidence |
|---|---|---|
| Document upload for telephony KYC | **BUILT** | `POST /kyc/documents` (`routes/kyc.py:86`). Kinds: `certificate_of_incorporation`, `gst_certificate`, `authorised_signatory_id`, `address_proof`. Per-business-type requirements in `services/kyc/state.py:40`. Object store backed (`services/kyc/documents.py`); storage keys are never returned to the client (`service.py:48`); re-upload replaces rather than duplicates; documents lock once `CARRIER_APPROVED` |
| Code submitting KYC to Plivo's API | **ABSENT** | `PlivoCarrier.submit`/`check` raise `CarrierNotConfigured`. `DEFAULT_CARRIER = "manual"` — staff forward by hand and type the verdict into `POST /kyc-admin/{org}/carrier-verdict` |
| State machine for number/account status | **BUILT (for the account, not the number)** | `services/kyc/state.py:20`. `not_started → submitted → under_review → forwarded → carrier_approved \| carrier_rejected`, with rejection loops back to `submitted`. `may_place_telephony_calls` accepts **only** `CARRIER_APPROVED` — our own staff approval deliberately cannot unblock calling. Admin queue at `/superadmin/verification`. A poll cron exists (`tasks/kyc_carrier_poll.py`) and correctly no-ops while the carrier is `pollable=False` |

**The gap in the state machine:** `CARRIER_APPROVED` is terminal — `_TRANSITIONS[CARRIER_APPROVED] = frozenset()` (`state.py:36`), with a comment acknowledging revocation isn't modelled. There is **no `SUSPENDED` state**. A carrier revocation, a compliance complaint, or a customer who stops paying cannot be moved back to blocked by any code path. It takes a manual DB update. For a government tender where suspension-on-complaint is a plausible requirement, that is a real hole.

Note also: **status lives on the organization, not the number.** There is no per-number lifecycle state (`pending` / `active` / `suspended` / `released`) at all — `telephony_phone_numbers` has only `is_active`, a boolean the customer controls.

---

## 6. Leak check

| Question | Answer |
|---|---|
| Org deleted → numbers released? | **No — and worse than it sounds.** There is no org-deletion route in the codebase at all. The FK is `ON DELETE CASCADE` (`db/models.py:317-322`), so a direct DB delete drops the phone-number rows, leaves the number rented at Plivo forever, and **destroys the only record that we hold it** |
| Stops paying → numbers released? | **No.** `BALANCE_ENFORCEMENT_ENABLED` (default `true`, `constants.py:113`) blocks *new calls* at reservation time. It never touches numbers. A dead account keeps its DID and we keep paying rent indefinitely |
| Customer clicks delete → number released? | **No.** `DELETE /phone-numbers/{id}` removes the row and makes no carrier call. Same evidence-destroying failure mode as org deletion, except a customer can trigger it |
| Reconciliation between Plivo's inventory and our DB | **ABSENT.** Nothing anywhere lists numbers from the carrier. No orphan detection, no drift alert, no report |

**The blunt version:** there are three independent paths (org delete, number delete, churn) that each leave us paying a carrier for a number we have no record of, and zero mechanisms that would ever surface it. You would discover it by reading a Plivo invoice line by line.

---

## Summary table

| Area | Item | Status |
|---|---|---|
| Flag | `MANAGED_TELEPHONY_ENABLED` gates KYC submission only | BUILT (narrow) |
| Subaccount | Plivo subaccount creation per org | ABSENT |
| Subaccount | `subaccount_auth_id` stored on org | ABSENT |
| Subaccount | Route/UI to mark a config platform-managed | ABSENT (writer exists, zero callers) |
| Numbers | Search available India numbers | ABSENT |
| Numbers | Purchase + assign to org | ABSENT |
| Numbers | Bind number → Plivo Application | PARTIAL (app URL synced; number link manual) |
| Numbers | List org's numbers in dashboard | BUILT |
| Numbers | Release on churn / downgrade | ABSENT |
| Numbers | Half-failed provisioning rollback | ABSENT (and DB commits before provider sync today) |
| Routing | Number → org → workflow resolution | BUILT |
| Routing | Mapping in DB (not hardcoded) | BUILT |
| Routing | Behaviour with no workflow attached | BUILT (rejects + logs; no customer alert) |
| Billing | Recurring monthly charge mechanism | **ABSENT** |
| Billing | Idle number generates revenue | **No — ₹0** |
| Billing | Number rental recorded as our cost | **ABSENT** |
| Billing | Scheduled job charging monthly fees | **ABSENT** |
| KYC | Document upload (GST, incorporation, ID, address) | BUILT |
| KYC | Submission to Plivo's API | ABSENT (manual carrier) |
| KYC | Account status state machine | BUILT |
| KYC | Suspension / revocation state | ABSENT |
| KYC | Per-number status state machine | ABSENT |
| Leak | Release on org deletion | ABSENT |
| Leak | Release on non-payment | ABSENT |
| Leak | Carrier ↔ DB reconciliation | ABSENT |

---

## Ranked dev-day estimate to make managed numbers sellable

Ranked by money protected per day of work, not by build order. Sequencing note follows.

| # | Work | Days | Why it ranks here |
|---|---|---|---|
| 1 | **Number inventory + nightly reconciliation** — a table recording carrier, carrier-side number id, monthly rental in paise, `rented_at`, `released_at`, assigned org; an ARQ cron that lists Plivo's `/PhoneNumber/` and diffs against it; alert on orphans and on drift both directions | **2** | Stops the three-path leak on day one and is a hard prerequisite for #2 and #3. Also the only thing that tells you what you're *already* paying for |
| 2 | **Release on delete / churn / suspension** — `release_number()` on the provider protocol, Plivo implementation, wired into number delete, org suspension and (if it ever exists) org deletion; soft-delete rows so the record survives the release | **1.5** | Closes the customer-triggerable leak. Cheap, and every day without it accrues rent |
| 3 | **Recurring rental billing** — a `RecurringChargeModel` + credit-ledger kind (this does *not* fit `CallCostItem`, which is a per-call receipt), monthly ARQ cron, proration for mid-cycle provisioning and release, insufficient-balance policy (suspend vs. accrue), dashboard surfacing, and rental recorded as our cost so margins stop lying | **4** | This is where managed-number revenue actually comes from. Also the largest single build, because none of the scaffolding exists |
| 4 | **Provisioning: search + purchase + app-link** — Plivo `/PhoneNumber/` search by country/city/type, rent with `app_id` set at purchase time so the console step disappears, store the carrier-side id, idempotency key, compensating release if any step after purchase fails | **3** | Turns "email us and we'll add your number" into a product. Ranks below billing because you can sell managed numbers provisioned by hand; you cannot sell them unbilled |
| 5 | **Provisioning UI** — search results, purchase confirmation with the monthly price shown, per-number status | **1.5** | |
| 6 | **Subaccount per organization** — `/Subaccount/` create on first managed provision, store the subaccount `auth_id`/`auth_token` in the config credentials, ensure inbound routing keys on the subaccount `AuthID` (see §3 — get this wrong and every managed inbound call is rejected), per-subaccount cost attribution | **2.5** | Required by the ISV model the flag was written for, and the clean answer to per-customer verification. Not required to take the first paying managed customer |
| 7 | **Suspension state + admin controls** — add `SUSPENDED` to the KYC machine with legal transitions in and out; expose `set_platform_managed` as an actual admin route (it has none); park a non-paying org's numbers instead of leaving them quietly working | **1.5** | The `set_platform_managed` half is a **hard blocker** — see sequencing |
| 8 | **Customer alert on inbound-with-no-workflow** — surface it in the dashboard instead of only `logger.warning` | **0.5** | Small, but it's the difference between "the number is broken" and a support ticket you can't reproduce |
| 9 | **Plivo compliance API integration** — fill in `PlivoCarrier.submit`/`check` | **3, or never** | **Do not put this on a critical path.** The estimate is meaningless until Plivo confirms the India compliance application is API-drivable and with which fields. The manual carrier path already works; treat this as an optimisation of staff time, not a launch dependency |

**Total excluding #9: ~16.5 dev-days.** With #9 assumed feasible: ~19.5.

**Sequencing, which differs from the ranking:** item #7's `set_platform_managed` route is a half-day and nothing about managed telephony is reachable without it — do that first, then #1, then #2, then #3. Items #4/#5 can run in parallel with #3 by a second person; they touch `services/telephony/**` and the UI, while #3 touches `services/billing/**`.

---

## What will silently lose money, in order

1. **Every managed number, every month, from the day it is provisioned.** No recurring charge exists. This is not a rounding error — a DID that generates no calls generates no revenue at all, while the carrier invoices us monthly. Confirm the current Plivo India DID rental against their live price list before modelling this; the figure is not in this repo and I am not going to guess it into a spreadsheet you'll bid from.
2. **Orphaned numbers after any delete.** Three paths, no reconciliation, and the delete removes the evidence. This grows monotonically and is invisible until someone audits a carrier invoice by hand.
3. **Overstated margins on every managed account.** Rental is absent from the cost engine, so the dashboard shows a per-call margin that ignores a real fixed cost. Decisions made on that number — pricing, discounting, tender bids — will be wrong in the same direction every time.
4. **Churned accounts keeping their numbers.** Balance enforcement stops their calls, which stops the only thing that was generating revenue, while the rent continues. The current design fails in the exact direction that costs the most.
