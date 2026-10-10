"""Reading an explicit preference out of a person's line, with no model.

"Tamil for calls, English for email", "weekly numbers, not daily", "call me
after 10". The plan (section 7, Personal adaptation) says an explicit
preference is saved within the person's scope straight away, and that the
model is not where it is kept; this is the half that hears it. Deterministic
on purpose: what is saved is exactly what the person said, the card shows it,
and a line that does not read as one of these shapes saves nothing.

**Each clause must be the whole preference.** A line is split into clauses at
commas, semicolons, full stops and "and"/"but", and a clause is read only if
a pattern matches all of it (a few polite words aside). "Call me after 10
about the GST notice" is a one-off ask, not a standing preference, and is
left alone; so is anything that ends in a question mark.

Kinds, and what code does with each (services/personal/preferences.py):

* ``language`` per topic -- calls, email, chat, reminders.
* ``call_window`` -- "call me after 10", "don't call before 10:30", "call me
  between 10 and 6". Only ever narrows the calling window; one that would
  open before or close after the platform's calling hours is refused here
  and said, because a learned preference never widens what code allows.
* ``channel`` for reminders -- in the app, WhatsApp, or a phone notification.
* ``cadence`` for reports -- daily, weekly, monthly.
* ``length`` for chat -- short or detailed replies.
* ``note`` -- "remember that I ...", "from now on ...": the person's own
  words, quoted to the model as a preference that grants nothing.

There is no kind for a recipient, a permission, an amount or a limit, and no
pattern produces one: "you can send email without asking" is at most a note,
and a note is never read by code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time

from api import constants
from api.services import member_preferences
from api.services.shell import languages as shell_languages

LANGUAGE = "language"
CALL_WINDOW = "call_window"
CHANNEL = "channel"
CADENCE = "cadence"
LENGTH = "length"
NOTE = "note"
KINDS = (LANGUAGE, CALL_WINDOW, CHANNEL, CADENCE, LENGTH, NOTE)

CALLS = "calls"
EMAIL = "email"
CHAT = "chat"
REMINDERS = "reminders"
REPORTS = "reports"
GENERAL = "general"
TOPICS = (CALLS, EMAIL, CHAT, REMINDERS, REPORTS, GENERAL)

#: The words for each topic. A blocklist would be wrong here: an unknown
#: topic word means the clause is not a preference we can apply, and it is
#: left for the model to answer rather than guessed into one we can.
_TOPIC_WORDS = {
    CALLS: ("call", "calls", "phone calls", "phone", "voice calls", "calling"),
    EMAIL: ("email", "emails", "e-mail", "e-mails", "mail", "mails"),
    CHAT: ("chat", "chats", "replies", "messages", "texts", "here", "this chat"),
    REMINDERS: ("reminder", "reminders"),
}
TOPIC_LABEL = {
    CALLS: "Calls",
    EMAIL: "Email",
    CHAT: "Replies",
    REMINDERS: "Reminders",
    REPORTS: "Numbers",
    GENERAL: "",
}

CHANNEL_WORDS = {
    "whatsapp": "whatsapp",
    "whats app": "whatsapp",
    "the app": "in_app",
    "app": "in_app",
    "in the app": "in_app",
    "in app": "in_app",
    "in-app": "in_app",
    "notification": "push",
    "notifications": "push",
    "push": "push",
    "a notification": "push",
    "phone notification": "push",
    "a phone notification": "push",
}
CHANNEL_LABEL = {
    "whatsapp": "on WhatsApp",
    "in_app": "in the app",
    "push": "as a phone notification",
}
CADENCES = ("daily", "weekly", "monthly")
LENGTHS = ("short", "detailed")

MAX_NOTE = 280


@dataclass(frozen=True)
class Candidate:
    """One preference read from a clause: what code stores and applies."""

    kind: str
    topic: str
    value: str
    label: str
    #: The clause it was read from, for the card's "From".
    said: str = ""


@dataclass
class Reading:
    """What a line held: preferences to save, and ones refused with why."""

    found: list[Candidate] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)


def _languages() -> dict[str, tuple[str, str]]:
    """Lower-case language name (English and native) -> (tag, English name)."""
    out: dict[str, tuple[str, str]] = {}
    for language in shell_languages.LANGUAGES:
        tag = language.code
        if tag not in member_preferences.LANGUAGES:
            tag = f"{language.code}-IN"
        if tag not in member_preferences.LANGUAGES:
            continue
        out[language.english.lower()] = (tag, language.english)
        out[language.native.lower()] = (tag, language.english)
    # Common ways of writing two of them.
    if "tamil" in out:
        out.setdefault("thamizh", out["tamil"])
        out.setdefault("tamizh", out["tamil"])
    if "bengali" in out:
        out.setdefault("bangla", out["bengali"])
    return out


def language_name(tag: str | None) -> str | None:
    for known, english in _languages().values():
        if known == tag:
            return english
    return None


_POLITE = re.compile(
    r"^(?:(?:please|pls|and|also|actually|ok|okay|so|oh|no|hey|decibyl)[\s,:]+)+",
    re.IGNORECASE,
)
_PREFIX = re.compile(
    r"^(?:i(?:'d| would)? (?:prefer|like|want)(?: to (?:use|have|get))?|"
    r"(?:always |from now on,? )?(?:use|speak|talk|write)(?: to me)?|"
    r"prefer|always|from now on,?|going forward,?|in future,?)\s+",
    re.IGNORECASE,
)
_SUFFIX = re.compile(
    r"\s+(?:please|pls|thanks|thank you|from now on|going forward|"
    r"in future|always|for me|to me|with me)$",
    re.IGNORECASE,
)


def _clauses(text: str) -> list[str]:
    # "between 10 and 6" is one clause, not two.
    text = re.sub(
        r"(between\s+\d{1,2}(?:[:.]\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\s+and\s+",
        r"\1 to ",
        text,
        flags=re.IGNORECASE,
    )
    parts = re.split(r"[,;\n]+|(?<!\d)\.(?!\d)|\s+(?:and|but)\s+", text)
    return [p.strip(" \t-–—!") for p in parts if p and p.strip(" \t-–—!")]


def _tidy(clause: str) -> str:
    clause = clause.strip().rstrip("!").strip()
    previous = None
    while previous != clause:
        previous = clause
        clause = _POLITE.sub("", clause).strip()
        clause = _PREFIX.sub("", clause).strip()
        clause = _SUFFIX.sub("", clause).strip()
    return clause


def _topic(word: str) -> str | None:
    word = word.strip().lower()
    for topic, words in _TOPIC_WORDS.items():
        if word in words:
            return topic
    return None


def _hour(raw: str, meridiem: str | None) -> time | None:
    """A clock time as a person says it for a call. "10" is 10:00 and "6"
    is 18:00: without am/pm, the hour inside the calling day is meant."""
    match = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?", raw.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if minute > 59:
        return None
    meridiem = (meridiem or "").lower().replace(".", "")
    if meridiem in ("pm", "in the evening", "evening", "at night", "night"):
        if hour < 12:
            hour += 12
    elif meridiem in ("am", "in the morning", "morning"):
        if hour == 12:
            hour = 0
    elif 1 <= hour <= 7:
        hour += 12
    if hour > 23:
        return None
    return time(hour, minute)


def _clock(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _window() -> tuple[time, time]:
    """The platform's calling hours, read the way the dialler reads them."""
    from api.services.compliance import dnd

    return (
        dnd._parse_hhmm(constants.CALLING_HOURS_START, time(9, 0)),
        dnd._parse_hhmm(constants.CALLING_HOURS_END, time(21, 0)),
    )


