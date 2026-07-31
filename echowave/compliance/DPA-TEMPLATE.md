# Data Processing Agreement — template

**This is drafting input, not legal advice, and it has not been reviewed by a
lawyer.** It is written to be a short, specific brief a solicitor can turn into
an executable document in one pass rather than five, because every factual
statement in it is taken from what the platform actually does — the retention
windows, the sub-processor list, the breach-scoping capability and the erasure
mechanism are all real and all verifiable in the running system. The legal
architecture around those facts still needs a professional.

Everything that could be settled by reading the code has been settled, and the
security annex was written against the running system rather than from
recollection — which turned up three gaps that are now stated plainly in Annex B
instead of quietly omitted. What is left is six decisions. None of them is a
fact anybody can look up; all of them are yours.

| # | Decision | Where | Why it cannot be defaulted |
|---|---|---|---|
| 1 | **Fix or disclose backups** | Annex B | There is no automated backup. This blocks sending the document at all — see the warning in Annex B. |
| 2 | **Liability cap** — inside or outside the general cap | § 12 | The most negotiated term in any DPA. Take advice before offering either. |
| 3 | **How it is accepted** — click-through or signed | § Acceptance | Commercial. The click-through route needs an acceptance record built first; see the closing note. |
| 4 | **Multi-factor authentication** — build it, or answer "no" | Annex B | The most requested control in a security review, and the cheapest listed to add. |
| 5 | **Encryption at rest** — verify the cloud volume setting | Annex B | Verifiable in five minutes, but only by someone with console access. Do not claim it unchecked. |
| 6 | **Personnel controls** | Annex B | Facts about how you employ people. |

A seventh applies only if you sell into the EU: § 10.2 needs an SCC module and a
transfer impact assessment. Ignore it until an EU-established customer appears.

Three items previously left open have been given the settled market default —
30 days' sub-processor change notice, 30 days to delete on termination, and
Chennai as the seat. Each is marked so you can see it was chosen rather than
researched, and each is safe to leave as it stands.

Anything still marked `[TO CONFIRM]` is a decision nobody has made yet. Do not
send it to a customer with those markers in place.

---

# DATA PROCESSING AGREEMENT

This Data Processing Agreement (**"DPA"**) forms part of the Agreement between:

**NAUTOMATION LABS PRIVATE LIMITED**, a company incorporated under the
Companies Act, 2013, having its registered office at No.86/18, Papanna Thottam,
Brindhavan Nagar, TNHB PH-7, Hosur – 635109, Krishnagiri District, Tamil Nadu,
India, operating the Decibyl platform (**"Decibyl"**, **"we"**, **"Processor"**),

and

the customer identified in the Agreement (**"Customer"**, **"you"**,
**"Data Fiduciary"**).

## 1. Definitions

1.1 **"DPDP Act"** means the Digital Personal Data Protection Act, 2023 and the
rules made under it.

1.2 **"GDPR"** means Regulation (EU) 2016/679, where applicable.

1.3 **"Personal Data"**, **"Data Principal"**, **"Data Fiduciary"** and
**"Data Processor"** have the meanings given in the DPDP Act. Where the GDPR
applies, "Controller" and "Processor" are to be read as corresponding to Data
Fiduciary and Data Processor respectively.

1.4 **"Call Data"** means audio recordings, transcripts, telephone numbers,
call metadata and any variables the Customer supplies or the Services extract
during a call.

1.5 **"Sub-processor"** means any third party engaged by Decibyl to process
Personal Data on the Customer's behalf.

## 2. Roles of the parties

2.1 **The Customer is the Data Fiduciary. Decibyl is the Data Processor.**

2.2 The Customer determines the purposes and means of processing: which numbers
are called, what the agent says, what data is collected, and how long it is
kept. Decibyl processes Personal Data only to provide the Services and only on
the Customer's documented instructions.

2.3 **This allocation is the foundation of this DPA.** It reflects the reality
that the Customer sources the contact list, defines the campaign and holds the
relationship with the Data Principal. Decibyl has no relationship with, and no
means of contacting, the people the Customer calls.

2.4 Decibyl acts as a Data Fiduciary in its own right only in respect of the
Customer's own account data — the names, email addresses and billing details of
the Customer's users. That processing is governed by the Decibyl Privacy Notice,
not by this DPA.

## 3. Scope of processing

