# Outreach: leads, drafts, Confirm all (flag `outreach`)

A business owner asks Decibyl, in Chat, to find customers and write to them.
Everything ships **off** behind `outreach` (`OUTREACH_ENABLED`), which honours
per-workspace overrides from the staff console.

## Three ways in, one way out

| The owner... | What Decibyl does |
| --- | --- |
| pastes their website, or describes the business | reads it (`web_fetch`), says who the ideal customer is, then `find_leads` |
| attaches a list (CSV or **Excel**) | reads it (`describe_table` / `rank_table` when long), picks the rows that fit and says why |
| pastes leads into the message | the same, from the lines given |

Then `save_prospects` (the Prospects list) and `draft_outreach`: one email per
lead, each a send card. Nothing is sent until the owner confirms, card by card
or with **Confirm all**. A follow-up is a `schedule_routine` that, after the
days chosen, drafts one follow-up each with `draft_outreach` (marked
`follow_up`).

## The lead-data slot (`api/services/outreach/leads.py`)

* `LeadProvider` is the interface: `estimate(Criteria)` and
  `search(key, Criteria, Budget)`. `PROVIDERS` registers them;
  `LEAD_DATA_PROVIDER` picks one (default `treg`; an unknown name falls back
  to Treg and logs it).
* **Treg** (https://treg.to), the default and the founder's "treg": one token
  over many lead-data vendors. A search is `treg.people.search` (routed,
  own keys first, cheapest first), `treg.people.email.find` for a match that
  came back without an address, and `treg.people.email.verify` on every
  address -- only `valid`/deliverable counts; `accept_all` does not.
  Every `/call/` carries `X-Treg-Token`, an `Idempotency-Key` from the run
  (a retried turn replays at Treg, never bills twice), `X-Treg-Route-Max-Cost`
  no higher than what is left of the run's cap, and
  `X-Treg-Route-Exclude: harvestapi` (the LinkedIn scraper; Decibyl never
  reads LinkedIn). The charge is read from `X-Treg-Cost-Micro` and recorded
  per call in vendor metering (`model_usage`, provider `treg`, unit
  `usd_micro`, feature `outreach`; behind `vendor_metering`).
* **Apollo** directly, for a business with its own Apollo key
  (`LEAD_DATA_PROVIDER=apollo`): People API Search, then Bulk People
  Enrichment; only `email_status == "verified"` addresses.
* **Priced before it runs.** The first `find_leads` call records the
  estimate on the thread ("about $0.07, at most $0.50. Nothing spent yet")
  and looks nothing up. The same search runs only when the person has
  written on the thread since that estimate (the model cannot agree for
  them), within 30 minutes, once; a repeat needs a new estimate. The run's
  cap is `LEAD_SEARCH_MAX_USD` (default 0.50).
* **Every stop is a state**, never an empty list: `needs_setup` (no token; a
  key form on the thread), `key_rejected` (a new form), `out_of_balance`
  (with Treg's `topup_url`), `rate_limited` (with `Retry-After`),
  `cap_reached`, `unavailable` (Treg's own provider account is out; not the
  balance). A stop after some leads were found returns them, marked partial.
* **Keys** are provider keys under component `data` (`treg` or `apollo`):
  the workspace's own first (the form on the thread stores it after the
  vendor checks it: Treg `GET /balance`, Apollo `GET /v1/auth/health`), then
  the platform's (Super admin, Provider keys). Never logged, never on the
  thread.
* Adding a provider: a class with `name`, `label`, `key_help`, `estimate`
  and `search`, a line in `PROVIDERS`, a line in `registry.DATA_PROVIDERS`,
  and a key check in `key_validation._DATA_CHECKS`.

## Send cards (`draft_outreach`)

* One ordinary `run_tool` card per lead on the person's own connected
  mailbox (`GMAIL_SEND_EMAIL`, else Outlook), so Confirm, the undo window,
  version binding, the operational quota and the prospect stamps
  (`send_approval.note_sent`) are the ones every send uses.
* Every `run_tool` card now carries a `preview`: To, Cc, Subject and the
  body in full, drawn from the arguments and redrawn on edit. It is not in
  the version (it is the arguments, read).
* Skipped and named: not an address, a junk address, no subject or body,
  listed twice, marked unsubscribed / bounced / declined / not interested,
  or already written to (allowed only as a follow-up).
* No mailbox connected: a Gmail connect card on the thread and the drafts
  handed back for the model to show. No card that cannot run.

## Confirm all

`POST /api/v1/timeline/actions/confirm-all` with `[{event_id, version}]`
(at most 25). Each card is the ordinary Confirm, with its version required
whatever the ledger switch says: a card edited since, settled, owned by
someone else or in another workspace is refused on its own line, and the
rest still go. `ConfirmAllBar` shows under the thread when two or more send
cards with a preview are waiting.

## Excel

`.xlsx` is read by document extraction (every sheet, rows with their
header), by the table tools (the first sheet with rows under a header), and
accepted by the composer's paperclip.

## Needs a human

* The founder's **Treg token** (Super admin, Provider keys, provider `treg`,
  component `data`; or the form on the thread). Until it is added, staging
  shows the `needs_setup` state; the flow is proven against recorded Treg
  responses in `api/tests/test_outreach_treg.py`.
* Sending needs the owner's own Gmail or Outlook connected (Composio).

## Rollback

`outreach` off: the tools and their rules are not offered, the key form is
not on the thread. The preview on send cards, Confirm all and Excel reading
are not behind the flag (they fix send cards and attachments everywhere).
