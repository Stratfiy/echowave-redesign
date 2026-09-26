"""Word templates with ``{{field_name}}`` placeholders, and filling them.

**Where placeholders may be.** Body paragraphs, table cells (nested tables
included), text boxes, and every header and footer. A placeholder is
``{{snake_case}}``, spaces inside the braces allowed.

**Split runs.** Word stores a paragraph as runs -- stretches of one
formatting -- and splits them for reasons nobody typing sees: a spell-check
squiggle, a pasted fragment, an autosave between keystrokes. So
``{{vendor_name}}`` is found in the paragraph's joined text, and its
replacement is written into the run the placeholder *starts* in, taking that
run's formatting; the runs it spanned give up their share of the text. Runs
outside a placeholder are never touched, so a bold label beside a plain value
stays bold.

**Line items.** A table row holding ``{{items.<column>}}`` is a repeating
row: it is cloned once per item, in order, and the original removed. A row
may also hold ordinary fields. No items, no row.

**What this does not do.** Arithmetic. Totals, tax and words are computed by
:mod:`money` and passed in as values; the template only places them.

**An unknown placeholder stays visible.** The drafting tool refuses to fill a
template with anything missing; if a field slips through anyway, a visible
``{{field}}`` is caught by whoever reads the draft, where a blank would not be.
"""

from __future__ import annotations

import copy
import io
import re
from collections.abc import Callable, Iterable
from typing import Any

import docx
from docx.oxml.ns import qn
from docx.text.run import Run

PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?)\s*\}\}")
ITEMS_PREFIX = "items."


class TemplateError(ValueError):
    """A template that cannot be read, in words a person can act on."""


def _open(data: bytes):
    try:
        return docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise TemplateError(
            "That template could not be opened as a Word (.docx) file."
        ) from exc


def _save(document) -> bytes:
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _roots(document) -> list[Any]:
    """Every XML tree text lives in: each distinct header and footer, then
    the body -- in reading order, so fields are listed as a person meets
    them."""
    roots: list[Any] = []
    seen: set[int] = set()
    for section in document.sections:
        for part in (
            section.header,
            section.first_page_header,
            section.even_page_header,
            section.footer,
            section.first_page_footer,
            section.even_page_footer,
        ):
            try:
                if part.is_linked_to_previous:
                    continue
                element = part._element
            except Exception:  # noqa: BLE001 - a header that is not there
                continue
            if id(element) not in seen:
                seen.add(id(element))
                roots.append(element)
    roots.append(document.element.body)
    return roots


def _paragraphs(root) -> list[Any]:
    return list(root.iter(qn("w:p")))


def _runs(paragraph) -> list[Run]:
    return [Run(r, None) for r in paragraph.findall(qn("w:r"))]


def _text(paragraph) -> str:
    return "".join(run.text for run in _runs(paragraph))


def _replace(paragraph, resolve: Callable[[str], str | None]) -> None:
    """Replace every placeholder ``resolve`` knows in one paragraph."""
    runs = _runs(paragraph)
    if not runs:
        return
    texts = [run.text for run in runs]
    full = "".join(texts)
    if "{{" not in full:
        return
    changes: list[tuple[int, int, str]] = []
    for match in PLACEHOLDER.finditer(full):
        value = resolve(match.group(1))
        if value is not None:
            changes.append((match.start(), match.end(), value))
    if not changes:
        return
    touched: set[int] = set()
    # Right to left, so the offsets of the earlier matches still hold.
    for start, end, value in reversed(changes):
        bounds = []
        position = 0
        for text in texts:
            bounds.append((position, position + len(text)))
            position += len(text)
        first = next(i for i, (s, e) in enumerate(bounds) if s <= start < e)
        last = next(i for i, (s, e) in enumerate(bounds) if s < end <= e)
        s_first, _ = bounds[first]
        s_last, _ = bounds[last]
        if first == last:
            text = texts[first]
            texts[first] = text[: start - s_first] + value + text[end - s_first :]
        else:
            texts[first] = texts[first][: start - s_first] + value
            for middle in range(first + 1, last):
                texts[middle] = ""
                touched.add(middle)
            texts[last] = texts[last][end - s_last :]
            touched.add(last)
        touched.add(first)
    for index in touched:
        runs[index].text = texts[index]


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def inspect(data: bytes) -> dict[str, list[str]]:
    """The fields and the line-item columns a template asks for, in the
    order a reader meets them."""
    document = _open(data)
    fields: list[str] = []
    columns: list[str] = []
    for root in _roots(document):
        for paragraph in _paragraphs(root):
            for match in PLACEHOLDER.finditer(_text(paragraph)):
                name = match.group(1)
                if name.startswith(ITEMS_PREFIX):
                    column = name[len(ITEMS_PREFIX) :]
                    if column not in columns:
                        columns.append(column)
                elif "." not in name and name not in fields:
                    fields.append(name)
    return {"fields": fields, "item_columns": columns}


