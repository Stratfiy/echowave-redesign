# Naming the models for the person actually buying

**Proposal, 20 Aug 2026.** Response to: name voices by character with a sample,
name language models by capability tier, show one price per minute.

## The short answer

**Do it.** And it is worth more than the usability it buys, because it changes
what our price *is* rather than only how it is displayed.

Three conditions, in section 4. One of them is not optional.

## 1. The picker already lies

Five managed language-model tiers are offered. They resolve to three models:

| Tier shown | Resolves to |
|---|---|
| `default` | google / gemini-2.5-flash |
| `fast` | google / gemini-3.5-flash-lite |
| `lite` | google / gemini-3.5-flash-lite |
| `zen` | google / gemini-3.5-flash-lite |
| `accurate` | openai / gpt-4o |

**Three of the five are the same model under three names.** A customer who
agonises between *fast*, *lite* and *zen* is choosing between identical things,
and one who picks *zen* over *lite* expecting it to be cheaper is wrong in a way
the screen actively encouraged.

So collapsing to three names is not simplification. It is the first time the
screen would be telling the truth:

| Shown | Today's model | Meaning to the buyer |
|---|---|---|
| **Lite** | gemini-3.5-flash-lite | Fastest and cheapest. Good for short, scripted calls. |
| **Normal** | gemini-2.5-flash | The default. Handles a real conversation. |
| **Smart** | gpt-4o | For calls where getting it wrong is expensive. |

## 2. The prize is fixed prices, not simpler words

Today a managed minute is **cost-plus**: whatever the vendors charge, times
1.4. The study already found what is wrong with that — *cutting COGS cuts our
margin*. Move to a cheaper voice and our revenue falls with our cost. We are
paid a percentage of our own inefficiency.

Naming tiers breaks that, because a name can carry a **price**:

```
Normal voice + Normal brain     ₹X.XX per minute
Smart voice  + Normal brain     ₹X.XX per minute
```

Now the customer buys *Normal*, not *Sarvam bulbul v2 at 1.4x*. When Sarvam
gets cheaper we keep the difference. When a model retires and its successor
costs 3.3x — which just happened with Flash-Lite — the customer's price does not
move and we absorb it out of headroom we priced in deliberately.

That is the difference between a product and a reseller's markup, and it is
the single most valuable thing in this proposal.

<!-- It also makes the bundle work: a minute entitlement is only meaningful if
     a minute has one price, which cost-plus does not give you. -->

**It also unblocks selling bundles in minutes — eventually.** A balance has to
be a balance today because a minute has no fixed price: it costs ₹5.17 on one
stack and ₹8.21 on another, so an entitlement in minutes would mean something
different for every customer. Give a minute a fixed all-in price and "500
minutes included" becomes sayable.

Worth being precise about what that would buy, because the competitors are not
doing it either: Bolna's "5,000 minutes" is five thousand minutes of *their
fee*, with providers still drawn from a balance on top — their plan price is
the fee times the minutes, to the cent. Fixed all-in tiers would let us sell a
minute that actually means a whole minute, which nobody currently offers.

## 3. Voices: do not invent names

The catalogue already carries `voice_id`, `name`, `gender` and `description`
per voice. What it lacks is a character label and a sample.

**Keep the vendor's human name and add the character**, the way ElevenLabs and
Retell already do:

> **Anushka** — warm, clear, everyday · ▶
> **Karun** — deep, steady, formal · ▶

Renaming *Anushka* to *Calm* costs more than it buys: the buyer eventually
hears the real name somewhere, two of our voices would end up wanting the same
character word, and a name is memorable in a way an adjective is not. The
adjective belongs beside the name, not instead of it.

**The sample matters more than either.** A voice is the one thing on this
screen nobody can evaluate by reading. One pre-generated line per managed
voice, in Hindi and English, played inline — that is the feature. Everything
else on the screen is a proxy for it.

<!-- Pre-generated at deploy, not synthesised on click: a click that costs a
     TTS call is a click somebody will make forty times while choosing. -->

## 4. Three conditions

### 4a. Bring-your-own-key keeps the detailed view — not optional

Somebody choosing which of *their own* keys to use must see the vendor and the
model. They are not buying a capability tier, they are pointing us at an
account they pay for. Hiding it would make the screen unusable for exactly the
customers on the tier where we earn the most per minute.

The redesign already makes this clean: whose key runs a slot is a property of
the slot. A slot set to Decibyl shows **Normal**; a slot set to OpenAI shows
**gpt-4o**. Same screen, two vocabularies, chosen by what the slot is.

### 4b. Simplify the choice, not the receipt

Hide the breakdown in the *picker*. Do not hide it on the *invoice*.

The study's own conclusion was that per-second itemised billing is a
differentiator a competitor cannot answer without rewriting their metering.
That argument dies the moment the invoice says "₹6.00/min × 340 minutes" and
nothing else. A customer who wants to know what they paid for speech must still
be able to find out — on **Usage** and on the **invoice**, where the question is
actually asked.

Nobody comparison-shops a breakdown. Everybody audits one.

### 4c. A named tier must not silently change a live agent

If superadmin repoints **Normal** from one model to another, every agent using
it changes behaviour on the next call. For a language model that is a quality
change; for a **voice it is a different person answering the phone**, which
regular callers notice and comment on.

So a repoint has to be effective-dated and existing agents pin what they
resolved to, with an explicit "always use the latest" opt-in. This is the same
lesson as the Flash-Lite retirement, one layer up: the tier map is already
env-overridable precisely so it can be changed without a release, which is also
what makes it easy to change without anyone noticing.

## 5. What this costs to build

| | |
|---|---|
| Collapse five LLM tiers to three, rename | Small. `managed_tiers.py` plus the picker. |
| Character labels on managed voices | Small. The `Voice` dataclass already has the field. |
| **Voice samples** | **Medium.** Generate one line per managed voice per language, store on S3, serve a URL. New plumbing, and the highest-value piece. |
| One price in the picker | Small. The estimator already returns a total. |
| **Fixed tier prices** | **Medium, and a pricing decision before it is a code one.** Needs the chars/min measurement to set the numbers with real headroom. |
| Pin resolved tiers per agent | Medium. Migration plus resolution change. |

## 6. What I would not do

**Do not hide the model name entirely, even on managed.** Put it behind a
disclosure — "Normal · gemini-2.5-flash" on hover, or a line in agent settings.
Some of your buyers are agencies building for clients, and an agency that
cannot tell a client which model runs their calls has a procurement problem we
created for no gain. The cost of showing it small is nothing; the cost of not
having it is a deal.
