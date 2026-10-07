"""Reading a vCard file, a CSV, or what the phone's contact picker returned.

Parsing only: each returns ``Incoming`` contacts and a count of entries that
named nobody. Storing them is ``store.upsert`` like every other source.

vCard: 2.1, 3.0 and 4.0 as phones and Google/Outlook export them -- folded
lines, ``item1.`` group prefixes, ``TYPE=`` parameters, quoted-printable
names (old Android exports), escaped commas. CSV: a header row, matched by
the words Google Contacts, Outlook and most phones use ("Name", "First
Name", "Phone 1 - Value", "Mobile Phone", "E-mail Address", "Company"...).
"""

from __future__ import annotations

import csv
import io
import quopri
import re
from typing import Any

from api.services.people.normalise import Incoming

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_CONTACTS = 5000


class ImportRefused(ValueError):
    """The file could not be read as contacts; the words are for the person."""


def _unfold(raw: str) -> list[str]:
    lines: list[str] = []
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        elif line.strip():
            lines.append(line)
    # quoted-printable soft breaks: a line ending in "=" continues.
    joined: list[str] = []
    for line in lines:
        if (
            joined
            and joined[-1].endswith("=")
            and "QUOTED-PRINTABLE" in joined[-1].upper()
        ):
            joined[-1] = joined[-1][:-1] + line
        else:
            joined.append(line)
    return joined


