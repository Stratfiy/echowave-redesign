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
AS_OF = "2026-09-14"

#: Share of LLM tokens assumed to be input, for blending a two-sided vendor
#: price into the single rate this schema carries. Voice agents resend the
#: conversation each turn, so they skew heavily to input.
LLM_INPUT_SHARE = 0.7

#: Written onto every seeded row so an operator can tell at a glance which
#: rates were chosen and which were merely defaulted. ``seed_rates`` reads the
#: prefix back to tell a row this file wrote from one a person wrote, so the
#: prefix is load-bearing: a refresh replaces the former and never the latter.
SEED_NOTE_PREFIX = "Seeded default"
SEED_NOTE = (
    f"{SEED_NOTE_PREFIX} — list price as of {AS_OF}. Verify before relying on margin."
)


def is_seeded_note(note: str | None) -> bool:
    """Whether a stored row is still this file's default rather than a choice.

    Read from the note's prefix, which is the only mark a seeded row carries.
    An operator who edits a seeded rate through the screen writes a new row
    with their own note (or none), and that row is theirs from then on.
    """
    return bool(note) and note.startswith(SEED_NOTE_PREFIX)


#: The page each vendor publishes its prices on. Every seeded row carries a
#: source a person can open and a date it was read (KAN-58): a price with no
#: provenance cannot be audited, only believed. A row names its own page when
#: the vendor prices a product somewhere other than the main card.
PROVIDER_SOURCES: dict[str, str] = {
    "openai": "https://developers.openai.com/api/docs/pricing",
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
    "google": "https://ai.google.dev/gemini-api/docs/pricing",
    "sarvam": "https://docs.sarvam.ai/api-reference-docs/pricing",
    "deepgram": "https://deepgram.com/pricing",
    "elevenlabs": "https://elevenlabs.io/pricing/api",
    "smallest": "https://smallest.ai/pricing/models",
    "cartesia": "https://cartesia.ai/pricing",
    "assemblyai": "https://www.assemblyai.com/pricing",
    "azure": "https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/",
    "rumik": "https://rumik.ai/pricing",
    "cerebras": "https://www.cerebras.ai/pricing",
    "deepseek": "https://api-docs.deepseek.com/quick_start/pricing",
    "mistral": "https://mistral.ai/pricing",
    "fireworks": "https://fireworks.ai/pricing",
    "twilio": "https://www.twilio.com/en-us/voice/pricing/in",
    "plivo": "https://www.plivo.com/voice/pricing/in/",
    "cloudonix": "https://cloudonix.io/pricing",
    "vobiz": "https://vobiz.ai/pricing",
    "telnyx": "https://telnyx.com/pricing/call-control",
    "vonage": "https://www.vonage.com/communications-apis/voice/pricing/",
    "decibyl": "https://developers.openai.com/api/docs/pricing",
    "decibylgeminilive": "https://ai.google.dev/gemini-api/docs/pricing",
    "decibylgeminilivevertex": "https://cloud.google.com/vertex-ai/generative-ai/pricing",
    "decibylopenairealtime": "https://developers.openai.com/api/docs/pricing",
    "decibylazurerealtime": "https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/",
}

#: When a row that does not say otherwise was last read off its vendor's page.
#: The book before KAN-58 was dated to a month, not a day, and pretending to a
#: day it never had would be the same fault the column exists to prevent.
LEGACY_CHECKED_ON = "2026-08"

