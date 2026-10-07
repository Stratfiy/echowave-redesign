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

* `LeadProvider` is the interface: `search(key, Criteria) -> SearchResult`.
  `PROVIDERS` registers them; `LEAD_DATA_PROVIDER` picks one (default
  `apollo`; an unknown name falls back to Apollo and logs it).
* **Apollo** is implemented: People API Search
  (`POST /api/v1/mixed_people/api_search`) for who matches, then Bulk People
  Enrichment (`POST /api/v1/people/bulk_match`, ten at a time) for work
  addresses. Only `email_status == "verified"` addresses become leads; the
  rest are counted and said, not dropped silently.
* **Keys** are provider keys under component `data`, provider `apollo`:
  1. the workspace's own (the key form on the thread, or an admin), charged
     as a tool call on their account (`lookup_source` OWN);
  2. the platform's (Super admin, Provider keys), charged the fee plus the
     vendor's price per verified address (`lookup_source` PLATFORM);
  3. none: `find_leads` answers `needs_setup` and puts **one** key form on
     the thread (`NEEDS_SECRET` with a `provider_key` target). The key is
     checked with Apollo (`GET /v1/auth/health`) and stored in the
     workspace's provider-key vault; it never reaches the thread or the
     model. "No key" never looks like "nobody matched".
* Adding a provider: a class with `name`, `label`, `key_help` and `search`,
  a line in `PROVIDERS`, a line in `registry.DATA_PROVIDERS`, and a key
  check in `key_validation._DATA_CHECKS`.

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

* **Which provider "treg" is.** Nothing by that name is in the repo; Apollo
  is the closest one the repo already referenced (`lookup_source`, the
  prospecting template). If "treg" is a different vendor, it is one class in
  `leads.py` plus the registry lines above.
* An Apollo **master** API key for people search (Apollo, Settings,
  Integrations, API).
* Sending needs the owner's own Gmail or Outlook connected (Composio).

## Rollback

`outreach` off: the tools and their rules are not offered, the key form is
not on the thread. The preview on send cards, Confirm all and Excel reading
are not behind the flag (they fix send cards and attachments everywhere).
