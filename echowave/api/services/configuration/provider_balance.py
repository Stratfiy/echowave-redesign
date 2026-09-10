"""How much runway is left in the accounts our keys draw on.

``credential_validation`` asks whether a key still *works*. That is a different
question from whether the account behind it can still *pay*, and the second one
fails in a way the first cannot see: a key with an exhausted balance is a
perfectly valid key. ElevenLabs answers ``/user`` cheerfully with nought
characters left; Deepgram authenticates fine on a zero balance; Plivo accepts
the credentials and refuses the call. Every probe in ``check_validity`` passes,
the managed tier stays on offer, and the first person to learn we are out of
credit is a customer whose call did not connect.

So this asks the other question, for the four accounts that will answer it.

**Only four, and that is the honest number.** OpenAI, Anthropic, Google, Groq
and the rest publish no balance endpoint at all — there is nothing to call, and
inventing a number for them would be worse than the gap. They are reported as
``unsupported`` with the reason, which is a true statement about our coverage
and reads as one on the screen.

Two shapes of answer, kept apart because they are not comparable:

``money``
    A balance in a currency — Deepgram, Plivo, Twilio. Spend it and it is gone
    until someone tops it up.
``quota``
    An allowance that refills on a cycle — ElevenLabs characters. Running it
    down is not the same emergency as running money out, because it comes back
    on its own at the reset date, which is why that date is carried.

**A threshold is only applied where we know the scale.** ``low`` needs a
denominator: for a quota that is the ceiling, and for money it is the
per-currency figure in :data:`LOW_BALANCE`. A currency we hold no figure for
gets ``ok`` or ``empty`` and never ``low``, because a bare number with no sense
of the burn rate behind it is not evidence of anything, and a threshold guessed
into existence would either cry wolf every hour or stay quiet through the
outage it exists to catch.

Which leaves Plivo, whose balance has no currency at all and is the one that
stops calls — so it would sit on ``ok`` until the moment it hit zero, and the
first warning would arrive after the outage. The way out is not to guess but
to ask: ``PLATFORM_PLIVO_LOW_BALANCE`` is the operator's own low-water mark,
in the account's own units, and the operator is the one person who knows what
currency those units are. Until it is set the row says as much, so nobody
reads a green pill as a balance somebody has vouched for.

Nothing here raises. Every failure is a vendor being unreachable, and a report
that one provider could not be read must still carry the other three.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import (
    PLATFORM_PLIVO_AUTH_ID,
    PLATFORM_PLIVO_AUTH_TOKEN,
    PLATFORM_PLIVO_LOW_BALANCE,
    PLATFORM_TWILIO_ACCOUNT_SID,
    PLATFORM_TWILIO_AUTH_TOKEN,
)
from api.services.configuration import platform_credentials

Status = Literal["ok", "low", "empty", "unsupported", "unreachable", "unconfigured"]

#: Short, because this runs inside a staff screen someone is watching and a
#: vendor that has not answered by now is one to report as unreachable rather
#: than hold the request open for.
TIMEOUT_SECONDS = 12.0

#: A quota with less than this fraction of its ceiling left is ``low``.
LOW_QUOTA_FRACTION = 0.10

#: Below this, a money balance is ``low``. These are roughly a working week of
#: our current volume, which is the notice period worth having: enough time for
#: a top-up to clear before the account empties. Tune them as volume grows —
#: they are a starting figure, not a constant of nature.
LOW_BALANCE: dict[str, float] = {
    "usd": 50.0,
    "inr": 4000.0,
}


@dataclass(frozen=True)
class ProviderBalance:
    """What one provider says is left, or why we cannot say.

    ``amount`` and ``currency`` are the money answer; ``used`` and ``limit`` the
    quota one. A given row fills in one pair, never both, and an ``unsupported``
    or ``unreachable`` row fills in neither — ``detail`` carries the reason
    instead, and it is written to be read by an operator rather than parsed.
    """

    provider: str
    status: Status
    kind: Literal["money", "quota"] | None = None
    amount: float | None = None
    currency: str | None = None
    used: float | None = None
    limit: float | None = None
    renews_at: datetime | None = None
    detail: str | None = None

    @property
    def remaining(self) -> float | None:
        """What is left, in whatever unit this row is denominated in."""
        if self.kind == "money":
            return self.amount
        if self.kind == "quota" and self.limit is not None and self.used is not None:
            return max(self.limit - self.used, 0.0)
        return None

    @property
    def needs_attention(self) -> bool:
        """Whether an operator should do something about this row today.

        Deliberately not true for ``unreachable``: a vendor having a bad minute
        is not evidence about our balance, and treating it as one would mean an
        alert every time a provider's status page went yellow.
        """
        return self.status in ("low", "empty")


def _classify_money(
    amount: float, currency: str | None, floor: float | None = None
) -> Status:
    """Where a balance sits, given whatever denominator we have.

    ``floor`` is an operator's own low-water mark, in the account's units, and
    takes precedence over the per-currency table — it is the only thing that
    can judge a balance whose currency the vendor never states.
    """
    if amount <= 0:
        return "empty"
    if floor is None:
        floor = LOW_BALANCE.get((currency or "").strip().lower())
    if floor is not None and amount < floor:
        return "low"
    return "ok"


def _classify_quota(used: float, limit: float) -> Status:
    if limit <= 0:
        # No ceiling to measure against. Nothing sensible to say beyond "we
        # asked", so do not pretend to a verdict.
        return "ok"
    left = max(limit - used, 0.0)
    if left <= 0:
        return "empty"
    if left / limit < LOW_QUOTA_FRACTION:
        return "low"
    return "ok"


def _number(value: Any) -> float | None:
    """Vendors send balances as strings as often as numbers."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# -- the vendors that answer ------------------------------------------------


