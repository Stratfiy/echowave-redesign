"""Callback factory helpers for :pyclass:`~api.services.workflow.pipecat_engine.PipecatEngine`.

Each helper takes a :class:`PipecatEngine` instance and returns an async
callback function suitable for passing to the various pipeline processors.
Separating these helpers into their own module keeps
``pipecat_engine.py`` focused on high-level engine orchestration logic while
encapsulating the callback implementations here for easier maintenance and
unit-testing.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger
from pipecat.frames.frames import (
    LLMMessagesAppendFrame,
)
from pipecat.utils.enums import EndTaskReason

from api.schemas.workflow_configurations import (
    DEFAULT_MAX_USER_IDLE_TIMEOUT_SECONDS,
)

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine import PipecatEngine


# ---------------------------------------------------------------------------
# User-idle handling
# ---------------------------------------------------------------------------


def _as_seconds(value) -> float:
    """A duration we are willing to hold a call open for, or zero.

    Deliberately strict about the type. Anything with a ``__float__`` would be
    accepted by a ``float()`` in a try block — a string, a Decimal, a test
    double — and the thing being decided here is how long a real person is left
    listening to silence. Only an actual number gets to decide that, and a bool
    is not one despite being an int: ``patience_seconds=True`` means somebody
    sent the wrong field, not one second.

    Negative is treated as absent rather than clamped: it is a malformed value,
    and the safe reading of a malformed value is "no special patience".
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value) if value > 0 else 0.0


#: How many times a quiet caller is asked before the call is ended. Two,
#: because one was measured to be too few: run 307 on the Narayani agent ended
#: with `user_idle_max_duration_exceeded` on a caller who had said nothing yet,
#: on a greeting that names five languages and takes a while to take in.
#:
#: It is a count rather than a longer tick on purpose. The tick also meters
#: per-node patience, so raising it would quietly change how long every patient
#: step waits -- a different decision, made by accident.
NUDGES_BEFORE_HANGING_UP = 2


class UserIdleHandler:
    """Helper class to manage user idle retry logic with state."""

    def __init__(self, engine: "PipecatEngine"):
        self._engine = engine
        self._retry_count = 0
        #: Seconds of silence waited through on the current node, in ticks of
        #: the pipeline's idle timeout. Reset whenever the caller speaks.
        self._waited_seconds = 0.0

    def reset(self):
        """Reset the retry count when user becomes active."""
        self._retry_count = 0
        self._waited_seconds = 0.0

    def _node_patience(self) -> float:
        """How long this step is willing to be silent before asking.

        Some steps ask a question; some send the caller away to do something.
        "Press 6# on the controller and tell me whether the GPS light is
        blinking" is the second kind, and the default timeout assumes the
        first — that a quiet caller is a caller thinking. Someone walking to a
        vehicle is not thinking, and prompting them twice and hanging up is
        exactly the wrong response to a person doing what we asked.

        None on the node means the agent's default, which is the behaviour
        every existing workflow already has.
        """
        node = getattr(self._engine, "_current_node", None)
        return _as_seconds(getattr(node, "patience_seconds", None) if node else None)

    async def handle_idle(self, aggregator):
        """Handle user idle event with escalating prompts.

        The contract is two strikes: the first asks whether the caller is
        still there, the second disconnects. Only the second may end the call
        — see the instruction on the first message for why that has to be said
        out loud.

        Before either, a step may ask for more rope. The idle event fires on
        the pipeline's own timer, so patience is spent in whole ticks of it:
        while the silence this node has absorbed is still under its budget we
        say nothing at all and take no strike. Saying nothing is the point —
        the caller is mid-task, and "are you still there?" every ten seconds
        is worse than the silence it interrupts.
        """
        patience = self._node_patience()
        if patience > 0:
            tick = self._engine_idle_tick()
            self._waited_seconds += tick
            if self._waited_seconds <= patience:
                logger.debug(
                    "User idle on a patient step: {}s of {}s waited, staying quiet",
                    self._waited_seconds,
                    patience,
                )
                return

        self._retry_count += 1
        logger.debug(f"Handling user_idle, attempt: {self._retry_count}")

        if self._retry_count <= NUDGES_BEFORE_HANGING_UP:
            # Two nudges before goodbye, not one. A person who has gone quiet
            # gets asked, gets asked once more, and only then is let go --
            # which is how a person behaves, and it doubles the silence a
            # caller is allowed without changing the tick.
            #
            # Raising the tick instead would have been the obvious fix and is
            # the wrong one: `_engine_idle_tick` also meters per-node patience,
            # so doubling it would silently double how long every patient step
            # waits. See the note on that method.
            first = self._retry_count == 1
            message = {
                "role": "user",
                "content": (
                    "The user has been quiet. Politely and briefly ask if "
                    "they're still there in the language that the user has "
                    "been speaking so far. Do not end the call and do not "
                    "call any tool — say the words and nothing else. They may "
                    "simply be thinking, and hanging up on someone who is "
                    "about to speak is worse than waiting."
                    if first
                    else "The user has still not spoken. Say once more, in a "
                    "different and shorter way, that you are still there and "
                    "happy to wait. Do not end the call and do not call any "
                    "tool. Repeating your last sentence word for word is what "
                    "a machine does; say it as a person would the second time."
                ),
            }
            await aggregator.push_frame(LLMMessagesAppendFrame([message], run_llm=True))
            return

        message = {
            "role": "user",
            "content": "The user has been quiet. We will be disconnecting the call now. Wish them a good day in the language that the user has been speaking so far.",
        }
        await aggregator.push_frame(LLMMessagesAppendFrame([message], run_llm=True))
        await self._engine.end_call_with_reason(
            EndTaskReason.USER_IDLE_MAX_DURATION_EXCEEDED.value
        )

    def _engine_idle_tick(self) -> float:
        """Seconds between idle events, as the pipeline was configured.

        Read from the engine rather than assumed, so raising the agent-level
        timeout does not silently multiply every node's patience by the same
        factor. Falls back to the shipped default when the engine cannot say.
        """
        tick = _as_seconds(getattr(self._engine, "user_idle_timeout_seconds", None))
        return tick or DEFAULT_MAX_USER_IDLE_TIMEOUT_SECONDS


