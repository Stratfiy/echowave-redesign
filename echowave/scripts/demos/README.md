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
