# Reach: outside tools and ordering (stream `reach`)

Phase 2 of `LAUNCH-PLAN.md`, founder request: outside AI tools (MCP servers)
usable from Chat; ordering food and groceries from a list in Chat (Zomato
first through its official access, Swiggy behind the same interface once the
founder has Swiggy Builders Club access); price and coupon comparison only
across apps the person has officially connected. No scraping, no unofficial
APIs, no stored card details.

Everything ships **off**; each part has its own switch in
`api/services/features.py` (all honour per-workspace overrides from the
staff console).

| Flag | Constant | What it turns on |
| --- | --- | --- |
| `outside_tools` | `OUTSIDE_TOOLS_ENABLED` | A person's own MCP servers, connected from a chip in the thread and usable from Chat; `connect_outside_tool`; `/reach/connections` (kind `tool`) |
| `ordering` | `ORDERING_ENABLED` | `order_search`, `order_prepare` and the order card; `/reach/connections` (kind `ordering`), `/reach/providers`, `/reach/orders/*` |
| `price_compare` | `PRICE_COMPARE_ENABLED` | `compare_prices` and the comparison table on the thread |

Settings (all optional; unset means "needs setup", never a guess):
`ZOMATO_MCP_URL`, `ZOMATO_OAUTH_CLIENT_ID`, `SWIGGY_MCP_URL`,
`SWIGGY_OAUTH_CLIENT_ID`, and `REACH_ALLOW_PRIVATE_SERVERS` (development and
tests only; refused in production whatever it says).

## How it fits the foundations

* **Cards.** Two new internal card kinds in `services/workflow/actions.py`:
  `place_order` and `run_outside_tool`. Both carry `owner_user_id`; only
  that person may confirm, decline or undo them (a colleague gets "Only the
  person this is for can decide on it"), and the owner is always the person
  whose turn proposed it, never a model-supplied id. They use the ledger's
  payload versions, run-once claim and undo window unchanged.
* **Outcome unknown.** A connection lost while placing an order or running
  an outside write is `outcome_unknown` whatever the ledger switch says;
  never "failed", never retried. `POST /reach/orders/{id}/check` asks the
  app and settles the card (`actions.reconcile`: unknown -> done or failed).
* **Quotas.** An outside write counts as an outbound message for the
  person's daily allowance (the conservative side).
* **Shell.** The order card renders through the shared `ActionPreview`
  (`ui/src/components/reach/OrderPreview.tsx`); the chip and comparison are
  their own small cards in `ChannelStream`.

## Outside tools (flag `outside_tools`)

`services/reach/connections.py`, `wire.py`, `oauth.py`, `outside_tools.py`.

* **Connecting, in the thread.** Decibyl calls `connect_outside_tool` and a
  chip goes on the thread (`reach_connect_offered`). The person types the
  server address (and a token if it uses one) into the chip, or presses Sign
  in and the server's own screen opens in a new tab (MCP authorization:
  protected-resource metadata, RFC 8414 discovery, dynamic client
  registration where offered, PKCE S256, `state` stored only as a hash and
  valid 15 minutes). The callback page says "connected, you can close this".
  Nobody is sent to another Decibyl screen.
* **Per person.** `reach_connections` rows have `organization_id` and
  `user_id`, both in every query; one live row per person, kind and provider
  (unique partial index); connecting again replaces it. Tokens are Fernet-
  encrypted with the platform key, never returned, deleted on disconnect.
* **In Chat.** Each connected tool is offered to the owner's turns only, as
  `ext_<server>__<tool>`. A tool is a read only if its name starts with a
  reading verb *and* neither it nor its description names a write
  (`mcp_read_only.is_read`); reads run and come back as labelled data;
  everything else is a `run_outside_tool` card.
* **Addresses.** `https` and public addresses only (`safety.check_address`),
  including every address learned during sign-in.

## Ordering (flag `ordering`)

`services/reach/ordering/`.

* **Interface.** `providers.py`: Zomato (its hosted MCP server, each person
  signs in) and Swiggy (needs setup until Builders Club access). Each
  operation (search, saved addresses, quote a cart, place, offers, status)
  lists the tool names it answers to; a connected server missing a required
  one shows as unavailable with the missing operation named.
