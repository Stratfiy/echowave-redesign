"""Mask OTPs and other secrets before a call's words are written down.

A bot that verifies a caller hears their OTP. A bot that takes a payment hears
a card number. Until this module, both landed verbatim in the stored
transcript, in ``realtime_feedback_events`` (which the QA pass reads to write
its summary and ``extracted_data``), in ``gathered_context`` (which webhooks
deliver), and so in the review inbox and the bot's own thread. An OTP is a
secret for about five minutes; a card number is one for years.

**Masked at write, never at display.** A display filter leaves the secret in
the row for the export, the webhook, the LLM prompt and the next screen
somebody builds. So the pass runs on the events before they are saved, on the
transcript before it is uploaded, and on the gathered context at completion,
and everything downstream reads the masked copy.

**Values, not field names.** An allowlist of "safe" keys is the silent-absence
shape: the next extraction somebody names ``customer_code`` keeps its secret
by default. So the rules below look at what a value *is*:

* a card number is 13 to 19 digits that pass Luhn;
* an Aadhaar number is 12 digits that pass Verhoeff;
* a PAN is five letters, four digits, a letter;
* an OTP or PIN is a 4 to 8 digit run given right after the bot asked for a
  code, or in a line that itself talks about a code. Without that context a
  6-digit run is an order number or a postal PIN and is left alone: masking
  every number would make the transcript useless for the operator, which is a
  different way of losing it.

Card, Aadhaar and account numbers keep their last four so a person can still
match the call to a record; a code keeps nothing, there is nothing to match.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from pipecat.utils.enums import RealtimeFeedbackType

MASK = "****"

#: Words in a bot's turn that mean the next thing the caller says is a code.
#: Also applied to the turn itself, so a bot repeating "your OTP is 482913"
#: is masked in its own line.
CODE_WORDS = re.compile(
    r"\b(?:otp|one[- ]?time (?:password|passcode|code)|passcode|pass ?code|"
    r"pin|verification code|security code|cvv|cvc|password)\b",
    re.IGNORECASE,
)

#: Words that mean the next number is an account, which keeps its last four.
ACCOUNT_WORDS = re.compile(
    r"\b(?:account number|account no|a/c number|card number|aadhaar|aadhar)\b",
    re.IGNORECASE,
)

#: Digits, optionally separated by single spaces or dashes ("4111 1111 1111
#: 1111", "98-76-54"). A run is masked as a whole, separators included.
DIGIT_RUN = re.compile(r"\d(?:[ \-]?\d)+")

PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")

#: Keys in a mapping whose string value is a code, whatever the digits look
#: like. A supplement to the value rules, not a replacement: an unnamed
#: secret is still caught by Luhn, Verhoeff and the PAN shape.
CODE_KEYS = re.compile(
    r"(?:^|_)(?:otp|pin|cvv|cvc|passcode|password|secret|token)(?:_|$)",
    re.IGNORECASE,
)
ACCOUNT_KEYS = re.compile(
    r"(?:^|_)(?:account|card|aadhaar|aadhar|pan)(?:_|$)", re.IGNORECASE
)


def luhn_ok(digits: str) -> bool:
    total = 0
    for index, ch in enumerate(reversed(digits)):
        n = int(ch)
        if index % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_ok(digits: str) -> bool:
    c = 0
    for index, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[index % 8][int(ch)]]
    return c == 0


def _keep_last_four(digits: str) -> str:
    return MASK + digits[-4:]


def _mask_run(run: str, *, code_context: bool, account_context: bool) -> str:
    """One digit run, decided by what it is, then by what was asked."""
    digits = re.sub(r"[ \-]", "", run)
    n = len(digits)
    if 13 <= n <= 19 and luhn_ok(digits):
        return _keep_last_four(digits)
    if n == 12 and verhoeff_ok(digits):
        return _keep_last_four(digits)
    if code_context and 4 <= n <= 8:
        return MASK
    if account_context and 9 <= n <= 18:
        return _keep_last_four(digits)
    return run


def mask_text(
    text: str, *, asked_for_code: bool = False, asked_for_account: bool = False
) -> str:
    """Mask the secrets in one line of conversation.

    ``asked_for_code`` / ``asked_for_account`` carry what the *previous* bot
    turn asked; the line's own words count too.
    """
    if not text:
        return text
    # A code word inside the line only covers what follows it: "order 482913
    # is ready, now read me the OTP" keeps the order number. The previous
    # turn's question covers the whole reply.
    code_word = CODE_WORDS.search(text)
    account_word = ACCOUNT_WORDS.search(text)
    code_from = 0 if asked_for_code else (code_word.start() if code_word else None)
    account_from = (
        0 if asked_for_account else (account_word.start() if account_word else None)
    )
    out = PAN.sub(lambda m: MASK + m.group(0)[-4:], text)

    def replace(m: re.Match[str]) -> str:
        return _mask_run(
            m.group(0),
            code_context=code_from is not None and m.start() >= code_from,
            account_context=account_from is not None and m.start() >= account_from,
        )

    return DIGIT_RUN.sub(replace, out)


def mask_feedback_events(events: Iterable[dict]) -> list[dict]:
    """The realtime feedback events with every spoken secret masked.

    Returns new dicts; the input is not touched. Non-text events pass
    through unchanged. The bot's turn sets the context for the caller's
    reply, so "please read me the OTP" followed by "482913" masks the code
    and leaves the order number two turns earlier alone.
    """
    asked_code = False
    asked_account = False
    out: list[dict] = []
    for event in events:
        kind = event.get("type")
        payload = event.get("payload") or {}
        text = payload.get("text")
        if kind == RealtimeFeedbackType.BOT_TEXT.value and isinstance(text, str):
            masked = mask_text(text)
            asked_code = bool(CODE_WORDS.search(text))
            asked_account = bool(ACCOUNT_WORDS.search(text))
            out.append({**event, "payload": {**payload, "text": masked}})
        elif kind == RealtimeFeedbackType.USER_TRANSCRIPTION.value and isinstance(
            text, str
        ):
            masked = mask_text(
                text, asked_for_code=asked_code, asked_for_account=asked_account
            )
            out.append({**event, "payload": {**payload, "text": masked}})
        else:
            out.append(event)
    return out


def mask_mapping(value: Any, *, key: str = "") -> Any:
    """Mask the secrets inside a gathered-context or annotations mapping.

    Recurses through dicts and lists. A string under a code-like key is
    masked whole if it is digits; every string gets the value rules.
    """
    if isinstance(value, dict):
        return {k: mask_mapping(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_mapping(v, key=key) for v in value]
    if isinstance(value, str):
        return mask_text(
            value,
            asked_for_code=bool(CODE_KEYS.search(key)),
            asked_for_account=bool(ACCOUNT_KEYS.search(key)),
        )
    if isinstance(value, int) and not isinstance(value, bool):
        # A code stored as a number: 482913 under "otp". Strings only would
        # let the integer form through, which is the same secret.
        masked = mask_mapping(str(value), key=key)
        return value if masked == str(value) else masked
    return value


async def mask_completed_run(workflow_run_id: int) -> bool:
    """Mask what a finished call gathered, before anything reads it.

    Runs first in the completion job: the QA pass, the webhooks and the
    thread row all read the run after this. Returns True when something was
    changed. Never raises: masking failing must not lose the call's costing
    or its outcome, and a run left unmasked is logged loudly rather than
    silently.
    """
    from loguru import logger

    from api.db import db_client

    try:
        run = await db_client.get_workflow_run(workflow_run_id)
        if run is None:
            return False
        changed = False
        gathered = run.gathered_context or {}
        masked_gathered = mask_mapping(gathered)
        if masked_gathered != gathered:
            await db_client.update_workflow_run(
                run_id=workflow_run_id, gathered_context=masked_gathered
            )
            changed = True
        logs = run.logs or {}
        events = logs.get("realtime_feedback_events")
        if isinstance(events, list):
            masked_events = mask_feedback_events(events)
            if masked_events != events:
                await db_client.update_workflow_run(
                    run_id=workflow_run_id,
                    logs={"realtime_feedback_events": masked_events},
                )
                changed = True
        return changed
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.error("Could not mask secrets on run {}: {}", workflow_run_id, exc)
        return False