_TIME = r"(\d{1,2}(?:[:.]\d{2})?)\s*(a\.?m\.?|p\.?m\.?|in the morning|in the evening|at night)?"


def _call_window(clause: str) -> Candidate | str | None:
    low = clause.lower()
    start = end = None
    match = re.fullmatch(
        rf"(?:only\s+)?(?:call|ring|phone)(?: me)?(?: only)?\s+(?:after|from)\s+{_TIME}",
        low,
    ) or re.fullmatch(
        rf"(?:don'?t|do not|never|no)\s+(?:call|ring|phone|calls)(?: me)?\s+before\s+{_TIME}",
        low,
    )
    if match:
        start = _hour(match.group(1), match.group(2))
    else:
        match = re.fullmatch(
            rf"(?:only\s+)?(?:call|ring|phone)(?: me)?(?: only)?\s+between\s+{_TIME}\s+(?:and|to|-)\s+{_TIME}",
            low,
        )
        if match:
            start = _hour(match.group(1), match.group(2))
            end = _hour(match.group(3), match.group(4))
        else:
            match = re.fullmatch(
                rf"(?:don'?t|do not|never|no)\s+(?:call|ring|phone|calls)(?: me)?\s+after\s+{_TIME}",
                low,
            )
            if not match:
                return None
            end = _hour(match.group(1), match.group(2))
    if start is None and end is None:
        return None
    opens, closes = _window()
    if start is not None and not (opens <= start < closes):
        return (
            f"Calls can only ring between {_clock(opens)} and {_clock(closes)}, "
            f"so a call from {_clock(start)} was not saved."
        )
    if end is not None and not (opens < end <= closes):
        return (
            f"Calls can only ring between {_clock(opens)} and {_clock(closes)}, "
            f"so a call until {_clock(end)} was not saved."
        )
    if start is not None and end is not None and end <= start:
        return None
    value = f"{_clock(start) if start else ''}-{_clock(end) if end else ''}"
    if start and end:
        label = f"Calls between {_clock(start)} and {_clock(end)}"
    elif start:
        label = f"Calls after {_clock(start)}"
    else:
        label = f"No calls after {_clock(end)}"
    return Candidate(CALL_WINDOW, CALLS, value, label, clause)


