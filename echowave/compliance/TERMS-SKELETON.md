# Terms of Service — skeleton

Drafted the same way as the rest of `compliance/`: the factual half is filled
from the codebase, and every commercial or legal decision is marked
`[TO CONFIRM]` rather than filled with something plausible.

**Why this file exists at all.** `services/compliance/agreements.py` lists
`terms` with `required=True`, puts it in `SIGNUP_AGREEMENTS`, and blocks a
campaign until it is accepted. Every customer has therefore been asked to
accept a Terms of Service **that was never written**, at a URL that answers
404. Of the two gaps that is the worse one: the DPA at least exists in draft.

Take this to counsel as input. A terms of service is a commercial contract —
what is sold, what it costs, when it stops — and almost none of that is
derivable from a repository.

---

# TERMS OF SERVICE

**Nautomation Labs Private Limited**, CIN U62011TZ2026PTC039414, registered at
No. 86/16, Papanna Thottam, Brindhavan Nagar, TNHB Phase 7, Hosur, Krishnagiri
– 635109, Tamil Nadu, India, trading as **Decibyl** ("Decibyl", "we") and the
organisation accepting these terms ("Customer", "you").

## 1. The service

1.1 Decibyl is a platform on which a Customer configures software agents that
carry out tasks on the Customer's behalf — answering and placing telephone
calls, replying to messages, running scheduled work, and reading from documents
the Customer supplies.

1.2 The agents act **on the Customer's instructions and in the Customer's
name.** Decibyl supplies the platform; the Customer decides what its agents say
and do. For every purpose — contract, data protection, and the telecoms rules
of the recipient's country — **the Customer is the principal and Decibyl is the
supplier of a tool.** A call its agent places is the Customer's call.

## 2. Accounts and access

2.1 The Customer is responsible for its users, for their credentials, and for
everything done through its account.

2.2 Decibyl may suspend an account that is materially in breach, that is not
paid, or whose use presents a legal or security risk to Decibyl or to a third
party. For non-payment Decibyl gives **seven days' notice** before suspension.
For a legal or security risk, suspension may be immediate — a platform that must
wait a week before stopping a scam campaign is part of the problem.

## 3. Acceptable use

3.1 The Customer must not use the Services to make calls or send messages
without the consent the law of the recipient's country requires, nor in breach
of a do-not-call or do-not-disturb register.

3.2 **The Customer is responsible for the consent of the people its agents
contact.** Decibyl provides the do-not-call list, the consent attestations and
the calling-window controls; it does not and cannot verify that a given number
consented.

3.3 The Customer must not use the Services to impersonate a person without
their authority, to deceive a recipient about the fact that they are speaking
with an automated agent where the law requires that disclosure, or for any
unlawful purpose.

3.4 The Customer must not use the Services for any of the following. Each is
either unlawful in a market we serve, or regulated by a body we are not
registered with, or both:

**(a) Deception and extraction of money.** Fraudulent, deceptive or "scam"
calls of any kind. Pressuring, frightening or misleading a recipient into
making a payment, disclosing a payment instrument, an OTP, a password or any
credential. Impersonating a bank, a government body, a courier, a law
enforcement agency or a utility.

**(b) Impersonating a person or organisation** without that person's or
organisation's authority.

**(c) Concealing that the recipient is speaking with an automated agent**
where the recipient asks, or where the law of their location requires
disclosure. Several jurisdictions now require it; where they do not, honesty
is still a condition of using this platform.

**(d) Medical advice.** Agents must not diagnose, offer treatment advice, or
tell anybody whether to take, change or stop a medication. Booking, reminding,
confirming and answering factual questions about a practice are not advice and
are permitted.

**(e) Financial, investment, insurance or legal advice.** These require
registration with SEBI, IRDAI or a bar council, which the Customer must hold
directly if it advises.

**(f) Emergency or life-safety use.** The Services must not be relied on to
reach emergency services, and must not be placed anywhere a failure to connect
could cause harm.

**(g) Debt collection**, unless the Customer is licensed to collect and
complies with the collection rules of the recipient's country.

**(h) Political campaigning or election calls**, which carry their own
restrictions in every market we serve.

**(i) Calls to children**, or the deliberate collection of a child's personal
data. The DPDP Act's children's-data provisions apply and are strict.

**(j) Recording without disclosure.** Recording is disclosed to the recipient.
The Customer must not disable that disclosure where the law of either party's
location requires consent to record.

**(k) Circumventing a do-not-call or do-not-disturb register**, a revoked
consent, or a request to stop calling.

**(l) Adult content, gambling, or any product whose sale is restricted in the
recipient's state**, without the licence that market requires.

3.5 Decibyl may suspend immediately on reasonable belief that any of § 3.4 is
occurring. We would rather be wrong about one campaign than carry a fraud ring
on a platform that dials for clinics.

## 4. Charges

4.1 Charges are as published at https://decibyl.ai/pricing or as agreed in an
order form.

4.2 Usage is metered per component in that component's own unit — speech per
minute, synthesis per character, language models per token, carriage per
minute — and converted to credits. Credits are consumed as the Services are
used.

4.3 **Credits included in a plan expire at the end of the billing period in
which they were granted and do not carry over. Credits purchased as a top-up do
not expire.** Where an account holds both, plan credits are consumed first, so
the balance that expires is spent before the balance that does not.

4.4 Charges are not refundable, including on termination and for part-periods,
except where the law requires otherwise or where Decibyl has charged in error.
Unused credits are not exchangeable for money.

4.5 Taxes are payable in addition. Indian accounts are charged GST at the
prevailing rate. `[TO CONFIRM — the export-of-services position for accounts
outside India: a chartered accountant's question, not a drafting one, and the
answer changes what is invoiced.]`

