# Records of processing activities

GDPR Art 30. A supervisory authority can ask for this and expect it to already
exist; assembling it after the request is itself evidence.

Decibyl processes in two distinct roles, and conflating them is the most common
mistake in a voice-AI ROPA. Sections 1–4 are **processor** activities: the
customer decided to call somebody, and we carry out that instruction. Sections
5–8 are **controller** activities: our own relationship with our own customer,
where nobody instructs us.

**Controller:** [TO CONFIRM — legal entity, registered address]
**Representative in the EU (Art 27):** [TO CONFIRM — required if EU data
subjects are targeted and no EU establishment exists]
**Grievance officer (DPDP s13):** set via `GRIEVANCE_OFFICER_NAME` /
`GRIEVANCE_OFFICER_EMAIL`, published at `GET /api/v1/privacy/subprocessors`
**Last reviewed:** [TO CONFIRM — date, and by whom]

---

## Part A — As processor, on customer instruction

### A1. Call audio and recordings

| | |
|---|---|
| **Purpose** | Conducting the conversation the customer's agent was configured to have, and retaining a recording for the customer's own quality review |
| **Data subjects** | People our customers call, or who call them. Not our customers' own staff. |
| **Categories** | Voice recording; phone number; anything the person says during the call |
| **Special categories** | Not sought. **Voice is biometric data only when processed for identification** — we do not do speaker identification or voiceprinting, so Art 9 is not engaged by the recording itself. Content the person volunteers may nonetheless be special category (health, for example), which is why sector configuration matters. |
| **Recipients** | STT vendor, LLM vendor, TTS vendor and the carrier used for that call. Per call, derivable from `call_cost_items` and returned by `GET /api/v1/privacy/subprocessors`. |
| **Retention** | `DEFAULT_RECORDING_RETENTION_DAYS`, default **90 days**; per-account override in `data_retention_policies`, minimum 1 day. Enforced nightly at 19:00 UTC by `purge_expired_call_data` — objects deleted from storage first, then the row cleared. |
| **Transfers** | Hosting region is the deployment's own. Model vendors are predominantly US. [TO CONFIRM — SCCs and a transfer impact assessment where EU personal data is in scope] |
| **Security** | Presigned URLs only, expiring; no public bucket policy; every access recorded in `data_access_log` |
| **Lawful basis** | The customer's, not ours. We act on documented instruction — Art 28. |

Disclosure that the call is recorded is spoken by the agent in its first turn
(`RECORDING_DISCLOSURE_TEXT`) unless a workflow explicitly opts out. Because the
disclosure is spoken into the call, it is present in the recording and the
transcript — the artefact is the evidence it happened.

### A2. Transcripts and conversation context

| | |
|---|---|
| **Purpose** | Driving the conversation; customer's reporting and quality review; populating the customer's own CRM through configured integrations |
| **Data subjects** | As A1 |
| **Categories** | Transcribed speech; variables the customer's workflow gathered (names, appointment times, dispositions — customer-defined) |
| **Recipients** | LLM vendor for the call; any integration the customer configured (`integrations`, `webhook_deliveries`) |
| **Retention** | `DEFAULT_TRANSCRIPT_RETENTION_DAYS`, default **365 days**, override per account. Deliberately longer than audio: a recording is a person's voice and rarely useful a month later; a transcript is text and is what reporting actually reads. |
| **Security** | As A1 |

### A3. Contact lists loaded for outbound campaigns

| | |
|---|---|
| **Purpose** | Placing the calls a customer's campaign was configured to place |
| **Data subjects** | People on the customer's list |
| **Categories** | Phone number, plus whatever fields the customer uploaded for personalisation |
| **Retention** | For the life of the campaign, then the account's transcript window. Deleted with the account. |
| **Note** | The customer warrants they may lawfully call these people. We have no way to verify consent that was collected before the data reached us, and the DPA should say so explicitly rather than leave it implied. |

### A4. Erasure and access requests received about call data

| | |
|---|---|
| **Purpose** | Executing DPDP s12(3) / GDPR Art 17 erasure and Art 20 export on the customer's instruction |
| **Categories** | A **SHA-256 hash of the phone number**, never the number. A register of people who asked to be forgotten is itself personal data, and a sensitive one. |
| **Retention** | Indefinite, deliberately. The record of an erasure is not the erased data; destroying it removes the only evidence the obligation was met. |
| **Table** | `erasure_requests` |

---

## Part B — As controller, for our own business

### B5. Customer accounts

| | |
|---|---|
| **Purpose** | Providing the service; authentication; support |
| **Data subjects** | Our customers' staff who hold logins |
| **Categories** | Email, password hash (**bcrypt**), organization membership, superuser flag. `users`, `organizations`. |
| **Lawful basis** | Performance of a contract — GDPR Art 6(1)(b) |
| **Retention** | Life of the account plus [TO CONFIRM] |

### B6. Billing, payments and tax documents

| | |
|---|---|
| **Purpose** | Charging for usage; issuing GST-compliant receipt vouchers and monthly tax invoices |
| **Categories** | Legal name, billing address, GSTIN, state code (`billing_profiles`); payment metadata and Razorpay order/payment ids (`payments`); credit movements (`credit_ledger`); issued documents (`tax_documents`) |
| **Card data** | **Never reaches us.** Razorpay is the payment processor; we hold their identifiers and the amount. |
| **Lawful basis** | Contract, and legal obligation for the tax records |
| **Retention** | **Longer than everything else, and this is intentional.** Indian GST record-keeping obliges retention for years after the conversation being billed should have been forgotten. GDPR Art 17(3)(b) carves out exactly this. What survives an erasure is "this call lasted 94 seconds and cost ₹3.20", which identifies nobody. |

### B7. KYC for telephony provisioning

| | |
|---|---|
| **Purpose** | Carrier and regulatory requirements for issuing phone numbers |
| **Categories** | Identity and address documents uploaded by the customer (`organization_kyc`, `kyc_documents`) |
| **Special categories** | Government identity documents. Held in a **separate bucket** (`KYC_BUCKET`) that has never had a public policy. |
| **Recipients** | The carrier the number is provisioned with |
| **Lawful basis** | Legal obligation on the carrier, passed through contractually |
| **Retention** | [TO CONFIRM — carrier requirements typically outlive the account] |

### B8. Access and audit logs

| | |
|---|---|
| **Purpose** | Answering "who reached my recording", and scoping a breach within the 72 hours Art 33 allows |
| **Categories** | User id or "unauthenticated", resource type and id, IP address, timestamp (`data_access_log`); billing changes (`billing_audit_log`) |
| **Lawful basis** | Legitimate interest in security and accountability — Art 6(1)(f); and Art 33 itself |
| **Retention** | [TO CONFIRM — must outlive the incident detection window it exists to serve] |
| **Note** | Access through a public share link is recorded with a null user id and the actor kind, which is precisely the case worth being able to see afterwards. |

---

## What this record deliberately does not claim

- **No profiling or automated decision-making with legal effect** (Art 22). The
  agent conducts a conversation; it does not decide anything about the person.
- **No training on customer data.** We do not train models. Whether a *vendor*
  trains on data sent to them is a setting in that vendor's account and belongs
  in the sub-processor assessment, not here. [TO CONFIRM — verified per vendor]
- **No sale or sharing for advertising.**
- **No certification.** SOC 2, ISO 27001 and HIPAA are absent from this document
  because none is held.
