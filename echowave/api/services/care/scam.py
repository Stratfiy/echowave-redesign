"""Is this a scam? A plain answer, and why.

A person pastes or forwards a message, or describes a call; Decibyl says
whether it looks like a scam, which warning signs it found, and what to do.
The check is a list of known warning signs (``SIGNS``) read in English,
Hindi and Hinglish -- no outside service is called, so it is instant, free
and the same every time. It says so: a new trick can get past a list, and
"no warning signs found" is never "safe".

Two promises, tested:

* Decibyl never asks for an OTP, PIN or password -- the answer says so in
  every verdict, and nothing here asks for anything.
* The words are not kept. Only the verdict and the sign codes are stored
  (``care_scam_checks``); a family member who was shared scam checks sees
  "a message that asked for an OTP", never the message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from api.db import db_client
from api.db.care_models import CareScamCheckModel
from api.services.care import SCAM_CHECK, CareError, on

LIKELY = "likely_scam"
CAREFUL = "be_careful"
NO_SIGNS = "no_signs_found"

MAX_CHARS = 5_000
NEVER_ASKS = "Decibyl will never ask you for an OTP, PIN or password."
LIMITS = (
    "This check looks for common warning signs. A new trick can get past it, "
    "so if something feels wrong, trust that."
)


def enabled(organization_id: int | None = None) -> bool:
    return on(SCAM_CHECK, organization_id)


@dataclass(frozen=True)
class Sign:
    code: str
    #: 3 is enough on its own for "likely a scam"; 2 is a strong hint; 1 a hint.
    weight: int
    why: str
    pattern: re.Pattern[str]
    #: A match right after "do not" / "never" is advice, not a request:
    #: "never share your OTP" is what a genuine bank message says.
    negatable: bool = False

    def found_in(self, text: str) -> bool:
        for match in self.pattern.finditer(text):
            if not self.negatable or not _NEGATION.search(
                text[max(0, match.start() - 14) : match.start() + 8]
            ):
                return True
        return False


_NEGATION = re.compile(
    r"\b(do not|don'?t|never|nobody will|no one will|mat|na)\b|मत|ना कभी|कभी नहीं",
    re.IGNORECASE,
)


def _p(*words: str) -> re.Pattern[str]:
    return re.compile("|".join(words), re.IGNORECASE)


SIGNS: tuple[Sign, ...] = (
    Sign(
        "asks_for_otp",
        3,
        "It asks for an OTP or a code sent to your phone. Banks and real "
        "companies never ask for this.",
        _p(
            r"\b(share|send|tell|give|forward|read|provide|confirm|enter)\b.{0,25}"
            r"\b(otp|one[ -]?time[ -]?pass\w*|verification code|the code)\b",
            r"\b(otp|code)\b.{0,15}\b(batao|bata do|bataiye|bhejo|bhejiye|de do)\b",
            r"ओटीपी.{0,15}(बताएं|बताओ|बता दो|भेजें|भेजो|दें|दे दो)",
        ),
        negatable=True,
    ),
    Sign(
        "asks_for_password_or_pin",
        3,
        "It asks for a password, PIN or CVV. Nobody genuine needs these.",
        _p(
            r"\b(share|send|tell|give|provide|confirm|enter|type)\b.{0,25}"
            r"\b(pass ?word|(upi |atm |card |m-?)?pin(?!\s*code)|cvv)\b",
            r"(पिन|पासवर्ड).{0,15}(बताएं|बताओ|भेजें|भेजो|दें)",
        ),
        negatable=True,
    ),
    Sign(
        "contains_code",
        1,
        "It has a one-time code in it. Never tell this code to anyone, even "
        "someone who says they are from your bank.",
        _p(
            r"\b(otp|one[ -]?time[ -]?pass\w*|verification code|login code|security code)\b"
            r"\D{0,25}\b\d{4,8}\b"
        ),
    ),
    Sign(
        "pay_to_receive",
        3,
        "It says you must pay, scan a QR code or enter your UPI PIN to receive "
        "money. You never need a PIN to receive money.",
        _p(
            r"scan.{0,20}(qr|code).{0,30}(receive|get|credit|refund)",
            r"(receive|get|claim).{0,30}(enter|put).{0,15}pin",
            r"collect request",
            r"(processing|registration|release|clearance) (fee|charge)",
            r"pay.{0,20}(to|for).{0,10}(release|claim|receive)",
        ),
    ),
    Sign(
        "remote_access_app",
        3,
        "It asks you to install an app or share your screen. That lets a "
        "stranger control your phone.",
        _p(
            r"any ?desk",
            r"team ?viewer",
            r"quick ?support",
            r"rust ?desk",
            r"screen ?shar",
            r"\.apk\b",
            r"install (this|the|an) app",
        ),
    ),
    Sign(
        "digital_arrest",
        3,
        "It threatens arrest, police, a court case or a 'digital arrest'. Police "
        "and courts do not arrest people over a call or video call.",
        _p(
            r"digital arrest",
            r"arrest warrant",
            r"\b(cbi|ncb|ed officer|narcotics)\b",
            r"\bfir\b",
            r"(police|court|legal) (case|action|notice)",
            r"गिरफ्तार",
            r"giraftar",
        ),
    ),
    Sign(
        "urgent_threat",
        2,
        "It rushes you or threatens that something will be blocked or cut off. "
        "Scammers hurry you so you do not stop to check.",
        _p(
            r"(account|card|sim|number|connection|electricity|power).{0,30}"
            r"(block|suspend|deactivat|cut|disconnect|close)",
            r"within \d+ ?(hours|hrs|minutes|mins)",
            r"\b(immediately|urgent(ly)?|last (warning|chance|reminder)|today itself)\b",
            r"tonight",
            r"बंद (हो जाएगा|कर दिया जाएगा)",
            r"band ho (jayega|jaega)",
            r"turant",
            r"तुरंत",
        ),
    ),
    Sign(
        "bill_cutoff",
        3,
        "It says your electricity, gas or phone will be cut off today unless you "
        "call a number or pay at once. Real companies send a written notice "
        "first and never call at night.",
        _p(
            r"(electricity|power|light|gas|connection|bijli).{0,40}"
            r"(cut|disconnect|band).{0,40}(tonight|today|aaj|\d{1,2}[:.]\d{2})",
        ),
    ),
    Sign(
        "kyc_update",
        2,
        "It says your KYC, PAN or Aadhaar needs updating through a link or a "
        "call. Banks ask you to visit the branch or use their own app.",
        _p(
            r"\bkyc\b",
            r"(pan|aadhaar|aadhar).{0,20}(update|link|expir|verify)",
            r"केवाईसी",
        ),
    ),
    Sign(
        "prize_or_lottery",
        2,
        "It says you have won a prize, lottery, gift or cashback. If you did not "
        "enter, you did not win.",
        _p(
            r"\b(lottery|lucky draw|jackpot|kbc)\b",
            r"you (have )?won",
            r"\b(prize|reward|cashback|gift card)\b",
            r"इनाम",
            r"लॉटरी",
            r"inaam",
        ),
    ),
    Sign(
        "paid_tasks",
        3,
        "It offers money for liking videos, rating hotels or doing small "
        "online tasks. These start by paying a little and end by taking a lot.",
        _p(
            r"(like|rate|review|subscribe).{0,25}(videos?|products?|hotels?|channels?).{0,30}"
            r"(earn|paid|income|rs|₹)",
            r"\btelegram task",
        ),
    ),
    Sign(
        "too_good_job_or_investment",
        2,
        "It promises easy money: a part-time job, tasks for money, or returns "
        "that are guaranteed or doubled.",
        _p(
            r"(part[ -]?time|work from home).{0,30}(job|earn|income)",
            r"(like|rate|review).{0,20}(videos?|products?|hotels?).{0,20}earn",
            r"double your money",
            r"guaranteed (return|profit)",
            r"\b(telegram task|crypto|bitcoin|forex)\b",
        ),
    ),
    Sign(
        "family_emergency_new_number",
        2,
        "It says a family member is in trouble and needs money, often from a new "
        "number. Call them on the number you already have.",
        _p(
            r"(new|changed) (my )?number",
            r"(accident|hospital|stuck|in trouble).{0,40}(send|transfer|need).{0,20}(money|rs|₹|rupees)",
            r"(mom|mum|dad|papa|mummy|beta|beti).{0,40}(send|transfer).{0,20}(money|rs|₹)",
        ),
    ),
    Sign(
        "sent_by_mistake",
        2,
        "It says money was sent to you by mistake and asks you to send it back. "
        "Check your bank app yourself first; often nothing arrived.",
        _p(
            r"(sent|transferred|credited).{0,20}(by mistake|wrongly|galti se)",
            r"galti se",
        ),
    ),
    Sign(
        "pretends_to_be_official",
        1,
        "It claims to be from a bank, the government, a courier, the phone "
        "company or the electricity board. Anyone can claim that.",
        _p(
            r"\b(rbi|sbi|hdfc|icici|axis|bank manager|income tax|trai|customs)\b",
            r"\b(fedex|dhl|courier|parcel)\b",
            r"electricity (board|office|department)",
            r"customer care",
        ),
    ),
    Sign(
        "keep_it_secret",
        2,
        "It tells you to keep this secret or not to tell family. Genuine people "
        "never ask that.",
        _p(
            r"(don'?t|do not) tell (anyone|anybody|your family)",
            r"keep (this|it) (secret|confidential)",
            r"kisi ko (mat|na) bata",
            r"किसी को (मत|न) बता",
        ),
    ),
    Sign(
        "short_or_odd_link",
        2,
        "It has a shortened or unusual link. Do not tap it; open the real app "
        "or website yourself.",
        _p(
            r"\b(bit\.ly|tinyurl|t\.co|cutt\.ly|rb\.gy|is\.gd|goo\.gl|shorturl)\b",
            r"https?://\d{1,3}(\.\d{1,3}){3}",
            r"https?://[^\s]*(-kyc|-bank|-refund|-reward|-update)[^\s]*",
        ),
    ),
    Sign(
        "has_link",
        1,
        "It has a link. Links in unexpected messages are the commonest trap.",
        _p(
            r"https?://",
            r"\bwww\.",
            r"\b[a-z0-9-]+\.(xyz|top|click|link|info|online|site)\b",
        ),
    ),
)

#: The two "a link" signs overlap: the stronger replaces the weaker.
_SUPERSEDES = {"short_or_odd_link": "has_link"}

WHAT_TO_DO = {
    LIKELY: [
        "Do not reply, call back, tap any link or share any code.",
        "If it says it is your bank, call the number on the back of your card.",
        (
            "If you already shared something or lost money, call 1930 (the "
            "national cyber crime helpline) now, or report at cybercrime.gov.in."
        ),
    ],
    CAREFUL: [
        "Do not tap links or share any code until you have checked.",
        "Check by calling the company or the person on a number you already have.",
    ],
    NO_SIGNS: [
        (
            "If anything feels wrong, check with the company or the person on a "
            "number you already have."
        ),
    ],
}

HEADLINES = {
    LIKELY: "This looks like a scam.",
    CAREFUL: "Be careful: this has warning signs.",
    NO_SIGNS: "I did not find warning signs.",
}


def assess(text: str) -> dict[str, Any]:
    """The verdict on one message or call description. Pure: stores nothing."""
    text = (text or "").strip()
    if not text:
        raise CareError("Paste the message, or say what the caller said.")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    found = [s for s in SIGNS if s.found_in(text)]
    codes = {s.code for s in found}
    found = [s for s in found if s.code not in {_SUPERSEDES.get(c) for c in codes}]
    strong = any(s.weight >= 3 for s in found)
    score = sum(s.weight for s in found)
    if strong or score >= 4:
        verdict = LIKELY
    elif score >= 2:
        verdict = CAREFUL
    else:
        verdict = NO_SIGNS
    found.sort(key=lambda s: -s.weight)
    return {
        "verdict": verdict,
        "headline": HEADLINES[verdict],
        "reasons": [{"code": s.code, "why": s.why} for s in found],
        "what_to_do": list(WHAT_TO_DO[verdict]),
        "never_asks": NEVER_ASKS,
        "limits": LIMITS,
    }


async def check(
    organization_id: int, user_id: int, *, text: str, kind: str = "message"
) -> dict[str, Any]:
    """Assess, keep the verdict (never the words), and return the answer."""
    if kind not in ("message", "call"):
        raise CareError("Say whether this was a message or a call.")
    answer = assess(text)
    async with db_client.async_session() as session:
        row = CareScamCheckModel(
            organization_id=organization_id,
            user_id=user_id,
            kind=kind,
            verdict=answer["verdict"],
            signals=[r["code"] for r in answer["reasons"]],
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        check_id = row.id
    if answer["verdict"] == LIKELY:
        from api.services.care import circle

        if circle.enabled(organization_id):
            person = await circle.person_name(organization_id, user_id)
            await circle.alert(
                organization_id,
                user_id,
                kind="scam_checked",
                share="scam_checks",
                title=(
                    f"{person} checked a {kind} with Decibyl and it looked like "
                    "a scam. They were told not to share any code. You might "
                    "check in with them."
                ),
                subject_id=check_id,
            )
    return {"id": check_id, "kind": kind, **answer}


async def recent(organization_id: int, user_id: int, limit: int = 10) -> list[dict]:
    async with db_client.async_session() as session:
        rows = (
            await session.scalars(
                select(CareScamCheckModel)
                .where(
                    CareScamCheckModel.organization_id == organization_id,
                    CareScamCheckModel.user_id == user_id,
                )
                .order_by(CareScamCheckModel.created_at.desc())
                .limit(limit)
            )
        ).all()
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "verdict": r.verdict,
            "headline": HEADLINES.get(r.verdict, r.verdict),
            "signals": list(r.signals or []),
            "at": r.created_at.isoformat(),
        }
        for r in rows
    ]