#: The survey of 14 Sept 2026 (KAN-58). Rows re-read that day carry it.
CHECKED_ON = "2026-09-14"


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
    #: Where this figure was read. Blank means the vendor's main pricing page
    #: in ``PROVIDER_SOURCES``; set it when the price lives elsewhere.
    source_url: str = ""
    #: The day it was read there, ISO ``YYYY-MM-DD`` (a month for rows the
    #: KAN-58 survey did not re-read; see ``LEGACY_CHECKED_ON``).
    checked_on: str = ""

    @property
    def source(self) -> str:
        return self.source_url or PROVIDER_SOURCES[self.provider]

    @property
    def source_checked_on(self) -> str:
        return self.checked_on or LEGACY_CHECKED_ON


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
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "openai",
        "gpt-4o-mini",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.15, 0.60),
        "$0.15/$0.60 per 1M, blended",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "openai",
        "gpt-4o",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(2.50, 10.00),
        "$2.50/$10.00 per 1M, blended",
        checked_on=CHECKED_ON,
    ),
    # Provider-wide Anthropic is Haiku, the cheapest of the family, following
    # the rule stated above: an unpriced model under-reports rather than over-
    # reports. Note what that means now the family is priced below — Opus is 5x
    # Haiku, so an unpriced *Opus* model falling through to here is sold at a
    # fifth of cost, which is a loss rather than a thin margin. Price a new
    # Claude model before offering it, not after the first invoice.
    DefaultRate(
        "anthropic",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(1.00, 5.00),
        "Claude Haiku 4.5 $1.00/$5.00 per 1M, blended",
        checked_on=CHECKED_ON,
    ),
    # The OpenAI default in the picker, and it had no row of its own — so it
    # fell through to the provider-wide fallback, which is deliberately set to
    # the cheapest common model. That priced it at roughly a thirteenth of what
    # it costs. Now the "Smart" managed tier resolves here, so the gap would
    # have been a managed model billed at a thirteenth of its cost.
    DefaultRate(
        "openai",
        "gpt-5",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(1.25, 10.00),
        "$1.25/$10.00 per 1M, blended — the Advanced brain",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "openai",
        "gpt-4.1",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(2.00, 8.00),
        "$2.00/$8.00 per 1M, blended",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "openai",
        "gpt-4.1-mini",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.40, 1.60),
        "$0.40/$1.60 per 1M, blended",
        checked_on=CHECKED_ON,
    ),
    # Anthropic. Priced because the platform-key path is only safe once these
    # rows exist: without them `rate_card` has no figure for the provider, and
    # `REMAINING-WORK.md` records what an empty price book does — it reports
    # 100% margin rather than an error, on every call, silently.
    #
    # First-party API list prices. Anthropic also sells the same models through
    # Bedrock and Vertex at partner rates; those are a different provider row
    # if we ever serve them that way, not an edit to these.
    DefaultRate(
        "anthropic",
        "claude-haiku-4-5",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(1.00, 5.00),
        "$1.00/$5.00 per 1M, blended",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "anthropic",
        "claude-sonnet-5",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(2.00, 10.00),
        "$2.00/$10.00 per 1M, blended",
        checked_on=CHECKED_ON,
    ),
    # The builder's default until KAN-58 moved it to Sonnet 5, which is both
    # newer and a third cheaper. Priced so a builder message from before the
    # move still re-costs at what it was.
    DefaultRate(
        "anthropic",
        "claude-sonnet-4-5",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(3.00, 15.00),
        "$3.00/$15.00 per 1M, blended",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "anthropic",
        "claude-sonnet-4-6",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(3.00, 15.00),
        "$3.00/$15.00 per 1M, blended",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "anthropic",
        "claude-opus-5",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(5.00, 25.00),
        "$5.00/$25.00 per 1M, blended",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "anthropic",
        "claude-opus-4-8",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(5.00, 25.00),
        "$5.00/$25.00 per 1M, blended",
        checked_on=CHECKED_ON,
    ),
    # The OpenAI-compatible vendors, all marked provisional.
    #
    # These figures are the vendors' published list prices as read when the
    # providers were added, and none has been confirmed against an invoice.
    # `provisional` is what says so. Today it gates nothing outside telephony,
    # so it is a marker rather than a guard — but a test asserts no managed
    # tier resolves to a provisionally-priced model, which is the case where
    # being wrong costs us money rather than costing a BYOK customer nothing.
    #
    # Verify against a real invoice before pointing a managed tier at any of
    # them, and drop the flag in the same change.
    DefaultRate(
        "cerebras",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.25, 0.69),
        "Cerebras gpt-oss-120b list, blended — unconfirmed",
        provisional=True,
    ),
    DefaultRate(
        "deepseek",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.28, 1.10),
        "DeepSeek chat list, blended — unconfirmed",
        provisional=True,
    ),
    DefaultRate(
        "mistral",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.20, 0.60),
        "Mistral Small list, blended — unconfirmed",
        provisional=True,
    ),
    DefaultRate(
        "fireworks",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.90, 0.90),
        "Fireworks 70B-class list, blended — unconfirmed",
        provisional=True,
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
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "google",
        "gemini-2.5-flash",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.30, 2.50),
        "$0.30/$2.50 per 1M, blended",
        checked_on=CHECKED_ON,
    ),
    # Retiring: the KAN-58 survey has it going on 16 Oct 2026, and Google's
    # deprecations page listed no date when read on 14 Sept. Either way nothing
    # points at it any more (a test holds that — the fast/lite/zen tiers moved
    # on), and the row stays because rate rows are effective-dated history: a
    # call priced against this model last quarter still has to be re-derivable
    # at the rate it was actually billed at.
    DefaultRate(
        "google",
        "gemini-2.5-flash-lite",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.10, 0.40),
        "$0.10/$0.40 per 1M, blended. Retiring 2026-10-16 — historical only.",
        checked_on=CHECKED_ON,
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
        checked_on=CHECKED_ON,
    ),
    # The current Flash. Google publishes a launch price that rises on 1 Jan
    # 2027 ($1.50/$7.50); this row is the price until then, and the rise is a
    # dated row to open on the day, not a figure to remember.
    DefaultRate(
        "google",
        "gemini-3.8-flash",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(0.75, 3.75),
        "$0.75/$3.75 per 1M through 31 Dec 2026, blended; $1.50/$7.50 from 1 Jan 2027.",
        checked_on=CHECKED_ON,
    ),
    # Sarvam publishes in rupees. Read off the pricing page on 14 Sept 2026
    # (KAN-58): ₹29.28/1M in, ₹73.20/1M out, ₹10.98 cached — which this
    # single-rate schema cannot express. The book carried ₹4/₹16 until then,
    # a figure from an earlier page, so the Lite brain was costed at about a
    # seventh of what it is. It is still the cheapest model on the card.
    # Named as well as provider-wide. The "Lite" managed tier resolves to this
    # model, and a tier that is a priced product should not be relying on a
    # fallback that happens to describe it — the day Sarvam ships a second
    # model, the fallback would price it as this one.
    DefaultRate(
        "sarvam",
        "sarvam-105b",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(_inr(29.28), _inr(73.20)),
        "Sarvam 105B — Rs29.28/Rs73.20 per 1M published, blended",
        checked_on=CHECKED_ON,
    ),
    # Listed on the page as "Sarvam 105B Chat" at the same price as 105B, and
    # named rather than left to the provider-wide fallback below, for the
    # reason that fallback's own comment gives: a tier that is a priced product
    # should not rely on a catch-all that merely happens to describe it.
    DefaultRate(
        "sarvam",
        "sarvam-105b-conversations",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(_inr(29.28), _inr(73.20)),
        "Sarvam 105B Chat — Rs29.28/Rs73.20 per 1M published, blended",
        checked_on=CHECKED_ON,
    ),
    # Gemma 4 31B served by Sarvam, in beta on the page and so provisional
    # here: a beta price is one the vendor has said it may move. Nothing
    # managed resolves to it, and a test holds that until the flag drops.
    DefaultRate(
        "sarvam",
        "gemma-4-31b",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(_inr(36.60), _inr(91.50)),
        f"{PROVISIONAL_MARKER} — Gemma-4 31B (beta) Rs36.60/Rs91.50 per 1M "
        "published, blended; a beta price the vendor may move.",
        provisional=True,
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "sarvam",
        "",
        CostComponent.LLM,
        RateUnit.THOUSAND_TOKENS,
        _blend(_inr(29.28), _inr(73.20)),
        "Sarvam provider-wide fallback, at the 105B price",
        checked_on=CHECKED_ON,
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
        checked_on=CHECKED_ON,
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
        checked_on=CHECKED_ON,
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
    # streaming above the batch rate — an earlier row quoted batch, which
    # understated every conversation.
    #
    # Multilingual, not monolingual: the page prices Nova-3 streaming at
    # $0.0048 for one language and $0.0058 for many, and this traffic switches
    # language mid-sentence. The KAN-58 survey carried $0.0077, the streaming
    # price before Deepgram cut it; the page is the source, so the page wins.
    DefaultRate(
        "deepgram",
        "",
        CostComponent.STT,
        RateUnit.MINUTE,
        0.0058,
        "Nova-3 multilingual streaming $0.0058/min (monolingual $0.0048). "
        "Batch is $0.0052 and does not apply here.",
        checked_on=CHECKED_ON,
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
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "deepgram",
        "flux-general-multi",
        CostComponent.STT,
        RateUnit.MINUTE,
        0.0078,
        "Flux multilingual streaming $0.0078/min.",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "sarvam",
        "",
        CostComponent.STT,
        RateUnit.MINUTE,
        _inr(30.0 / 60),
        "Saarika — Rs30/hour published. Diarization is Rs45/hour and not this row.",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "sarvam",
        "saarika:v2.5",
        CostComponent.STT,
        RateUnit.MINUTE,
        _inr(30.0 / 60),
        "Rs30/hour published. Single-language transcription.",
        checked_on=CHECKED_ON,
    ),
    # The model the default managed tier resolves to, and dearer than saarika
    # for a reason worth stating: it is one model over 22 Indian languages plus
    # English with automatic detection, so a caller who switches language
    # mid-sentence is transcribed rather than lost. Priced by name rather than
    # left on the sarvam provider-wide row, because a tier we sell must not be
    # priced identically to whatever the vendor ships next.
    # Sarvam's own pricing page prices STT as one flat real-time-streaming
    # tier -- Rs30/hour -- with no separate figure for saaras:v3. It is the
    # same product line as saarika:v2.5, priced identically; batch and
    # diarization are the only rows Sarvam breaks out separately (below).
    # Confirmed against sarvam.ai/pricing 27 Aug 2026, the day the account's
    # first real call used this model and found no rate on file for it.
    DefaultRate(
        "sarvam",
        "saaras:v3",
        CostComponent.STT,
        RateUnit.MINUTE,
        _inr(30.0 / 60),
        "Rs30/hour published, same real-time-streaming tier as saarika:v2.5.",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "elevenlabs",
        "",
        CostComponent.STT,
        RateUnit.MINUTE,
        0.0065,
        "Scribe v2 Realtime $0.39/hour. Batch Scribe v2 is $0.22/hour, which is "
        "not the traffic a live agent generates.",
        checked_on=CHECKED_ON,
    ),
    # A supported STT provider with no row until KAN-58, so every minute on it
    # was reported uncosted. Universal-Streaming is one price in English and
    # multilingual, and the page bills a streaming session by its open time,
    # idle included — a call that sits on hold still pays.
    DefaultRate(
        "assemblyai",
        "",
        CostComponent.STT,
        RateUnit.MINUTE,
        0.15 / 60,
        "Universal-Streaming $0.15/hour, English and multilingual alike; billed "
        "on session time.",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "azure", "", CostComponent.STT, RateUnit.MINUTE, 0.0167, "$1.00/hour standard"
    ),
)

