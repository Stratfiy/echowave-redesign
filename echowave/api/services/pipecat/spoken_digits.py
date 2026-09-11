"""How Indians actually say numbers, and what the transcriber does with it.

Numbers are the payload of most calls this platform runs: a phone number to
call back on, an order number, an amount, an OTP. A number the agent hears
wrong is not a degraded call, it is a failed one — and unlike a misheard
sentence, nothing downstream can recover it.

Turning on Deepgram's ``numerals`` fixes the easy half ("nine eight seven" →
"987"). This module is the half it does not do, because it is not how the
model was trained and it is specific to how numbers are said here:

* **"double" and "triple".** "double five" is 55, "triple seven" is 777, and
  in India this is the *default* way to read a repeated digit aloud rather
  than a flourish. Deepgram returns the words.
* **"oh" for zero.** "nine eight oh two" is 9802. Left alone, "oh" survives as
  a word in the middle of a digit run.
* **Hindi digits inside English.** A caller reading a number in code-mixed
  speech switches register mid-string — "nau nau eight seven" — and an English
  model transcribes the Hindi words phonetically.

**The conservative rule.** Every function here rewrites a digit *run* and
never a lone word. "double" in "double room", "do" in "do it", "char" in
"charge" must survive untouched, and the only reliable signal that a word is
part of a number is that it sits among other number words. A normaliser that
mangles ordinary sentences to fix phone numbers is a worse bug than the one it
fixes, so where this is unsure it does nothing.
"""

from __future__ import annotations

import re

# Digit words we accept inside a run. Hindi entries are the common Latin
# transliterations a transcriber produces for code-mixed speech — several
# spellings each, because there is no standard one and callers do not know
# there would be.
_DIGIT_WORDS: dict[str, str] = {
    # English
    "zero": "0",
    "nought": "0",
    "naught": "0",
    "oh": "0",
    "o": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    # Hindi / Urdu, as transliterated by an English-language transcriber
    "shunya": "0",
    "sunya": "0",
    "ek": "1",
    "eak": "1",
    "do": "2",
    "teen": "3",
    "char": "4",
    "chaar": "4",
    "paanch": "5",
    "panch": "5",
    "pach": "5",
    "chhe": "6",
    "che": "6",
    "chha": "6",
    "saat": "7",
    "sat": "7",
    "aath": "8",
    "aat": "8",
    "nau": "9",
    "no": "9",
}

