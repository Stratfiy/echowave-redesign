"""Phone numbers do not belong in logs.

A log line is the copy of a number nobody audits: it goes to a file, a
shipping pipeline, a vendor's dashboard, and it stays there after the
customer has asked to be forgotten. Carriers, the missed-call path and
any future line that interpolates a caller all go through one loguru
patcher here, so masking is not a convention each author has to remember.

Ten or more digits in a run (with spaces, dashes or a leading plus) is a
phone number: an Indian mobile is ten, with the country code twelve. Run
ids, row ids and Twilio SIDs are shorter or carry letters and pass through.
"""

from __future__ import annotations

import re
from typing import Any

PHONE = re.compile(r"(?<![\w.])\+?(?:\d[\s-]?){9,}\d(?![\w.])")


def _mask(match: re.Match[str]) -> str:
    digits = "".join(ch for ch in match.group(0) if ch.isdigit())
    if len(digits) < 10:
        return match.group(0)
    return f"…{digits[-4:]}"


def mask_phone_numbers(text: str) -> str:
    """Every phone-number-shaped run in ``text`` becomes its last four."""
    if not text:
        return text
    return PHONE.sub(_mask, text)


def redact_record(record: dict[str, Any]) -> None:
    """loguru patcher: rewrite the message before any handler sees it."""
    try:
        record["message"] = mask_phone_numbers(record["message"])
    except Exception:  # noqa: BLE001 - never let masking break logging
        pass


__all__ = ["mask_phone_numbers", "redact_record"]
