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

**Decibyl** `[TO CONFIRM — legal entity name, CIN, registered address]`
("Decibyl", "we") and the organisation accepting these terms ("Customer",
"you").

## 1. The service

1.1 Decibyl is a platform on which a Customer configures software agents that
carry out tasks on the Customer's behalf — answering and placing telephone
calls, replying to messages, running scheduled work, and reading from documents
the Customer supplies.

1.2 The agents act **on the Customer's instructions and in the Customer's
name.** Decibyl supplies the platform; the Customer decides what its agents say
and do.

## 2. Accounts and access

2.1 The Customer is responsible for its users, for their credentials, and for
everything done through its account.

2.2 Decibyl may suspend an account that is materially in breach, that is not
paid, or whose use presents a legal or security risk to Decibyl or to a third
party. Where practicable Decibyl gives notice first.
`[TO CONFIRM — notice period before suspension for non-payment.]`

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

3.4 `[TO CONFIRM — whether to prohibit specific categories outright: political
campaigning, debt collection, healthcare triage, financial advice. Each is a
commercial decision about which markets to serve and each carries its own
regulator.]`

## 4. Charges

4.1 Charges are as published at `[TO CONFIRM — pricing page URL]` or as agreed
in an order form.

4.2 Usage is metered per component in that component's own unit — speech per
minute, synthesis per character, language models per token, carriage per
minute — and converted to credits. Credits are consumed as the Services are
used.

4.3 `[TO CONFIRM — whether plan credits expire at the end of a billing cycle
while purchased top-ups do not. The ledger already distinguishes the two; the
terms must say which.]`

4.4 `[TO CONFIRM — refund position. Prepaid balances, part-months, and whether
unused credit is refundable on termination.]`

4.5 Taxes are payable in addition. `[TO CONFIRM — GST treatment for domestic
accounts and the export position for accounts outside India.]`

## 5. Third-party services

5.1 The Services depend on third parties — telephony carriers, speech and
language model vendors, and any application the Customer connects. They are
listed at `[TO CONFIRM — public sub-processor URL]`.

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

7.1 `[TO CONFIRM — whether any uptime commitment is given. A number here is a
promise; none is honest for a single-region deployment. Decide before a
customer asks rather than during the conversation.]`

7.2 Decibyl may modify the Services. Where a change materially reduces
functionality the Customer relies on, Decibyl gives reasonable notice.

## 8. Term and termination

8.1 These terms apply from acceptance until the account is closed.

8.2 Either party may terminate for material breach not remedied within
`[TO CONFIRM — cure period]` days of notice.

8.3 On termination the Customer may export its data for
`[TO CONFIRM — export window]`, after which it is deleted in accordance with
the DPA.

## 9. Warranties and liability

9.1 The Services are provided as described and without other warranty to the
extent the law allows.

9.2 `[TO CONFIRM — liability cap. Must be consistent with § 12 of the DPA; two
different caps in two documents is the defect a counterparty's counsel finds
first.]`

9.3 Neither party excludes liability it cannot lawfully exclude.

## 10. General

10.1 Governing law and jurisdiction: `[TO CONFIRM — must match the DPA's seat.]`

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