async def _elevenlabs(client: httpx.AsyncClient, api_key: str) -> ProviderBalance:
    """Characters left in the current billing cycle.

    A quota rather than money: ElevenLabs bills a plan and meters characters
    against it, and the count resets on ``next_character_count_reset_unix``
    without anybody topping anything up.
    """
    response = await client.get(
        "https://api.elevenlabs.io/v1/user/subscription",
        headers={"xi-api-key": api_key},
    )
    response.raise_for_status()
    payload = response.json() or {}

    used = _number(payload.get("character_count"))
    limit = _number(payload.get("character_limit"))
    if used is None or limit is None:
        return ProviderBalance(
            "elevenlabs",
            "unreachable",
            detail="ElevenLabs answered without a character count.",
        )

    reset = _number(payload.get("next_character_count_reset_unix"))
    return ProviderBalance(
        "elevenlabs",
        _classify_quota(used, limit),
        kind="quota",
        used=used,
        limit=limit,
        renews_at=datetime.fromtimestamp(reset, UTC) if reset else None,
        detail=f"{payload.get('tier') or 'unknown'} plan",
    )


async def _deepgram(client: httpx.AsyncClient, api_key: str) -> ProviderBalance:
    """Money left, summed across the project's balances.

    Two round trips because the balance endpoint is per project and the key
    does not name one. Summed rather than first-wins: a Deepgram project can
    hold several balances (a paid one alongside promotional credit), and the
    one that happens to sort first is not the one that matters.
    """
    headers = {"Authorization": f"Token {api_key}"}
    projects = await client.get("https://api.deepgram.com/v1/projects", headers=headers)
    projects.raise_for_status()
    rows = (projects.json() or {}).get("projects") or []
    if not rows:
        return ProviderBalance(
            "deepgram", "unreachable", detail="This Deepgram key sees no projects."
        )

    project_id = rows[0].get("project_id")
    balances = await client.get(
        f"https://api.deepgram.com/v1/projects/{project_id}/balances", headers=headers
    )
    balances.raise_for_status()
    entries = (balances.json() or {}).get("balances") or []

    total = 0.0
    seen = False
    units = None
    for entry in entries:
        amount = _number(entry.get("amount"))
        if amount is None:
            continue
        seen = True
        total += amount
        units = units or entry.get("units")

    if not seen:
        return ProviderBalance(
            "deepgram", "unreachable", detail="Deepgram returned no balance figures."
        )

    # Deepgram calls the unit "usd"; anything else we pass through untouched
    # rather than assume, and _classify_money declines to judge what it does
    # not have a floor for.
    currency = (units or "usd").lower()
    return ProviderBalance(
        "deepgram",
        _classify_money(total, currency),
        kind="money",
        amount=total,
        currency=currency,
        detail=f"project {project_id}" if project_id else None,
    )


