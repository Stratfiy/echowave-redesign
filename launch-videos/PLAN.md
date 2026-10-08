# Decibyl launch sequence and video plan

Every launch capability gets its own short video, shown through real use cases
across adult age groups and job roles, plus a how-to tutorial. This plan
fixes what gets made and in what order; `specs/` holds one script per video
and `engine/` turns a script into a finished video.

Sources: `echowave/LAUNCH-PLAN.md` (streams and founder capabilities),
`echowave/handoff/product-engineering-handoff.txt` sections 2, 6, 16, 21–23.

## Ground rules for every video

1. **Taglines are the documents' own words.** "Talk to Decibyl. Get everyday
   things done." (handoff 2, website copy to test), "One assistant for a
   person's whole life", "acts on the phone and WhatsApp in their language",
   "teaches them at their own pace", "always asks before it acts"
   (LAUNCH-PLAN). One-line positioning is still open with the founder, so no
   new positioning strings, prices or plan names.
2. **Never claim** "for everyone", "best latency", guaranteed learning or exam
   results, unlimited free calling, or that the virtual card exists (handoff 2
   "Avoid claiming"). Trading content is "information only, no advice".
3. **Adults only.** Learning launches with adults first; nothing aimed at
   children (handoff 6).
4. **Show only what works.** A feature video ships only after its flow is
   verified on staging (launch rule 7). Features still waiting on something
   are marked ⏳ below and are scripted but held.
5. **The real UI.** Scenes use the shipped shell (Chat and Today, the
   composer, docked approval with "Do it" / "Don't" per the 7 Oct design
   reference, the agent faces). Names, times and amounts on screen are
   illustrative; no invented numbers, metrics or testimonials.
6. **Every video ends on the way in:** "Join the free early access list" at
   app.decibyl.ai while the beta is invite-only (handoff 16).

## Formats

| Kind | Length | Shape | Where |
|---|---|---|---|
| Hero | 20–25 s | 16:9 + 9:16 | Site, X, LinkedIn, YouTube, Reels/Shorts |
| Feature spot | 15–20 s | 16:9 + 9:16 | Social, site feature sections |
| Use-case story | 18–25 s | 9:16 first, 16:9 | Reels, Shorts, WhatsApp status, LinkedIn |
| Tutorial | 45–75 s | 16:9 | Help centre, YouTube, onboarding email |

Language versions: English first; Hindi captions for every use-case story;
Tamil, Telugu and Bengali for the stories whose subject speaks them. In-app
text stays as the person in the story would see it (their language).

## The people (adults, illustrative)

| # | Person | Age band | Role | What they hand to Decibyl |
|---|---|---|---|---|
| P1 | Riya, 22 | 18–24 | Final-year student / job seeker | Learn Excel at her pace, interview prep, research a company |
| P2 | Arjun, 27 | 25–34 | Freelance designer | Inbox triage, invoices: "who owes me", follow-ups |
| P3 | Dr. Meera, 34 | 25–34 | Dentist, own clinic | Calls and appointments, missed calls, reminders |
| P4 | Suresh, 41 | 35–44 | Kirana shop owner | WhatsApp orders, daily accounts to Excel, payment follow-ups |
| P5 | Kavya, 38 | 35–44 | Sales manager | Meeting mode: notes, decisions, actions; follow-ups |
| P6 | Rahul, 36 | 35–44 | Startup founder | Shared agents and team knowledge, desktop work, build an agent |
| P7 | Fatima, 45 | 45–54 | Chartered accountant | Client inbox, deadlines, research with sources |
| P8 | Vikram, 31 | 25–34 | Real-estate agent | Outbound calls, site-visit bookings, follow-ups |
| P9 | Anita, 52 | 45–54 | School teacher | Learn a new tool, lesson prep research, parent messages |
| P10 | Priya, 33 + Amma, 70 | 25–34 / 65+ | Working daughter + her mother | Medicine reminders, clinic calls, family circle (the hero story) |
| P11 | Ramesh, 67 | 65+ | Retired bank officer | Simple mode, scam check, step-by-step tech help |
| P12 | Neha, 29 | 25–34 | Product analyst who trades | Trading summaries by interest (information only) |
| P13 | Sunita, 47 | 45–54 | Home-maker running a tiffin service | Order lists, customer WhatsApp, ordering supplies |
| P14 | Imran, 39 | 35–44 | HR / recruiter | Interview scheduling, candidate follow-ups, research |