| | |
|---|---|
| **Subject matter** | Provision of the Decibyl conversational voice-AI platform |
| **Duration** | The term of the Agreement, plus the retention periods in § 8 |
| **Nature** | Placing and receiving calls; speech recognition; language-model inference; speech synthesis; recording; transcription; storage; analytics |
| **Purpose** | Operating automated voice conversations as configured by the Customer |
| **Categories of Data Principal** | The individuals the Customer calls or who call the Customer; the Customer's own personnel using the platform |
| **Types of Personal Data** | Telephone numbers; voice recordings; transcripts of what was said; any personal data the Customer uploads as campaign variables or the agent collects during a call; call metadata (time, duration, outcome) |

3.2 **The Customer must not upload, and must not configure its agents to
collect, special-category or sensitive personal data** — including health,
financial account credentials, biometric data or government identifiers —
unless the parties have agreed additional measures in writing. The Services are
not configured for that data and no additional safeguards for it are implied by
this DPA.

## 4. Customer obligations

**This section is the one that matters most and is most often ignored.**

4.1 The Customer warrants that, for every telephone number it uploads or causes
to be called, it has a lawful basis under the DPDP Act — in most cases the free,
specific, informed and unambiguous consent of the Data Principal, obtained with
the notice required by section 5 of the DPDP Act.

4.2 The Customer warrants that it has given, or will give, that notice —
including the fact that an automated voice system may call, that the call may be
recorded, and the purposes for which the data will be used.

4.3 **Telemarketing.** Where a call constitutes commercial communication, the
Customer is responsible for compliance with the Telecom Commercial
Communications Customer Preference Regulations and the DND registry maintained
under them, including registration as a sender where required, scrubbing against
customer preferences, and observing permitted calling hours. **Decibyl does not
scrub against the DND registry on the Customer's behalf and does not verify that
the Customer is entitled to call any number it supplies.**

4.4 The Customer must not use the Services to place calls it is not lawfully
entitled to place, and indemnifies Decibyl against claims arising from its
failure to hold a lawful basis for the numbers it supplies.

4.5 The Customer is responsible for responding to Data Principals who exercise
their rights, using the tools described in § 7.

## 5. Decibyl's obligations

5.1 **Instructions.** Decibyl processes Personal Data only on the Customer's
documented instructions, which comprise the Agreement, this DPA, and the
Customer's configuration of the Services. Decibyl will inform the Customer if,
in its opinion, an instruction infringes applicable data protection law.

5.2 **Confidentiality.** Personnel with access to Personal Data are bound by
confidentiality obligations and are granted access on a need-to-know basis.

5.3 **Security.** Decibyl maintains the technical and organisational measures
set out in Annex B.

5.4 **Recording disclosure.** The Services speak a disclosure at the start of
each recorded call, before the agent's greeting, so that the notice forms part
of the recording itself. The Customer may vary the wording; the Customer is
responsible for the wording it configures. Disabling the disclosure is a
Customer decision with Customer consequences.

5.5 **No secondary use.** Decibyl does not sell Personal Data and **does not use
Customer Call Data to train, fine-tune or evaluate any machine-learning model,**
whether its own or a third party's.

## 6. Sub-processors

6.1 The Customer authorises Decibyl to engage Sub-processors, subject to this
section.

6.2 **The current list of Sub-processors for the Customer's own account is
available at any time from the platform** at `GET /api/v1/privacy/subprocessors`.
That list is derived from the Customer's actual configuration rather than
maintained by hand, so it reflects the providers that account's data genuinely
reaches — a customer using only Indic providers does not appear to be sending
data to vendors it never uses.

6.3 Decibyl imposes on each Sub-processor data protection obligations no less
protective than those in this DPA, and remains liable to the Customer for a
Sub-processor's performance.

6.4 **Change notice.** Decibyl will give the Customer at least **30 days'** notice before engaging a new Sub-processor that will
process the Customer's Call Data. The Customer may object on reasonable data
protection grounds, in which case the parties will discuss in good faith; if no
resolution is reached the Customer may terminate the affected Services without
penalty.

6.5 A Sub-processor the Customer chooses itself — where the Customer supplies
its own provider API keys — is engaged by the Customer directly, and Decibyl is
not responsible for that provider's processing beyond transmitting the data as
instructed.

## 7. Data Principal rights

7.1 Decibyl provides the following, which the Customer may use to meet its own
obligations:

| Right | How it is met |
|---|---|
| Access / portability | Machine-readable export of all Call Data for a number or an account |
| Erasure | Deletion of recordings, transcripts and collected variables for a number, across every call |
| Correction | The Customer may edit or delete campaign data directly |
| Grievance | Contact details of Decibyl's Grievance Officer are published in-product and at Annex C |

