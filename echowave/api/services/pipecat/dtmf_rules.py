"""What the caller typed on the keypad, as something the agent can read.

Every telephony serializer in pipecat already turns a keypress into an
``InputDTMFFrame`` — Twilio, Plivo, Telnyx, Exotel, Vonage, Vobiz, all of them.
Nothing downstream has ever read one, so the digits a caller pressed have been
arriving and being dropped, the same way the detected language was.

That matters more here than it sounds. A caller reading out a ten-digit mobile
number, an order id or an OTP is where speech recognition is at its worst and
the keypad is at its best, and "sorry, could you say that again" on the third
attempt is where the call is lost. It is also what a caller reaches for by
instinct the moment they are asked for a number.

**Digits are collected, not delivered one at a time.** Nobody presses one key
and waits. Handing the agent "9", then "8", then "7" would produce a reply
after each one, so an entry ends when one of three things happens:

* ``#`` — every IVR on earth has trained callers to end with it;
* a pause — the caller has stopped typing, which is the only signal available
  when they do not know about ``#``;
* the buffer filling — a caller leaning on a key should not be able to grow it
  without limit.

The pause is the interesting one, and it is a trade rather than a value to be
optimised: too short and a ten-digit number arrives as two entries, too long
and the agent sits silent after the caller has plainly finished.
"""

from __future__ import annotations

#: Ends an entry immediately. Callers have been trained on this for decades.
TERMINATOR = "#"

#: Long enough for a phone number, a long order id, or a card's last digits.
#: Short enough that a stuck key cannot grow the context.
MAX_DIGITS = 32

#: Silence after the last key that means the caller has finished typing.
#: Under a second cuts people who are reading a number off a screen in half;
#: much over two and the agent feels asleep to somebody who has finished.
DEFAULT_QUIET_SECONDS = 2.0


class DigitBuffer:
    """Keys pressed so far, and the rules for when they become an entry.

    Deliberately free of frames, timers and pipelines: what an entry *is* is
    worth being able to check without any of them. The processor around it owns
    the clock and calls :meth:`flush` when the quiet time is up.
    """

    def __init__(self, max_digits: int = MAX_DIGITS) -> None:
        self._digits: list[str] = []
        self._max_digits = max_digits

    @property
    def pending(self) -> str:
        return "".join(self._digits)

    def press(self, digit: str) -> str | None:
        """Record a keypress. Returns the finished entry, or ``None``.

        ``None`` means the caller may still be typing — the processor should
        (re)start its quiet timer and wait.
        """
        if not isinstance(digit, str) or len(digit) != 1:
            return None

        if digit == TERMINATOR:
            # The terminator is not part of what they typed. A caller entering
            # "9876543210#" means the number, not the number and a hash.
            return self.flush()

        self._digits.append(digit)
        if len(self._digits) >= self._max_digits:
            return self.flush()
        return None

    def flush(self) -> str | None:
        """The entry so far, clearing the buffer. ``None`` when empty.

        Empty rather than an empty string: a quiet timer that fires with
        nothing buffered — a lone ``#``, or a second timeout after a flush —
        must produce no turn at all, not a turn saying nothing.
        """
        entry = self.pending
        self._digits.clear()
        return entry or None


def describe(entry: str) -> str:
    """How the keypress entry is put to the model.

    Stated as an event rather than smuggled in as if the caller had said it.
    The distinction is not cosmetic: an agent that knows these were *typed*
    can trust them — the caller read them off their own screen — where a
    spoken ten-digit number is the least reliable thing on the call and worth
    reading back. It also reads correctly in the transcript afterwards, where
    "9876543210" on a line of its own would look like something we misheard.
    """
    return f"[The caller pressed these keys on their phone: {entry}]"
