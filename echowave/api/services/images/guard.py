"""Never invent a fact on a poster.

A poster is the business speaking in public. A price, an offer, a date, a
phone number, an address or a claim ("No. 1 in Chennai") the business did
not give is a promise somebody else made in their name -- the incident this
exists after was test data that looked invented. So the rule is enforced
here, not only asked for in a prompt:

* **The tool takes a brief, not a prompt.** The model fills structured
  fields -- the business name, the headline, the other lines of text, the
  language, the format, the look -- and the prompt the provider sees is
  composed by ``compose`` from them. Every word on the image comes from the
  text fields, and the prompt tells the provider to put nothing else there.
* **Every fact in the text must be in what the person said.** Each number
  (prices, percentages, dates, times, phone numbers, PIN codes), web
  address, email address and handle in the text fields, and each claim word
  ("best", "No. 1", "guaranteed"), is looked for in the person's own words
  (and, for an edit, in the brief the person already approved). One that is
  not there turns the request back with what to ask for -- before any
  provider is called, so nothing is spent on a poster that would lie.
* **The look carries no text.** "Use our blue, festive, diyas" is a look;
  "with 50% OFF in gold" is text smuggled past the check, and is refused
  with a pointer to the text fields.

Words themselves are not checked against the person's words: a headline is
the model's to write ("Diwali Sale" from "make me a Diwali sale poster"),
and a Tamil version is a translation. Facts are what cannot be made up.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

#: Claims a business must have made itself before a poster makes them.
CLAIM_WORDS = (
    "best",
    "no.1",
    "no. 1",
    "no 1",
    "#1",
    "number one",
    "number 1",
    "cheapest",
    "lowest price",
    "lowest prices",
    "guaranteed",
    "guarantee",
    "100%",
    "certified",
    "award",
    "award-winning",
    "official",
    "free",
    "limited stock",
    "only today",
    "last day",
    "trusted by",
)

_URL = re.compile(
    r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|in|co|net|org|shop|store)\b",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9_.]{2,}")
_IMAGE_ID = re.compile(r"img_[0-9a-f]{32}")
_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_QUOTES = re.compile(r"[\"“”‘’]|'[^']{2,}'")
_CURRENCY = re.compile(r"[₹$€£]|\brs\.?\s*\d|\binr\b", re.IGNORECASE)


def _ascii_digits(text: str) -> str:
    """Devanagari, Tamil, Bengali... digits read as 0-9, so a Hindi poster's
    ५०% is the 50% the person said."""
    out = []
    for char in text:
        if char.isdigit() and not ("0" <= char <= "9"):
            try:
                out.append(str(unicodedata.digit(char)))
                continue
            except (TypeError, ValueError):
                pass
        out.append(char)
    return "".join(out)


def _joined(text: str) -> str:
    """Digits split by a space, hyphen, dot or comma read as one number, so
    "98765 43210" and "1,499" match "9876543210" and "1499"."""
    return re.sub(r"(?<=\d)[\s\-.,]+(?=\d)", "", text)


def _numbers(text: str) -> set[str]:
    text = _ascii_digits(text)
    return set(re.findall(r"\d+", text)) | set(re.findall(r"\d+", _joined(text)))


def _strip_leading_zeros(groups: set[str]) -> set[str]:
    return {g.lstrip("0") or "0" for g in groups}


@dataclass
class Brief:
    business_name: str
    headline: str = ""
    lines: list[str] = field(default_factory=list)
    #: Empty means English; kept empty so an edit can tell "not said" apart.
    language: str = ""
    format: str = ""
    look: str = ""

    def texts(self) -> list[str]:
        return [t for t in [self.business_name, self.headline, *self.lines] if t]

    def as_dict(self) -> dict:
        return {
            "business_name": self.business_name,
            "headline": self.headline,
            "lines": list(self.lines),
            "language": self.language,
            "format": self.format,
            "look": self.look,
        }


def _clean(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def brief_from(arguments: dict) -> Brief:
    lines = arguments.get("lines") or []
    if isinstance(lines, str):
        lines = [lines]
    return Brief(
        business_name=_clean(arguments.get("business_name"), 120),
        headline=_clean(arguments.get("headline"), 160),
        lines=[_clean(line, 200) for line in lines if _clean(line, 200)][:6],
        language=_clean(arguments.get("language"), 40),
        format=_clean(arguments.get("format"), 40),
        look=_clean(arguments.get("look"), 400),
    )


def merged(base: Brief, new: Brief) -> Brief:
    """An edit's brief: what it says, and the approved brief for the rest."""
    return Brief(
        business_name=new.business_name or base.business_name,
        headline=new.headline or base.headline,
        lines=list(new.lines) or list(base.lines),
        language=new.language or base.language,
        format=new.format or base.format,
        look=new.look or base.look,
    )