def fill(data: bytes, values: dict[str, Any], items: Iterable[dict[str, Any]]) -> bytes:
    """The template with every known placeholder replaced and each repeating
    row cloned once per item."""
    document = _open(data)
    items = list(items or [])

    def field(name: str) -> str | None:
        if name.startswith(ITEMS_PREFIX):
            return None
        return _as_text(values.get(name))

    for root in _roots(document):
        # Rows first: a repeating row is cloned with its own item's values
        # before the ordinary fields are filled everywhere.
        for row in list(root.iter(qn("w:tr"))):
            if ITEMS_PREFIX not in "".join(_text(p) for p in _paragraphs(row)):
                continue
            for item in items:
                clone = copy.deepcopy(row)

                def resolve(name: str, item: dict[str, Any] = item) -> str | None:
                    if name.startswith(ITEMS_PREFIX):
                        value = item.get(name[len(ITEMS_PREFIX) :])
                        return "" if value is None else _as_text(value)
                    return field(name)

                for paragraph in _paragraphs(clone):
                    _replace(paragraph, resolve)
                row.addprevious(clone)
            row.getparent().remove(row)
        for paragraph in _paragraphs(root):
            _replace(paragraph, field)
    return _save(document)


def docx_from_text(text: str) -> bytes:
    """A plain template from plain text: a paragraph per line, and the lines
    that carry ``{{items.…}}`` (cells split on ``|`` or a tab) as a table
    with a heading row. For a Google Doc read as text when it cannot be
    exported as Word."""
    document = docx.Document()
    lines = (text or "").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if "{{" + ITEMS_PREFIX in line.replace("{{ ", "{{"):
            block = []
            while index < len(lines) and ITEMS_PREFIX in lines[index]:
                block.append(
                    [c.strip() for c in re.split(r"\s*[|\t]\s*", lines[index])]
                )
                index += 1
            width = max(len(cells) for cells in block)
            table = document.add_table(rows=1, cols=width)
            table.style = "Table Grid"
            for cell, raw in zip(table.rows[0].cells, block[0]):
                match = PLACEHOLDER.search(raw)
                label = match.group(1).split(".", 1)[-1] if match else raw
                cell.text = label.replace("_", " ").title()
            for cells in block:
                row = table.add_row()
                for cell, raw in zip(row.cells, cells):
                    cell.text = raw
            continue
        document.add_paragraph(line)
        index += 1
    return _save(document)


def text_of(data: bytes) -> tuple[list[str], list[list[list[str]]]]:
    """Paragraph text and tables (rows of cell text) in body order, for the
    fallback PDF and for reading a document back."""
    document = _open(data)
    paragraphs = [p.text for p in document.paragraphs]
    tables = [
        [[cell.text for cell in row.cells] for row in table.rows]
        for table in document.tables
    ]
    return paragraphs, tables


__all__ = [
    "ITEMS_PREFIX",
    "PLACEHOLDER",
    "TemplateError",
    "docx_from_text",
    "fill",
    "inspect",
    "text_of",
]
