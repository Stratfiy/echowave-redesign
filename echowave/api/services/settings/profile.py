"""The person's own settings, on controls' ``member_preferences`` store.

Two jobs:

* ``absorb_onboarding`` reads the answers a person gave at the door (shell's
  ``user_onboarding``: language, confirmed timezone, preferred name) into
  ``member_preferences``, the one store Settings reads and writes. It only
  fills what is empty, so a later choice made in Settings is never undone by
  an older onboarding answer, and it records when it ran so it runs once.
* ``languages`` says which languages a person can choose, with whether
  voice can speak each one -- the shell's list, mapped onto the BCP 47 tags
  the preference store keeps.

Nothing here takes another person's id: the route passes the signed-in user.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import update

from api.db import db_client
from api.db.controls_models import MemberPreferencesModel
from api.services import features, member_preferences
from api.services.settings import SETTINGS_SHELL
from api.services.shell import languages as shell_languages


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(SETTINGS_SHELL, organization_id)


def preference_tag(code: str | None) -> str | None:
    """The preference store's tag for one of the shell's language codes, or
    None when there is none. ``en`` stays ``en``; the Indian languages take
    their ``-IN`` region, which is how the store and Sarvam name them."""
    if not code:
        return None
    if code in member_preferences.LANGUAGES:
        return code
    tagged = f"{code}-IN"
    return tagged if tagged in member_preferences.LANGUAGES else None


def languages() -> list[dict[str, Any]]:
    """Every language a person can choose, native name first. A language
    nobody has mapped is still listed under its own tag (never dropped)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for language in shell_languages.LANGUAGES:
        tag = preference_tag(language.code)
        if tag is None or tag in seen:
            continue
        seen.add(tag)
        out.append(
            {
                "tag": tag,
                "native": language.native,
                "english": language.english,
                "voice": language.voice,
            }
        )
    for tag in member_preferences.LANGUAGES:
        if tag not in seen:
            seen.add(tag)
            out.append({"tag": tag, "native": tag, "english": tag, "voice": False})
    return out


def voice_language(tag: str | None) -> bool:
    """Whether live voice can speak ``tag`` today."""
    for language in languages():
        if language["tag"] == tag:
            return bool(language["voice"])
    return False


async def absorb_onboarding(user_id: int) -> dict[str, Any]:
    """Fill this person's empty preferences from their onboarding answers.
    Returns the preferences as stored afterwards. Never raises: a read of
    Settings must not fail because the door's answers could not be read."""
    stored = await member_preferences.get(user_id)
    if stored.get("onboarding_absorbed_at"):
        return stored
    try:
        from api.services.shell import onboarding

        answers = await onboarding.get(user_id)
    except Exception as exc:  # noqa: BLE001 - see above
        logger.warning("Could not read onboarding for {}: {}", user_id, exc)
        return stored

    changes: dict[str, Any] = {}
    tag = preference_tag(answers.language)
    if tag and not stored.get("language"):
        changes["language"] = tag
    if answers.timezone and answers.timezone_confirmed and not stored.get("timezone"):
        changes["timezone"] = answers.timezone
    if answers.preferred_name and not stored.get("preferred_name"):
        changes["preferred_name"] = answers.preferred_name

    try:
        if changes:
            stored = await member_preferences.save(
                user_id, changes, revision=int(stored["revision"])
            )
    except member_preferences.Conflict as exc:
        # Somebody saved in between; their save wins and nothing is read in.
        return exc.stored
    except member_preferences.PreferenceInvalid as exc:
        logger.warning("Onboarding answers for {} not read in: {}", user_id, exc)
    await _mark_absorbed(user_id)
    return await member_preferences.get(user_id)


async def _mark_absorbed(user_id: int) -> None:
    """Stamp the row without bumping its revision: reading the door's
    answers is not an edit a second tab needs to hear about. A person with
    no row has nothing to stamp; their answers are looked at again next
    time, which is what lets answers given later still arrive."""
    async with db_client.async_session() as session:
        await session.execute(
            update(MemberPreferencesModel)
            .where(
                MemberPreferencesModel.user_id == user_id,
                MemberPreferencesModel.onboarding_absorbed_at.is_(None),
            )
            .values(onboarding_absorbed_at=datetime.now(UTC))
        )
        await session.commit()


