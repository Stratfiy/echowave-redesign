# Roadmap

**Written 2 Sept 2026.** Ordered by what earns revenue soonest with five
people, a commission sales team, and ₹5L. `BUILD-PLAN.md` covers pricing and
billing specifically; this is everything else.

The constraint that shapes the order: **sales is commission-based, so headcount
scales without burn — but only if a rep can sell and set up with no training.**
Every item is judged on whether it multiplies a rep or not.

---

## Evidence behind the ordering

From competitor reviews (Trustpilot: Vapi, Retell, Synthflow, Bolna,
Smith.ai) and TRAI Q1 FY2026-27 data, Sept 2026:

- **This category fails at operations, not AI.** A week of downtime with
  nobody answering support is the most repeated complaint across every vendor.
- **Latency causes switching.** "3-5 second pause after each turn" (Vapi, 2★).
  Every US-hosted competitor pays a trans-Pacific hop *per conversational
  turn* on an Indian call. Our India-region media path is a real, measurable
  advantage.
- **A bad AI receptionist is worse than none.** "it's even got me bad reviews"
  (Smith.ai, 1★). Sell "won't embarrass you", not "saves money".
- **Outbound cold-calling in India is a landmine.** TRAI actioned 183,839
  telecom resources in one quarter; UCC complaints up ~40% QoQ. Inbound and
  consented callback only.

**Unvalidated and load-bearing:** no evidence exists that an Indian SMB pays
for missed-call recovery. It is proven for US solo attorneys at $200-500/mo
with 5-7 year retention. One HN signal says Indian SMBs want WhatsApp, not
phone. Field visits settle this, not more research.

---

## Now — the volume engine

| # | Item | Done means |
|---|------|-----------|
| 1 | Missed-call callback | ✅ shipped `d181eca` |
| 2 | Missed-call events UI | Operator can see who rang, when, called back or refused and why |
| 3 | **Template gallery** | A rep picks a template, fills 6 fields, agent is live in 10 min |
| 4 | Rep setup wizard | The handover test is true: a receptionist reconfigures unaided in 3 min |

**#3 is the highest-ROI item on this page.** Every rep added multiplies by it.
Nothing else scales with headcount we don't pay for.

Templates are vertical-agnostic — the same six work for a clinic, a salon, a
gym, a coaching centre, a garage. Strip the nouns and the job is identical:
*a business pays for leads, the phone rings, nobody picks up, the money is
gone.*

---

## Next — make it stick

| # | Item | Why |
|---|------|-----|
| 5 | Day-14 auto report | Calls answered, booked, ₹ recovered. Retention, upsell and referral in one sheet |
| 6 | Structured outcomes per call | Name, requirement, budget as fields — turns calls into a lead list |
| 7 | Human handoff SLA, as a headline feature | The #3 finding above. Escalate within N seconds, always available |
| 8 | Status page + published uptime | The #1 finding. Cheap to build, and the loudest unmet need in the category |
| 9 | WhatsApp handoff after a call | India's actual follow-up channel — and the hedge against the WhatsApp-first risk |

**#5 before any pricing work.** Sophisticated pricing is worthless if
customers churn at month 2.

---

## Then — expand the account

| # | Item | Why |
|---|------|-----|
| 10 | Bundles (Lite/Standard/Smart/Realtime) | Simple pricing, 59-71% margin, routing arbitrage is ours |
| 11 | Rate-card margin alerts | Managed keys means we absorb every provider price move |
| 12 | Published India latency benchmark | Reproducible, against Vapi/Retell/Bolna. Marketing weapon, not a chart |
| 13 | One-click cancel | Every competitor is hated for making this hard. Nearly free to differentiate on |
| 14 | Voice control inside the agent | "Book Mrs Sharma Tuesday 5pm" — a feature of the clinic product, not a product |
| 15 | Partner/reseller console | Dealers and MRs get logins and commission tracking |

---

## Later — the second product

Dictate (seat-priced, **USD from day one** — rupee revenue gets a rupee
multiple) and the widget SDK. Both real, neither funded. Not before 1-9 are
earning.

---

## Not building

| Item | Why |
|------|-----|
| Outbound cold-calling / bulk dialling in India | TRAI risk is existential. Revenue is tempting; consented callback only |
| Generic dictation | Apple and Google give it away free. Only contextual dictation into a business system is interesting, and that is unvalidated |
| Voice-agent testing as a standalone product | Crowded builder-tools market, few buyers. Ship enough analytics to retain agencies, do not position on it |
| BYOK below enterprise | Managed keys is where the margin is. BYOK customers are the ones who do not need us |
| An 18th LLM provider | 17 is already past the point of differentiation |
| EMR write-back / clinical manifests | Deferred with the healthcare-only strategy. Depth does not scale with commission reps |
| EU/US launch | US ~Q2 2027 at the earliest, EU later. ICP does not translate and managed defaults are India-tuned |

---

## Open decisions

1. **`VERIFICATION_CHANNEL=voice`** on the server — one line, unblocks trial
   signups placing their first call. No DLT needed for voice.
2. **Is the Indian SMB inbound demand phone-first or WhatsApp-first?** Field
   visits, not research.
3. **Bundle prices.** Costed at 59-71% margin in `COMPETITIVE-BUNDLES-2026.md`;
   needs a founder call before it goes on the pricing page.