async def _plivo(
    client: httpx.AsyncClient, auth_id: str, token: str
) -> ProviderBalance:
    """Cash credits on Decibyl's own Plivo account.

    This is the one that stops calls. Every managed number, every outbound
    campaign minute and every verification SMS is billed here, so it empties
    faster than anything else on this screen and takes the whole platform's
    telephony with it.

    Plivo does not name a currency on the account resource. Rather than assume
    one — an Indian account bills in INR, a US one in USD, and guessing wrong
    would put the threshold out by a factor of eighty — the row carries no
    currency, and the low-water mark comes from the operator instead:
    ``PLATFORM_PLIVO_LOW_BALANCE``, in whatever units the account is in.

    Without it this row is only ever ``ok`` or ``empty``, and ``empty`` is the
    moment calls have already stopped — a warning that arrives after the
    outage it was meant to prevent. So when no floor is set the row says so
    in as many words, because a green "Healthy" pill on an account nobody has
    given a threshold reads as reassurance it has not earned.
    """
    response = await client.get(
        f"https://api.plivo.com/v1/Account/{auth_id}/", auth=(auth_id, token)
    )
    response.raise_for_status()
    payload = response.json() or {}

    credits = _number(payload.get("cash_credits"))
    if credits is None:
        return ProviderBalance(
            "plivo", "unreachable", detail="Plivo answered without a credit figure."
        )

    floor = plivo_floor()
    notes = [payload.get("name") or None]
    if floor is None:
        notes.append(
            "no low-balance threshold set — set PLATFORM_PLIVO_LOW_BALANCE "
            "to be warned before this empties"
        )
    return ProviderBalance(
        "plivo",
        _classify_money(credits, None, floor),
        kind="money",
        amount=credits,
        detail=" · ".join(note for note in notes if note) or None,
    )


def plivo_floor() -> float | None:
    """The operator's low-water mark for Decibyl's Plivo account.

    Nonsense is treated as unset rather than as zero, and said out loud: a
    typo'd threshold that silently became "warn below nothing" would leave the
    account looking healthy all the way down, which is the exact failure the
    setting exists to prevent.
    """
    if not PLATFORM_PLIVO_LOW_BALANCE:
        return None
    floor = _number(PLATFORM_PLIVO_LOW_BALANCE)
    if floor is None:
        logger.warning(
            "PLATFORM_PLIVO_LOW_BALANCE is {!r}, which is not a number. No "
            "low-balance threshold is being applied to the Plivo account.",
            PLATFORM_PLIVO_LOW_BALANCE,
        )
    return floor


async def _twilio(client: httpx.AsyncClient, sid: str, token: str) -> ProviderBalance:
    response = await client.get(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Balance.json",
        auth=(sid, token),
    )
    response.raise_for_status()
    payload = response.json() or {}

    balance = _number(payload.get("balance"))
    if balance is None:
        return ProviderBalance(
            "twilio", "unreachable", detail="Twilio answered without a balance."
        )
    currency = (payload.get("currency") or "usd").lower()
    return ProviderBalance(
        "twilio",
        _classify_money(balance, currency),
        kind="money",
        amount=balance,
        currency=currency,
    )


#: Vendors with a balance endpoint, and how to read it. Membership here is the
#: definition of "supported" — see :func:`can_read_balance`.
MODEL_PROVIDERS = {
    "elevenlabs": _elevenlabs,
    "deepgram": _deepgram,
}

#: Why the rest cannot be read. Kept as prose because it goes straight onto the
#: screen: an operator who sees a blank next to OpenAI should learn that there
#: is nothing to call rather than that we forgot.
NO_BALANCE_API: dict[str, str] = {
    "openai": "OpenAI publishes no balance endpoint. Watch the credit balance "
    "on platform.openai.com/settings/organization/billing.",
    "openai_realtime": "Billed to the same OpenAI account, which publishes no "
    "balance endpoint.",
    "anthropic": "Anthropic publishes no balance endpoint. Watch the credit "
    "balance in the Anthropic Console.",
    "google": "Google bills through Cloud Billing, which reports spend rather "
    "than a remaining balance.",
    "google_realtime": "Billed to the same Google Cloud account, which reports "
    "spend rather than a remaining balance.",
    "groq": "Groq publishes no balance endpoint.",
    "cartesia": "Cartesia publishes no balance endpoint.",
    "sarvam": "Sarvam publishes no balance endpoint. Watch the credits on the "
    "Sarvam dashboard.",
    "azure": "Azure bills through a subscription, which reports spend rather "
    "than a remaining balance.",
}


