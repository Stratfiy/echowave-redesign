"""A starter price book, so the cost engine has something to price with.

`provider_rates` ships empty. Nothing seeds it, so on a fresh install every
estimate is the platform fee and a warning — the model picker shows the same
number whatever you choose, and margin reporting says provider cost is zero
because no rate was on file, not because the call was free. Every comparable
product ships a vendor price book; this is ours.

**These are approximate published list prices, and they will be wrong for you.**
Three reasons, all of which matter:

* Vendor prices change, and this file does not. Treat `AS_OF` as an expiry date,
  not a footnote.
* Almost nobody pays list. Volume commitments, startup credits and negotiated
  rates all move the real number, usually downward.
* **LLM rates here are blended.** Vendors price input and output tokens
  separately; this schema carries one rate per model because a call's token
  split is not known until it happens. The blend below assumes
  `LLM_INPUT_SHARE` input — voice agents resend a growing transcript every turn,
  so they are input-heavy — and a different mix moves the number materially.

So this is a starting point that makes the machinery work on day one, not a
statement about what anything costs. Every row is written with a note saying so,
which surfaces in the rate card, and the seeding script refuses to overwrite a
rate an operator has already set. Correct them in
`/superadmin/billing/rate-card`, where the value that actually bills lives.

Prices are held in **USD**, because that is the currency vendors quote in.
Conversion to millipaise happens at seed time against the configured USD→INR
rate, so the conversion is explicit and re-runnable rather than baked into a
constant nobody can audit.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.enums import CostComponent, RateUnit

#: When these prices were last checked against vendor pricing pages. A price
#: book with no date is one nobody can tell is stale.
AS_OF = "2026-08"

#: Share of LLM tokens assumed to be input, for blending a two-sided vendor
#: price into the single rate this schema carries. Voice agents resend the
#: conversation each turn, so they skew heavily to input.
LLM_INPUT_SHARE = 0.7

#: Written onto every seeded row so an operator can tell at a glance which
#: rates were chosen and which were merely defaulted.
SEED_NOTE = (
    f"Seeded default — list price as of {AS_OF}. Verify before relying on margin."
)


#: Stamped into the basis of any rate that is a stand-in rather than a price
#: somebody read off a vendor's published card, and carried through into the
#: seeded row's note. Machine-readable on purpose: "this figure is a guess" has
#: to be answerable from the database, because by the time it reaches a screen
#: or an invoice the guess looks exactly like a fact.
PROVISIONAL_MARKER = "PROVISIONAL"


@dataclass(frozen=True)
class DefaultRate:
    provider: str
    #: Empty string is the provider-wide fallback for any model without a row.
    model: str
    component: CostComponent
    unit: RateUnit
    usd_per_unit: float
    #: How the number was arrived at, for anyone auditing it later.
    basis: str
    #: Not a published price. See ``PROVISIONAL_MARKER``; the basis of any row
    #: set here must say so in words too, since that is what an operator reads.
    provisional: bool = False


#: Reference USD→INR for the few vendors who publish in rupees. Matches
#: ``money.DEFAULT_USD_INR_PAISE``; a test holds them together, because if they
#: drift the rupee prices below silently stop being the rupee prices published.
REFERENCE_USD_INR = 96.0


def _inr(rupees_per_unit: float) -> float:
    """A rupee-quoted vendor price, expressed in this file's USD.

    Indian vendors — Sarvam foremost — publish in ₹, and this schema is USD, so
    their rows are a round trip: ₹ here, back to ₹ at seed time against whatever
    USD→INR is then configured. The trip is lossless only while that rate equals
    ``REFERENCE_USD_INR``. It is worth the wart to keep one unit in the file, but
    it means a rupee vendor's row moves when the dollar moves and its real price
    did not — check these two rows first when Sarvam's cost looks off.
    """
    return rupees_per_unit / REFERENCE_USD_INR


def _blend(input_per_million: float, output_per_million: float) -> float:
    """Vendor per-million-token prices to one blended USD per 1k tokens."""
    per_million = input_per_million * LLM_INPUT_SHARE + output_per_million * (
        1 - LLM_INPUT_SHARE
    )
    return per_million / 1000


#: Language models — unit is 1k tokens, blended. Provider-wide fallbacks are the
#: cheapest common model, so an unpriced model under-reports rather than
#: over-reports; a surprise on the invoice should be pleasant.
LLM_RATES = (
    DefaultRate(
        "openai",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.15, 0.60),
        "gpt-4o-mini list, blended",
    ),
    DefaultRate(
        "openai",
        "gpt-4o-mini",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.15, 0.60),
        "$0.15/$0.60 per 1M, blended",
    ),
    DefaultRate(
        "openai",
        "gpt-4o",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(2.50, 10.00),
        "$2.50/$10.00 per 1M, blended",
    ),
    DefaultRate(
        "anthropic",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(1.00, 5.00),
        "Claude Haiku 4.5 $1.00/$5.00 per 1M, blended",
    ),
    # The OpenAI default in the picker, and it had no row of its own — so it
    # fell through to the provider-wide fallback, which is deliberately set to
    # the cheapest common model. That priced it at roughly a thirteenth of what
    # it costs. Now the "Smart" managed tier resolves here, so the gap would
    # have been a managed model billed at a thirteenth of its cost.
    DefaultRate(
        "openai",
        "gpt-4.1",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(2.00, 8.00),
        "$2.00/$8.00 per 1M, blended",
    ),
    DefaultRate(
        "openai",
        "gpt-4.1-mini",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.40, 1.60),
        "$0.40/$1.60 per 1M, blended",
    ),
    # Provider-wide Google is Flash rather than Flash-Lite: Flash is what the
    # default managed tier resolves to, and the fallback should not quote a
    # cheaper model than the one actually running.
    DefaultRate(
        "google",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.30, 2.50),
        "Gemini 2.5 Flash $0.30/$2.50 per 1M, blended",
    ),
    DefaultRate(
        "google",
        "gemini-2.5-flash",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.30, 2.50),
        "$0.30/$2.50 per 1M, blended",
    ),
    # Retired 2026-10-16. The row stays because rate rows are effective-dated
    # history: a call priced against this model last quarter still has to be
    # re-derivable at the rate it was actually billed at. Nothing points at it
    # any more — the fast/lite/zen tiers moved to 3.5 Flash-Lite below.
    DefaultRate(
        "google",
        "gemini-2.5-flash-lite",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.10, 0.40),
        "$0.10/$0.40 per 1M, blended. Retired 2026-10-16 — historical only.",
    ),
    # The successor the fast/lite/zen tiers now resolve to.
    #
    # Was ASSUMED at 3.1 Flash-Lite's $0.25/$1.50, because nobody had read this
    # model's own page. Read now: Google publishes $0.30/$2.50, so the stand-in
    # under-reported the cost by 54% rather than over-reporting it as its note
    # claimed. Worth remembering the next time a guess is defended as being the
    # conservative direction — a guess has no direction, only an error bar
    # nobody measured.
    DefaultRate(
        "google",
        "gemini-3.5-flash-lite",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.30, 2.50),
        "$0.30/$2.50 per 1M, blended",
    ),
    # Sarvam publishes in rupees: ₹4/1M in, ₹16/1M out (₹2.5 cached, which this
    # single-rate schema cannot express). Roughly a quarter of what the previous
    # seeded guess assumed.
    # Named as well as provider-wide. The "Lite" managed tier resolves to this
    # model, and a tier that is a priced product should not be relying on a
    # fallback that happens to describe it — the day Sarvam ships a second
    # model, the fallback would price it as this one.
    DefaultRate(
        "sarvam",
        "sarvam-105b",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(_inr(4.0), _inr(16.0)),
        "Sarvam-105B — Rs4/Rs16 per 1M published, blended",
    ),
    DefaultRate(
        "sarvam",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(_inr(4.0), _inr(16.0)),
        "Sarvam provider-wide fallback, at the 105B price",
    ),
)


def _realtime_blend(provider: str) -> float:
    """Blended USD per 1k tokens for a speech-to-speech model.

    Derived from ``realtime_pricing`` rather than written down, so the seeded
    rate moves with the model that explains it instead of drifting away from it.
    """
    from api.services.billing.realtime_pricing import blended_usd_per_1k_tokens
    from api.services.billing.realtime_rates import price_for

    price = price_for(provider)
    if price is None:  # pragma: no cover - guarded by a test
        raise KeyError(f"No realtime price book entry for {provider!r}")
    return blended_usd_per_1k_tokens(price)


#: Speech-to-speech models, recorded by the pipeline as ordinary LLM usage.
#:
#: The provider names are the ones ``provider_from_processor`` derives from the
#: service class — ``decibylgeminilive``, not ``google_realtime`` — because that
#: is what lands in ``call_cost_items``. Getting these strings wrong is not a
#: visible failure: the rate simply never matches, every realtime call is
#: reported uncosted, and margin reads 100%.
#:
#: The rate is a blend across audio in, re-sent context and audio out at a
#: three-minute reference call. Realtime billing has four prices and this schema
#: has one field, so a blend is unavoidable; see ``blended_usd_per_1k_tokens``.
REALTIME_RATES = (
    DefaultRate(
        "decibylgeminilive",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _realtime_blend("google_realtime"),
        "Gemini Live audio tokens, blended at a 3-minute call",
    ),
    DefaultRate(
        "decibylgeminilivevertex",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _realtime_blend("google_vertex_realtime"),
        "Gemini Live on Vertex, blended at a 3-minute call",
    ),
    DefaultRate(
        "decibylopenairealtime",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _realtime_blend("openai_realtime"),
        "GPT Realtime audio tokens, blended at a 3-minute call",
    ),
    DefaultRate(
        "decibylazurerealtime",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _realtime_blend("azure_realtime"),
        "Azure GPT Realtime audio tokens, blended at a 3-minute call",
    ),
)

#: Speech to text — unit is a minute of audio.
STT_RATES = (
    # Streaming, not batch. A voice agent transcribes live, and Deepgram bills
    # streaming at nearly twice the batch rate ($0.0077 vs $0.0043) — the
    # previous row quoted batch, which understated every conversation by ~44%.
    DefaultRate(
        "deepgram",
        "",
        CostComponent.STT,
        RateUnit.MINUTE,
        0.0077,
        "Nova-3 streaming $0.0077/min. Batch is $0.0043 and does not apply here.",
    ),
    # Flux is priced separately from Nova-3 and, without its own rows, fell
    # through to the fallback above — so every Flux minute was costed as a
    # Nova-3 minute. Worth a row of its own now that Flux is the default: it is
    # the model that skips the endpointing wait, so it is the one most calls
    # will actually run on.
    DefaultRate(
        "deepgram",
        "flux-general-en",
        CostComponent.STT,
        RateUnit.MINUTE,
        0.0065,
        "Flux English streaming $0.0065/min.",
    ),
    DefaultRate(
        "deepgram",
        "flux-general-multi",
        CostComponent.STT,
        RateUnit.MINUTE,
        0.0078,
        "Flux multilingual streaming $0.0078/min.",
    ),
    DefaultRate(
        "sarvam",
        "",
        CostComponent.STT,
        RateUnit.MINUTE,
        _inr(30.0 / 60),
        "Saarika — Rs30/hour published. Diarization is Rs45/hour and not this row.",
    ),
    DefaultRate(
        "sarvam",
        "saarika:v2.5",
        CostComponent.STT,
        RateUnit.MINUTE,
        _inr(30.0 / 60),
        "Rs30/hour published. Single-language transcription.",
    ),
    # The model the default managed tier resolves to, and dearer than saarika
    # for a reason worth stating: it is one model over 22 Indian languages plus
    # English with automatic detection, so a caller who switches language
    # mid-sentence is transcribed rather than lost. Priced by name rather than
    # left on the sarvam provider-wide row, because a tier we sell must not be
    # priced identically to whatever the vendor ships next.
    DefaultRate(
        "sarvam",
        "saaras:v3",
        CostComponent.STT,
        RateUnit.MINUTE,
        0.53 / 60,
        "$0.53/hour published. Multilingual with automatic language detection.",
    ),
    DefaultRate(
        "elevenlabs",
        "",
        CostComponent.STT,
        RateUnit.MINUTE,
        0.0065,
        "Scribe v2 Realtime $0.39/hour. Batch Scribe v2 is $0.22/hour, which is "
        "not the traffic a live agent generates.",
    ),
    DefaultRate(
        "azure", "", CostComponent.STT, RateUnit.MINUTE, 0.0167, "$1.00/hour standard"
    ),
)

#: Speech synthesis — unit is 1k characters, which is what voice vendors bill.
TTS_RATES = (
    DefaultRate(
        "openai",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.0150,
        "$15 per 1M characters",
    ),
    DefaultRate(
        "elevenlabs",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.0500,
        "Flash v2.5 $0.05/1k chars. Still the line that varies most by plan.",
    ),
    # Twice the Flash price, and without this row it fell through to the
    # provider-wide one above — so an account on multilingual was billed at
    # half what it cost us. The only case in this file where the card was
    # knowingly under the vendor.
    DefaultRate(
        "elevenlabs",
        "eleven_multilingual_v2",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.1000,
        "Multilingual v2 $0.10/1k chars — twice Flash/Turbo.",
    ),
    DefaultRate(
        "cartesia",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.0500,
        "Sonic — $50 per 1M characters, pay-as-you-go",
    ),
    # Two Bulbul generations at a 2x price difference, so the provider-wide
    # fallback matters. It is v2, because that is what the default managed tier
    # resolves to — quoting v3 here made every managed call read twice its real
    # synthesis cost.
    DefaultRate(
        "sarvam",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        _inr(1.50),
        "Bulbul v2 — Rs15 per 10k characters published",
    ),
    DefaultRate(
        "sarvam",
        "bulbul:v2",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        _inr(1.50),
        "Rs15 per 10k chars. The model the default managed tier resolves to.",
    ),
    DefaultRate(
        "sarvam",
        "bulbul:v3",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        _inr(3.00),
        "Rs30 per 10k chars (v3 beta) — twice v2",
    ),
    # Rumik publishes per 1k input characters, which is already this unit.
    # Mulberry is the cheapest synthesis on this card — a third of Bulbul v2 —
    # and synthesis is the largest provider line on a call, so the difference
    # is worth more than it looks.
    DefaultRate(
        "rumik",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        _inr(0.50),
        "Silk Mulberry 1.5 — Rs0.50 per 1k chars (launch pricing)",
    ),
    DefaultRate(
        "rumik",
        "mulberry",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        _inr(0.50),
        "Rs0.50 per 1k chars published (launch pricing, verify on dashboard)",
    ),
    DefaultRate(
        "rumik",
        "muga",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        _inr(0.99),
        "Rs0.99 per 1k chars published — the expressive model, twice Mulberry",
    ),
    DefaultRate(
        "smallest",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.0250,
        "Lightning V3.1 — approx $0.25 per 10k characters",
    ),
)

#: Carriage — unit is a call minute. Outbound domestic; international and
#: inbound differ, sometimes by an order of magnitude, and no single row can
#: express that.
TELEPHONY_RATES = (
    # India, not the US, because that is the traffic. Twilio publishes roughly
    # Rs1.20/min to Indian mobiles and Rs0.65 to landlines; mobile is the row,
    # since a campaign dials mobiles.
    DefaultRate(
        "twilio",
        "",
        CostComponent.TELEPHONY,
        RateUnit.MINUTE,
        _inr(1.20),
        "India outbound to mobile, approx Rs1.20/min. Landline approx Rs0.65.",
    ),
    # Plivo publishes Rs0.60/min for India outbound local, and Rs0.34/min over
    # SIP / Browser SDK. This row is the higher one deliberately: under-reporting
    # carriage overstates margin, and which of the two applies depends on how
    # calls are actually placed. Correct it to Rs0.34 once the account is
    # confirmed to be dialling over SIP.
    #
    # Worth flagging against the tender model, which assumed Rs0.25/min: even
    # the SIP rate is above that, and market benchmarks for outbound-to-mobile
    # via aggregators run Rs0.80-Rs1.80/min. Telephony is the largest single
    # cost line in a cheap stack and the easiest to under-budget.
    DefaultRate(
        "plivo",
        "",
        CostComponent.TELEPHONY,
        RateUnit.MINUTE,
        _inr(0.60),
        "India outbound local Rs0.60/min published. SIP / Browser SDK is Rs0.34.",
    ),
    # Four carriers below carry a stand-in rather than a published price, and
    # they are here rather than absent on purpose. A supported carrier with no
    # row does not cost nothing — it costs an unpriced line, which reports zero
    # provider cost and so overstates margin on every call that uses it. The
    # stand-in is the Twilio India mobile rate, which is the right order of
    # magnitude for Indian outbound and deliberately not cheap.
    #
    # **What changed, and why it now needs a flag.** These rows used to be
    # cost-only, so erring high was free: we under-reported our own margin
    # until somebody replaced them. Carriage is now marked up into the sell
    # price like every other component, so a stand-in that is too high
    # overcharges the customer and one that is too low is sold at a loss. There
    # is no safe direction any more — only a verified figure, or refusing to
    # sell the carrier. So ``provisional`` marks them, and
    # ``billing/carrier_rates.py`` refuses to put a configuration on the
    # managed path while its carrier's rate still carries the marker.
    #
    # A customer on their own carrier account is unaffected either way: their
    # minutes are on their own invoice and we bill no carriage at all — see
    # ``services/telephony/carriage.py``.
    #
    # TODO: replace all four with the carriers' published India outbound rates.
    DefaultRate(
        "cloudonix",
        "",
        CostComponent.TELEPHONY,
        RateUnit.MINUTE,
        _inr(1.20),
        f"{PROVISIONAL_MARKER} — stand-in at the Twilio India mobile rate. "
        "Not Cloudonix's published price.",
        provisional=True,
    ),
    DefaultRate(
        "vobiz",
        "",
        CostComponent.TELEPHONY,
        RateUnit.MINUTE,
        _inr(1.20),
        f"{PROVISIONAL_MARKER} — stand-in at the Twilio India mobile rate. "
        "Not Vobiz's published price.",
        provisional=True,
    ),
    # No ARI row, and that is not an oversight. ARI is a self-hosted Asterisk
    # the customer runs; the trunk behind it is theirs and so is its bill.
    # There is no vendor price for us to pass through, and carriage.py already
    # declines to bill a customer-owned configuration — a row here would only
    # matter if we ever ran Asterisk ourselves, and then it would need our own
    # trunk's rate rather than a vendor's.
    # Telnyx and Vonage carried their **US** outbound rates — $0.0070 and
    # $0.0139, about Rs0.73 and Rs1.45 — against traffic that is Indian. India
    # mobile termination is a different market and a dearer one; a US rate read
    # as an India rate understates the cost of every managed minute on these
    # carriers, and since carriage is marked up, it undersells them too.
    #
    # Neither publishes an India figure this codebase can cite, so they get the
    # same treatment as the two above: an India-shaped stand-in, flagged, and
    # unsellable as managed carriage until somebody puts the real number in.
    DefaultRate(
        "telnyx",
        "",
        CostComponent.TELEPHONY,
        RateUnit.MINUTE,
        _inr(1.20),
        f"{PROVISIONAL_MARKER} — stand-in at the Twilio India mobile rate. "
        "Was Telnyx's US outbound rate ($0.0070), which is not this traffic.",
        provisional=True,
    ),
    DefaultRate(
        "vonage",
        "",
        CostComponent.TELEPHONY,
        RateUnit.MINUTE,
        _inr(1.20),
        f"{PROVISIONAL_MARKER} — stand-in at the Twilio India mobile rate. "
        "Was Vonage's US outbound rate ($0.0139), which is not this traffic.",
        provisional=True,
    ),
)

DEFAULT_RATES: tuple[DefaultRate, ...] = (
    *LLM_RATES,
    *REALTIME_RATES,
    *STT_RATES,
    *TTS_RATES,
    *TELEPHONY_RATES,
)


def usd_to_mpaise(usd: float, *, usd_inr: float) -> int:
    """One USD price to millipaise, rounded half-up.

    ₹1 is 100 paise is 100,000 millipaise. Rounding away from zero rather than
    banker's rounding, to match how every other money conversion in this
    codebase behaves — consistency matters more than the last thousandth of a
    paise on a rate that is an estimate anyway.
    """
    from decimal import ROUND_HALF_UP, Decimal

    mpaise = Decimal(str(usd)) * Decimal(str(usd_inr)) * Decimal(100_000)
    return int(mpaise.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