#: Digit words written in an Indic script, which is what a transcriber running
#: in Tamil, Telugu, Kannada or Hindi returns when the caller says a number.
#:
#: This was the hole. The list above is English plus *Latin-transliterated*
#: Hindi, so a caller speaking Tamil who says "seven zero seven five" -- in
#: English, as almost everyone here reads a phone number -- came back as
#: ``செவன் ஜீரோ செவன் ஃபைவ்`` and matched nothing. Measured on run 303: the
#: agent read that back as "0705", losing a digit and reordering the rest, and
#: the caller spent the next minute correcting it. A misheard phone number is
#: not a degraded call, it is a failed one.
#:
#: Two registers per script, because callers use both in one breath -- run 303
#: has ``செவன், ஏழு`` (the English word, then the Tamil one, for the same
#: digit):
#:
#: * English digit words spelt phonetically in the local script.
#: * The language's own numerals.
#:
#: Entries marked *observed* are copied from real transcriber output on this
#: account; the rest follow the same spelling pattern and are inferred. An
#: inferred spelling that is wrong simply never matches, which is why guessing
#: is safe here in a way it would not be elsewhere -- and why a wrong guess is
#: invisible rather than harmful.
_INDIC_DIGIT_WORDS: dict[str, str] = {
    # ---- Tamil: English digits in Tamil script (ஜீரோ/ஃபைவ்/செவன் observed)
    "ஜீரோ": "0",
    "ஒன்": "1",
    "டூ": "2",
    "த்ரீ": "3",
    "ஃபோர்": "4",
    "ஃபைவ்": "5",
    "சிக்ஸ்": "6",
    "செவன்": "7",
    "எயிட்": "8",
    "நைன்": "9",
    # ---- Tamil numerals (ஏழு observed)
    "பூஜ்யம்": "0",
    "சுழியம்": "0",
    "ஒன்று": "1",
    "இரண்டு": "2",
    "மூன்று": "3",
    "நான்கு": "4",
    "ஐந்து": "5",
    "ஆறு": "6",
    "ஏழு": "7",
    "எட்டு": "8",
    "ஒன்பது": "9",
    # ---- Telugu: English digits in Telugu script -- all ten observed in one
    # utterance on run 302, "వన్ టూ త్రీ ఫోర్ ఫైవ్ సిక్స్ సెవెన్ ఎయిట్ నైన్ జీరో"
    "జీరో": "0",
    "వన్": "1",
    "టూ": "2",
    "త్రీ": "3",
    "ఫోర్": "4",
    "ఫైవ్": "5",
    "సిక్స్": "6",
    "సెవెన్": "7",
    "ఎయిట్": "8",
    "నైన్": "9",
    # ---- Telugu numerals (సున్నా, రెండు, నాలుగు observed)
    "సున్నా": "0",
    "ఒకటి": "1",
    "రెండు": "2",
    "మూడు": "3",
    "నాలుగు": "4",
    "ఐదు": "5",
    "ఆరు": "6",
    "ఏడు": "7",
    "ఎనిమిది": "8",
    "తొమ్మిది": "9",
    # ---- Kannada: English digits in Kannada script
    "ಜೀರೋ": "0",
    "ಒನ್": "1",
    "ಟೂ": "2",
    "ತ್ರೀ": "3",
    "ಫೋರ್": "4",
    "ಫೈವ್": "5",
    "ಸಿಕ್ಸ್": "6",
    "ಸೆವೆನ್": "7",
    "ಎಯ್ಟ್": "8",
    "ನೈನ್": "9",
    # ---- Kannada numerals
    "ಸೊನ್ನೆ": "0",
    "ಒಂದು": "1",
    "ಎರಡು": "2",
    "ಮೂರು": "3",
    "ನಾಲ್ಕು": "4",
    "ಐದು": "5",
    "ಆರು": "6",
    "ಏಳು": "7",
    "ಎಂಟು": "8",
    "ಒಂಬತ್ತು": "9",
    # ---- Devanagari: English digits in Devanagari script
    "ज़ीरो": "0",
    "जीरो": "0",
    "वन": "1",
    "टू": "2",
    "थ्री": "3",
    "फोर": "4",
    "फाइव": "5",
    "सिक्स": "6",
    "सेवन": "7",
    "एट": "8",
    "नाइन": "9",
    # ---- Devanagari numerals, Hindi and Marathi. The Latin transliterations
    # of these are already above; these are the same words in their own script.
    "शून्य": "0",
    "एक": "1",
    "दो": "2",
    "दोन": "2",
    "तीन": "3",
    "चार": "4",
    "पाँच": "5",
    "पांच": "5",
    "पाच": "5",
    "छह": "6",
    "छे": "6",
    "सहा": "6",
    "सात": "7",
    "आठ": "8",
    "नौ": "9",
    "नऊ": "9",
}

_DIGIT_WORDS.update(_INDIC_DIGIT_WORDS)

#: Punctuation to shave off a token before matching it. The Devanagari danda
#: ends a sentence the way a full stop does, so "नौ।" has to reach the lookup
#: as "नौ" or the last digit of every Hindi number is lost.
_TOKEN_PUNCTUATION = ".,।॥"

# Words that repeat the digit after them.
_REPEATERS: dict[str, int] = {"double": 2, "triple": 3, "treble": 3}

# A token is part of a run if it is a digit word, a repeater, or already
# digits. `numerals` means most of a run arrives already converted, so a run is
# usually a mix.
_ALREADY_DIGITS = re.compile(r"^\d+$")