def _fact_problems(text: str, said: str, said_numbers: set[str]) -> list[str]:
    problems: list[str] = []
    said_folded = said.casefold()
    plain = set(re.findall(r"\d+", _ascii_digits(text)))
    joined = set(re.findall(r"\d+", _joined(_ascii_digits(text))))
    known = _strip_leading_zeros(said_numbers)
    if plain and not (
        _strip_leading_zeros(plain) <= known or _strip_leading_zeros(joined) <= known
    ):
        missing = sorted(_strip_leading_zeros(plain) - known)
        problems.append(
            f"“{text}” has {', '.join(missing)}, which the person did not give "
            "(a price, date, time, phone number or other figure)"
        )
    for pattern, what in (
        (_URL, "web address"),
        (_EMAIL, "email"),
        (_HANDLE, "handle"),
    ):
        for match in pattern.findall(text):
            if match.casefold().rstrip(".,") not in said_folded:
                problems.append(f"“{match}” is a {what} the person did not give")
    folded = f" {text.casefold()} "
    for claim in CLAIM_WORDS:
        if re.search(rf"(?<![\w]){re.escape(claim)}(?![\w])", folded) and not re.search(
            rf"(?<![\w]){re.escape(claim)}(?![\w])", said_folded
        ):
            problems.append(f"“{claim}” is a claim the person did not make")
    return problems


def check(brief: Brief, *, said: str, edit_instruction: str = "") -> list[str]:
    """What is wrong with this brief, as lines to ask the person about.
    Empty means it may be drawn."""
    problems: list[str] = []
    # An image id's hex is not something the person said.
    said = _IMAGE_ID.sub(" ", said or "")
    if not brief.business_name:
        problems.append("the business or brand name is missing -- ask for it")
    if not brief.headline and not brief.lines:
        problems.append(
            "there is no text for the image -- ask what it should say "
            "(the offer or the message)"
        )
    said_numbers = _numbers(said)
    for text in brief.texts():
        problems.extend(_fact_problems(text, said, said_numbers))
    if edit_instruction:
        problems.extend(_fact_problems(edit_instruction, said, said_numbers))
    look = _HEX.sub("", brief.look)
    if look and (
        re.search(r"\d", _ascii_digits(look))
        or _QUOTES.search(look)
        or _CURRENCY.search(look)
        or _URL.search(look)
        or _EMAIL.search(look)
    ):
        problems.append(
            "the look has text, numbers or quotes in it; put words that go on "
            "the image in headline or lines, and keep the look to colours, "
            "style and imagery"
        )
    # One line per fact, in the order found.
    seen: list[str] = []
    for problem in problems:
        if problem not in seen:
            seen.append(problem)
    return seen


#: Said to every provider: nothing on the image but the given text.
NOTHING_ELSE = (
    "Put no other words, letters, numbers, prices, discounts, dates, times, "
    "phone numbers, addresses, web addresses, logos or claims anywhere on "
    "the image. Spell every word exactly as given."
)

#: Nova Canvas takes what to avoid apart, and reads negations badly in the
#: prompt itself.
AVOID = (
    "extra text, misspelled words, gibberish lettering, watermark, invented "
    "prices, invented phone numbers, invented dates, invented addresses"
)


def compose(
    brief: Brief,
    *,
    format_label: str,
    aspect: str,
    references: int = 0,
    edit_instruction: str = "",
) -> str:
    """The prompt the provider is sent, from the brief alone."""
    text_lines = [f"- Business name: {brief.business_name}"]
    if brief.headline:
        text_lines.append(f"- Headline (largest text): {brief.headline}")
    for line in brief.lines:
        text_lines.append(f"- {line}")
    parts: list[str] = []
    if edit_instruction:
        parts.append(
            "Edit the attached poster. Change only this: "
            f"{edit_instruction}. Keep everything else as it is."
        )
    parts.append(
        f"Design a polished, print-ready {format_label} (aspect {aspect}) for "
        f"{brief.business_name}, a business in India."
    )
    parts.append(
        f"The image carries exactly this text, in {brief.language or 'English'}, and "
        "nothing more:\n" + "\n".join(text_lines)
    )
    parts.append(NOTHING_ELSE)
    if brief.look:
        parts.append(f"Look and feel: {brief.look}.")
    else:
        parts.append(
            "Look and feel: clean, bold and easy to read on a phone, with a "
            "clear hierarchy and generous margins."
        )
    if references:
        which = "other attached image" if edit_instruction else "attached image"
        parts.append(
            f"Use the {which}{'s' if references > 1 else ''} as the business's "
            "own logo or product photo: place it faithfully, without "
            "redrawing or changing any text in it."
        )
    return "\n\n".join(parts)
