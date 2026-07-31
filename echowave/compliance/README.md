# Compliance documents

Five documents a regulator, an auditor or an enterprise customer's security team
will ask for. They are drafts, and the split between what is drafted here and
what is not is deliberate.

**What is here** is the factual half: what data the system holds, where it goes,
how long it stays, what protects it, and which of that is enforced by code
rather than by intention. Those facts are derivable from the codebase, and a
lawyer who cannot read the codebase would have to ask or guess — guessing
produces a document that is confidently wrong in a way nobody notices until it
matters.

**What is not here** is the legal half: the lawful basis you rely on, the
contractual terms, the retention justification for your own market, the risk
appetite in a DPIA. Those are decisions, not facts, and they are not ours to
make.

So: take these to a lawyer as input, not as output. Every place a decision or a
company-specific detail is needed is marked `[TO CONFIRM]` rather than filled
with something plausible.

| File | What it is | Who asks for it |
|---|---|---|
| `ROPA.md` | Records of processing activities | GDPR Art 30 — a supervisory authority, on request |
| `DPA-ANNEX-II.md` | Technical and organisational measures | GDPR Art 32 — every enterprise customer's DPA |
| `PRIVACY-NOTICE-FACTS.md` | The factual sections of a privacy notice | DPDP s5, GDPR Arts 13–14 — published |
| `TRUST.md` | Trust page content | Prospects, during procurement |
| `DPA-TEMPLATE.md` | The processing agreement to send customers | DPDP s8(2) — every customer, before their first campaign |

`../PRIVACY.md` is the engineering companion to all five: what the controls are
and why they are built the way they are.

## Keeping them true

These describe running code, so they go stale the way code does. The parts most
likely to drift, and where the truth lives:

| Claim | Source of truth |
|---|---|
| Retention windows | `api/constants.py`, `data_retention_policies` |
| Sub-processors | `GET /api/v1/privacy/subprocessors` — derived, not a list to edit |
| What is logged on access | `api/services/privacy/access_log.py` |
| Encryption and key handling | `api/services/security/`, `api/utils/auth.py` |

The sub-processor list is the one that matters most and the one hand-maintained
documents always get wrong: a stale sub-processor list is worse than none,
because it is a specific written claim that is now false. Read it from the
endpoint rather than copying it into a document.

## What no document can supply

No certification is claimed anywhere in these files, and none should be added
until it exists. SOC 2 is an audit, HIPAA needs a BAA with every sub-processor
touching PHI, and ISO 27001 is a management system with a certificate number.
Writing "SOC 2 aligned" on a trust page is a claim a procurement team will ask
you to evidence, and having to withdraw it costs more than never having made it.