# Below this, a run is more likely an ordinary phrase than a number. "do" alone
# is the verb; "do do" is 22 and nobody says that by accident. Four is chosen
# because every number worth rescuing here — a phone number, an OTP, an order
# number — is longer, and the words that collide with ordinary English ("oh",
# "no", "do", "one", "o") stop colliding once four of them sit together.
MIN_RUN_TOKENS = 4

# Words that never start or end a run on their own but may sit inside one.
# "and" in "nine hundred and two" is not our problem — `numerals` handles
# cardinals — but it does appear between digit groups when a caller pauses.
_RUN_GLUE = {"and", "-", "dash"}


def _token_value(token: str) -> str | None:
    """The digits this token contributes, or None if it is not part of a run."""
    lowered = token.lower().strip(_TOKEN_PUNCTUATION)
    if _ALREADY_DIGITS.match(lowered):
        return lowered
    return _DIGIT_WORDS.get(lowered)


def _is_run_token(token: str) -> bool:
    lowered = token.lower().strip(_TOKEN_PUNCTUATION)
    return (
        _token_value(token) is not None or lowered in _REPEATERS or lowered in _RUN_GLUE
    )


def _collapse(tokens: list[str]) -> str | None:
    """Turn one run of tokens into a digit string, or None if it isn't one.

    Returns None rather than a partial result when a repeater has nothing to
    repeat ("double" at the end of a run): a caller was interrupted mid-number
    and inventing a digit would be worse than leaving the words alone.
    """
    out: list[str] = []
    pending_repeat = 0
    counted = 0

    for token in tokens:
        lowered = token.lower().strip(_TOKEN_PUNCTUATION)
        if lowered in _RUN_GLUE:
            continue
        if lowered in _REPEATERS:
            if pending_repeat:
                return None  # "double triple" is not a thing anyone says
            pending_repeat = _REPEATERS[lowered]
            continue
        value = _token_value(token)
        if value is None:
            return None
        counted += 1
        if pending_repeat:
            # "double five" repeats the digit; "double 55" is not meaningful,
            # so a repeater only applies to a single digit.
            if len(value) != 1:
                return None
            out.append(value * pending_repeat)
            pending_repeat = 0
        else:
            out.append(value)

    if pending_repeat:
        return None
    if counted < 2:
        return None
    return "".join(out)


def normalise_spoken_digits(text: str, min_run_tokens: int = MIN_RUN_TOKENS) -> str:
    """Rewrite runs of spoken digits into digit strings, leaving prose alone.

    Only runs of at least ``min_run_tokens`` number-ish tokens are touched, so
    an ordinary sentence containing "one" or "do" passes through unchanged.
    """
    if not text:
        return text

    tokens = text.split()
    result: list[str] = []
    index = 0

    while index < len(tokens):
        if not _is_run_token(tokens[index]):
            result.append(tokens[index])
            index += 1
            continue

        start = index
        while index < len(tokens) and _is_run_token(tokens[index]):
            index += 1
        run = tokens[start:index]

        # Trailing glue belongs to the sentence, not the number: "nine one two
        # and then..." ends at "two". Rewinding hands those tokens back to the
        # outer loop.
        #
        # `len(run) > 1` is load-bearing rather than defensive. Rewinding to
        # `start` leaves the outer loop looking at the same glue token that
        # began this run, which is still a run token, which starts the same run
        # again — a hang, on a live call, inside a processor whose try/except
        # cannot catch one. It took a stray "and" in ordinary speech to reach
        # it: "call me and let you know" never returned.
        while len(run) > 1 and run[-1].lower().strip(".,") in _RUN_GLUE:
            index -= 1
            run = run[:-1]

        # A run made only of glue is prose. "and", "dash" and "-" are run
        # tokens so they can sit *inside* a number; on their own they are
        # words, and emitting them here is also what guarantees the loop moves
        # forward.
        if all(token.lower().strip(".,") in _RUN_GLUE for token in run):
            result.extend(run)
            continue

        # Leading glue is the sentence too, and was being eaten: "call me on
        # and nine one two" came back as the number alone, a word short of what
        # was said. Trailing glue has always been handed back; there is no
        # reason the other end should behave differently.
        while run and run[0].lower().strip(".,") in _RUN_GLUE:
            result.append(run[0])
            run = run[1:]

        # Preserve whatever punctuation ended the final token.
        trailing = ""
        if run:
            last = run[-1]
            stripped = last.rstrip(".,")
            trailing = last[len(stripped) :]

        collapsed = _collapse(run) if len(run) >= min_run_tokens else None
        if collapsed is None:
            result.extend(run)
        else:
            result.append(collapsed + trailing)

    return " ".join(result)


