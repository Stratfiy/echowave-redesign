# Model selection: what was wrong, what it is now, and where we stand

Written after collapsing the three-tab model screen. It records the diagnosis,
because the fix only makes sense if you can see what the old shape was actually
doing wrong, and because the same mistake is easy to make again.

---

## 1. The diagnosis

The screen offered three tabs and asked you to pick one:

| Tab | What it actually meant |
|---|---|
| **Speech to Speech** | An *architecture* — one model hears and speaks — **and** implicitly "on your own key" |
| **Decibyl** | A *way of paying* — we hold the keys |
| **BYOK** | A *way of paying* — you hold the keys — **and** implicitly "cascade architecture" |

**These are not three comparable options.** Two are about who pays for
inference; one is about the shape of the pipeline. They were presented as
mutually exclusive because they shared a single stored field, `mode`, which had
been made to carry both meanings at once.

Four consequences, each of which was visible on the screen:

1. **Nothing said that picking Speech to Speech meant bringing your own key.**
   That fact was in neither the label nor the description. It was in the shape
   of the stored JSON, where realtime lived inside the BYOK branch.

2. **A mixed stack was unrepresentable.** "Your Indic transcriber, my OpenAI
   contract" could not be expressed — and that is the stack most Indian
   accounts actually want, because Sarvam beats the Western models on Telugu
   while a customer with negotiated OpenAI pricing wants their own rate on the
   reasoning.

3. **Managed speech-to-speech was impossible** under any combination, because
   realtime was inside BYOK.

4. **Switching tabs looked like switching the account over**, so the screen had
   grown a warning strip explaining that it had not. That strip is a good
   marker: when a UI needs a paragraph explaining that it does not mean what it
   appears to mean, the model underneath is wrong.

Keys made it worse. They were stored **inline, in plaintext, inside the model
configuration JSON**, which meant a key could only be entered where a model was
being chosen. Every model screen was also a key-entry screen, you could not
store a key you were not immediately using, and switching a slot's provider
discarded the key you had just pasted because it lived in the branch you
navigated away from.

---

## 2. What it is now

Two questions, asked separately, in the places they belong.

### Shape — on the model screen

Cascade or speech-to-speech. Two cards, with what each is actually good for
written on it. That is the entire top-level choice.

### Whose key — per model

`Decibyl` is an ordinary option in every provider dropdown. A slot is managed
exactly when you pick it there. Half your stack can be managed and half your
own. There is deliberately **no mode field** anywhere in the new schema:
managed-ness is derived from the slots, because a stored summary of the slots
would eventually disagree with them and the summary is what people would trust.

```
architecture: pipeline | realtime
  stt:  decibyl (managed)  |  sarvam / deepgram / … (your key)
  llm:  decibyl (managed)  |  openai / google / …   (your key)
  tts:  decibyl (managed)  |  sarvam / elevenlabs / … (your key)
```

### Keys — on their own screen

`/provider-keys` does one job: hold the vendor keys you have accounts with,
grouped by what they serve, with a pause switch for rotating at the vendor. It
says plainly that **anything without a key runs on ours**, because the old
arrangement made BYOK look mandatory.

Keys are Fernet-encrypted, scoped to the organization, and write-only — last
four characters, never read back.

### What made this small

Almost none of this needed new machinery, which is worth recording:

- `LLMConfig` and its siblings were **already** discriminated unions including
  the Decibyl variants.
- `managed_resolution` **already** rewrote each section independently.

Mixed stacks were representable and resolvable the entire time. A single
validator (`_reject_decibyl_provider`) and a single dropdown filter were the
only things forbidding them. The redesign is mostly the removal of those two,
plus a vault to get keys out of the config.

---

## 3. Where we stand against the market

Researched August 2026. Sources at the end.

### The competitive standard