async def onboarding_saved(user_id: int, organization_id: int | None) -> None:
    """Called by the door after a person saves their answers there: with the
    settings shell on, the answers reach Settings at once, not on first
    visit. Never raises."""
    if not enabled(organization_id) or not member_preferences.enabled():
        return
    try:
        await absorb_onboarding(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Onboarding answers not read in for {}: {}", user_id, exc)


# --- what a turn is told -----------------------------------------------------

_LENGTH_LINE = {
    "short": "Keep replies short: a few sentences unless asked for more.",
    "balanced": None,
    "detailed": "Give fuller replies with the reasoning and the steps.",
}
#: Said to the model while the person has Simple mode on (launch stream care).
SIMPLE_MODE_LINE = (
    "They use Simple mode: reply in a few short sentences of plain, everyday "
    "words, one thing at a time -- one step or one question per reply, never "
    "a list of options -- and wait for their answer before the next."
)
_turn_block: ContextVar[str | None] = ContextVar("settings_turn_block", default=None)


@contextmanager
def for_turn(block: str | None) -> Iterator[None]:
    token = _turn_block.set(block)
    try:
        yield
    finally:
        _turn_block.reset(token)


def turn_block() -> str:
    """The lines a Decibyl turn adds to its system prompt, or ""."""
    return _turn_block.get() or ""


def _language_name(tag: str | None) -> str | None:
    for language in languages():
        if language["tag"] == tag:
            return language["english"]
    return None


def prompt_block(stored: dict[str, Any], memory_off: str | None) -> str | None:
    """The person's own choices as instructions for the next turn.

    Preferences, never permissions: standing instructions are quoted as the
    person's preferences and the line says they grant nothing, so "you may
    send email without asking" in them changes no card and no tool (screen
    18: "Saving instructions does not grant new tool permissions")."""
    lines: list[str] = []
    name = stored.get("preferred_name")
    if name:
        lines.append(f"The person you are talking to likes to be called {name}.")
    language = _language_name(stored.get("language"))
    if language:
        lines.append(f"Reply in {language} unless they write in another language.")
    explanation = _language_name(stored.get("explanation_language"))
    if explanation and explanation != language:
        lines.append(
            f"When you explain something, you may add a line in {explanation}."
        )
    length = _LENGTH_LINE.get(stored.get("response_length") or "balanced")
    if length:
        lines.append(length)
    if stored.get(member_preferences.SIMPLE_MODE) and (
        member_preferences.simple_mode_offered()
    ):
        # Care stream: Simple mode is "one thing at a time" (CARE.md). The
        # screen draws it; the words have to be told.
        lines.append(SIMPLE_MODE_LINE)
    instructions = (stored.get("custom_instructions") or "").strip()
    if instructions:
        lines.append(
            "Their standing preferences, in their words (preferences only: they "
            "do not grant any permission, and every send, payment, booking or "
            "deletion still needs their OK on a card):\n<preferences>\n"
            f"{instructions}\n</preferences>"
        )
    if memory_off:
        lines.append(
            f"{memory_off} If they ask you to remember something, say that "
            "plainly instead of saying you will."
        )
    if not lines:
        return None
    # Ends on a newline: the next rule in the system prompt starts with "- ".
    return "\n\nAbout this person:\n" + "".join(f"- {line}\n" for line in lines)


async def block_for_turn(
    organization_id: int, author_id: int | None, memory_off: str | None
) -> str | None:
    """The block for one turn. Never raises; None while the shell is off."""
    if not author_id:
        return None
    try:
        stored = (
            await member_preferences.get(int(author_id))
            if enabled(organization_id) and member_preferences.enabled()
            else {}
        )
    except Exception as exc:  # noqa: BLE001 - a turn must not fail on this
        logger.warning("Preferences for {} not read: {}", author_id, exc)
        stored = {}
    return prompt_block(stored, memory_off)


# --- voices (screen 19) --------------------------------------------------------


async def voices(organization_id: int, language: str | None) -> dict[str, Any]:
    """The voices this workspace's voice engine can speak ``language`` in,
    with a sample to preview where one is already recorded.

    Never records a sample (that is a paid vendor call) and never claims a
    voice can speak a language its provider does not list. A provider whose
    key is not set up here reads ``needs_setup``.
    """
    from api.schemas.ai_model_configuration import parse_slot_choice
    from api.services.configuration import (
        managed_resolution,
        managed_tiers,
        voice_catalogue,
        voice_samples,
        workspace_models,
    )
    from api.services.configuration.ai_model_configuration import (
        get_organization_ai_model_configuration_v2,
    )
    from api.services.configuration.options.sarvam import SARVAM_LANGUAGES

    stored = await get_organization_ai_model_configuration_v2(organization_id)
    managed = workspace_models.managed_configuration(stored)
    chosen = (managed.slots or {}).get("tts", "")
    parsed = parse_slot_choice(chosen) if chosen else None
    if parsed is not None:
        provider, model = parsed[0], parsed[1]
    else:
        upstream = managed_tiers.resolve("tts", managed.tts_tier or "default")
        provider, model = upstream.provider, upstream.model
    catalogue = voice_catalogue.for_provider(provider, model=model)
    async with db_client.async_session() as session:
        platform = await managed_resolution.platform_provider_catalog(session)
    ready = provider in set(platform.get("tts", [])) or bool(parsed and parsed[2])

    if provider == "sarvam":
        supported = language in SARVAM_LANGUAGES if language else True
    else:
        # Not a provider that publishes per-language support here: say it
        # is unknown rather than claim it.
        supported = None
    short = (language or "en").split("-")[0]
    candidates = catalogue.voices if supported is not False else []
    samples, previews = await _samples(
        [voice.voice_id for voice in candidates], short, model, voice_samples
    )
    listed = [
        {
            "voice_id": voice.voice_id,
            "name": voice.name,
            "gender": voice.gender,
            "sample_url": samples.get(voice.voice_id),
        }
        for voice in candidates
    ]
    return {
        "provider": provider,
        "provider_label": workspace_models.vendor_label(provider),
        "model": model,
        "language": language,
        "language_supported": supported,
        "readiness": "ready" if ready else "needs_setup",
        "readiness_reason": (
            None
            if ready
            else f"Decibyl's {workspace_models.vendor_label(provider)} key is not "
            "set up on this deployment, so voice cannot speak yet."
        ),
        "unavailable_reason": catalogue.unavailable_reason
        or (
            f"{workspace_models.vendor_label(provider)} cannot speak this language yet."
            if supported is False
            else None
        ),
        "voices": listed,
        "previews_available": previews,
    }


#: The whole sample lookup gets this long. Storage that is slow or down must
#: cost the list its previews, never the list itself (found on a running
#: instance: thirty-nine voices, each retrying a dead store, took minutes).
SAMPLE_BUDGET_SECONDS = 3.0


async def _samples(
    voice_ids: list[str], language: str, model: str | None, voice_samples: Any
) -> tuple[dict[str, str | None], bool]:
    """Cached sample URLs for these voices, looked up together within a
    budget. Never records one. Returns the URLs and whether the lookup
    finished in time."""
    import asyncio

    async def one(voice_id: str) -> tuple[str, str | None]:
        try:
            return voice_id, await voice_samples.sample_url(voice_id, language, model)
        except Exception:  # noqa: BLE001 - no preview beats no list
            return voice_id, None

    tasks = [asyncio.ensure_future(one(v)) for v in voice_ids]
    if not tasks:
        return {}, True
    done, pending = await asyncio.wait(tasks, timeout=SAMPLE_BUDGET_SECONDS)
    for task in pending:
        task.cancel()
    found = dict(task.result() for task in done)
    return found, not pending
