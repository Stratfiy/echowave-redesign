"""Whether this agent follows the caller into another language.

Split from ``language_follower`` for the same reason ``spoken_digits`` is split
from its frame processor: this is one decision made once per call, and it is
worth being able to test without a pipeline.

It used to be a single environment variable, which made following a property of
the deployment rather than of the agent — and it is not one. A clinic line
wants to move to Kannada when the caller does; the same account's compliance
line reading out a disclosure approved in one language does not. Neither is a
fact about which server the call landed on, and with one switch for the whole
install an account could have it for both or for neither.

Off unless somebody turned it on. Nothing follows today, so a default of "on"
would change the behaviour of every live call in one deploy, on a judgement
nobody made per agent.
"""

from __future__ import annotations

from typing import Any

#: The per-agent key in ``workflow_configurations``.
CONFIG_KEY = "follow_caller_language"

#: The per-agent key listing which languages this agent may speak at all.
LANGUAGES_KEY = "agent_languages"


def should_follow_caller_language(
    run_configs: Any,
    *,
    is_realtime: bool = False,
) -> bool:
    """Does this call follow the caller's language?

    Realtime speech-to-speech is always ``False``, whatever was configured.
    Those models hear the caller directly and answer in the language they hear,
    so there are no ``TranscriptionFrame``s to watch and no TTS settings to
    push — turning it on would do nothing, and pinning a language on one would
    make it worse at exactly what it is already good at.
    """
    if is_realtime:
        return False
    if not isinstance(run_configs, dict):
        return False
    return bool(run_configs.get(CONFIG_KEY))


def allowed_languages(run_configs: Any) -> frozenset[str] | None:
    """The languages this agent is allowed to answer in, or None for any.

    Speech recognition guesses the language of every utterance, and on a short
    one in a noisy room it guesses wrong. That was survivable while a wrong
    guess only changed the voice. It stopped being survivable once the model
    was told as well: a clinic in Hosur whose prompt lists Tamil, Kannada,
    English and Hindi was handed "the caller has switched to Telugu, reply only
    in Telugu" -- a later and more specific instruction than the operator's own
    list, so it won, and the agent answered a Tamil speaker in Telugu and then
    in Punjabi.

    An operator naming the languages their line actually serves is the thing
    that makes the guess safe: anything outside the list is treated as noise
    rather than as a language change. Most businesses serve two or three, and
    the ones they serve are not a secret.

    None when nothing is declared, which keeps every existing agent behaving
    exactly as it does today.
    """
    if not isinstance(run_configs, dict):
        return None
    declared = run_configs.get(LANGUAGES_KEY)
    if not isinstance(declared, (list, tuple, set, frozenset)):
        return None

    tags = {
        str(entry).strip().lower().split("-")[0]
        for entry in declared
        if str(entry or "").strip()
    }
    # An empty or all-blank list says nothing, and must not be read as
    # "this agent may speak no languages at all".
    return frozenset(tags) or None
