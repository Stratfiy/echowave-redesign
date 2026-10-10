"""Take personal identifiers out of text before it is stored for training.

Phone numbers, email addresses, Aadhaar numbers and PAN numbers are replaced
by a marker (``[PHONE]``, ``[EMAIL]``, ``[AADHAAR]``, ``[PAN]``). The marker
keeps the sentence usable as training text -- "call me on [PHONE]" is still
a sentence about calling -- and says what was there.

This is pattern matching, and says so: it does not find names, addresses or
anything that is not one of the four shapes. It errs towards removing too
much (a long run of digits is taken for a phone number) because the cost of
a missed number is a customer's identifier inside a training set, and the
cost of an extra marker is a slightly worse example.

Order matters: an email first (its digits are not a phone), then the PAN,
then an Aadhaar written in groups, then phone shapes, then an unbroken
12-digit Aadhaar, then a last catch-all for any other long digit run. A
number is labelled by the first rule that takes it.
"""

from __future__ import annotations

import re
from typing import Any

#: Longest text kept in any one column, after redaction.
MAX_TEXT_CHARS = 8000

EMAIL = "[EMAIL]"
PHONE = "[PHONE]"
AADHAAR = "[AADHAAR]"
PAN = "[PAN]"

_EMAIL = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"
)
# Five letters, four digits, one letter. Case-insensitive: people type it
# in small letters too.
_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}[0-9]{4}[A-Za-z](?![A-Za-z0-9])")
# Aadhaar never starts with 0 or 1. Written 4-4-4 with spaces or hyphens.
_AADHAAR_GROUPED = re.compile(r"(?<!\d)[2-9]\d{3}[ \-]\d{4}[ \-]\d{4}(?!\d)")
_AADHAAR_PLAIN = re.compile(r"(?<!\d)[2-9]\d{11}(?!\d)")
_PHONE_PATTERNS = (
    # +<country code> followed by 7-13 more digits, in any grouping.
    re.compile(r"(?<!\w)\+\d{1,3}[ \-.()]*(?:\d[ \-.()]*){6,12}\d(?!\d)"),
    # An Indian mobile: optional 91 or 0, then six-to-nine and nine digits,
    # contiguous or split 5-5.
    re.compile(r"(?<!\d)(?:91[ \-]?|0)?[6-9]\d{4}[ \-]?\d{5}(?!\d)"),
    # A landline with its STD code: 0, two-to-four digits, six-to-eight more.
    re.compile(r"(?<!\d)0\d{2,4}[ \-]?\d{6,8}(?!\d)"),
)
# Anything else that is ten or more digits, however it is spaced.
_LONG_DIGITS = re.compile(r"(?<!\d)(?:\d[ \-]?){9,14}\d(?!\d)")


def redact(text: Any) -> str:
    """``text`` with the four kinds of identifier replaced, and cut to
    ``MAX_TEXT_CHARS``. Anything that is not a string is read as its text."""
    if text is None:
        return ""
    out = text if isinstance(text, str) else str(text)
    out = _EMAIL.sub(EMAIL, out)
    out = _PAN.sub(PAN, out)
    out = _AADHAAR_GROUPED.sub(AADHAAR, out)
    for pattern in _PHONE_PATTERNS:
        out = pattern.sub(PHONE, out)
    out = _AADHAAR_PLAIN.sub(AADHAAR, out)
    out = _LONG_DIGITS.sub(PHONE, out)
    return out[:MAX_TEXT_CHARS]


def redact_value(value: Any) -> Any:
    """``redact`` applied to every string inside a list or dict."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    return value
