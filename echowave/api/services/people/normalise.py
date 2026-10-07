"""Numbers and addresses in one shape, so two spellings of one contact meet.

Phones become E.164 with India as the default country: ``98765 43210``,
``09876543210``, ``+91-98765-43210`` and ``919876543210`` are all
``+919876543210``. A number with its own ``+`` or ``00`` keeps its country.
Anything too short to be a phone number is dropped, not guessed at -- a
wrong number joined to a contact would put one person's calls on another's
page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT_COUNTRY_CODE = "91"
MAX_NAME = 200
MAX_TEXT = 200
_EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def phone(raw: object) -> str | None:
    """E.164, or None when it is not a usable number."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # "ext. 12", "x12": the extension is not part of the number.
    text = re.split(r"(?i)\s*(?:ext\.?|x)\s*\d+$", text)[0]
    plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if plus:
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif len(digits) == 10 and digits[0] in "6789":
        digits = DEFAULT_COUNTRY_CODE + digits
    elif len(digits) == 11 and digits.startswith("0"):
        digits = DEFAULT_COUNTRY_CODE + digits[1:]
    elif len(digits) == 12 and digits.startswith(DEFAULT_COUNTRY_CODE):
        pass
    elif len(digits) <= 10:
        # A landline without its area code, a short code: not dialable from
        # anywhere, so not joined to anybody.
        return None
    if not 8 <= len(digits) <= 15:
        return None
    return "+" + digits


def email(raw: object) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip().strip("<>").strip().lower()
    text = text.removeprefix("mailto:")
    if len(text) > 320 or not _EMAIL.match(text):
        return None
    return text


def text(raw: object, limit: int = MAX_TEXT) -> str | None:
    if raw is None:
        return None
    value = re.sub(r"[\x00-\x1f\x7f]", " ", str(raw))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit] or None


def name_key(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


@dataclass
class Incoming:
    """One contact as a source described it, before it is stored."""

    name: str | None = None
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    company: str | None = None
    relation: str | None = None
    external_id: str | None = None
    etag: str | None = None

    def clean(self) -> Incoming | None:
        """Normalised, de-duplicated; None when nothing identifies anyone."""
        phones = list(dict.fromkeys(p for p in map(phone, self.phones) if p))[:10]
        emails = list(dict.fromkeys(e for e in map(email, self.emails) if e))[:10]
        name = text(self.name, MAX_NAME)
        if not name:
            name = emails[0] if emails else (phones[0] if phones else None)
        if not name:
            return None
        return Incoming(
            name=name,
            phones=phones,
            emails=emails,
            company=text(self.company),
            relation=text(self.relation),
            external_id=text(self.external_id, 300),
            etag=text(self.etag, 300),
        )


def mask(value: str) -> str:
    """A number or address as a line may show it to someone else."""
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:1]}…@{domain}"
    return f"…{value[-4:]}" if len(value) > 4 else value