# ---------------------------------------------------------------------------
# The other direction: how the agent reads a number back.
# ---------------------------------------------------------------------------
#
# TTS reads "9876543210" as a cardinal — "nine billion, eight hundred and
# seventy six million…" — which is useless to somebody writing it down, and is
# the most common complaint about an agent repeating a number.
#
# Spacing the digits fixes that, and doing it by length alone breaks something
# worse. "1200" is four digits whether it is an OTP or a price, and an agent
# that says "one two zero zero rupees" instead of "twelve hundred rupees" is a
# new bug traded for an old one. So this is deliberately narrow: a ten-digit
# number, which in India is a mobile number and nothing else, or a shorter one
# the sentence has already labelled as a code.

# Exactly ten digits. Not 4+, for the reason above.
_PHONE_DIGITS = re.compile(r"(?<!\d)\d{10}(?!\d)")

# 4-8 digits, spelled out only when the surrounding words say it is a
# reference rather than a quantity.
_SHORT_DIGITS = re.compile(r"(?<!\d)\d{4,8}(?!\d)")

# Words that, appearing anywhere in the sentence, mean a short number is
# something the listener has to write down.
_CODE_WORDS = (
    "otp",
    "code",
    "pin",
    "password",
    "reference",
    "ref",
    "order",
    "booking",
    "ticket",
    "invoice",
    "account",
    "policy",
    "complaint",
    "docket",
)

# Money, anywhere in the sentence, vetoes the whole thing. Reading a price out
# digit by digit is worse than reading a code as a cardinal.
_MONEY_WORDS = ("rupee", "rupees", "rs", "inr", "paise", "lakh", "crore")

# Not a word, so it is matched as a substring — it is punctuation that can sit
# flush against the amount.
_MONEY_SYMBOLS = ("₹",)

# Words are matched as words. As substrings, "rs" hit inside "first", "hours"
# and "yours", so any sentence containing one of those silently switched the
# read-back off — and a ten-digit mobile number went back to being read as
# "nine billion, eight hundred and seventy six million", which is the original
# complaint this module exists to answer. "pin" did the same inside "shipping",
# and "ref" inside "prefer".
_WORDS = re.compile(r"[a-z]+")


def _mentions(text: str, words: tuple[str, ...]) -> bool:
    return bool(set(_WORDS.findall(text.lower())) & set(words))


def as_spoken_digits(value: str) -> str:
    """Space a digit string so TTS reads it digit by digit."""
    return " ".join(value)


def spell_out_long_numbers(text: str) -> str:
    """Space out numbers the listener is expected to write down.

    Ten-digit numbers always. Four to eight digits only when the sentence
    names them as a code, and never when it mentions money.
    """
    if not text:
        return text

    lowered = text.lower()
    if _mentions(lowered, _MONEY_WORDS) or any(s in text for s in _MONEY_SYMBOLS):
        return text

    result = _PHONE_DIGITS.sub(lambda m: as_spoken_digits(m.group(0)), text)

    if _mentions(lowered, _CODE_WORDS):
        result = _SHORT_DIGITS.sub(lambda m: as_spoken_digits(m.group(0)), result)

    return result
