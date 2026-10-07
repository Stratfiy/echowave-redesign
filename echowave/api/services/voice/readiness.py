"""Whether live voice and calls can run for this person, said honestly.

The design's capability contract: ``available``, ``needs_setup``,
``disabled_by_policy`` or ``unavailable``, each with a reason and a next
step. Asked before a session or a call starts, never by trying it -- nothing
here calls a provider, so asking costs nothing and cannot spend money.

A missing key is ``needs_setup`` with the slot named, never a button that
connects to silence (the defect ``key_readiness`` was written for).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from loguru import logger

from api.services import features
from api.services.voice import catalogue

AVAILABLE = "available"
NEEDS_SETUP = "needs_setup"
DISABLED_BY_POLICY = "disabled_by_policy"
UNAVAILABLE = "unavailable"


@dataclass
class Readiness:
    state: str
    reason: str | None = None
    next_step: str | None = None
    #: What the session would run on, for the screen and the session record.
    #: Names only, never a key.
    config: dict[str, Any] = field(default_factory=dict)
    #: Said beside an available state: e.g. the person's language is typed
    #: and captioned but not spoken.
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _section(effective: Any, name: str) -> Any:
    return getattr(effective, name, None)


def _has_key(section: Any) -> bool:
    return bool(section is not None and (getattr(section, "api_key", "") or "").strip())


async def _speech_slots(organization_id: int) -> tuple[list[str], dict[str, Any]]:
    """The speech slots that cannot run, and what the others would run on."""
    from api.services.configuration.ai_model_configuration import (
        get_effective_ai_model_configuration_for_organization,
    )

    effective = await get_effective_ai_model_configuration_for_organization(
        organization_id
    )
    missing: list[str] = []
    config: dict[str, Any] = {}
    for slot, label in (("stt", "transcriber"), ("tts", "voice")):
        section = _section(effective, slot)
        if not _has_key(section):
            missing.append(label)
            continue
        config[slot] = {
            "provider": getattr(section, "provider", None),
            "model": getattr(section, "model", None),
        }
    return missing, config


async def _brain_ready(organization_id: int) -> str | None:
    """None when Decibyl's model can answer a turn; else why not."""
    from api.db import db_client
    from api.services.agent_builder import settings

    try:
        async with db_client.async_session() as session:
            model = await settings.resolve_for_organization(
                session, None, organization_id=organization_id
            )
    except settings.BuilderUnavailable as exc:
        return str(exc) or "Decibyl's model is not set up."
    if not (getattr(model, "api_key", "") or "").strip():
        return "Decibyl's model has no key."
    return None


async def live_voice(
    *, organization_id: int, user_id: int, language: str | None = None
) -> Readiness:
    """Whether this person can talk with Decibyl now."""
    if not features.is_on("decibyl_voice", organization_id):
        return Readiness(
            DISABLED_BY_POLICY,
            reason="Live voice is switched off for this workspace.",
            next_step="Use Dictate, or type your message.",
        )
    try:
        missing, config = await _speech_slots(organization_id)
        brain = await _brain_ready(organization_id)
    except Exception as exc:  # noqa: BLE001 - a readiness question must answer
        logger.warning("Voice readiness for org {} failed: {}", organization_id, exc)
        return Readiness(
            UNAVAILABLE,
            reason="We could not check whether voice is set up.",
            next_step="Try again in a moment, or type your message.",
        )
    if brain:
        missing.append("assistant model")
    if missing:
        return Readiness(
            NEEDS_SETUP,
            reason=f"Live voice needs a {_join(missing)} before it can start.",
            next_step=(
                "An admin can add one under Settings, Models. Until then, "
                "type or use Dictate."
            ),
            config=config,
        )
    notes = []
    spoken = catalogue.spoken_language(language)
    if language and spoken is None:
        notes.append(
            "Your language is not spoken by the voice yet. Decibyl will answer "
            "in captions and text in it, and speak in English (India)."
        )
    config["language"] = spoken or "en-IN"
    return Readiness(AVAILABLE, config=config, notes=notes)


async def calls(*, organization_id: int) -> Readiness:
    """Whether Decibyl can place a call for this workspace ("call it for me")."""
    if not features.is_on("call_for_me", organization_id):
        return Readiness(
            DISABLED_BY_POLICY,
            reason="Calls on your behalf are switched off for this workspace.",
        )
    from api.db import db_client
    from api.services.voice import appointments

    try:
        telephony = await db_client.get_default_telephony_configuration(organization_id)
        policy = await appointments.get_policy(organization_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Call readiness for org {} failed: {}", organization_id, exc)
        return Readiness(UNAVAILABLE, reason="We could not check the phone line.")
    if not telephony:
        return Readiness(
            NEEDS_SETUP,
            reason="No phone line is connected, so Decibyl cannot place a call.",
            next_step="An admin can connect one under Settings, Phone numbers.",
        )
    if not policy.get("call_workflow_id"):
        return Readiness(
            NEEDS_SETUP,
            reason="No Call and Appointment helper is chosen to make the call.",
            next_step="Choose one under Settings, Voice and language, Calls.",
        )
    return Readiness(AVAILABLE)


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"