#: Speech synthesis — unit is 1k characters, which is what voice vendors bill.
TTS_RATES = (
    # Smallest publishes per 10k characters; both Lightning models are
    # pay-as-you-go with no plan tiers. Confirmed against
    # smallest.ai/pricing/models on 14 Sep 2026, the day the `natural` tier
    # was pointed at v3.1 Pro. Pro is named so a tier we sell is never priced
    # off the provider-wide row.
    DefaultRate(
        "smallest",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.0175,
        "Lightning v3.1 $0.175 per 10k characters.",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "smallest",
        "lightning_v3.1",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.0175,
        "Lightning v3.1 $0.175 per 10k characters.",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "smallest",
        "lightning_v3.1_pro",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.0195,
        "Lightning v3.1 Pro $0.195 per 10k characters. The `natural` managed tier.",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "openai",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.0150,
        "$15 per 1M characters",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "elevenlabs",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.0500,
        "Flash v2.5 $0.05/1k chars. Still the line that varies most by plan.",
        checked_on=CHECKED_ON,
    ),
    # Twice the Flash price, and without this row it fell through to the
    # provider-wide one above — so an account on multilingual was billed at
    # half what it cost us. The only case in this file where the card was
    # knowingly under the vendor.
    # Named even though the provider-wide row above is already its price. That
    # row exists to catch a model we have not listed; leaving Flash to inherit
    # it means the catalogue flags our most-used voice model priced_by_fallback
    # -- the flag that means "check this" -- and that Flash would follow the
    # default anywhere it moved. Every model in ELEVENLABS_TTS_MODELS carries
    # its own row, and a test holds that.
    DefaultRate(
        "elevenlabs",
        "eleven_flash_v2_5",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.0500,
        "Flash v2.5 $0.05/1k chars, ~75ms. The provider-wide row is the same "
        "price and stays as the catch-all for anything unlisted.",
        checked_on=CHECKED_ON,
    ),
    # Priced explicitly even though it matches the provider-wide row today.
    # Without it the catalogue marks the model priced_by_fallback, which is the
    # flag meaning "check this one" -- and it would silently follow the Flash
    # rate if that ever moved.
    DefaultRate(
        "elevenlabs",
        "eleven_v3_conversational",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.0500,
        "v3 Conversational $0.05/1k chars, same as Flash. Low-latency v3 for "
        "realtime speech.",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "elevenlabs",
        "eleven_multilingual_v2",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.1000,
        "Multilingual v2 $0.10/1k chars — twice Flash/Turbo.",
        checked_on=CHECKED_ON,
    ),
    # Cartesia's page prices plans, not characters, as of 14 Sept 2026 (Scale
    # is $299 a month for about 10,667 minutes) and publishes no overage
    # figure. The $50 per 1M this row has always carried is not on the page
    # any more, so it is provisional: the figure to put here is the one on our
    # own invoice, at /superadmin/billing/rate-card. Nothing managed resolves
    # to Cartesia.
    DefaultRate(
        "cartesia",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        0.0500,
        f"{PROVISIONAL_MARKER} — Sonic, $50 per 1M characters as previously "
        "listed; the page now prices plans only. Set from the invoice.",
        provisional=True,
        checked_on=CHECKED_ON,
    ),
    # Two Bulbul generations at a 2x price difference, so the provider-wide
    # fallback matters. It is v3, because that is what the default managed tier
    # resolves to (``managed_tiers``), and the fallback has to quote the
    # generation actually running: the book carried v2 here after the tier had
    # moved, so every managed call read half its real synthesis cost — the
    # "rate book had ₹1.50" in KAN-58.
    DefaultRate(
        "sarvam",
        "",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        _inr(3.00),
        "Bulbul v3 — Rs30 per 10k characters published; the generation the "
        "default managed tier runs.",
        checked_on=CHECKED_ON,
    ),
    # No longer on Sarvam's pricing page as of 14 Sept 2026, which lists v3
    # only. The row stays at the last published figure so history re-costs.
    DefaultRate(
        "sarvam",
        "bulbul:v2",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        _inr(1.50),
        "Rs15 per 10k chars, last published figure; not on the page since Sept 2026.",
        checked_on=CHECKED_ON,
    ),
    # Kept at list on purpose. The account carries Rs25,000 of Sarvam credit
    # for six months from Sep 2026 and 30% off list after that; the card
    # states what the vendor charges, so margin reads conservative rather
    # than a discount somebody has to remember to remove.
    DefaultRate(
        "sarvam",
        "bulbul:v3",
        CostComponent.TTS,
        RateUnit.THOUSAND_CHARS,
        _inr(3.00),
        "Rs30 per 10k chars published — twice v2",
        checked_on=CHECKED_ON,
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
)

#: Carriage — unit is a call minute. Outbound domestic; international and
#: inbound differ, sometimes by an order of magnitude, and no single row can
#: express that.
TELEPHONY_RATES = (
    # India, not the US, because that is the traffic. Twilio's India page on
    # 14 Sept 2026: $0.0496/min outbound to a mobile, $0.0699 to a landline and
    # $0.0699 inbound to an Indian number — about Rs4.76 a minute to a mobile,
    # four times the Rs1.20 this row used to carry and seven times the $0.0075
    # in the KAN-58 survey, which is Twilio's US rate. Mobile is the row, since
    # a campaign dials mobiles. Dollar-quoted, because Twilio invoices in
    # dollars, so the rupee figure follows the exchange rate the way the bill
    # does.
    DefaultRate(
        "twilio",
        "",
        CostComponent.TELEPHONY,
        RateUnit.MINUTE,
        0.0496,
        "India outbound to mobile $0.0496/min; landline and inbound $0.0699.",
        checked_on=CHECKED_ON,
    ),
    # Plivo's India voice page quotes Rs0.38/min for both outbound and inbound
    # (audio streaming included at no per-minute charge), read again 14 Sept
    # 2026 and confirmed against the account's own console 27 Aug 2026 -- not
    # the older Rs0.60 outbound / Rs0.34 SIP split this row used to carry,
    # which was a generic PSTN quote for a different Plivo product.
    #
    # The blank-model row is the fallback for a call direction that never got
    # tagged -- pre-migration history, or a code path that has not been
    # updated yet. Both direction-specific rows below exist so that when Plivo
    # inevitably prices the two differently (they already price the pair as
    # separate line items, even though today's number is identical), only the
    # affected row needs correcting at /superadmin/billing/rate-card -- not a
    # code change and not a redeploy.
    DefaultRate(
        "plivo",
        "",
        CostComponent.TELEPHONY,
        RateUnit.MINUTE,
        _inr(0.38),
        "Plivo Voice AI Telephony, Rs0.38/min -- fallback for an untagged call direction.",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "plivo",
        "outbound",
        CostComponent.TELEPHONY,
        RateUnit.MINUTE,
        _inr(0.38),
        "Plivo Voice AI Telephony, outbound Rs0.38/min, confirmed on account 27 Aug 2026.",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "plivo",
        "inbound",
        CostComponent.TELEPHONY,
        RateUnit.MINUTE,
        _inr(0.38),
        "Plivo Voice AI Telephony, inbound Rs0.38/min, confirmed on account 27 Aug 2026.",
        checked_on=CHECKED_ON,
    ),
    # Four carriers below carry a stand-in rather than a published price, and
    # they are here rather than absent on purpose. A supported carrier with no
    # row does not cost nothing — it costs an unpriced line, which reports zero
    # provider cost and so overstates margin on every call that uses it. The
    # stand-in is Rs1.20/min: the order of magnitude of Indian mobile
    # termination on a wholesale carrier, and deliberately not cheap. (It was
    # described as "the Twilio India mobile rate" when Twilio's page said so;
    # Twilio now lists four times that, which says more about Twilio's India
    # margin than about these four.)
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
        f"{PROVISIONAL_MARKER} — stand-in at Rs1.20/min, the Indian mobile order of magnitude. "
        "Not Cloudonix's published price.",
        provisional=True,
    ),
    DefaultRate(
        "vobiz",
        "",
        CostComponent.TELEPHONY,
        RateUnit.MINUTE,
        _inr(1.20),
        f"{PROVISIONAL_MARKER} — stand-in at Rs1.20/min, the Indian mobile order of magnitude. "
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
        f"{PROVISIONAL_MARKER} — stand-in at Rs1.20/min, the Indian mobile order of magnitude. "
        "Was Telnyx's US outbound rate ($0.0070), which is not this traffic.",
        provisional=True,
    ),
    DefaultRate(
        "vonage",
        "",
        CostComponent.TELEPHONY,
        RateUnit.MINUTE,
        _inr(1.20),
        f"{PROVISIONAL_MARKER} — stand-in at Rs1.20/min, the Indian mobile order of magnitude. "
        "Was Vonage's US outbound rate ($0.0139), which is not this traffic.",
        provisional=True,
    ),
)