## 5. Third-party services

5.1 The Services depend on third parties — telephony carriers, speech and
language model vendors, and any application the Customer connects. The current
list is at https://decibyl.ai/trust and is generated from the platform rather
than maintained by hand.

5.2 The Customer's use of a connected application is governed by that
application's own terms. Decibyl is not responsible for a third party's
availability or acts.

## 6. Customer content and data

6.1 The Customer owns its content — its documents, its contact lists, its
agents' configuration, and the recordings and transcripts of its calls.

6.2 Decibyl processes personal data in that content as a processor, under the
Data Processing Agreement, which forms part of these terms.

6.3 **Decibyl does not use Customer content to train models**, and requires the
same of its model vendors. `[TO CONFIRM — verify the opt-out is contractually
in place with every vendor before publishing this sentence. It is the single
most-read line in any AI terms of service and the most damaging to get wrong.]`

## 7. Availability

7.1 **No uptime commitment is given.** Decibyl operates from a single region
and will not promise a figure it cannot presently evidence. Status and incident
history are published; a service credit scheme may be offered under a separate
order form.

7.2 Decibyl may modify the Services. Where a change materially reduces
functionality the Customer relies on, Decibyl gives reasonable notice.

## 8. Term and termination

8.1 These terms apply from acceptance until the account is closed.

8.2 Either party may terminate for material breach not remedied within **30
days** of notice.

8.3 On termination the Customer may export its data for **90 days**, after
which it is deleted in accordance with the DPA. Financial records are retained
for eight years as the Companies Act 2013 s128 requires.

## 9. Warranties and liability

9.1 The Services are provided **as described and "as is"**. To the fullest
extent the law allows, Decibyl gives no other warranty, express or implied,
including any implied warranty of merchantability, fitness for a particular
purpose, or that the Services will be uninterrupted or error-free.

9.1.1 **Agents are probabilistic.** They are built on speech and language
models that can mishear, misunderstand, or produce an incorrect answer. The
Customer is responsible for reviewing what its agents are configured to say and
do, for testing them before use, and for the consequences of what they say. The
platform provides the test call, the transcript, the recording and the timeline
so the Customer can exercise that responsibility.

9.2 Each party's total liability under these terms is limited to the charges
paid by the Customer in the twelve months before the claim. **This cap does not
apply** to a breach of confidentiality, a personal data breach caused by that
party, infringement of the other party's intellectual property, fraud, or any
liability the law does not permit to be limited.

`[TO CONFIRM — counsel to confirm the figure. It must remain identical to § 12
of the DPA; two different caps in two documents is the defect a counterparty's
counsel finds first.]`

9.2.1 **Aggregate, not per-claim.** The cap in § 9.2 applies to all claims
taken together, however many there are and however they are framed.

9.2.2 **Excluded losses.** Neither party is liable for indirect or
consequential loss, or for loss of profit, revenue, goodwill, business,
anticipated savings, or data, however arising, even if advised it was possible.

9.2.3 **Time limit.** A claim must be brought within twelve months of the
Customer becoming aware of the facts giving rise to it.

9.2.4 **Third parties.** Decibyl is not liable for the acts, omissions,
availability, pricing or accuracy of a telephony carrier, a model vendor, a
payment processor or any application the Customer connects, beyond selecting
them with reasonable care.

## 9A. The Customer's responsibility for its own calls

9A.1 **The Customer decides who is contacted and what is said.** Decibyl
supplies the platform and does not originate, review or approve any campaign,
contact list, script or agent configuration.

9A.2 **The Customer warrants** that it has the consent every person its agents
contact is entitled to under the law of that person's location, that it holds
any licence or registration its use requires, and that its use complies with
§ 3.

9A.3 **The Customer will indemnify Decibyl** against any claim, penalty, fine
or regulatory action, and the reasonable cost of responding to one, arising
from: a breach of § 3 or § 9A.2; a call or message its agents made; content it
supplied; or personal data it instructed Decibyl to process. **This indemnity
is not subject to the cap in § 9.2** — a cap that limited it would mean Decibyl
paying for a customer's unlawful campaign out of that customer's own
subscription fee.

9A.4 Decibyl may disclose what a regulator or a court lawfully requires about a
Customer's use, and will tell the Customer unless prohibited from doing so.

9.3 Nothing in these terms excludes or limits liability that cannot lawfully be
excluded or limited — including for death or personal injury caused by
negligence, for fraud or fraudulent misrepresentation, and for any liability
under applicable data protection law that the law does not permit to be
limited. **Where a court finds any limitation in § 9 unenforceable, it is to be
read down to the maximum the law permits rather than struck out.**

## 10. General

10.1 These terms are governed by the laws of India, and the courts of
**Chennai** have exclusive jurisdiction — the same seat as the DPA.

10.2 Decibyl may update these terms. A change that alters what the Customer is
agreeing to is published with a new version, and acceptance is requested again
— the acceptance record stores the version, so an old acceptance is never
treated as agreement to a new document.

10.3 These terms, the DPA and any order form are the whole agreement.

---

## What is already true in code, and should not be re-decided

| Clause | Enforced by |
|---|---|
| Versioned re-acceptance (10.2) | `compliance/agreements.py` — acceptance rows store the version and are never updated |
| Per-component metering (4.2) | `billing/cost_engine.py`, `enums.RateUnit` |
| Plan vs top-up credit (4.3) | `CreditLedgerKind.PLAN` / `TOPUP` |
| DNC and calling windows (3.2) | `compliance/dnd.py`, `do_not_call_entries` |
| Deletion on termination (8.3) | retention policies, `DEFAULT_RECORDING_RETENTION_DAYS` |