def _language(clause: str) -> Candidate | None:
    low = clause.lower()
    names = _languages()
    alternation = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    topics = "|".join(
        re.escape(w)
        for w in sorted(
            {w for ws in _TOPIC_WORDS.values() for w in ws}, key=len, reverse=True
        )
    )
    match = re.fullmatch(
        rf"(?:in\s+)?({alternation})\s+(?:for|on|in)\s+(?:my\s+|the\s+)?({topics})", low
    )
    if match:
        name, word = match.group(1), match.group(2)
    else:
        match = re.fullmatch(
            rf"(?:my\s+|the\s+)?({topics})\s+(?:in|should be in|to be in)\s+({alternation})",
            low,
        ) or re.fullmatch(rf"(?:call|ring|phone)(?: me)?\s+in\s+({alternation})()", low)
        if not match:
            return None
        if match.group(2) == "":
            name, word = match.group(1), "calls"
        else:
            word, name = match.group(1), match.group(2)
    topic = _topic(word)
    if topic is None or name not in names:
        return None
    tag, english = names[name]
    return Candidate(LANGUAGE, topic, tag, f"{TOPIC_LABEL[topic]} in {english}", clause)


def _channel(clause: str) -> Candidate | None:
    low = clause.lower()
    words = "|".join(re.escape(w) for w in sorted(CHANNEL_WORDS, key=len, reverse=True))
    match = re.fullmatch(
        rf"(?:remind me|send (?:me )?(?:my )?reminders|reminders)\s+(?:on|by|via|over|as|in|through)\s+({words})",
        low,
    ) or re.fullmatch(rf"({words})\s+for\s+(?:my\s+)?reminders", low)
    if not match:
        return None
    channel = CHANNEL_WORDS[match.group(1)]
    return Candidate(
        CHANNEL, REMINDERS, channel, f"Reminders {CHANNEL_LABEL[channel]}", clause
    )


def _cadence(clause: str) -> Candidate | None:
    low = clause.lower()
    things = r"(?:numbers|reports?|summary|summaries|updates?|figures|stats)"
    cad = "|".join(CADENCES)
    match = (
        re.fullmatch(rf"({cad})\s+(?:the\s+)?{things}(?:\s+only)?", low)
        or re.fullmatch(
            rf"(?:send |give |show )?(?:me )?(?:the |my )?{things}\s+({cad})(?:\s+only)?",
            low,
        )
        or re.fullmatch(rf"{things}\s+(?:once\s+a\s+|every\s+)(day|week|month)", low)
    )
    if not match:
        return None
    word = match.group(1)
    word = {"day": "daily", "week": "weekly", "month": "monthly"}.get(word, word)
    return Candidate(CADENCE, REPORTS, word, f"Numbers {word}", clause)


def _length(clause: str) -> Candidate | None:
    low = clause.lower()
    match = re.fullmatch(
        r"(?:keep\s+)?(?:your\s+|the\s+)?(?:replies|answers|responses|it)\s+(short|shorter|brief|detailed|longer|in detail)",
        low,
    ) or re.fullmatch(
        r"(short|shorter|brief|detailed|longer)\s+(?:replies|answers|responses)", low
    )
    if not match:
        return None
    value = "short" if match.group(1) in ("short", "shorter", "brief") else "detailed"
    return Candidate(
        LENGTH,
        CHAT,
        value,
        "Short replies" if value == "short" else "Detailed replies",
        clause,
    )