7.2 **Erasure is real deletion, not flagging.** Stored audio objects are deleted
before the database row is cleared, so a row can never report "erased" while the
recording still exists.

7.3 Billing records — call duration, cost and tax documents — survive erasure.
Retention of those records is required by tax law and is expressly permitted by
the DPDP Act and by GDPR Article 17(3)(b). What is retained is the arithmetic of
a call, not its content.

7.4 If a Data Principal contacts Decibyl directly, Decibyl will not respond
substantively but will refer them to the Customer and inform the Customer
without undue delay.

## 8. Retention and deletion

8.1 Default retention periods, which the Customer may shorten in-product:

| Data | Default |
|---|---|
| Audio recordings | 90 days |
| Transcripts and collected variables | 365 days |
| Billing and tax records | As required by Indian tax law |

8.2 Audio and text are aged separately by design: a recording is a person's
voice and is rarely useful a month later, while a transcript is what reporting
actually reads.

8.3 Deletion is enforced by an automated nightly job, not by request. Decibyl
monitors the count of records past their retention window; the target is zero.

8.4 On termination, Decibyl will delete Call Data within **30 days**, except records it is required by law to retain.

## 9. Personal Data Breach

9.1 Decibyl will notify the Customer **without undue delay and in any event
within 48 hours** of becoming aware of a Personal Data Breach affecting the
Customer's data.

9.2 The notification will describe the nature of the breach, the categories and
approximate volume of data concerned, the likely consequences, and the measures
taken.

9.3 Decibyl maintains an access log recording every retrieval of a recording or
transcript, with the identity of the accessor and the time. This makes it
possible to state which specific calls were reached during a given window — the
question a regulator asks and the one that cannot be answered retrospectively if
the log was not being written.

9.4 Notification to the Data Protection Board of India and to affected Data
Principals is the Customer's responsibility as Data Fiduciary. Decibyl will
provide the information reasonably required to make it.

## 10. International transfers

10.1 The Services use Sub-processors located outside India, listed per § 6.2.
The DPDP Act permits transfer of personal data outside India except to countries
restricted by notification of the Central Government.

10.2 Where the GDPR applies, transfers outside the EEA are made under the
European Commission's Standard Contractual Clauses, which are incorporated by
reference. `[TO CONFIRM — the SCC module and a transfer impact assessment are
required before selling to EU-established customers.]`

10.3 The Customer may restrict processing to providers in a given region by
configuring its account accordingly; Decibyl does not warrant that every
component is available in every region.

## 11. Audit

11.1 Decibyl will make available to the Customer the information reasonably
necessary to demonstrate compliance with this DPA.

11.2 The Customer may audit no more than once in any twelve-month period, on at
least 30 days' written notice, during business hours, subject to
confidentiality, and at the Customer's cost — save where the audit reveals
material non-compliance.

11.3 Decibyl may satisfy an audit request by providing a current third-party
certification or report where one exists. `[TO CONFIRM — Decibyl holds no
security certification at the date of this template. Do not state or imply
otherwise.]`

## 12. Liability

12.1 Each party's liability under this DPA is subject to the limitations in the
Agreement. `[TO CONFIRM — whether data protection liability sits inside or
outside the general cap is the single most negotiated term in any DPA. Take
advice before offering either.]`

## 13. Term, governing law and precedence

13.1 This DPA takes effect on the Effective Date of the Agreement and continues
for as long as Decibyl processes Personal Data on the Customer's behalf.

13.2 This DPA is governed by the laws of India, and the courts at
**Chennai** have exclusive jurisdiction. `[TO CONFIRM — Chennai is the seat of
the High Court for Tamil Nadu, where Decibyl is registered, so it is the natural
default. Change it if your counsel prefers the Krishnagiri district courts or a
neutral commercial seat.]`

13.3 In the event of conflict, this DPA prevails over the Agreement in respect
of data protection.

## Acceptance

`[TO CONFIRM — choose one and delete the other.]`

**Option A — accepted online.** This DPA is incorporated by reference into the
Decibyl Terms of Service and is accepted when the Customer's authorised user
affirmatively accepts those Terms. No signature is required. Decibyl records the
version accepted, the accepting user, and the time of acceptance.

**Option B — signed.** Executed by the authorised representatives of both
parties below.

| | Decibyl | Customer |
|---|---|---|
| Name | | |
| Title | | |
| Signature | | |
| Date | | |

---

## Annex A — Processing details

