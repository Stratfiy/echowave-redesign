# Privacy notice — the factual sections

For decibyl.ai/privacy. **Not a finished notice.** What is drafted here is the
part a lawyer would otherwise have to ask us for or invent: what is actually
collected, where it actually goes, and how long it actually stays. What is not
drafted is the part that is a legal decision — lawful bases, warranties,
governing law, the children's-data position.

Written in plain language on purpose. DPDP s5 requires a notice a Data Principal
can understand, and GDPR Art 12 requires "concise, transparent, intelligible".
Both are failed by the same paragraph of defensive drafting.

---

## Two audiences, and the notice must say which is which

This is the structural decision to get right, and most voice-AI privacy notices
get it wrong by writing only for the first audience.

1. **Customers** — businesses that sign up and build agents. We are the
   controller of their account data.
2. **People our customers call** — who never visited the site and never agreed
   to anything with us. For their conversation we are a **processor**: the
   business that called them decided to, and their rights are exercised against
   that business, not against us.

The notice should say plainly that if you received a call and want your data
deleted, the fastest route is the company that called you — and that we will
support them in doing it. Sending that person in a circle is the complaint that
gets escalated.

---

## Section: What we collect

### If you are a customer

- Your email address and a password (stored hashed, never readable).
- Your organisation's billing details: legal name, address, GSTIN where you have
  one, and state — needed to issue a valid tax invoice.
- Payment identifiers from Razorpay. **We never see or store card numbers.**
- Identity documents, if you request phone numbers, because carriers require KYC
  before issuing them.
- Usage: calls placed, their duration, their cost.

### If we called you on a customer's behalf

- The recording of the call.
- A transcript of it.
- Your phone number.
- Whatever information you gave during the conversation, as the business that
  called you configured their agent to collect.

**You are told at the start of the call that it is recorded.** The agent says so
in its first sentence, before anything else. [TO CONFIRM — say here whether
customers can disable this on your platform, and under what conditions you
permit it.]

---

## Section: How long we keep it

| What | How long | Why that long |
|---|---|---|
| Call recordings | **90 days** by default | A voice is among the most identifying data there is, and is rarely useful a month after the call |
| Transcripts | **365 days** by default | Text, far less sensitive, and what quality review and reporting actually read |
| Billing and tax records | Longer — required by Indian GST law | Retained after the conversation itself is deleted. What remains is a duration and an amount, which identifies nobody. |
| Account details | While the account is open | [TO CONFIRM — plus what period after closure] |
| Record that someone asked for erasure | Kept | It is the evidence the request was honoured. It contains a one-way hash of the number, not the number. |

Deletion is automatic, not on request: a job runs every night and deletes what
has passed its window, from storage as well as from the database.

Customers can set shorter windows for their own account. The minimum is one day.

---

## Section: Who else sees it

A call is carried by a phone network and processed by speech recognition, a
language model and speech synthesis. Those are separate companies and they
receive the audio or its text.

**We publish the current list, and it is generated from the system rather than
maintained by hand** — so it cannot quietly go out of date. Customers see the
vendors that handled *their* calls at
`https://decibyl.ai/app/privacy/subprocessors`. [TO CONFIRM — the public URL you
will host the general list at.]

Beyond those: Amazon Web Services hosts the platform, and Razorpay processes
payments.

We do not sell data, we do not share it for advertising, and we do not train AI
models on it. [TO CONFIRM — whether every vendor account is on terms that
exclude *their* training on data sent to them. Most vendors have a setting and
the default is not always the one you want. Do not publish this sentence until
each is verified.]

---

## Section: Your rights

Customers can, from inside the product:

- **Export** everything held about a phone number or about the whole account, as
  a machine-readable file.
- **Delete** a phone number's data across every call, or the account's call
  content entirely.
- **See who accessed** a recording or transcript, and when.
- **Change how long** recordings and transcripts are kept.

If you received a call and want your data erased, contact the business that
called you. If you cannot identify them, contact us and we will help — but we
will not confirm whether your number appears in a particular customer's account,
because doing so would itself disclose information about you to whoever asked.

**Grievance officer (DPDP s13):** [TO CONFIRM — name, email, postal address. Set
via `GRIEVANCE_OFFICER_NAME`, `GRIEVANCE_OFFICER_EMAIL` and
`GRIEVANCE_OFFICER_ADDRESS`, so the site and the product cannot disagree about
who it is.]

Response time: [TO CONFIRM — GDPR Art 12(3) sets one month; DPDP Rules set
specific periods. State one and meet it.]

---

## Section: Where it is processed

[TO CONFIRM] The platform runs in a US region and several AI vendors are US
companies. If you are in the EEA or the UK, your data is transferred outside it
and that needs a stated legal mechanism — Standard Contractual Clauses — and a
transfer impact assessment behind them. Do not publish a location claim until
this is decided; it is one of the first things a regulator checks and one of the
easiest to disprove.

---

## Left for legal

Not drafted here, on purpose:

- Lawful basis for each purpose, and legitimate-interest assessments
- Children's data — DPDP s9 bans behavioural tracking of children and requires
  verifiable parental consent, and a phone system cannot tell how old the person
  answering is. This needs a position, not a paragraph.
- Cookies and the website's own tracking
- Complaints route to the Data Protection Board of India and to EU supervisory
  authorities
- Notice-change procedure
- Governing law

## Do not write

- Any certification claim. SOC 2, ISO 27001 and HIPAA compliance are not held.
- "Bank-grade" or "military-grade" encryption. It means nothing, and a security
  reviewer reads it as a signal that nobody technical checked the page.
- "We take your privacy seriously" as the opening line, followed by six thousand
  words that avoid saying what is collected.
