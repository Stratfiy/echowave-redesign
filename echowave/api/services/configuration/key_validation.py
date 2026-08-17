"""Check a provider key against the vendor before storing it.

Nothing used to. A typo'd, revoked, or wrong-account key was accepted in
silence and failed for the first time **on a real customer call**, where it
surfaces as "the agent didn't answer" rather than as "that key is wrong". A
Connect button that does not connect is the same lie in a nicer shape.

The probes already existed — ``check_validity.UserConfigurationValidator`` holds
one per vendor and has done since long before this module — but they only ran
on the model-configuration save path, never on the vault. This is the thin layer
that points them at a key on its way in.

**Three outcomes, not two**, and the distinction is the whole design:

``valid``
    The vendor accepted the key. Store it.
``invalid``
    The vendor rejected it. Refuse the save — this is the typo we are here to
    catch, and storing it would only defer the failure to a live call.
``unverified``
    We could not ask: no probe exists for this vendor, or the vendor is
    unreachable. **Store it anyway** and say so. Refusing here would mean a
    vendor's outage, or our own missing probe, stops a customer configuring
    their account — turning our incompleteness into their outage. The customer
    is told the key is stored but unchecked, which is exactly the state they are
    in, and is still better than the silence they had before.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from loguru import logger

#: How long to wait for a vendor. Short: this runs inside a save the customer is
#: watching, and a vendor that has not answered in this long is one we should
#: report as unreachable rather than hold a request open for.
TIMEOUT_SECONDS = 12.0


@dataclass(frozen=True)
class ValidationResult:
    outcome: Literal["valid", "invalid", "unverified"]
    #: Shown to the customer. For ``invalid`` it is the vendor's own complaint
    #: where we have one, because "the key was rejected by OpenAI" tells someone
    #: what to fix and "validation failed" does not.
    message: str | None = None

    @property
    def may_store(self) -> bool:
        return self.outcome != "invalid"


def _validator():
    # Imported lazily: check_validity pulls in every provider SDK at module
    # scope, and the routes that store a key should not pay that cost -- nor
    # fail to import -- when no key is being validated.
    from api.services.configuration.check_validity import UserConfigurationValidator

    return UserConfigurationValidator()


def can_validate(provider: str) -> bool:
    """Whether a probe exists for this vendor at all."""
    try:
        return (provider or "").strip().lower() in _validator()._validator_map
    except Exception:  # noqa: BLE001 -- a missing SDK is "cannot validate"
        return False


async def validate_key(provider: str, api_key: str) -> ValidationResult:
    """Ask the vendor whether this key works.

    Never raises. Every failure mode this can hit is a thing to report to the
    customer as an outcome, and a save path that can be broken by a vendor's
    500 is a save path that will be.
    """
    provider = (provider or "").strip().lower()

    try:
        validator = _validator()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cannot build the key validator: {}", exc)
        return ValidationResult(
            "unverified", "The key was stored but could not be checked."
        )

    if provider not in validator._validator_map:
        return ValidationResult(
            "unverified",
            f"The key was stored. Decibyl cannot check {provider} keys "
            "automatically, so place a test call to confirm it works.",
        )

    def _run() -> bool:
        # The probes are blocking HTTP against a vendor, so they go to a thread
        # rather than stalling the event loop for the length of a round trip.
        return bool(validator._check_api_key(provider, api_key))

    try:
        ok = await asyncio.wait_for(asyncio.to_thread(_run), timeout=TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("Validating a {} key timed out.", provider)
        return ValidationResult(
            "unverified",
            f"The key was stored. {provider} did not respond in time, so it "
            "could not be checked — place a test call to confirm it works.",
        )
    except ValueError as exc:
        # The probes raise ValueError with the vendor's own complaint. That is
        # the rejection we exist to catch.
        return ValidationResult("invalid", str(exc))
    except Exception as exc:  # noqa: BLE001
        # An SDK that is missing, or a vendor returning something its client
        # library cannot parse. Not evidence the key is wrong.
        logger.warning("Could not validate a {} key: {}", provider, exc)
        return ValidationResult(
            "unverified",
            f"The key was stored. Decibyl could not reach {provider} to check "
            "it — place a test call to confirm it works.",
        )

    if ok:
        return ValidationResult("valid")

    return ValidationResult(
        "invalid",
        f"{provider} rejected this key. Check that it is correct, has not been "
        "revoked, and belongs to the right account.",
    )