_NOTE = re.compile(
    r"^(?:please\s+)?(?:remember|note|keep in mind)\s+(?:that\s+)?(i\s+.+)$",
    re.IGNORECASE,
)


def _note(clause: str) -> Candidate | None:
    match = _NOTE.match(clause.strip())
    if not match:
        return None
    words = match.group(1).strip().rstrip(".")
    if len(words) < 6 or len(words) > MAX_NOTE:
        return None
    # Said back in the second person on the card: "You ..."
    label = "You" + words[1:] if words.lower().startswith("i ") else words
    return Candidate(NOTE, GENERAL, words, label[:200], clause)


_READERS = (_call_window, _language, _channel, _cadence, _length)


def read(text: str) -> Reading:
    """Every explicit preference in ``text``. Nothing for a question."""
    reading = Reading()
    text = (text or "").strip()
    if not text or text.endswith("?") or len(text) > 600:
        return reading
    # A note is a whole sentence ("remember that I take calls after lunch,
    # not before"): read before the line is split at its commas.
    note = _note(text)
    if note is not None:
        reading.found.append(note)
        return reading
    seen: set[tuple[str, str]] = set()
    for raw in _clauses(text):
        clause = _tidy(raw)
        if not clause:
            continue
        for reader in _READERS:
            got = reader(clause)
            if got is None:
                continue
            if isinstance(got, str):
                reading.refused.append(got)
                break
            key = (got.kind, got.topic)
            if key in seen:
                break
            seen.add(key)
            reading.found.append(
                Candidate(got.kind, got.topic, got.value, got.label, raw.strip())
            )
            break
    return reading


def read_for(kind: str, topic: str, text: str) -> Candidate | str | None:
    """A correction of one preference: the whole line read as before, or a
    bare value ("Hindi", "11", "WhatsApp", "weekly") read as that kind."""
    reading = read(text)
    for found in reading.found:
        if found.kind == kind and (found.topic == topic or kind == NOTE):
            return found
    if reading.refused:
        return reading.refused[0]
    bare = _tidy(text).lower()
    if kind == NOTE:
        words = (text or "").strip()
        if 2 <= len(words) <= MAX_NOTE:
            return Candidate(NOTE, GENERAL, words, words[:200], words)
        return None
    if kind == LANGUAGE:
        names = _languages()
        if bare in names:
            tag, english = names[bare]
            return Candidate(
                LANGUAGE, topic, tag, f"{TOPIC_LABEL[topic]} in {english}", text
            )
        return None
    if kind == CALL_WINDOW:
        got = _call_window(f"call me after {bare}")
        return got
    if kind == CHANNEL and bare in CHANNEL_WORDS:
        channel = CHANNEL_WORDS[bare]
        return Candidate(
            CHANNEL, REMINDERS, channel, f"Reminders {CHANNEL_LABEL[channel]}", text
        )
    if kind == CADENCE and bare in CADENCES:
        return Candidate(CADENCE, REPORTS, bare, f"Numbers {bare}", text)
    if kind == LENGTH and bare in ("short", "shorter", "brief", "detailed", "longer"):
        value = "short" if bare in ("short", "shorter", "brief") else "detailed"
        return Candidate(
            LENGTH,
            CHAT,
            value,
            "Short replies" if value == "short" else "Detailed replies",
            text,
        )
    return None


# --- "what do you know about me" ---------------------------------------------

_ABOUT_ME = re.compile(
    r"^(?:hey\s+|so\s+|ok\s+|okay\s+)?(?:decibyl[,\s]+)?(?:please\s+)?"
    r"(?:what\s+(?:do|did)\s+you\s+(?:know|remember|keep|have)|"
    r"what\s+have\s+you\s+(?:learn(?:ed|t)|saved|kept|remembered)|"
    r"what\s+you\s+know|show\s+(?:me\s+)?what\s+you\s+(?:know|remember))"
    r"\s+(?:about|of|on)\s+me[\s?.!]*$",
    re.IGNORECASE,
)


def asks_about_me(text: str) -> bool:
    """Whether the whole line is "what do you know about me"."""
    return bool(_ABOUT_ME.match((text or "").strip()))
