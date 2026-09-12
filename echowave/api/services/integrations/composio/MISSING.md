# Indian SaaS the Composio catalogue does not reach

Audited against all 1,540 Composio toolkits on 2026-09-12, not against the
first page of them — an earlier pass checked only 500 and wrongly reported
MSG91, Wati and Plivo as absent.

Everything here is a candidate for a first-party `http_api` tool. That path
already exists and already handles OAuth refresh grants with per-vendor header
quirks (`api/services/integrations/oauth2.py`), so each of these is a config
job rather than an integration project. What it does *not* have is a way for a
non-developer to add one, which is the actual gap — see "The real gap" below.

## Already covered — do not rebuild

| App | How |
|---|---|
| MSG91 | Composio, API key. SMS + WhatsApp. |
| Wati | Composio, API key. WhatsApp BSP. |
| Plivo | Composio, API key. Voice + SMS. |
| Razorpay | Composio, API key. |
| Cashfree | Composio, `cashfree_payments_mcp`, needs our OAuth app. |
| Shopify | Composio, API key. |
| Zoho Books / Invoice / Bigin / Desk / Mail | Composio, one click. |
| WhatsApp (Meta Cloud API) | Composio one-click, **and** our own platform sender in `api/services/messaging/platform_whatsapp.py`. Prefer ours. |

## Missing, in the order a customer will ask

### 1. Logistics — nothing Indian at all
Shiprocket, Delhivery, Bluedart, DTDC, Ekart, XpressBees, Ecom Express,
NimbusPost, Shipway, Pickrr.

Composio has only Shippo, Shipday, Shipengine and Starshipit, all US/AU.
"Where is my order" is a top-three reason an Indian D2C customer phones, so
this is the most valuable gap on the page. Shiprocket and Delhivery both
publish clean REST APIs with token auth.

### 2. WhatsApp BSPs beyond MSG91 and Wati
Gupshup, Interakt, AiSensy, Zoko, DoubleTick, 360dialog.

All near-identical in shape: POST a template name plus variables to a URL with
an API key in a header. One adapter with a per-provider config covers every
one of them; ten separate connectors would be the wrong build.

### 3. SMS providers
Kaleyra, Exotel, Textlocal, Karix, ValueFirst, Twilio, Sinch, Infobip.

Same adapter shape as the BSPs above. Note DLT registration is a prerequisite
for *any* of these in India — entity, sender ID and per-template approval with
TRAI — and that gate applies to us, not to the connector.

### 4. Payments
PayU, PhonePe, Paytm, Instamojo, BillDesk, CCAvenue, Juspay, Easebuzz.

The agent's closing move is a payment link on WhatsApp, so a business on PayU
rather than Razorpay currently cannot complete that loop.

### 5. Accounting and GST
Tally, Vyapar, Marg, Busy, ClearTax, myBillBook, Khatabook.

Composio's `tally` is **Tally Forms**, a form builder — not the accounting
software. Easy to mistake and worth not mistaking.

Tally proper is the hard one and should be deferred until a customer blocks on
it: its API is a local XML socket on the desktop machine, not a cloud endpoint,
so it needs an agent running inside the customer's network. That is a product,
not a connector.

### 6. Clinic and practice management
Practo, Clinicea, Medixcel, Docon, HealthPlix, Halemind, Bahmni, EasyClinic.

None in Composio, and most have no public API at all — several are on-premise.
For the clinic ICP the realistic integration stays Google Sheets and Calendar,
which is what most clinics actually run on anyway. Do not promise Practo.

### 7. HR and payroll
Keka, Darwinbox, Zoho People, GreytHR, RazorpayX Payroll, sumHR.

Only relevant once there is an HR agent. Parked.

### 8. Coaching and education
Classplus, Teachmint, Extramarks, Vedantu.

Coaching centres are a named ICP for the receptionist agent, so this earns a
place above HR. Classplus and Teachmint both have partner APIs.

### 9. Indian CRM
LeadSquared, Kylas, Sell.Do, TeleCRM.

LeadSquared is the one that shows up in real deals; the rest can wait.

## The real gap is not the list

Every app above is a day of work as an `http_api` tool. What does not exist is
a way for anyone but us to add one: `http_api` tools are authored by someone
who knows what a POST body is, and the customer is a clinic owner.

What is missing is a **first-party connector catalogue** — named integrations
with the URL, method and parameters pre-filled by us, where the customer
supplies only their own credential. "Connect Shiprocket" should be one screen.

Until that exists, each connector above is also a support burden, which is the
argument for building the catalogue before building many more connectors.

## How to check this list is still true

```
COMPOSIO_KEY=ak_... python3 - <<'PY'
import json, os, urllib.request
K = os.environ["COMPOSIO_KEY"]
def page(cursor=None):
    u = "https://backend.composio.dev/api/v3.1/toolkits?limit=500"
    if cursor: u += f"&cursor={cursor}"
    r = urllib.request.Request(u); r.add_header("x-api-key", K)
    return json.loads(urllib.request.urlopen(r, timeout=60).read())
items, cur = [], None
while True:
    b = page(cur); items += b["items"]; cur = b.get("next_cursor")
    if not cur: break
slugs = {t["slug"] for t in items}
for q in ("shiprocket", "delhivery", "gupshup", "interakt", "payu", "practo"):
    print(q, q in slugs)
PY
```

Composio adds toolkits steadily, so re-run before quoting any of this to a
customer.
