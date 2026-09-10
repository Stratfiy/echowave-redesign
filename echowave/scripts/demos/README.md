# Demo builders

One-shot scripts that create a demo agent on an account through the public API.
Each makes ordinary things — tools and an agent — that are editable afterwards on
the canvas or the prompts view, and each prints its workflow definition when run
without an API key, which is what the tests validate against the engine.

## Elock support (Kritilabs)

The client's support line for an e-lock on a tanker truck, from their workflow
document: verify the caller by TT number and 10-digit invoice, walk them through
the controller (6# refresh, GPS light, 3# for an OTP, 2# + OTP + # to open, 1# to
lock), check OTP status with their backend, ask consent before a manual OTP, and
hand to a person when the lock still will not open. "No invoice for this trip"
ends with the Sales Officer guidance.

```bash
# From the echowave/ directory. Needs nothing installed beyond Python.
python -m scripts.demos.elock_support \
    --base-url https://app.decibyl.ai \
    --api-key dk_... \
    --validate-url   https://client.example/api/validate \
    --otp-status-url https://client.example/api/otp/status \
    --manual-otp-url https://client.example/api/otp/manual \
    --transfer-to    +919999999999
```

It creates three HTTP tools (`validate_customer`, `otp_status`,
`trigger_manual_otp`), one transfer tool (`transfer_to_support`) and the agent,
attaches each tool to the step that uses it, and prints the agent URL.

Until the client's endpoints exist, point the three URLs at a mock that returns
`{"valid": true}` / `{"triggered": true}` / `{"sent": true}` and the conversation
runs end to end. Put the agent on a number under Phone numbers, or hear it in
the browser from the agent's Test button.

## Narayani Dental Clinic, Hosur

A dental front desk: books, reschedules and cancels appointments, answers
timings, directions and the consultation fee from the clinic's own facts, and
sends anything clinical to a callback or, if urgent, to a person. Follows the
caller between Tamil, Kannada, English and Hindi.

```bash
python -m scripts.demos.narayani_dental \
    --base-url https://app.decibyl.ai \
    --api-key dk_... \
    --slots-url https://clinic.example/api/slots \
    --book-url  https://clinic.example/api/book \
    --transfer-to +919999999999
```

Doctors, hours and address are placeholders in `CLINIC` at the top of the
script; change them before the demo. The two URLs can point at a mock returning
`{"slots": [...]}` and `{"booked": true, "reference": "ND-1042"}`.

## Logicorp quote desk

An international courier quote line for [Logicorp](https://logicorp.in), the
logistics aggregator, on the DHL Express 2026 Time Definite export rate guide.
The caller says the destination and the weight; the agent works out the zone,
the chargeable weight (volumetric at /5000), the base rate, names the
surcharges that clearly apply, and captures the lead for a written quote or
hands to sales. The zone map and rate table live in the global prompt so a
number comes back in one turn; the PDF itself is uploaded to the knowledge base
and attached to the quoting and surcharge steps for anything the prompt does
not carry.

```bash
python -m scripts.demos.logicorp_quotes \
    --base-url https://app.decibyl.ai \
    --api-key dk_... \
    --rate-guide ./Time_Definite_Export.pdf \
    --lead-url https://logicorp.in/api/leads \
    --transfer-to +919999999999
```

The lead URL can point at a mock returning `{"captured": true, "reference":
"LQ-2041"}`. Pass `--document-uuid` instead of `--rate-guide` to attach a
document that is already in the knowledge base. Every rupee figure the agent
may say is in `NON_DOC_RATES`, `DOC_RATES`, `MULTIPLIER_RATES` and `SURCHARGES`
at the top of the script; update those when DHL publishes a new guide.