Per-assistant, per-component provider selection with independent BYOK is what
the leaders do. **Vapi** is the reference: choose LLM, STT, TTS and telephony
independently, bring your own key for any of them, or plug in self-hosted
models. **Retell** manages more of the stack with a curated model list and no
self-hosted models, trading flexibility for a pre-optimised 200–300 ms pipeline.
**Bland** is the opposite pole: one flat rate, no BYOK, no provider selection.

Before this change we were closest to Bland's rigidity while charging like a
platform that offered choice. We are now at Vapi's level of per-slot
flexibility.

### Where we are now ahead

1. **Per-slot mixing of managed and BYOK.** Vapi and Retell treat BYOK as a
   credential you either have or do not. Being able to run our Indic speech
   with your own reasoning model, and see exactly which slots are on whose
   keys, is not something the others express cleanly.

2. **Live cost per minute as you choose.** We have a real rate card, per-model
   measured usage, and a blended INR figure in the picker. No competitor shows
   you what a stack will cost while you assemble it — they publish a platform
   fee and leave provider cost to you.

3. **Managed tiers named by behaviour, not vendor.** A customer on `fast` keeps
   working when we move that tier to a different provider. Competitors name
   vendors in customer configuration, which makes every vendor change a
   customer migration.

4. **India-first, structurally.** Sarvam Indic STT/TTS as the managed default,
   GST-correct billing with receipt vouchers, an INR rate card, DPDP tooling,
   and 15-second pulse billing rather than whole minutes. Nobody in the
   comparison set does India properly.

### Where we are still behind — worth being honest

| Gap | Who does it better | Notes |
|---|---|---|
| **Warm transfer with full context** | Retell passes the conversation to the human agent so the caller does not repeat themselves | We should confirm what our transfer carries |
| **Latency** | Retell's opinionated runtime holds 200–300 ms | We have per-turn metrics; we have never published a number |
| **No-code builder polish** | Synthflow's visual flow builder for non-technical teams | Ours is developer-shaped |
| **Self-hosted / custom model endpoints** | Vapi supports arbitrary and self-hosted LLM endpoints | We have Speaches and HuggingFace but not a general custom-endpoint story |
| **Tax document PDFs, e-invoicing, credit notes** | — | Already on the revenue-blocking list in `HANDOVER.md` §10 |
| **Role model** | — | Built: `OrganizationRole` (member/admin/owner) + `StaffRole` (support/superadmin). See `HANDOVER.md` §10. |

### What to build next, in order

1. **Show measured latency per stack in the picker**, from `call_turn_metrics`.
   We already collect p50/p95 per language and model. A picker that shows *our
   own measured* TTFB beside each option is something no competitor can copy
   without the data, and it directly answers the objection Retell wins on.
2. **Per-agent model override in the builder.** The backend already supports it
   (`model_configuration_v2_override` on the workflow); it has never been
   surfaced. This is the single cheapest remaining win.
3. **Warm transfer with context**, to close the clearest functional gap.
4. **A custom LLM endpoint provider**, which is the main thing Vapi has that we
   structurally cannot express.

---

## 4. Sources

- [Retell vs Vapi vs Bland: We Built on All 3 (2026), TECHSY](https://techsy.io/en/blog/retell-ai-vs-vapi-vs-bland)
- [Retell AI vs Vapi (2026): Which Wins?, Famulor](https://www.famulor.io/blog/retell-ai-vs-vapi-2026-which-platform-is-actually-better)
- [Voice AI Platforms Compared 2026, Ortavox](https://ortavox.ai/blog/voice-ai-platform-comparison-2026)
- [Vapi — Build Advanced Voice AI Agents](https://vapi.ai/platform)
- [Vapi vs Synthflow (2026), Retell AI](https://www.retellai.com/blog/vapi-vs-synthflow)
- [Top AI Voice Agents 2026 Compared, GrowwStacks](https://growwstacks.com/blog/top-ai-voice-agents-2026-comparison)
- [Retell vs Vapi vs Bland vs Synthflow (2026), tested.media](https://tested.media/retell-vs-vapi-vs-bland-vs-synthflow/)
