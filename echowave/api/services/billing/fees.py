"""Decibyl's own charges: the BYOK platform uplift and the add-on rates.

Neither of these is a vendor's price. They are the two things we charge on top
of measured provider usage, and they share one property that earns them a
module of their own: **both are asked twice.**

``costing`` asks what a completed call is charged. ``estimator`` asks what a
call on this stack *will* be charged, before anyone builds it. When those two
answer from separate copies of a rule, the quote and the invoice drift — and
they did. The estimator's own docstring described the BYOK uplift and never
applied it, and add-ons appeared on no estimate at all, so flipping either flag
would have made every quote wrong by up to $0.025 a minute, which is more than
the platform fee itself.

So the rules live here, take plain arguments rather than a run, and are the only
place either figure is decided.

**Both are flag-gated in this module, not at the call sites.** ``costing``
charges nothing while a flag is off, so an estimate must quote nothing while a
flag is off; gating in one place is what makes that true by construction rather
than by two matching ``if`` statements.

**Read through ``constants``, never from-imported.** The rates are documented as
switchable by environment without a deploy, and binding them at import time
would quietly make that untrue.
"""

from __future__ import annotations

from api import constants
from api.services.billing.addons import by_key as addon_by_key
from api.services.billing.money import usd_to_mpaise


def byok_uplift_micros(tier: str) -> int:
    """What a BYOK tier adds to the platform rate, in micro-dollars per minute.

    An uplift on whatever rate resolved rather than an absolute rate, so a
    negotiated account rate and a volume tier both survive a BYOK call instead
    of being overwritten by a flat number.

    Sized per component because the components are not worth the same: the
    margin a BYOK call takes from us is roughly thirty times larger when it is
    the voice than when it is the language model. ``usage.byok_platform_tier``
    explains the cut; these two figures are what it costs.

    Zero for ``managed``, and zero for everything while the flag is off.
    """
    if not constants.BYOK_TIERED_FEE_ENABLED:
        return 0
    if tier == "tts":
        return constants.BYOK_TTS_UPLIFT_MICROS_USD
    if tier == "stt":
        return constants.BYOK_STT_UPLIFT_MICROS_USD
    return 0


def uplifted_platform_rate_mpaise(
    *, rate_mpaise: int, tier: str, usd_inr_paise: int | None
) -> int:
    """``rate_mpaise`` with the BYOK uplift for ``tier`` added.

    The uplift is quoted in dollars and the rate is billed in rupees, so it
    needs the same FX rate the platform rate itself was resolved at. A
    rupee-native contract has no dollar figure to convert against and is
    returned untouched: inventing an FX rate to add a dollar-quoted uplift to a
    rate deliberately written in rupees would be the one place this codebase
    billed at a made-up number.

    Added to the rate *before* the fee is computed, never as a second line, so
    the fee rounds exactly once — the same single rounding the receipt does.
    """
    micros = byok_uplift_micros(tier)
    if not micros or usd_inr_paise is None:
        return rate_mpaise
    return rate_mpaise + usd_to_mpaise(micros_usd=micros, usd_inr_paise=usd_inr_paise)


def addon_rates_mpaise(
    *, keys: tuple[str, ...] | list[str], usd_inr_paise: int | None
) -> dict[str, int]:
    """Per-minute rates in mpaise for the priced features named in ``keys``.

    Quoted in dollars and settled in rupees, so they need the same FX rate the
    platform fee was resolved at — two conversions of one currency on a single
    receipt is how a total stops reconciling. A rupee-native contract has no FX
    rate at all and gets no dollar-quoted add-on: there is no honest number to
    convert.

    An unknown key is dropped rather than raising. Usage recorded by a newer
    runtime than the costing code must not fail a receipt, and a caller asking
    to price a feature we have retired should get a quote without it.
    """
    if usd_inr_paise is None or not constants.ADDON_BILLING_ENABLED:
        return {}

    rates: dict[str, int] = {}
    for key in keys:
        addon = addon_by_key(key)
        if addon is None or not addon.is_billable:
            continue
        rate = usd_to_mpaise(
            micros_usd=addon.micros_usd_per_minute, usd_inr_paise=usd_inr_paise
        )
        # A rate that rounds to nothing is not a charge. Mirrors the cost
        # engine, which skips a non-positive add-on rate rather than writing a
        # zero line onto a receipt.
        if rate > 0:
            rates[key] = rate
    return rates
