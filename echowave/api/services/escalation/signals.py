"""What the caller said, read deterministically.

The model is good at noticing that a caller is upset and bad at being the
only thing between "my father has chest pain" and a person. So the
dependable signals are read here, from the caller's own words, with no model
in the loop: an explicit request for a person, and the policy topics.

The model still contributes -- it can report what it judged through the
``report_escalation_signal`` tool (see ``runtime``) -- but what happens next
is decided by ``evaluator`` from these readings, in code.

**Strong and weak requests.** "Let me talk to a human" is a request; act on
it at once. "Are you a robot?" mentions a person without asking for one; a
single mention is not a request, but a caller who mentions one twice is
insisting, and insisting is a request. That is the line between honouring a
caller and over-escalating an ambiguous sentence.

**A request is asked of us.** "I'll speak to someone at home and call you
back", "I need to check with my manager" and "transfer me the refund" name a
person or a transfer verb without asking this business for one, so a strong
request needs the shape of a request ("can I / let me / I want to ... speak
to", "connect me to ...", "transfer me" on its own), a person who is not the
caller's own ("my manager", "apne manager"), and not someone at home.

**An accusation is not a report.** "Aap log fraud ho, my cake never came" is
an angry customer, not a fraud on their account; "someone used my card" is.
Calling the business a fraud reads as fraud only alongside a sign that the
caller themselves was defrauded.

English, Hindi and the Hinglish callers actually use. A phrase list will
always be incomplete; the model's signal covers what it misses, and
``api/tests/escalation_phrases.py`` is the labelled set every change here is
measured against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional

RequestStrength = Optional[Literal["strong", "weak"]]

_PUNCT = re.compile(r"[^\w\s₹ऀ-ॿ]", re.UNICODE)


def normalise(text: str) -> str:
    # Apostrophes go first, so "can't" and "cant" read the same.
    text = (text or "").replace("'", "").replace("’", "")
    return " ".join(_PUNCT.sub(" ", text.lower()).split())


_PERSON = (
    r"(human|human being|real person|person|someone|somebody|live agent|agent|"
    r"representative|rep|executive|manager|supervisor|staff|operator|"
    r"customer care|customer service|customer support|team member|real human)"
)
#: Only the nouns that cannot mean anything but "not you".
_PERSON_STRICT = (
    r"(human|human being|real person|live agent|representative|manager|"
    r"supervisor|operator|real human|customer care)"
)
#: The person nouns that could just as well be somebody at the caller's end.
_GENERIC = frozenset(
    {"person", "someone", "somebody", "staff", "kisi", "koi", "banda", "aadmi", "admi"}
)
_ARTICLE = r"(?:(?:a|an|the|your|some|any|a real|real)\s+)?"
#: The shapes of asking: "can I", "let me", "I want to"...
_LEAD = (
    r"(?:let me|can i|could i|may i|can you let me|i want to|i wanna|i need to|"
    r"i would like to|id like to|i have to|i must|please let me|want to|need to)"
)
_TALK = r"(?:talk|speak|chat)"
_FILLER = (
    r"(?:please|pls|just|ok|okay|bas|umm|um|uh|hmm|haan|han|sorry|so|hello|hi|"
    r"ji|arre|yaar|listen|look)"
)

_STRONG = [
    re.compile(
        rf"\b{_LEAD}\s+(?:just\s+|please\s+)?{_TALK}\s+(?:to|with)\s+{_ARTICLE}"
        rf"(?P<who>{_PERSON})\b"
    ),
    # "Speak to a manager." -- the imperative, at the start of what they
    # said, after the fillers people say first ("umm", "haan", "sorry").
    re.compile(
        rf"^(?:{_FILLER}\s+)*{_TALK}\s+(?:to|with)\s+{_ARTICLE}(?P<who>{_PERSON})\b"
    ),
    re.compile(
        rf"\b(?:connect|transfer|put)\s+me\s+(?:through\s+)?(?:to|with)\s+"
        rf"{_ARTICLE}(?P<who>{_PERSON})\b"
    ),
    # "Transfer me." with nothing after it but "now" or "please" -- never
    # "transfer me the refund".
    re.compile(
        r"\b(?:connect|transfer) me(?:\s+(?:now|please|right now|immediately|asap|"
        r"through|to them|to someone|ji|sir|madam|yaar|abhi))*$"
    ),
    re.compile(r"\bput me through\b(?!\s+(?:the|my|it|this)\b)"),
    re.compile(
        rf"\b(?:i want|i need|can i have|let me have|get me|give me|find me)\s+"
        rf"{_ARTICLE}(?P<who>{_PERSON_STRICT})\b"
    ),
    re.compile(
        r"\b(dont|do not) want (to (talk|speak) (to|with) )?(a |an |the |this )?"
        r"(bot|robot|machine|ai|computer|recording)\b"
    ),
    # Hinglish, romanised. A possessive in front ("apne manager se") is the
    # caller's own person, which is no request of ours.
    re.compile(
        r"\b(?:(?P<own>apne|apni|mere|meri|hamare|humare|uske|unke)\s+)?"
        r"(?P<who>insaan|insan|aadmi|admi|banda|vyakti|manager|executive|agent|"
        r"kisi|koi)\b(\s+\w+){0,3}?\s+(se|ko)\s+(baat|bat)\s+"
        r"(karni|karna|karao|karwao|karwa|kara|karaiye|karwaiye|krao|krni)\b"
    ),
    re.compile(r"\b(?P<who>kisi|koi)\s+(insaan|aadmi|banda)\s+(se|ko)\b"),
    # Devanagari.
    re.compile(r"(?<!अपने )(इंसान|आदमी|किसी|मैनेजर|एजेंट)\s*(\S+\s+){0,2}(से|को)\s*बात"),
]

_NEGATED = re.compile(
    rf"\b(dont|do not|no need to|not|never)\b(\s+\w+){{0,2}}\s+"
    rf"(talk|speak|transfer|connect)\b(\s+\w+){{0,3}}\s+{_PERSON}\b"
)

#: Somebody at the caller's end: "someone at home", "ghar mein kisi se".
_AT_HOME = re.compile(
    r"\b(at home|back home|my (wife|husband|family|mother|mom|mum|father|dad|son|"
    r"daughter|parents|brother|sister|friend|partner|boss|accountant)|ghar|gharwal\w*|"
    r"family|biwi|pati|papa|mummy)\b|(घर|परिवार)"
)

#: "My manager", "our supervisor": the caller's own people, not a request.
_OWN_PERSON = re.compile(
    r"\b(my|our|his|her|their|mere|meri|hamare|humare|apne|apni)\s+"
    r"(manager|supervisor|executive|boss|agent|representative|operator)\b"
)
_OWN_PERSON_DEVANAGARI = re.compile(r"(मेरे|मेरी|अपने|अपनी|हमारे)\s+(मैनेजर|एजेंट)")

_WEAK = [
    re.compile(
        r"\b(human|real person|representative|manager|supervisor|customer care|"
        r"executive|operator|live agent|insaan|aadmi)\b"
    ),
    re.compile(r"\bare you (a |an )?(bot|robot|machine|ai|real person|human)\b"),
    re.compile(r"(इंसान|मैनेजर)"),
]


def _strong_match(said: str) -> bool:
    for pattern in _STRONG:
        match = pattern.search(said)
        if match is None:
            continue
        groups = match.groupdict()
        if groups.get("own"):
            continue
        who = (groups.get("who") or "").strip()
        if who in _GENERIC and _AT_HOME.search(said):
            continue
        return True
    return False


def human_request(text: str) -> RequestStrength:
    """``strong`` for a request, ``weak`` for a mention, None for neither."""
    said = normalise(text)
    if not said:
        return None
    if _NEGATED.search(said):
        return None
    if _strong_match(said):
        return "strong"
    mention = _OWN_PERSON_DEVANAGARI.sub(" ", _OWN_PERSON.sub(" ", said))
    for pattern in _WEAK:
        if pattern.search(mention):
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
    # Conservative on purpose: distress, self-harm, abuse, and a caller who
    # says they are old and alone. Not "I'm confused about my bill".
    "vulnerable_caller": [
        re.compile(
            r"\b(i (feel|am feeling|have been feeling|am) (so |very )?(hopeless|"
            r"worthless|suicidal|helpless|desperate)|i (want|wanna) to (die|hurt "
            r"myself|harm myself)|hurt myself|harm myself|self harm|no reason to "
            r"live|(dont|do not) want to live|cant go on|cannot go on|cant cope|"
            r"cannot cope|nobody (cares about me|to help me|can help me)|no one "
            r"(cares about me|to help me|can help me)|i am being (abused|beaten|"
            r"threatened)|(he|she|they) (hits|beats|threatens) me|domestic violence|"
            r"(im|i am) \d{2} (years old )?and (i )?live alone|i live alone and (im|i am) "
            r"(confused|scared|lost)|(im|i am) (old|elderly) and (alone|confused)|"
            r"(im|i am) (all )?alone and (confused|scared|lost)|jeene ka (mann|man) "
            r"nahi|mar (jaana|jana) (chahta|chahti)|khud ko (nuksan|nuksaan|hurt)|"
            r"koi (madad|help) karne wala nahi|bahut (dar|darr) lag raha)\b"
        ),
        re.compile(
            r"(जीने का मन नहीं|मर जाना चाहत|खुद को नुकसान|कोई मदद करने वाला नहीं|"
            r"बहुत डर लग रहा)"
        ),
    ],
}

#: The business called a fraud: "aap log fraud ho", "you guys are a scam".
_ACCUSATION = re.compile(
    r"\b(you|you people|you guys|your company|your shop|your service|your app|"
    r"this company|this shop|aap|aap log|aap logon|aap sab|tum|tum log|"
    r"tumhari company|aapki company|ye company|yeh company)\b(\s+\w+){0,3}?\s+"
    r"(fraud|frauds|fraudster|fraudsters|scam|scammers|scamsters|chor|cheat|"
    r"cheaters|dhokhebaaz|dhokebaaz|thug)\b"
)
#: Signs the caller themselves was defrauded.
_DEFRAUDED = re.compile(
    r"\b(someone|somebody|my (card|account|upi|bank|money|phone|otp|wallet)|"
    r"unauthori[sz]ed|hacked|stole|stolen|otp|phishing|deducted|debited|"
    r"scammed me|cheated me|kat gaye|kaat liye|mere saath|mere account|used my)\b"
)

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


def _phrase_in(phrases: Iterable[str], said: str) -> str | None:
    padded = f" {said} "
    for phrase in phrases:
        wanted = normalise(phrase)
        if wanted and f" {wanted} " in padded:
            return phrase
    return None


@dataclass
class Reading:
    """What one utterance carried."""

    request: RequestStrength = None
    topics: list[str] = field(default_factory=list)
    #: The custom phrase that matched, when one did.
    custom: str | None = None
    refund_amount: int | None = None
    #: The never-transfer phrase that matched, when one did.
    never: str | None = None
    #: The out-of-scope phrase that matched, when one did.
    out_of_scope: str | None = None
    #: "Fraud" said of the business, read as an accusation and not a report.
    accusation: bool = False


def read(
    text: str,
    *,
    custom_topics: Iterable[str] = (),
    refund_limit: int | None = None,
    never_topics: Iterable[str] = (),
    out_of_scope: Iterable[str] = (),
) -> Reading:
    said = normalise(text)
    reading = Reading(request=human_request(text))
    if not said:
        return reading
    for topic, patterns in _TOPIC_PATTERNS.items():
        if any(p.search(said) for p in patterns):
            reading.topics.append(topic)
    if (
        "fraud" in reading.topics
        and _ACCUSATION.search(said)
        and not _DEFRAUDED.search(said)
    ):
        reading.topics.remove("fraud")
        reading.accusation = True
    if _REFUND.search(said):
        figures = amounts(text)
        if figures:
            reading.refund_amount = max(figures)
            if refund_limit is not None and reading.refund_amount > refund_limit:
                reading.topics.append("refund_over_limit")
    reading.custom = _phrase_in(custom_topics, said)
    reading.never = _phrase_in(never_topics, said)
    reading.out_of_scope = _phrase_in(out_of_scope, said)
    return reading


__all__ = ["Reading", "amounts", "human_request", "normalise", "read"]