Coverage: 18–24 ×1, 25–34 ×6, 35–44 ×5, 45–54 ×3, 65+ ×2.
Roles: student, freelancer, clinic, shop, sales, founder, finance, real estate,
education, family care, retiree, analyst, home business, HR.

## Video matrix

`F` = feature spot, `S` = use-case story, `T` = tutorial. ⏳ = held until
verified or until a dependency lands.

### Everyday (Chat, Today, approvals, voice)

| ID | Kind | Feature | Story / angle | Person |
|---|---|---|---|---|
| H1 | Hero | Everything | Looking after Amma, end to end (**done**: `brag-output/brag.mp4`) | P10 |
| H1v | Hero 9:16 | Everything | Vertical cut of H1 | P10 |
| F01 | F | Approvals | "Always asks before it acts": docked "Decibyl wants to…", Do it / Don't, run once | P4 |
| F02 | F | Today + daily brief | One ordered list; brief on WhatsApp at your time | P7 |
| F03 | F | Talk vs Dictate | Live voice, interrupt mid-sentence, it stops and listens | P8 |
| F04 | F | Languages | Same task in Hindi, Tamil, Telugu, Bengali, English | P13 |
| S01 | S | Today | "What's on today?" before clinic opens | P3 |
| S02 | S | Routines from chat | "Every Monday at 10, send me pending payments" → "Will do, every Monday at 10." | P4 |
| T01 | T | Getting started | Early access → language → first task → first result | — |
| T02 | T | Approvals | Read a preview, Do it / Don't, change and re-approve, undo window | — |
| T03 | T | Today and routines | Reminders, routines, the daily brief time and channel | — |
| T04 | T | Talk and Dictate | Voice session, interrupt, captions, switch language | — |

### Work (five launch agents, meetings)

| ID | Kind | Feature | Story / angle | Person |
|---|---|---|---|---|
| F05 | F | Inbox | Summarise, pull commitments, draft reply, send only on approval | P7 |
| F06 | F | Follow-up | "Who owes me" list, scheduled nudges, delivery state | P2 |
| F07 | F | Research | Sources, facts vs inference, saved report and export | P1 |
| F08 | F | Meeting mode | Consent, capture, summary / decisions / actions confirmed one by one | P5 |
| S03 | S | Follow-up | Three unpaid invoices chased politely, one paid by evening | P2 |
| S04 | S | Meeting mode | Client call → actions in Today before she's back at her desk | P5 |
| S05 | S | Research | Company research the night before an interview | P1 |
| S06 | S | Inbox + research | GST deadline questions from five clients, answered with sources | P7 |
| S07 | S | Interview scheduling | Candidates booked across three calendars | P14 |
| T05 | T | Inbox and email identity | Connect mail from chat, summaries, approve a send | — |
| T06 | T | Meeting mode | Start, consent, stop, review actions | — |
| T07 | T | Research | Ask, check sources, save and export | — |

### Business (calls, appointments, WhatsApp, ordering)

| ID | Kind | Feature | Story / angle | Person |
|---|---|---|---|---|
| F09 | F | Call and Appointment | Answers the clinic line, books within your hours, escalates when unsure | P3 |
| F10 | F | Missed calls handled | Every missed call gets a callback or a WhatsApp, logged in Today | P8 |
| F11 | F | WhatsApp channel | Customers message, Decibyl answers from your price list | P4 |
| F12 | F | Ordering ⏳ | Zomato order through approval (Swiggy when access arrives) | P13 |
| S08 | S | Calls | Site visits booked while he's driving | P8 |
| S09 | S | Shop | Evening: orders, dues, accounts exported to Excel | P4 |
| S10 | S | Home business | Tomorrow's tiffin orders gathered from WhatsApp | P13 |
| T08 | T | Phone number and calls ⏳ | Verification, number, inbound hours, call log | — |
| T09 | T | WhatsApp | Connect, unknown senders treated as strangers, replies | — |

### Learn

| ID | Kind | Feature | Story / angle | Person |
|---|---|---|---|---|
| F13 | F | Learning Guide | Explanation, practice, feedback, review in Today, in your language | P9 |
| S11 | S | Learning | Excel from zero, lesson 3 of 10, at her own pace | P1 |
| S12 | S | Learning | A teacher learns a new tool on her bus ride, by voice | P9 |
| T10 | T | Learning session | Set a goal, practise, see progress from practice | — |

