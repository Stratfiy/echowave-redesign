# Decibyl launch videos

A plan for seven short product videos, made with [`/brag`](https://github.com/latent-spaces/brag):
one launch film that says what Decibyl is, then six use-case films, each showing one
job Decibyl does from start to finish.

Every plan here follows brag's own method (inspect the code, answer the rubric, write
`brag-plan.md`) and its rules: 15–25 seconds, the real product on screen, no generic
SaaS language, and a hook in the first two seconds.

## Why a series and not one video

Decibyl does many things: phone calls, WhatsApp, payments, documents, a morning brief,
learning, care. One 20-second video can't explain them all, and a video that lists
features teaches nobody. So each video has one job:

- **The launch film** gives the one idea everything else hangs on: *one assistant that
  works on your phone and WhatsApp, in your language, and asks before it acts.*
- **Each use-case film** shows one starter from the Chat home
  (`ui/src/components/home/HomeAboveTheFold.tsx`), from the request to the finished
  result. The viewer sees their own problem solved, not a list of capabilities.

All seven end on the same frame: the calm Chat home with the composer, so the series
reads as one product.

## The series

| # | Video | Starter it shows | Who it is for | Format | Length |
|---|---|---|---|---|---|
| 00 | [Launch: "It asks first"](00-launch/brag-plan.md) | all of them, briefly | everyone | landscape + vertical | 22s |
| 01 | [The front desk that never misses a call](01-receptionist/brag-plan.md) | "Answer my phone and book appointments" | clinics, salons, dental | vertical | 20s |
| 02 | [WhatsApp, answered](02-whatsapp/brag-plan.md) | "Reply to customers on WhatsApp" | shops, real estate, hotels | vertical | 18s |
| 03 | [Collections without the awkward call](03-payments/brag-plan.md) | "Chase overdue payments" | lenders, distributors, agencies | landscape | 20s |
| 04 | [Your documents, answering staff](04-knowledge/brag-plan.md) | "Answer staff questions from our documents" | teams of 10–500 | landscape | 18s |
| 05 | [Good morning, here's your day](05-today/brag-plan.md) | "Send me a summary every morning" | owners, managers | vertical | 18s |
| 06 | [Learn anything, in your language](06-learning/brag-plan.md) | "Teach me something" | individuals, families | vertical | 20s |

## Release order

1. **Launch day:** 00 (landscape on X, LinkedIn and the site; vertical on Reels and Shorts).
2. **Days 2–4:** 01, 02, 03. These are the paid use cases, the reason a business signs up.
   One a day, each pinned under the launch post as a reply.
3. **Week 2:** 04 and 05, which show depth for buyers who are already looking.
4. **Week 3:** 06, the personal side, for a broader audience.

Each video gets its own share copy (in each plan), and each links to the Chat home with
its starter pre-filled: `app.decibyl.ai/overview?say=<starter>`. The `?say=` parameter
already puts words in the box (see `HomeAboveTheFold.tsx`).

## Shared visual identity

From `ui/src/app/globals.css`, `ui/src/app/layout.tsx` and `ui/src/components/brand/BlobFace.tsx`:

| Role | Value |
|---|---|
| Canvas | `#ffffff` (`--background`) |
| Ink: text and primary buttons | `#0d0d0d` (`--foreground`, `--primary`) |
| Body text | `#5d5d5d` (`--brand-body`) |
| Chips | `#f0f0f0` fill, `#e5e5e6` border |
| Brand gradient | `linear-gradient(to bottom, #e6e6b6, #c4d0da)` (`--brand-gradient`) |
| Agent blob faces | pastels `#F7B5E3`, `#FFD66B`, `#CDEB7A`; eyes `#0d0d0d` |
| Warm accent | `#df8e1d` (`--brand-amber`), used sparingly for "waiting for your approval" |
| Type | Lato, the app's self-hosted font |

The look is calm: white, ink, one pastel blob, lots of space. The product's personality
comes from the blob faces and the plain copy, not from gradients or glow.

## Rules every video follows

1. **The approval card shows up in every video that takes an action.** "Ask before
   acting" is what sets Decibyl apart. Show the real `ActionPreview`/`ApprovalDock` card,
   with the exact recipient, amount or message, and a tap on **Do it** (the shipped button label;
   the card also offers **Don't** and "See everything it will do").
2. **Use the product's own words.** The starters, status lines ("Decibyl is waiting
   for your approval.") and pack descriptions are the script. Banned: "streamline",
   "supercharge", "elevate", "unlock", "AI-powered".
3. **Show the voice honestly.** Per screen 05 of the design handoff, a live voice
   session shows one real audio meter, captions and a state label. No animated face, no
   fake waveform. The video follows the same rule.
4. **Show more than one language.** At least one line in Hindi, Tamil or another
   supported language in every video with a call or a message, with English captions.
   Packs list `en, hi, ta, te, kn`.
5. **Fictional data only.** No real customer, phone number, email or internal URL.
   Names used in the plans (Narayani Dental, Acme Print, Priya) are stand-ins taken
   from pack examples and component fixtures.
6. **No price or positioning string without the founder.** `LAUNCH-PLAN.md` rule 8.
   Lines like "billed in 15-second pulses" or any ₹ rate are marked **[founder sign-off]**
   in the plans. Cut them if they aren't approved.

## Making a video

Install brag in Claude Code (`/plugin marketplace add latent-spaces/brag`, then
`/plugin install brag@brag`), then from `echowave/`:

```text
/brag --format vertical
Build the video in launch-videos/01-receptionist/brag-plan.md. Use that plan as the
storyboard; don't write a new one.
```

On Opus 5.5, brag hands off to `/brag-slim`, which renders the app's real components
(BlobFace, ChannelComposer, ApprovalDock, HomeToday) instead of redrawing them, which
suits this repo. Output lands in `brag-output/` (gitignore it or move the finished
`brag.mp4` somewhere else).

Requirements: Node 22+ and FFmpeg on `PATH`. The full `/brag` workflow also needs the
Hyperframes CLI (`npx hyperframes doctor`).

## Beyond brag

brag caps a video at 25 seconds on purpose. Two longer pieces fit the launch but are
outside what brag makes:

- **A 60–90s "first agent" walkthrough** for the docs and onboarding emails: pick
  Inbound, describe the use case, Test Agent, talk to it. That's a screen recording
  with a voiceover, using the README's "Your first agent" steps as the script.
- **A 2-minute founder demo** for sales calls, which strings 01, 03 and 05 together
  with a real call.