def can_read_balance(provider: str) -> bool:
    """Whether asking this vendor for a balance is even possible."""
    return (provider or "").strip().lower() in MODEL_PROVIDERS


async def read_model_provider_balance(provider: str, api_key: str) -> ProviderBalance:
    """Ask one vendor what is left. Never raises.

    Every way this can fail — no endpoint, a timeout, a rejection, a body we
    cannot parse — is a row on a report, not an exception for a caller to
    handle. A balance screen that 500s because one vendor is down tells an
    operator less than one that shows three balances and a shrug.
    """
    provider = (provider or "").strip().lower()

    if provider in NO_BALANCE_API:
        return ProviderBalance(provider, "unsupported", detail=NO_BALANCE_API[provider])
    fetch = MODEL_PROVIDERS.get(provider)
    if fetch is None:
        return ProviderBalance(
            provider,
            "unsupported",
            detail="Decibyl has no balance probe for this provider.",
        )

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            return await fetch(client, api_key)
    except httpx.HTTPStatusError as exc:
        # Worth separating from a transport failure: a 401 here means the key
        # is wrong, which is credential_validation's business, not ours. Either
        # way we learned nothing about the balance.
        return ProviderBalance(
            provider,
            "unreachable",
            detail=f"{provider} answered {exc.response.status_code} to the "
            "balance request.",
        )
    except Exception as exc:  # noqa: BLE001 - a report must survive one bad vendor
        logger.warning("Could not read the {} balance: {}", provider, exc)
        return ProviderBalance(
            provider, "unreachable", detail=f"Could not reach {provider}."
        )


async def _carrier_balance(
    provider: str, fetch, *credentials: str | None
) -> ProviderBalance:
    if not all(credentials):
        return ProviderBalance(
            provider,
            "unconfigured",
            detail=f"Decibyl holds no {provider} account credentials.",
        )
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            return await fetch(client, *credentials)
    except httpx.HTTPStatusError as exc:
        return ProviderBalance(
            provider,
            "unreachable",
            detail=f"{provider} answered {exc.response.status_code} to the "
            "balance request.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read the {} balance: {}", provider, exc)
        return ProviderBalance(
            provider, "unreachable", detail=f"Could not reach {provider}."
        )


async def read_all(session: AsyncSession) -> list[ProviderBalance]:
    """Every account Decibyl pays from, asked at once.

    The model providers come from the platform vault; the carriers from the
    environment, because Decibyl's own Plivo and Twilio accounts are not rows
    in ``platform_provider_credentials`` — they predate it and are read from
    settings by the KYC and messaging paths too.

    One row per *account*, not per credential. A provider that serves two
    components on one key would otherwise appear twice with the same figure and
    invite someone to add them together.

    Concurrent, because this is four independent round trips behind a screen
    someone is waiting on, and doing them in sequence would make the page as
    slow as the sum of the vendors' worst days.
    """
    jobs: list[Any] = []

    for provider in sorted(MODEL_PROVIDERS):
        key = None
        for component in ("tts", "stt", "llm"):
            key = await platform_credentials.resolve_api_key(
                session, component=component, provider=provider
            )
            if key:
                break
        if not key:
            jobs.append(
                _ready(
                    ProviderBalance(
                        provider,
                        "unconfigured",
                        detail=f"No platform {provider} key is stored.",
                    )
                )
            )
            continue
        jobs.append(read_model_provider_balance(provider, key))

    jobs.append(
        _carrier_balance(
            "plivo", _plivo, PLATFORM_PLIVO_AUTH_ID, PLATFORM_PLIVO_AUTH_TOKEN
        )
    )
    jobs.append(
        _carrier_balance(
            "twilio",
            _twilio,
            PLATFORM_TWILIO_ACCOUNT_SID,
            PLATFORM_TWILIO_AUTH_TOKEN,
        )
    )

    return list(await asyncio.gather(*jobs))


async def _ready(balance: ProviderBalance) -> ProviderBalance:
    """A settled answer, shaped like the awaitables it is gathered with."""
    return balance
