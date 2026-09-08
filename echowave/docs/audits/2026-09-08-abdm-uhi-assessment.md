# ABDM / UHI — is it worth building on, and as what

**Written 8 September 2026.** An assessment, not a design. The question was
whether integrating each Indian clinic vendor (Practo Ray, HealthPlix, MocDoc,
DocEngage, Halemind) is the right shape, or whether the national layer is.

## The short answer

**Build on UHI, and the timing is unusually good — but the rules are being
written this quarter, so build the assessment now and the integration when the
market rules land.**

Integrating Practo Ray reaches Practo Ray's customers. Integrating UHI reaches
every ABDM-connected provider in India through one interface, without a
bilateral agreement per vendor. For an agent whose healthcare job is *book,
reschedule, remind, recover a no-show*, that is the whole problem solved at the
standards layer.

## What UHI is

An open network under ABDM, run by the National Health Authority, deliberately
modelled on UPI. Three roles:

| Role | Who | Decibyl could be |
|---|---|---|
| **EUA** — End User Application | Patient-facing. The NHA's own description names *"virtual assistants using different languages"* as an EUA form | **Yes, and this is the interesting one** |
| **HSPA** — Health Service Provider Application | The clinic's system, publishing availability and accepting bookings | Only if we sell to clinics as their PMS, which we do not |
| **Gateway** | NHA-run. Routes discovery between the two | No |

The protocol is Beckn-style: an EUA broadcasts a `search`, the gateway fans it
out, HSPAs answer `on_search`, then `select` / `init` / `confirm` completes a
booking. The same shape ONDC uses for commerce.

## Why the EUA role is the one that matters

A clinic buying a Decibyl agent to answer its own phone does not need UHI to
book into its own calendar — that is its own PMS, and a direct integration or a
webhook is simpler.

UHI pays off in the case a single-clinic integration cannot reach at all:

- A patient calls **one number** and the agent finds a doctor across many
  providers — a helpline, an insurer, a hospital chain, a state health line.
- A clinic that is full can offer a slot at a partner it never integrated with.
- Teleconsultation discovery, which is a UHI service type in its own right.

That is a product no competitor built on US rails will ship for India, and it
is reachable because the network does the fan-out.

## Status, as of today — and this is the part that decides the timing

Three facts, and they point the same way:

1. **UHI was formally launched in July 2026** by the Union Health Minister as
   an interoperable network for digital health services.
2. **The NHA opened a consultation on *Operationalising UHI in India* in
   August 2026** — one month ago. It covers search and discovery rules,
   payment and settlement, and grievance redressal. In other words the
   *market rules* are being decided right now.
3. **The reference implementation is early.** The NHA's own repo reports
   protocol specification `0.0.1`, the Gateway as pre-release, and the Network
   Registry as upcoming.

So: politically backed and launching, technically young. A network whose
settlement and grievance rules are still in consultation will move under
anything built against it this quarter.

**That is an argument about sequencing, not about whether.** Being early on a
government network is how the first cohort of UPI apps got their position.

## What it would cost us

Roughly, and honestly — this is an estimate from the protocol shape, not from
having built it:

| Piece | Notes |
|---|---|
| NDHM sandbox registration, role declaration | Paperwork; needs a legal entity and a named security contact |
| ABHA identity flow | The patient's health id. Consent-driven |
| Beckn-style `search` → `on_search` → `select` → `init` → `confirm` | The core. Async, callback-based — the agent asks, and answers arrive over time, which does not fit a synchronous tool call |
| Consent manager state machine (HIE-CM) | Required to touch records, **not** required to book |
| FHIR R4 bundles, India profiles | Only if we exchange records. Booking alone avoids this |
| Request signing (JWT) | Standard |
| Production credentials | Granted after a security review |

**The one genuinely hard part is asynchrony.** UHI discovery is a broadcast
with replies arriving over seconds from many providers. Our tool calls are
request/response inside a live conversation with a caller waiting. Bridging
that means the agent says "let me check who has a slot" and holds the line
while replies accumulate — which is a real conversational design problem, not
just plumbing. It is also exactly the kind of thing a voice agent should be
good at, and a chatbot is not.

## Prerequisite we do not have yet

Booking into UHI means handling health data. Two things must land first:

1. **The `annotations` erasure fix** — done, `11e114a`. Extracted fields no
   longer survive a purge. Without it we would be retaining symptoms and
   appointment reasons past a patient's erasure request.
2. **A DPIA.** Voice plus AI plus health data is high-risk processing under
   GDPR Art 35 and is the right discipline for DPDP too. `PRIVACY.md` records
   this as not started.

## Recommendation

- **Do not start the UHI integration this quarter.** The market rules are in
  consultation and the Gateway is pre-release; anything built now is rework.
- **Do file for sandbox access now.** Registration, role declaration and a
  security review take weeks and are not blocked by the spec moving. Being in
  the sandbox when v1 lands is the whole advantage.
- **Respond to the consultation.** A voice-first EUA has requirements nobody
  else in that consultation will raise — asynchronous discovery against a
  caller on hold, and language. Shaping a rule is cheaper than working around
  it later.
- **Meanwhile, ship the direct path.** The OAuth credential (`fe5d802`) plus a
  curated tool bundle covers any clinic whose PMS has an API today, which is
  what a customer signing this quarter actually needs.

## Sources

- [NHA-ABDM/UHI reference implementation](https://github.com/NHA-ABDM/UHI) — protocol 0.0.1, Gateway pre-release
- [UHI product overview, abdm.gov.in](https://abdm.gov.in/UHI/product-overview)
- [Union Health Minister launches UHI (July 2026)](https://www.datais.info/india/govtpressreleases/union-health-minister-shri-jagat-prakash-nadda-launches-unified-health-interface-the-interoperable-network-for-digital-health-services-uhi-enables/c2721a45f9499cbfe391ce527e6d997abe06297f/)
- [NHA consultation paper on operationalising UHI](https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID=1883652)
- [ABDM integration developer guide](https://www.openmalo.com/blog/abdm-integration-developer-guide)
- [ABDM 2026 rollout for Indian clinics](https://ichelonconsulting.com/insights/abdm-2026-rollout-update-doctors-clinics-india)
