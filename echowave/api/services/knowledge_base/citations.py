"""How a passage from Files says where it came from.

Every passage handed to Decibyl or an agent carries the file's name, the
folder it sits in, and the page or the sheet and rows it was read from, so
an answer can say "rates.xlsx in Pricing/2026, sheet Rates, row 14" and a
person can go and check it.

The name and the folder are read from the file's row and the folder tree at
the moment the passage is read -- never stored on the passage -- so a rename
or a move shows in the next answer, with nothing re-read.
"""

from __future__ import annotations

from typing import Any


def _span(label: str, numbers: list[int]) -> str:
    numbers = sorted({int(n) for n in numbers if n is not None})
    if not numbers:
        return ""
    if len(numbers) == 1:
        return f"{label} {numbers[0]}"
    return f"{label}s {numbers[0]}-{numbers[-1]}"


def location(chunk_metadata: dict[str, Any] | None) -> str:
    """Where in the file: "page 3", "pages 3-4", "sheet Rates, rows 4-9", or empty."""
    meta = chunk_metadata or {}
    parts = []
    sheet = meta.get("sheet")
    if sheet:
        parts.append(f"sheet {sheet}")
        rows = _span("row", list(meta.get("rows") or []))
        if rows:
            parts.append(rows)
    else:
        pages = _span("page", list(meta.get("pages") or []))
        if pages:
            parts.append(pages)
    return ", ".join(parts)


def cite(filename: str, folder_path: str, chunk_metadata: dict[str, Any] | None) -> str:
    """One line naming the file, its folder, and where in it."""
    where = location(chunk_metadata)
    text = filename or "a file"
    if folder_path:
        text = f"{text} (in {folder_path})"
    if where:
        text = f"{text}, {where}"
    return text


def fields(
    *,
    filename: str,
    folder_path: str,
    document_uuid: str | None,
    chunk_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """The citation keys a passage carries, for the model and the thread."""
    meta = chunk_metadata or {}
    out: dict[str, Any] = {
        "filename": filename,
        "folder": folder_path,
        "citation": cite(filename, folder_path, meta),
    }
    if document_uuid:
        out["document_uuid"] = str(document_uuid)
    if meta.get("sheet"):
        out["sheet"] = meta["sheet"]
        if meta.get("rows"):
            out["rows"] = list(meta["rows"])
    elif meta.get("pages"):
        out["pages"] = list(meta["pages"])
    return out
