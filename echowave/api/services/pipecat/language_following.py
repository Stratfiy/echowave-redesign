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
