# Going live

The order matters in two places, and both are easy to get wrong once:

* **Create the admin account before disabling signup.** Nothing in the signup
  flow sets the staff flag, and with `ENABLE_SIGNUP=false` there is no way to
  create the first account at all. Do it in the order below and this is a
  non-event; do it backwards and you are editing the database by hand.
* **Set `RAZORPAY_WEBHOOK_SECRET` before taking a single payment.** Without it
  top-ups are refused outright — deliberately, because the alternative is
  charging a customer and crediting nobody.

---

## 1. Environment

Values containing spaces **must be quoted**, and so must anything containing
`<`, `>` or `&`. `set -a && source api/.env` is shell, so an unquoted
`SUPPLIER_LEGAL_NAME=Nautomation Labs Private Limited` silently sets the
variable to `Nautomation` and tries to run `Labs`.

### Required — the app misbehaves quietly without these

| Variable | Consequence if unset |
|---|---|
| `DATABASE_URL`, `REDIS_URL` | Nothing starts |
| `PUBLIC_BASE_URL` | Webhook and media URLs point at localhost |
| `PLATFORM_CREDENTIAL_SECRET` | Provider keys cannot be stored — saving raises. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | No top-up can be created |
| `RAZORPAY_WEBHOOK_SECRET` | **Top-ups refused.** Must match the value entered in the Razorpay dashboard exactly |
| `SUPPLIER_LEGAL_NAME`, `SUPPLIER_GSTIN` | No tax document is issued. Payments still credit, and the omission is logged as an error — a real payment is never rolled back over a missing variable |
| `SUPPLIER_ADDRESS`, `SUPPLIER_LUT_NUMBER` | Printed blank on documents |
| `SUPPLIER_HAS_LUT=true` | Every foreign customer's top-up is refused |

`SUPPLIER_STATE_CODE` defaults to the first two digits of the GSTIN and decides
CGST+SGST versus IGST for every domestic customer. It is the single most
consequential value here — get it wrong and every invoice carries the right
total split the wrong way, which is a filing correction rather than a bug.

### Required to keep things private

| Variable | Why |
|---|---|
| `MINIO_PUBLIC_BUCKET` | **Leave unset.** `true` grants the recordings bucket anonymous read, write *and* delete. Access is by presigned URL and needs no policy |
| `ENABLE_SIGNUP=false` | After creating your accounts, unless you want open registration |
| `CORS_ALLOWED_ORIGINS` | Only when `DEPLOYMENT_MODE != oss`. Behind one reverse proxy the UI and API are same-origin and CORS does not apply |

### Optional

`SENTRY_ORG` and `SENTRY_PROJECT` enable source-map upload so stack traces are
readable. Errors are reported either way.

---

## 2. Database

```bash
set -a && source api/.env && set +a
alembic -c api/alembic.ini upgrade head
alembic -c api/alembic.ini check     # must print "No new upgrade operations detected"
```

If `check` ever fails, **do not** run `--autogenerate` and apply what it
produces without reading it. It has proposed destructive column drops before.

---

## 3. The first admin

Signup must still be open for this step.

1. Sign up through the UI at `https://<your-host>/auth/signup`.
2. Grant staff access:
   ```bash
   set -a && source api/.env && set +a
   python -m scripts.grant_superuser you@yourdomain.com
   python -m scripts.grant_superuser --list      # confirm
   ```
3. Open `/superadmin`. It is deliberately absent from the sidebar — the whole
   area is gated at router level, so a customer who guesses the URL gets
   nothing.
4. Set `ENABLE_SIGNUP=false` and restart, if you do not want open registration.

`--revoke` takes staff access away again.

---

## 4. Razorpay

In the Razorpay dashboard, Settings → Webhooks:

| Field | Value |
|---|---|
| URL | `https://<your-host>/api/v1/billing/razorpay/webhook` |
| Secret | You choose it. Put the identical value in `RAZORPAY_WEBHOOK_SECRET` |
| Events | `payment.captured`, `payment.failed` |

The endpoint must be publicly reachable over HTTPS with a valid certificate —
Razorpay will not deliver to a self-signed one. Do not IP-allowlist it: Razorpay
publishes no fixed source range, and the signature is the authentication.

Verify with a test-mode payment before switching to live keys. A captured
payment should credit the account **net of GST** and issue a receipt voucher
numbered `RV/<FY>/000001`.

---

## 5. Prices

Everything is set in the admin dashboard under **Billing → Rate card** — the
platform rate, volume tiers, the USD→INR rate, and every provider rate. Nothing
is hardcoded and nothing needs a deploy to change.

Set provider rates before the first call. Usage with no rate on file is recorded
as **uncosted, not free**: the call still bills the platform fee, the provider
cost is reported as missing on the unit-economics screen, and margin is
overstated until you fill it in.

---

## 6. Before opening the doors

* Make one real end-to-end call. Everything in this repo is tested against a
  real database, but no test places an actual call through a carrier.
* Confirm `https://<your-host>/api/v1/health` responds and reports the auth
  provider you expect.
* Check that the MinIO endpoint is **not** reachable from the internet, or that
  you are on S3 (`ENABLE_AWS_S3=true`).
* Take a backup, and check you can restore it. The credit ledger is the only
  record of what every customer has paid.

---

## What is still missing

Listed here rather than discovered later:

* **No PDF for tax documents.** They are issued, numbered and readable through
  the API; a customer cannot download one.
* **No e-invoicing (IRN via the IRP).** Mandatory above ₹5 crore aggregate
  turnover.
* **No credit notes.** A refund is issued from the Razorpay dashboard and
  reflected with a staff credit adjustment.
* **No low-balance email.** The Billing screen warns in amber and a refused run
  says what the fix is, but nobody is emailed.
* **Number provisioning is not built.** `is_platform_managed` is the flag the
  KYC gate keys on and nothing sets it yet, so the gate is correct but dormant.

See `KNOWN_ISSUES.md` for anything open, and `DASHBOARD.md` for how a call is
priced and what every billing number means.
