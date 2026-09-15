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


def last_four(number: Any) -> str:
    """A number as a log line should carry it: its last four digits."""
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    return f"…{digits[-4:]}" if len(digits) >= 4 else "…"


def short_token(token: Any) -> str:
    """A token as a log line should carry it: enough to find, not to use."""
    text = str(token or "")
    return f"{text[:6]}…" if len(text) > 6 else "…"


def redact_record(record: dict[str, Any]) -> None:
    """loguru patcher: rewrite the message before any handler sees it."""
    try:
        record["message"] = mask_phone_numbers(record["message"])
    except Exception:  # noqa: BLE001 - never let masking break logging
        pass


__all__ = ["last_four", "mask_phone_numbers", "redact_record", "short_token"]
