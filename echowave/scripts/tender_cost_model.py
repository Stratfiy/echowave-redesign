"""The cost model behind TENDER-COST-AND-QUOTE.md.

A cost report whose numbers live only in a document is one nobody can check
and nobody can update. Every figure in that document comes from here, so when
an assumption moves — call length, connect rate, the carrier's real quote —
the answer moves with it instead of quietly going stale.

Run it:

    set -a && source api/.env && set +a && python -m scripts.tender_cost_model

Nothing here touches the database — the env is only needed because importing
the price book pulls in ``api.constants``.

Provider prices are read from the seeded price book rather than restated, so
a correction there (Sarvam TTS was understated 1.56x until recently) reaches
this model without anyone remembering to copy it across.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from api.enums import CostComponent
from api.services.billing.default_rates import (
    DEFAULT_RATES,
    REFERENCE_USD_INR,
    _blend,
)


def _rate(provider: str, component: CostComponent) -> float:
    """Rupees per unit for a provider-wide seeded rate."""
    row = next(
        r
        for r in DEFAULT_RATES
        if r.provider == provider and r.component == component and r.model == ""
    )
    return row.usd_per_unit * REFERENCE_USD_INR


@dataclass(frozen=True)
class Assumptions:
    farmers: int = 80_000
    max_attempts: int = 3
    target_connects: int = 50_000

    #: Conversation length excluding ring. The single largest cost lever in
    #: the model: every per-minute line scales with it.
    call_seconds: float = 150.0
    ring_connected: float = 8.0
    #: An unanswered call still holds a channel while it rings, which is why
    #: channel sizing is not the same question as billable minutes.
    ring_failed: float = 30.0

    #: Share of the call the agent is talking. Drives TTS, which drives cost.
    agent_speech_share: float = 0.65
    chars_per_minute_speech: float = 2300.0
    turns: int = 8
    tokens_per_turn: int = 1650

    #: Fraction of agent speech that must be synthesised live. The rest is
    #: pre-rendered: the fixed advisory body, the finite slot values (mandal,
    #: crop, season) and the top FAQ answers, assembled at call time. Only a
    #: genuinely novel question needs live synthesis. Assembling pre-rendered
    #: fragments is not an IVR — the conversation is still driven by the model,
    #: which is what §6 of the requirement actually asks for — but it does
    #: depend on the knowledge base covering the common questions well. If FAQ
    #: coverage disappoints, this rises and so does cost.
    live_tts_share: float = 0.25
    segments: int = 400
    advisory_seconds: float = 90.0

    #: Unverified. Get a written quote before signing anything.
    carrier_rupees_per_minute: float = 0.25

    #: **Campaign-scoped, not a calendar month.** The database and cache stay
    #: up for roughly four weeks across setup, pilot, campaign and reporting;
    #: the media fleet runs only inside the 09:00–19:00 calling window, which
    #: is 70 hours rather than 730. Provisioning a full month of everything is
    #: the single most expensive mistake available in this budget — it was
    #: ₹1.91L before this line was worked properly.
    infra_rupees_campaign: int = 1_00_000
    campaign_one_offs: int = 50_000

    calling_days: int = 7
    calling_hours_per_day: int = 10


@dataclass(frozen=True)
class Volume:
    connect_rate: float
    attempts: float
    failed: float
    connects: float
    conversation_minutes: float
    carrier_minutes: float
    avg_sip_channels: float
    avg_concurrent_calls: float


def volume(a: Assumptions) -> Volume:
    """Attempts, connections and concurrency implied by the target.

    The connect rate is *derived from the target*, not assumed: the tender
    fixes the list size, the attempt cap and the number of connections, which
    together determine the per-attempt answer rate it is relying on. Naming
    that rate is the whole point — see ``feasibility``.
    """
    reach = a.target_connects / a.farmers
    p = 1 - (1 - reach) ** (1 / a.max_attempts)
    attempts = a.farmers * sum((1 - p) ** i for i in range(a.max_attempts))
    failed = attempts - a.target_connects

    seconds = a.calling_days * a.calling_hours_per_day * 3600
    channel_seconds = failed * a.ring_failed + a.target_connects * (
        a.call_seconds + a.ring_connected
    )

    return Volume(
        connect_rate=p,
        attempts=attempts,
        failed=failed,
        connects=a.target_connects,
        conversation_minutes=a.target_connects * a.call_seconds / 60,
        carrier_minutes=a.target_connects * (a.call_seconds + a.ring_connected) / 60,
        avg_sip_channels=channel_seconds / seconds,
        avg_concurrent_calls=a.target_connects * a.call_seconds / seconds,
    )


def feasibility(a: Assumptions, connect_rate: float) -> dict[str, float]:
    """What the tender's target becomes at a connect rate we did not choose.

    Below the rate implied by the target, the target is unreachable no matter
    how well the campaign runs — the list is simply too short for the attempt
    cap. This function is the one that decides whether the contract is safe to
    sign, so it returns the list size that *would* make it reachable.
    """
    reach = 1 - (1 - connect_rate) ** a.max_attempts
    return {
        "reach": reach,
        "connects": a.farmers * reach,
        "list_needed": a.target_connects / reach,
        "shortfall": max(0.0, a.target_connects - a.farmers * reach),
    }


def agent_cost_per_minute(a: Assumptions) -> dict[str, float]:
    """Rupees per conversation minute, by stack."""
    tts_chars = a.chars_per_minute_speech * a.agent_speech_share
    sarvam_tts = _rate("sarvam", CostComponent.TTS) / 1000  # per character
    sarvam_stt = _rate("sarvam", CostComponent.STT)  # per minute

    tokens_per_minute = a.turns * a.tokens_per_turn / (a.call_seconds / 60)
    flash = _blend(0.075, 0.30) * REFERENCE_USD_INR  # rupees per 1k tokens
    gpt4o = _blend(2.50, 10.00) * REFERENCE_USD_INR
    llm = tokens_per_minute / 1000 * flash

    conv_minutes = volume(a).conversation_minutes
    prerender = (
        a.segments * (a.advisory_seconds / 60 * a.chars_per_minute_speech) * sarvam_tts
    )

    def realtime(
        usd_in_per_m: float,
        usd_out_per_m: float,
        tok_s_in: float,
        tok_s_out: float,
        cache_discount: float = 0.0,
    ) -> float:
        """Speech-to-speech, which re-sends the audio context every turn.

        That re-send is the cost, not the audio itself, and it is why context
        caching moves the number so much more than the model choice does.
        """
        context_multiple = (a.turns + 1) / 2
        audio_in = a.call_seconds * tok_s_in
        resent = audio_in * context_multiple
        if cache_discount:
            resent = audio_in + (resent - audio_in) * (1 - cache_discount)
        audio_out = a.call_seconds * a.agent_speech_share * tok_s_out
        usd = resent / 1e6 * usd_in_per_m + audio_out / 1e6 * usd_out_per_m
        return usd * REFERENCE_USD_INR / (a.call_seconds / 60)

    return {
        "C Gemini Live + context cache": realtime(3, 12, 32, 25, cache_discount=0.75),
        "A Hybrid Sarvam": (
            tts_chars * a.live_tts_share * sarvam_tts
            + sarvam_stt
            + llm
            + prerender / conv_minutes
        ),
        "B Gemini Live native": realtime(3, 12, 32, 25),
        "D Sarvam full cascade": tts_chars * sarvam_tts + sarvam_stt + llm,
        "E OpenAI GPT Realtime": realtime(32, 64, 10, 20),
        "F Deepgram + GPT-4o + ElevenLabs": (
            _rate("deepgram", CostComponent.STT)
            + tokens_per_minute / 1000 * gpt4o
            + tts_chars * _rate("elevenlabs", CostComponent.TTS) / 1000
        ),
    }


def effort_scenario(a: Assumptions, connect_rate: float) -> dict[str, float]:
    """Cost of dialling the whole list under an *effort* commitment.

    Two different deals hide behind the same campaign. Committing to 50,000
    connections makes a poor connect rate a breach; committing to 80,000
    farmers at up to three attempts makes it merely cheap. This function
    models the second, where connections are an outcome rather than a promise.

    **The risk inverts, and that is the point.** Under an outcome commitment a
    low connect rate is the thing to fear. Under an effort commitment at a
    fixed price it is a *good* connect rate that costs money — more farmers
    answer, more conversations run, and every conversation is variable cost
    against fixed revenue. Success becomes the downside case, which is exactly
    the sort of exposure nobody prices for because it does not feel like risk.
    """
    reach = 1 - (1 - connect_rate) ** a.max_attempts
    attempts = a.farmers * sum((1 - connect_rate) ** i for i in range(a.max_attempts))
    connects = a.farmers * reach
    conv_minutes = connects * a.call_seconds / 60
    carrier_minutes = connects * (a.call_seconds + a.ring_connected) / 60

    # Priced off the real conversation volume, not the planning figure: the
    # per-minute agent cost carries a pre-render term amortised over minutes,
    # so it has to be recomputed rather than reused.
    scenario = replace(a, target_connects=round(connects))
    agent = agent_cost_per_minute(scenario)["A Hybrid Sarvam"]

    cost = (
        agent * conv_minutes
        + carrier_minutes * a.carrier_rupees_per_minute
        + a.infra_rupees_campaign
        + a.campaign_one_offs
    )
    return {
        "attempts": attempts,
        "connects": connects,
        "conversation_minutes": conv_minutes,
        "cost": cost,
    }


def report(a: Assumptions | None = None) -> None:
    a = a or Assumptions()
    v = volume(a)
    rupee = lambda x: f"₹{x / 1e5:.2f}L"

    print("VOLUME")
    print(f"  implied per-attempt connect   {v.connect_rate:>10.1%}")
    print(f"  total dial attempts           {v.attempts:>10,.0f}")
    print(f"  successful connections        {v.connects:>10,.0f}")
    print(f"  conversation minutes          {v.conversation_minutes:>10,.0f}")
    print(f"  carrier-billable minutes      {v.carrier_minutes:>10,.0f}")
    print(f"  average SIP channels          {v.avg_sip_channels:>10.1f}")
    print(f"  average concurrent calls      {v.avg_concurrent_calls:>10.1f}")

    print("\nFEASIBILITY — the target at connect rates we do not control")
    print(f"  {'rate':>6}  {'reach':>7}  {'connects':>9}  {'list needed':>12}")
    for p in (0.30, v.connect_rate, 0.25, 0.22, 0.20, 0.15):
        f = feasibility(a, p)
        flag = "  <-- implied by the tender" if abs(p - v.connect_rate) < 1e-9 else ""
        print(
            f"  {p:>6.1%}  {f['reach']:>7.1%}  {f['connects']:>9,.0f}  "
            f"{f['list_needed']:>12,.0f}{flag}"
        )

    telephony = v.carrier_minutes * a.carrier_rupees_per_minute / v.conversation_minutes
    infra = a.infra_rupees_campaign / v.conversation_minutes

    print(
        f"\nPER CONVERSATION MINUTE   (telephony ₹{telephony:.2f}, infra ₹{infra:.2f})"
    )
    print(f"  {'stack':<34}{'agent':>7}{'all-in':>8}{'campaign':>11}")
    stacks = agent_cost_per_minute(a)
    for name, agent in sorted(stacks.items(), key=lambda kv: kv[1]):
        allin = agent + telephony + infra
        print(
            f"  {name:<34}{agent:>7.2f}{allin:>8.2f}"
            f"{rupee(allin * v.conversation_minutes):>11}"
        )

    chosen = stacks["A Hybrid Sarvam"] + telephony + infra
    cost = chosen * v.conversation_minutes + a.campaign_one_offs
    print(f"\nPRICING — stack A, attributable cost {rupee(cost)}")
    for price in (7_00_000, 7_50_000, 8_00_000, 8_50_000, 9_00_000):
        print(
            f"  {rupee(price)}  margin {(price - cost) / price:>6.1%}"
            f"  profit {rupee(price - cost)}"
            f"  ₹{price / v.connects:>5.2f}/connection"
            f"  ₹{price / v.attempts:.2f}/attempt"
        )

    print("\nEFFORT COMMITMENT — 80,000 farmers x 3 attempts, connections not promised")
    print(
        f"  {'rate':>6}{'attempts':>10}{'connects':>10}{'conv min':>10}{'cost':>9}",
        end="",
    )
    prices = (5_00_000, 5_50_000, 5_75_000, 6_00_000)
    for price in prices:
        print(f"{'₹' + f'{price / 1e5:.2f}L':>9}", end="")
    print()
    for p in (0.20, 0.25, 0.279, 0.30, 0.35):
        s = effort_scenario(a, p)
        print(
            f"  {p:>6.1%}{s['attempts']:>10,.0f}{s['connects']:>10,.0f}"
            f"{s['conversation_minutes']:>10,.0f}{rupee(s['cost']):>9}",
            end="",
        )
        for price in prices:
            print(f"{(price - s['cost']) / price:>9.1%}", end="")
        print()
    print("  (last four columns are margin at that price — note it FALLS as the")
    print("   connect rate rises: under a fixed price, success is the downside)")

    print("\nSENSITIVITY — cost delta against the base case")
    base = chosen * v.conversation_minutes
    for label, alt in (
        ("call runs 210s not 150s", replace(a, call_seconds=210)),
        ("agent speaks 80% not 65%", replace(a, agent_speech_share=0.80)),
        ("no pre-rendering (all TTS live)", replace(a, live_tts_share=1.0)),
        ("carrier ₹0.40 not ₹0.25", replace(a, carrier_rupees_per_minute=0.40)),
    ):
        av = volume(alt)
        acost = (
            agent_cost_per_minute(alt)["A Hybrid Sarvam"]
            + av.carrier_minutes
            * alt.carrier_rupees_per_minute
            / av.conversation_minutes
            + alt.infra_rupees_campaign / av.conversation_minutes
        ) * av.conversation_minutes
        print(f"  {label:<34}{rupee(acost - base):>10}")


if __name__ == "__main__":
    report()