def _value(params: str, value: str) -> str:
    upper = params.upper()
    if "QUOTED-PRINTABLE" in upper:
        charset = "utf-8"
        match = re.search(r"CHARSET=([\w-]+)", params, re.IGNORECASE)
        if match:
            charset = match.group(1)
        try:
            value = quopri.decodestring(value.encode("latin-1")).decode(
                charset, "replace"
            )
        except (LookupError, UnicodeEncodeError):
            pass
    return (
        value.replace("\\n", " ")
        .replace("\\N", " ")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def parse_vcard(data: bytes) -> tuple[list[Incoming], int]:
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raw = data.decode("latin-1")
    if "BEGIN:VCARD" not in raw.upper():
        raise ImportRefused("That file has no contacts in it (no BEGIN:VCARD).")
    contacts: list[Incoming] = []
    skipped = 0
    current: dict[str, Any] | None = None
    for line in _unfold(raw):
        if ":" not in line:
            continue
        head, _, value = line.partition(":")
        name, _, params = head.partition(";")
        prop = name.split(".")[-1].upper()  # "item1.TEL" -> "TEL"
        if prop == "BEGIN" and value.strip().upper() == "VCARD":
            current = {"fn": None, "n": None, "tel": [], "email": [], "org": None}
            continue
        if current is None:
            continue
        if prop == "END" and value.strip().upper() == "VCARD":
            n = current["n"]
            full = current["fn"] or (
                " ".join(p for p in (n[1], n[2], n[0]) if p).strip() if n else None
            )
            item = Incoming(
                name=full,
                phones=current["tel"],
                emails=current["email"],
                company=current["org"],
            ).clean()
            if item is None:
                skipped += 1
            else:
                contacts.append(item)
            current = None
            if len(contacts) >= MAX_CONTACTS:
                break
            continue
        value = _value(params, value).strip()
        if prop == "FN":
            current["fn"] = value
        elif prop == "N":
            current["n"] = (value.split(";") + ["", "", ""])[:3]
        elif prop == "TEL":
            current["tel"].append(value)
        elif prop == "EMAIL":
            current["email"].append(value)
        elif prop == "ORG":
            current["org"] = value.split(";")[0] or None
    return contacts, skipped


def _column(headers: list[str], *words: str, avoid: tuple[str, ...] = ()) -> list[int]:
    found = []
    for i, header in enumerate(headers):
        h = header.strip().lower()
        if any(w in h for w in words) and not any(a in h for a in avoid):
            found.append(i)
    return found


def parse_csv(data: bytes) -> tuple[list[Incoming], int]:
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raw = data.decode("latin-1")
    try:
        dialect = csv.Sniffer().sniff(raw[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(raw), dialect))
    if len(rows) < 2:
        raise ImportRefused("That file needs a header row and at least one contact.")
    headers = rows[0]
    full = _column(
        headers,
        "name",
        avoid=(
            "first",
            "last",
            "given",
            "family",
            "middle",
            "company",
            "org",
            "file as",
            "nick",
            "phonetic",
            "suffix",
            "prefix",
        ),
    )
    first = _column(headers, "first name", "given name")
    last = _column(headers, "last name", "family name", "surname")
    phones = _column(
        headers, "phone", "mobile", "tel", "number", "whatsapp", avoid=("type", "label")
    )
    emails = _column(headers, "mail", avoid=("type", "label"))
    company = _column(
        headers,
        "company",
        "organization",
        "organisation",
        "org name",
        avoid=("type", "title", "department", "label"),
    )
    relation = _column(
        headers, "relation", "notes", "job title", "title", avoid=("type", "label")
    )
    if not (phones or emails):
        raise ImportRefused(
            "No phone or email column found. Name a column Phone, Mobile or Email."
        )
    contacts: list[Incoming] = []
    skipped = 0
    for row in rows[1:]:
        cell = lambda i: row[i].strip() if i < len(row) else ""
        name = next((cell(i) for i in full if cell(i)), "")
        if not name:
            name = " ".join(
                p
                for p in (
                    next((cell(i) for i in first if cell(i)), ""),
                    next((cell(i) for i in last if cell(i)), ""),
                )
                if p
            )
        numbers: list[str] = []
        for i in phones:
            # Google exports "a ::: b" for two numbers in one cell.
            numbers.extend(p for p in re.split(r":::|;|/", cell(i)) if p.strip())
        addresses: list[str] = []
        for i in emails:
            addresses.extend(e for e in re.split(r":::|;|,", cell(i)) if e.strip())
        item = Incoming(
            name=name,
            phones=numbers,
            emails=addresses,
            company=next((cell(i) for i in company if cell(i)), None),
            relation=next((cell(i) for i in relation if cell(i)), None),
        ).clean()
        if item is None or not (item.phones or item.emails):
            skipped += 1
            continue
        contacts.append(item)
        if len(contacts) >= MAX_CONTACTS:
            break
    return contacts, skipped


def parse_file(filename: str, data: bytes) -> tuple[list[Incoming], int, str]:
    """Contacts, how many entries named nobody, and the source key."""
    if len(data) > MAX_FILE_BYTES:
        raise ImportRefused("That file is over 5 MB. Export fewer contacts at a time.")
    lowered = (filename or "").lower()
    head = data[:2048].decode("utf-8", "ignore").upper()
    if lowered.endswith((".vcf", ".vcard")) or "BEGIN:VCARD" in head:
        contacts, skipped = parse_vcard(data)
        return contacts, skipped, "vcard"
    if lowered.endswith((".csv", ".tsv", ".txt")):
        contacts, skipped = parse_csv(data)
        return contacts, skipped, "csv"
    raise ImportRefused("Upload a .vcf (vCard) or .csv file.")


def parse_picker(items: list[dict[str, Any]]) -> tuple[list[Incoming], int]:
    """What ``navigator.contacts.select`` returned: ``name``, ``tel`` and
    ``email`` are each a list."""
    contacts: list[Incoming] = []
    skipped = 0
    for raw in items[:MAX_CONTACTS]:
        names = raw.get("name") or []
        item = Incoming(
            name=(names[0] if isinstance(names, list) and names else None),
            phones=[str(p) for p in (raw.get("tel") or []) if p],
            emails=[str(e) for e in (raw.get("email") or []) if e],
        ).clean()
        if item is None:
            skipped += 1
        else:
            contacts.append(item)
    return contacts, skipped