As set out in § 3.

## Annex B — Technical and organisational measures

Every item below is implemented in the platform as at the date of this document.
Do not add to this list without checking the code first; an overstated security
annex is a misrepresentation in a contract, not marketing copy.

| Measure | What is in place |
|---|---|
| Encryption in transit | TLS 1.2+ on all API and web traffic, with HTTP redirected to HTTPS and certificates issued by Let's Encrypt. Transport to telephony and model providers is HTTPS/WSS. |
| Access to recordings | Time-limited presigned URLs only. The storage bucket grants no anonymous access. |
| Access logging | Every retrieval of a recording or transcript is recorded with accessor identity and timestamp |
| Tenant isolation | Every data access is scoped by organisation at the query level |
| Credential storage | Provider API keys are encrypted at rest with AES-128-CBC + HMAC-SHA256 (Fernet). The system refuses to store a key at all if the encryption secret is absent, rather than falling back to plaintext. |
| Password storage | bcrypt with a per-password salt. Passwords are never stored or logged in recoverable form. |
| Session tokens | Signed JSON Web Tokens (HS256) with a bounded lifetime |
| Retention enforcement | Automated nightly deletion against per-account windows, monitored for overdue records |
| Erasure | Storage objects deleted before database references are cleared |

**Measures not currently in place.** Stated because a security annex that omits
them implies them, and a customer discovering the gap later has been misled by
the omission rather than by a sentence.

| Measure | Position |
|---|---|
| Multi-factor authentication | Not available. Access is by password and bearer token. `[TO CONFIRM — this is the most commonly requested control in a security review and the cheapest of these to add. Decide whether to build it before answering a questionnaire that asks.]` |
| Encryption at rest (database and object storage) | Not applied at the application layer; it depends on the encryption setting of the underlying cloud volume. `[TO CONFIRM — verify whether the EBS volume and any S3 bucket in use are encrypted, and state the answer. Do not claim it unverified.]` |
| Backups | **No automated backup is configured.** `[TO CONFIRM — see the note below. This must be fixed before it is described to a customer either way.]` |
| Redundancy and failover | Single region, single host, no automated failover. Recovery is a manual redeploy. |
| Security certification | None held. See § 11.3. |
| Personnel controls | `[TO CONFIRM — background checks, confidentiality undertakings and security training are facts about how you employ people, and only you know them.]` |

> **Do not send this document to a customer until the backup line is resolved.**
> The credit ledger is the only record of what every customer has paid; there is
> no other copy of it. This is a larger commercial risk than any clause in the
> agreement above, and it is the one thing on this page that could end the
> business rather than cost it a customer.

## Annex C — Contacts

| | |
|---|---|
| Grievance Officer | `[TO CONFIRM — name]` |
| Email | privacy@decibyl.ai |
| Postal | No.86/18, Papanna Thottam, Brindhavan Nagar, TNHB PH-7, Hosur – 635109, Krishnagiri District, Tamil Nadu, India |
| Security contact | security@decibyl.ai |

The current Grievance Officer details are also served live at
`GET /api/v1/privacy/subprocessors`, so the contract and the product cannot
drift apart on who it is.

---

## Note on click-through acceptance

Option A is enforceable in India and is what the larger vendors do. Section 10A
of the Information Technology Act, 2000 provides that a contract is not
unenforceable merely because it was formed electronically, and Indian courts
have upheld click-wrap agreements where the terms were reasonably notified,
accessible before acceptance, and accepted by an affirmative act.

Three conditions have to hold, and the third is the one that is usually missed:

1. **Reasonable notice.** A visible link to the DPA at the point of acceptance,
   not buried in a footer.
2. **Affirmative action.** An unticked checkbox the user must tick, or an
   "I agree" button. A pre-ticked box is not acceptance, and continuing to use
   the site is not acceptance.
3. **A record.** Which version was accepted, by which user, at what time, from
   what address. **Without that record there is nothing to produce in a dispute,
   and the click-wrap is worth very little.** This is an engineering
   requirement, not a drafting one — the platform does not currently store it.

Two things a click-through cannot do, however it is worded:

- **It cannot create the consent of the person being called.** That consent is
  between the Customer and the Data Principal. A Customer clicking "I agree" is
  warranting it holds that consent; it is not obtaining it.
- **It will not satisfy every enterprise buyer.** Large customers and regulated
  industries will want a negotiated, signed document regardless. Expect to
  maintain both: click-through for self-serve, a signable version for deals
  above `[TO CONFIRM]`.