### Family and care

| ID | Kind | Feature | Story / angle | Person |
|---|---|---|---|---|
| F14 | F | Simple mode | Large text, voice first, one thing at a time | P11 |
| F15 | F | Scam check | "Is this message real?" with the reasons | P11 |
| F16 | F | Medicine calls + family alerts | A call in her language; the family hears if it's missed | P10 |
| S13 | S | Tech help | Setting up UPI step by step, at his speed | P11 |
| S14 | S | Family circle | Daughter in Bengaluru, mother in Madurai: one shared view | P10 |
| T11 | T | Simple mode and family circle | Turn on simple mode, add family with consent | — |

### Power and teams

| ID | Kind | Feature | Story / angle | Person |
|---|---|---|---|---|
| F17 | F | Build anything | Describe an agent, routine or tracker; it builds it | P6 |
| F18 | F | Private browser | Its own browser, live view, Take over, approval before submit | P6 |
| F19 | F | Desktop ⏳ | Windows and Mac app, work on my computer with Stop | P6 |
| F20 | F | Memory and privacy | Inspect, edit, forget; personal stays personal | P2 |
| F21 | F | Person + business | One assistant, separate scopes, shared team knowledge | P6 |
| F22 | F | Trading summaries | Morning summary by interest, information only, no advice | P12 |
| S15 | S | Founder | New hire asks the team agent; it answers from team knowledge | P6 |
| S16 | S | Analyst | Pre-market summary of her watchlist on WhatsApp | P12 |
| T12 | T | Build an agent | Describe it, test it in chat, put it to work | — |
| T13 | T | Private browser | Watch, take over, approve | — |
| T14 | T | Memory and privacy | Memory manager, export, deletion, temporary chats | — |
| T15 | T | Teams | Workspace, sharing agents and knowledge, roles | — |
| T16 | T | Desktop ⏳ | Install, shortcut, allowed apps, Stop | — |

Totals: 2 hero cuts, 22 feature spots, 16 stories, 16 tutorials = 56 videos,
before language versions and second aspect ratios.

## Launch sequence (relative days, no calendar dates)

| When | Theme | Ships |
|---|---|---|
| L−10 → L−1 | Tease, fill the waitlist | 3 teasers cut from H1 (faces wake up · "What can I do for you?" · "Always asks before it acts"), each ending "Join the free early access list" |
| **L0** | Launch | H1 + H1v, T01 Getting started, F01 Approvals, S01 Clinic morning, S03 Freelancer follow-ups |
| L+1 → L+7 | Everyday | F02 Today, F03 Talk, F04 Languages, S02 Routines, T02, T03, T04 |
| L+8 → L+14 | Work | F05 Inbox, F06 Follow-up, F07 Research, F08 Meetings, S04–S07, T05–T07 |
| L+15 → L+21 | Business | F09 Calls, F10 Missed calls, F11 WhatsApp, S08–S10, T09 (T08, F12 when ready) |
| L+22 → L+28 | Learn | F13, S11, S12, T10 |
| L+29 → L+35 | Family and care | F14–F16, S13, S14, T11 |
| L+36 → L+42 | Power and teams | F17, F18, F20–F22, S15, S16, T12–T15 (F19, T16 when signing lands) |

Cadence inside a week: day 1 feature spot (16:9 + 9:16), day 3 first story
(9:16, Hindi captions), day 5 second story, tutorials published to the help
centre the same day as their feature spot. Each post links the matching
tutorial.

## Measuring it (handoff 16)

Tag every link with the video ID. Track early-access sign-ups by video and
use case, and, more importantly, **activation by the use case that brought
the person in** (first meaningful task, then a second within 7 days).
Double down on the stories whose viewers activate, not the ones with the most
views.

## Production

One engine, one script per video (`specs/<ID>.json`): scenes from a shared
kit (browser chat with docked approval, phone call, WhatsApp thread, Today on
a phone, tutorial step with callouts, kinetic captions, outro), the voice of
the hero video (colour wash, vivid faces, 3D devices, camera moves), and a
soundtrack generated from the same script so every effect lands on its
action. Each video ships as `out/<ID>/` with `video.mp4`, `poster.jpg` and
`share-copy.txt`.