def create_user_idle_handler(engine: "PipecatEngine") -> UserIdleHandler:
    """Return a UserIdleHandler that manages user-idle timeouts with state."""
    return UserIdleHandler(engine)


# ---------------------------------------------------------------------------
# Max-duration handling
# ---------------------------------------------------------------------------


def create_max_duration_callback(engine: "PipecatEngine"):
    """Return a callback that cancels the task when the hard call limit is exceeded."""

    async def handle_max_duration():
        logger.debug("Max call duration exceeded. Terminating call")
        await engine.end_call_with_reason(
            EndTaskReason.CALL_DURATION_EXCEEDED.value,
            abort_immediately=True,
        )

    return handle_max_duration


# ---------------------------------------------------------------------------
# Generation-started handling
# ---------------------------------------------------------------------------


def create_generation_started_callback(engine: "PipecatEngine"):
    """Return a callback that resets flags at the start of each LLM generation."""

    async def handle_generation_started():
        logger.debug("LLM generation started in callback processor")
        # Clear reference text from previous generation
        engine._current_llm_generation_reference_text = ""

    return handle_generation_started


def create_aggregation_correction_callback(engine: "PipecatEngine"):
    """Create a callback that uses engine's reference text to correct corrupted aggregation."""

    def correct_corrupted_aggregation(ref: str, corrupted: str) -> str:
        """Correct corrupted text by aligning it with reference text.

        This is a pure function that doesn't depend on engine instance.
        """
        # 1) Safety check: if ref (minus spaces) is shorter than corrupted, bail out
        # also if corrupted is less than 10 characters, lets also return that since most likely
        # Elevenlabs returned the right alignment
        alnum_corr = "".join(ch for ch in corrupted if ch.isalnum())
        alnum_ref = "".join(ch for ch in ref if ch.isalnum())

        if corrupted in ref or len(alnum_ref) < len(alnum_corr) or len(alnum_corr) < 10:
            return corrupted

        logger.debug(
            f"In correct_corrupted_aggregation: ref: {ref} corrupted: {corrupted}"
        )

        # 2) Find where in `ref` we should start aligning.
        #    We take the first N (N=10) characters of `corrupted`
        #    and look for all their occurrences in `ref`.
        #    We pick the *last* one
        prefix = corrupted[:10]

        # find all start‐indices of that prefix in ref
        starts = [m.start() for m in re.finditer(re.escape(prefix), ref)]
        start_idx = starts[-1] if starts else 0

        # 3) Now run the same two‑pointer scan from start_idx
        i, j = start_idx, 0
        out_chars = []
        while i < len(ref) and j < len(corrupted):
            r_ch, c_ch = ref[i], corrupted[j]
            if r_ch == c_ch:
                out_chars.append(r_ch)
                i += 1
                j += 1

            elif c_ch == " ":
                # extra space in corrupted → skip it
                j += 1

            elif r_ch == " " or r_ch in ".,;:!?":
                # missing structural char in corrupted → emit from ref
                out_chars.append(r_ch)
                i += 1

            else:
                # letter mismatch → best‑effort copy from ref
                out_chars.append(r_ch)
                i += 1
                j += 1

        # 4) A final check - the final created output should be exactly
        # as corrupted sentence sans whitespace.
        alnum_out = "".join([ch for ch in out_chars if ch.isalnum()])
        if alnum_out != alnum_corr:
            return corrupted

        # 5) Join and return exactly what we built
        return "".join(out_chars)

    def correct_aggregation(corrupted: str) -> str:
        reference = engine._current_llm_generation_reference_text

        if not reference:
            return corrupted

        # Apply the correction algorithm
        corrected = correct_corrupted_aggregation(reference, corrupted)
        return corrected

    return correct_aggregation