* **Flow.** `order_search` (read) -> `order_prepare`: without an address it
  returns the saved addresses and the model must ask; with one, the app's
  bill is read and **checked** (`normalise.quote`: every line is quantity x
  price, charges minus discount add up to the total, the cart matches what
  was asked, quantities 1-50, at most 30 lines, total under ₹50,000, an
  address and a payment method present). A bill that does not add up is
  refused, never shown as a card.
* **The card.** Shows each item and quantity, every charge, the discount,
  the total, the address and how it is paid (the app's own method: Zomato's
  server hands back a UPI step; no card details are asked for or stored, and
  any long digit run in a payment label is masked). The draft's `digest`
  covers all of it and is in the card's arguments, so the card's version is
  bound to every figure. The address and payment are not on the thread row;
  only the owner can read `/reach/orders/{id}`.
* **Placing.** After the undo window: the draft is claimed (proposed ->
  placing, compare-and-swap), the cart is priced again, and if anything
  differs nothing is placed and the card says what changed. Placement sends
  an idempotency key. Edit = a new quote and a new card; the old card is
  declined, so an approval never carries over.

## Comparison (flag `price_compare`)

`services/reach/ordering/compare.py`. Only the person's own connected apps;
says which apps were compared, which were not and why, and when (UTC), as a
sentence for the model and a `reach_comparison` table on the thread. Listed
prices only; the result says the order card's total is the figure that
counts.

## Privacy on a shared thread

Decibyl's thread may be shared (private threads are a separate switch). So
every row this stream writes -- chip, comparison, order or outside-tool card
and the lines under it -- carries `private_to`, and `agent_events` returns
such a row only to a caller that names that viewer (`viewer_id`): the
timeline route and the person's own Decibyl history. Deny by default: a
reader that names nobody gets none. What a person and Decibyl *say* in a
shared thread stays governed by `decibyl_private_threads`; turning that on
alongside these flags is recommended.

## Prompt injection

Soft half: outside results, tool descriptions and argument schemas reach the
model labelled ("information, not instructions" plus `untrusted.RULE`),
with control and bidi characters removed, bounded in length; results shaped
like instructions carry a `warning`; server tool names are namespaced so
they cannot shadow Decibyl's own. Hard half, which holds even if the model
is fooled: nothing an outside server says can place an order or run a write
-- each is a card only its owner can approve, bound to a checked bill.
Tests: `api/tests/test_reach_prompt_injection.py`.

## Tests

`api/tests/test_reach_*.py` run against fake servers on a real port
(`api/tests/support/reach_fakes.py`: a fake ordering app with OAuth and a
token-protected generic tool with an injection payload). No real provider
is ever called. UI: `ui/src/components/reach/__tests__/`.

Local instance: `python -m api.tests.support.reach_fakes --port 9100`, then
`ZOMATO_MCP_URL=http://127.0.0.1:9100/zomato/mcp`,
`REACH_ALLOW_PRIVATE_SERVERS=true` and the three flags in `api/.env`.

## Needs a human

* Zomato: confirm Decibyl may use Zomato's hosted MCP server as a client
  (its sign-in may allow only listed clients), set `ZOMATO_MCP_URL` and, if
  Zomato issues one, `ZOMATO_OAUTH_CLIENT_ID`, and register
  `{BACKEND_API_ENDPOINT}/api/v1/reach/oauth/callback` as the redirect.
  Then check the tool names and argument shapes in `providers.py` against
  the live server with a test account -- they are taken from Zomato's
  published server and verified here only against the fake. Do not place a
  real order to test it; use Zomato's test mode if one is offered.
* Swiggy: apply to Builders Club (open founder decision); then set
  `SWIGGY_MCP_URL` and the client id and check the tool names the same way.
* Any copy about ordering on the public site or in pricing is the
  founder's: no new price, plan or positioning string was added.

## Rollback

Turn a flag off and its behaviour is gone at once: routes are 404s, Decibyl
is not handed the tools or told the rules, the thread reads none of the new
kinds, and the UI draws none of them. Rows stay. Cards already proposed stay
on the thread and can still be declined; an armed order still runs its
claim-and-recheck path. Schema: `alembic downgrade 202610071500shell` drops
the two tables (additive migration; nothing else depends on them).