#: Embeddings — unit is 1k tokens, unblended (vendors quote embeddings at one
#: flat rate, unlike LLM input/output). Only query-time embedding during
#: in-call knowledge-base retrieval prices against this table; ingestion-time
#: embedding (document upload) is a separate, unmetered event — see
#: ``PRICING-DECISIONS.md``.
EMBEDDING_RATES = (
    DefaultRate(
        "openai",
        "",
        CostComponent.EMBEDDING,
        RateUnit.THOUSAND_TOKENS,
        0.00002,
        "text-embedding-3-small, $0.02/1M tokens — the account's own OpenAI key.",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "openai",
        "text-embedding-3-small",
        CostComponent.EMBEDDING,
        RateUnit.THOUSAND_TOKENS,
        0.00002,
        "$0.02/1M tokens published list price.",
        checked_on=CHECKED_ON,
    ),
    DefaultRate(
        "openai",
        "text-embedding-3-large",
        CostComponent.EMBEDDING,
        RateUnit.THOUSAND_TOKENS,
        0.00013,
        "$0.13/1M tokens published list price.",
        checked_on=CHECKED_ON,
    ),
    # Azure OpenAI mirrors OpenAI's list pricing for the same deployment model,
    # the same convention PROVIDER-PRICING.md documents for Azure's LLM rows.
    DefaultRate(
        "azure",
        "",
        CostComponent.EMBEDDING,
        RateUnit.THOUSAND_TOKENS,
        0.00002,
        "Azure OpenAI mirrors OpenAI list pricing for the same deployment.",
    ),
    # The Decibyl-managed path (MPS) proxies to an OpenAI-compatible embeddings
    # endpoint on our own key, defaulting to the same text-embedding-3-small.
    # What MPS actually charges Decibyl for it is not published anywhere this
    # file can cite, so this is seeded at the same list price MPS is presumed
    # to be reselling — provisional until reconciled against an actual MPS
    # invoice, the same caveat PROVIDER-PRICING.md raises for every seeded row.
    DefaultRate(
        "decibyl",
        "",
        CostComponent.EMBEDDING,
        RateUnit.THOUSAND_TOKENS,
        0.00002,
        f"{PROVISIONAL_MARKER} — assumed equal to OpenAI's $0.02/1M list price; "
        "not reconciled against an MPS invoice.",
        provisional=True,
    ),
)

DEFAULT_RATES: tuple[DefaultRate, ...] = (
    *LLM_RATES,
    *REALTIME_RATES,
    *STT_RATES,
    *TTS_RATES,
    *TELEPHONY_RATES,
    *EMBEDDING_RATES,
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
