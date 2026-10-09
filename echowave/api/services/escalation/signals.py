"""What the caller said, read deterministically.

The model is good at noticing that a caller is upset and bad at being the
only thing between "my father has chest pain" and a person. So the
dependable signals are read here, from the caller's own words, with no model
in the loop: an explicit request for a person, and the policy topics.

The model still contributes -- it can report what it judged through the
``report_escalation_signal`` tool (see ``runtime``) -- but what happens next
is decided by ``evaluator`` from these readings, in code.

**Strong and weak requests.** "Let me talk to a human" is a request; act on
it at once. "Are you a robot?" or "my manager said to call" mentions a person
without asking for one; a single mention is not a request, but a caller who
mentions one twice is insisting, and insisting is a request. That is the line
between honouring a caller and over-escalating an ambiguous sentence.

English, Hindi and the Hinglish callers actually use. A phrase list will
always be incomplete; the model's signal covers what it misses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional

RequestStrength = Optional[Literal["strong", "weak"]]

_PUNCT = re.compile(r"[^\w\s₹\u0900-\u097F]", re.UNICODE)


def normalise(text: str) -> str:
    # Apostrophes go first, so "can't" and "cant" read the same.
    text = (text or "").replace("'", "").replace("\u2019", "")
    return " ".join(_PUNCT.sub(" ", text.lower()).split())


_PERSON = (
    r"(human|human being|real person|person|someone|somebody|live agent|agent|"
    r"representative|rep|executive|manager|supervisor|staff|operator|"
    r"customer care|customer service|team member|real human)"
)
#: Only the nouns that cannot mean anything but "not you".
_PERSON_STRICT = (
    r"(human|human being|real person|live agent|representative|manager|"
    r"supervisor|operator|real human|customer care)"
)
_TALK = r"(talk|speak|connect|transfer|put me through)"

_STRONG = [
    re.compile(
        rf"\b{_TALK}\b(\s+\w+){{0,3}}?\s+(to|with|a|an|the|your|some|real|me)\s+"
        rf"((a|an|the|your|some|real)\s+)?{_PERSON}\b"
    ),
    re.compile(
        rf"\b(i want|i need|can i have|let me have|get me|give me|find me)\s+"
        rf"((a|an|the|your|some)\s+)?{_PERSON_STRICT}\b"
    ),
    re.compile(r"\b(transfer|connect) me\b"),
    re.compile(r"\bput me through\b"),
    re.compile(
        r"\b(dont|do not) want (to (talk|speak) (to|with) )?(a |an |the |this )?"
        r"(bot|robot|machine|ai|computer|recording)\b"
    ),
    # Hinglish, romanised.
    re.compile(
        r"\b(insaan|insan|aadmi|admi|banda|vyakti|manager|executive|agent|"
        r"kisi|koi)\b(\s+\w+){0,3}\s+(se|ko)\s+(baat|bat)\s+"
        r"(karni|karna|karao|karwao|karwa|kara|karaiye|karwaiye|krao|krni)\b"
    ),
    re.compile(r"\b(kisi|koi)\s+(insaan|aadmi|banda)\s+(se|ko)\b"),
    # Devanagari.
    re.compile(r"(इंसान|आदमी|किसी|मैनेजर|एजेंट)\s*(\S+\s+){0,2}(से|को)\s*बात"),
]

_NEGATED = re.compile(
    rf"\b(dont|do not|no need to|not|never)\b(\s+\w+){{0,2}}\s+"
    rf"(talk|speak|transfer|connect)\b(\s+\w+){{0,3}}\s+{_PERSON}\b"
)

_WEAK = [
    re.compile(
        r"\b(human|real person|representative|manager|supervisor|customer care|"
        r"executive|operator|live agent|insaan|aadmi)\b"
    ),
    re.compile(r"\bare you (a |an )?(bot|robot|machine|ai|real person|human)\b"),
    re.compile(r"(इंसान|मैनेजर)"),
]


def human_request(text: str) -> RequestStrength:
    """``strong`` for a request, ``weak`` for a mention, None for neither."""
    said = normalise(text)
    if not said:
        return None
    if _NEGATED.search(said):
        return None
    for pattern in _STRONG:
        if pattern.search(said):
            return "strong"
    for pattern in _WEAK:
        if pattern.search(said):
            return "weak"
    return None


_TOPIC_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "emergency": [
        re.compile(
            r"\b(chest pain|heart attack|cant breathe|cannot breathe|not breathing|"
            r"unconscious|fainted|collapsed|bleeding|stroke|seizure|overdose|"
            r"suicide|kill myself|end my life|ambulance|emergency|on fire|"
            r"accident|saans nahi|behosh|khoon)\b"
        ),
        re.compile(r"(सीने में दर्द|सांस नहीं|बेहोश|खून|एम्बुलेंस|आत्महत्या)"),
    ],
    "fraud": [
        re.compile(
            r"\b(fraud|fraudulent|scam|scammed|scammer|phishing|hacked|"
            r"unauthori[sz]ed (transaction|payment|debit|charge)|"
            r"stole my|stolen (card|phone|money)|someone used my|"
            r"(shared|gave) (my |the )?otp|deducted without|dhokha|dhokhadhadi|thagi)\b"
        ),
        re.compile(r"(धोखा|ठगी|फ्रॉड)"),
    ],
    "legal_threat": [
        re.compile(
            r"\b(lawyer|advocate|attorney|legal action|legal notice|sue you|sue your|"
            r"take you to court|court|consumer forum|consumer court|police complaint|"
            r"file an fir|fir|case karunga|case kar dunga|notice bhejunga)\b"
        ),
        re.compile(r"(वकील|कोर्ट|कानूनी|पुलिस में शिकायत)"),
    ],
    "regulated_advice": [
        re.compile(
            r"\b(which (stock|share|shares|mutual fund|fund|policy) should|"
            r"should i (invest|buy (shares|stock|a policy))|investment advice|"
            r"tax advice|insurance advice|medical advice|diagnose|diagnosis|"
            r"prescribe|prescription for|what dose|dosage|how many tablets)\b"
        ),
    ],
}

_REFUND = re.compile(
    r"\b(refund|money back|reimburse|reimbursement|paisa wapas|paise wapas|return my money)\b"
    r"|(पैसे वापस|रिफंड)"
)
_AMOUNTS = [
    re.compile(
        r"(?:rs|inr|₹|rupees?)\s*\.?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakh|lakhs|hazar)?"
    ),
    re.compile(
        r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakh|lakhs|hazar)?\s*(?:rs|inr|₹|rupees?|rupaye)\b"
    ),
]
_MULTIPLIER = {
    "k": 1000,
    "thousand": 1000,
    "hazar": 1000,
    "lakh": 100000,
    "lakhs": 100000,
}


def amounts(text: str) -> list[int]:
    """Rupee amounts written in a sentence ("₹5,000", "12k rupees")."""
    said = (text or "").lower()
    found: list[int] = []
    for pattern in _AMOUNTS:
        for match in pattern.finditer(said):
            raw, unit = match.group(1), match.group(2)
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue
            found.append(int(value * _MULTIPLIER.get(unit or "", 1)))
    return found


@dataclass
class Reading:
    """What one utterance carried."""

    request: RequestStrength = None
    topics: list[str] = field(default_factory=list)
    #: The custom phrase that matched, when one did.
    custom: str | None = None
    refund_amount: int | None = None


def read(
    text: str,
    *,
    custom_topics: Iterable[str] = (),
    refund_limit: int | None = None,
) -> Reading:
    said = normalise(text)
    reading = Reading(request=human_request(text))
    if not said:
        return reading
    for topic, patterns in _TOPIC_PATTERNS.items():
        if any(p.search(said) for p in patterns):
            reading.topics.append(topic)
    if _REFUND.search(said):
        figures = amounts(text)
        if figures:
            reading.refund_amount = max(figures)
            if refund_limit is not None and reading.refund_amount > refund_limit:
                reading.topics.append("refund_over_limit")
    for phrase in custom_topics:
        wanted = normalise(phrase)
        if wanted and f" {wanted} " in f" {said} ":
            reading.custom = phrase
            break
    return reading


__all__ = ["Reading", "amounts", "human_request", "normalise", "read"]
