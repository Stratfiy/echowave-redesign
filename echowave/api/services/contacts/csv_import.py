"""Turning an uploaded CSV into contacts, and saying what it could not use.

The file comes from a person, so most of this module is about the ways a real
spreadsheet differs from the one the feature was designed against: the phone
column is called something different, Excel has written a BOM at the front,
numbers have been stored as text with a leading apostrophe or as a float that
lost its leading zero, and a few rows are blank.

**Nothing is silently dropped.** A row that cannot be used is counted and its
line number reported, because "I uploaded 4,000 contacts and 3,712 arrived" is
only answerable if the import said which ones and why at the time. The
alternative — importing what parses and staying quiet — produces an inbound
list that is wrong in a way nobody discovers until a customer is not
recognised.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any

from api.utils.telephony_address import normalize_telephony_address

#: Ceiling on one upload. High enough for any list somebody curates by hand,
#: low enough that a mis-selected export cannot be parsed into memory
#: unbounded. A larger list is a real requirement, and the honest answer then
#: is a background job rather than a bigger number here.
MAX_CONTACT_ROWS = 50_000

#: Header names taken as the phone column, lowercased and stripped. Ordered:
#: the first match wins, so a file with both "phone" and "alternate phone"
#: uses the one that is plainly the primary.
_PHONE_HEADERS = (
    "phone",
    "phone_number",
    "phone number",
    "mobile",
    "mobile_number",
    "mobile number",
    "number",
    "contact",
    "contact_number",
    "contact number",
    "msisdn",
    "to",
    "to_number",
)

_NAME_HEADERS = ("name", "full_name", "full name", "customer_name", "customer name")


@dataclass
class ContactImportResult:
    """Rows ready to write, and an account-readable account of the rest."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    skipped: int = 0
    #: ``(line number, why)``. Capped — a file where every row is broken should
    #: say so once, not return 50,000 identical complaints.
    problems: list[tuple[int, str]] = field(default_factory=list)
    phone_column: str | None = None
    truncated: bool = False

    _PROBLEM_CAP = 25

    def note(self, line: int, why: str) -> None:
        self.skipped += 1
        if len(self.problems) < self._PROBLEM_CAP:
            self.problems.append((line, why))


def _clean_cell(value: Any) -> str:
    """One cell, as a person meant it rather than as the spreadsheet wrote it.

    Strips the apostrophe Excel prepends to keep a number as text, and the
    trailing ``.0`` it leaves when the column was typed as a float — which is
    also how a leading zero disappears, so the result may be a local number
    that only normalizes with a country hint.
    """
    text = "" if value is None else str(value).strip()
    if text.startswith("'"):
        text = text[1:].strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def _pick(headers: list[str], candidates: tuple[str, ...]) -> str | None:
    lowered = {h.strip().lower().lstrip("﻿"): h for h in headers if h}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def parse_contacts_csv(
    content: bytes,
    *,
    country_hint: str | None = None,
    phone_column: str | None = None,
) -> ContactImportResult:
    """Parse an uploaded CSV into contact rows.

    ``country_hint`` is the account's country, used to resolve local numbers —
    an Indian list full of ``09876543210`` normalizes to ``+919876543210``
    with it and not at all without it, so passing it is what makes a typical
    domestic export work.

    Every column other than phone and name is kept in ``attributes``, verbatim.
    Guessing which of an account's own columns matter would be guessing about
    their business; the agent's prompt decides what to use.
    """
    result = ContactImportResult()

    # utf-8-sig drops the BOM Excel writes. A file that is not UTF-8 at all is
    # decoded leniently rather than refused: losing one accented character in a
    # name is better than rejecting a list over a byte in row 900.
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    if not headers:
        result.note(0, "The file has no header row.")
        return result

    phone_key = phone_column or _pick(headers, _PHONE_HEADERS)
    if not phone_key:
        result.note(
            0,
            "No phone column found. Name one of the columns 'phone' (or "
            f"'mobile', 'number'), or choose one explicitly. Found: "
            f"{', '.join(h for h in headers if h)}.",
        )
        return result
    result.phone_column = phone_key

    name_key = _pick(headers, _NAME_HEADERS)
    seen: set[str] = set()

    for line, raw_row in enumerate(reader, start=2):
        if len(result.rows) >= MAX_CONTACT_ROWS:
            result.truncated = True
            break

        raw_phone = _clean_cell(raw_row.get(phone_key))
        if not raw_phone:
            # A trailing blank line is the common case and is not worth
            # reporting as a problem; a blank phone mid-file is.
            if any(_clean_cell(v) for v in raw_row.values()):
                result.note(line, "No phone number in this row.")
            continue

        try:
            address = normalize_telephony_address(raw_phone, country_hint=country_hint)
        except ValueError:
            result.note(line, f"{raw_phone!r} is not a usable phone number.")
            continue

        # The normalizer never rejects: anything it cannot read as a number or
        # a SIP URI comes back as a "sip_extension", which is right for a
        # carrier's dial string and wrong here. A cell reading "call him back"
        # would otherwise be stored as a contact whose phone is that sentence —
        # matching nobody, forever, while counting towards the imported total.
        #
        # A bare extension of digits is still allowed, because a SIP deployment
        # legitimately has those and refusing them would make this import
        # unusable for ARI accounts.
        if address.address_type == "sip_extension" and not address.canonical.isdigit():
            result.note(line, f"{raw_phone!r} is not a usable phone number.")
            continue

        normalized = address.canonical

        if normalized in seen:
            # Within one file. Across files the upsert handles it, but here we
            # would be sending two rows with the same conflict key in a single
            # statement, which Postgres refuses outright.
            result.note(line, f"{raw_phone!r} appears earlier in this file.")
            continue
        seen.add(normalized)

        attributes = {
            key: _clean_cell(value)
            for key, value in raw_row.items()
            if key and key not in (phone_key, name_key) and _clean_cell(value)
        }

        result.rows.append(
            {
                "phone_raw": raw_phone,
                "phone_normalized": normalized,
                "name": _clean_cell(raw_row.get(name_key)) or None
                if name_key
                else None,
                "attributes": attributes,
            }
        )

    return result
