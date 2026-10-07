"""An app's answers, read into one shape -- and checked.

What an ordering server returns is outside content: it decides the prices
the card shows. So a quote is not just read, it is checked: every line's
total is its quantity times its price, the charges and the discount add up
to the total, nothing is negative, and the quantities are the ones asked
for. A bill that does not add up is refused with that reason; it never
becomes a card a person might approve without doing the sum themselves.

Money is held in paise (integers). A field named ``*_paise`` is taken as
paise; any other amount is taken as rupees.
"""

from __future__ import annotations

import re
from typing import Any

from api.services.reach import safety

MAX_LINES = 30
MAX_QUANTITY = 50
#: A bill over this (₹50,000) is not a food or grocery order; refused.
MAX_TOTAL_PAISE = 5_000_000
#: Rounding slack when an app works in rupees with decimals.
TOLERANCE_PAISE = 2


class BadQuote(ValueError):
    """The app's quote cannot be shown as a card, with the reason."""


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def paise(data: dict[str, Any], *names: str) -> int | None:
    """The first amount under any of ``names`` (or ``name_paise``), in paise."""
    for name in names:
        value = data.get(f"{name}_paise")
        if value is not None:
            try:
                return int(round(float(value)))
            except (TypeError, ValueError):
                return None
        value = data.get(name)
        if value is not None:
            if isinstance(value, dict):
                inner = paise(value, "amount", "value")
                if inner is not None:
                    return inner
                continue
            try:
                return int(
                    round(float(str(value).replace(",", "").replace("₹", "")) * 100)
                )
            except (TypeError, ValueError):
                return None
    return None


def text(value: Any, limit: int = 120) -> str:
    return safety.clean_text(value, limit).replace("\n", " ").strip()


def mask(label: str) -> str:
    """No run of five or more digits survives into a label we keep."""
    return re.sub(
        r"\d{5,}", lambda m: "•" * (len(m.group()) - 4) + m.group()[-4:], label
    )


def _rows(data: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    return []


def search_results(data: Any) -> list[dict[str, Any]]:
    out = []
    for row in _rows(data, "results", "items", "products", "restaurants", "dishes")[
        :40
    ]:
        item_id = _first(row, "item_id", "id", "product_id", "dish_id")
        name = _first(row, "name", "title", "item_name")
        if item_id is None or not name:
            continue
        out.append(
            {
                "store_id": text(
                    _first(row, "store_id", "restaurant_id", "res_id") or "", 80
                ),
                "store_name": text(
                    _first(row, "store_name", "restaurant_name", "restaurant") or ""
                ),
                "item_id": text(item_id, 80),
                "name": text(name),
                "price_paise": paise(row, "price", "unit_price"),
                "available": bool(row.get("available", True)),
            }
        )
    return out


def addresses(data: Any) -> list[dict[str, Any]]:
    out = []
    for row in _rows(data, "addresses", "results")[:10]:
        address_id = _first(row, "id", "address_id")
        if address_id is None:
            continue
        out.append(
            {
                "id": text(address_id, 80),
                "label": text(
                    _first(row, "label", "name", "tag") or "Saved address", 60
                ),
                "line": text(
                    _first(row, "line", "address", "display_address") or "", 200
                ),
            }
        )
    return out


def offers(data: Any) -> list[dict[str, Any]]:
    out = []
    for row in _rows(data, "offers", "coupons", "results")[:10]:
        code = _first(row, "code", "coupon_code", "id")
        if not code:
            continue
        out.append(
            {
                "code": text(code, 40),
                "description": text(_first(row, "description", "title") or "", 160),
                "saving_paise": paise(row, "saving", "discount", "amount"),
            }
        )
    return out


def quote(data: Any, asked: list[dict[str, Any]]) -> dict[str, Any]:
    """The cart as quoted, checked line by line. Raises :class:`BadQuote`."""
    if not isinstance(data, dict):
        raise BadQuote("The app did not return a bill.")
    body = data.get("cart") if isinstance(data.get("cart"), dict) else data
    lines = _rows(body, "items", "lines")
    if not lines:
        raise BadQuote("The app returned an empty cart.")
    if len(lines) > MAX_LINES:
        raise BadQuote("That is more lines than one order can carry.")
    wanted = {str(a.get("item_id")): int(a.get("quantity") or 0) for a in asked}
    items = []
    subtotal = 0
    for line in lines:
        item_id = text(_first(line, "item_id", "id", "product_id") or "", 80)
        quantity = int(_first(line, "quantity", "qty") or 0)
        unit = paise(line, "unit_price", "price")
        total = paise(line, "line_total", "total")
        if total is None and unit is not None:
            total = unit * quantity
        if not item_id or quantity < 1 or unit is None or total is None:
            raise BadQuote(
                "A line in the app's bill is missing its item, quantity or price."
            )
        if quantity > MAX_QUANTITY or unit < 0 or total < 0:
            raise BadQuote(
                "A line in the app's bill has an impossible quantity or price."
            )
        if abs(unit * quantity - total) > TOLERANCE_PAISE:
            raise BadQuote("A line in the app's bill does not add up.")
        if wanted and wanted.get(item_id) != quantity:
            raise BadQuote(
                "The app's cart does not match what was asked for (an item or a "
                "quantity changed)."
            )
        subtotal += total
        items.append(
            {
                "item_id": item_id,
                "name": text(_first(line, "name", "title") or item_id),
                "quantity": quantity,
                "unit_price_paise": unit,
                "line_total_paise": total,
            }
        )
    if wanted and len(items) != len(wanted):
        raise BadQuote("The app's cart does not match what was asked for.")
    charges = []
    for row in _rows(body, "charges", "fees", "taxes"):
        amount = paise(row, "amount", "value")
        if amount is None or amount < 0:
            raise BadQuote("A charge in the app's bill has no amount.")
        charges.append(
            {
                "label": text(_first(row, "label", "name") or "Charge", 60),
                "amount_paise": amount,
            }
        )
    discount = paise(body, "discount") or 0
    total = paise(body, "total", "grand_total", "to_pay")
    if total is None:
        raise BadQuote("The app's bill has no total.")
    if discount < 0 or total <= 0 or total > MAX_TOTAL_PAISE:
        raise BadQuote("The app's total is not one a card can show.")
    if (
        abs(subtotal + sum(c["amount_paise"] for c in charges) - discount - total)
        > TOLERANCE_PAISE
    ):
        raise BadQuote("The app's bill does not add up to its total.")
    store = body.get("store") if isinstance(body.get("store"), dict) else {}
    address = body.get("address") if isinstance(body.get("address"), dict) else {}
    payment = body.get("payment") if isinstance(body.get("payment"), dict) else {}
    if not address:
        raise BadQuote("The app's bill does not say where it will be delivered.")
    if not payment:
        raise BadQuote("The app's bill does not say how it will be paid.")
    return {
        "cart_id": text(_first(body, "cart_id", "id") or "", 120) or None,
        "store": {
            "id": text(_first(store, "id", "store_id") or "", 80),
            "name": text(_first(store, "name") or "", 120),
        },
        "items": items,
        "charges": charges,
        "discount_paise": discount,
        "subtotal_paise": subtotal,
        "total_paise": total,
        "currency": text(body.get("currency") or "INR", 8),
        "coupon": text(body.get("coupon") or "", 40) or None,
        "address": {
            "id": text(_first(address, "id", "address_id") or "", 80),
            "label": text(_first(address, "label", "tag") or "Address", 60),
            "line": text(_first(address, "line", "address") or "", 200),
        },
        "payment": {
            "method": text(_first(payment, "method", "id") or "", 40),
            "label": mask(
                text(_first(payment, "label", "name") or "Pay on the app", 80)
            ),
        },
        "offers": offers(body.get("offers") or []),
    }


def placed(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise BadQuote("The app did not say whether the order was placed.")
    order_id = _first(data, "order_id", "id")
    if not order_id:
        raise BadQuote("The app did not return an order number.")
    link = _first(data, "payment_link", "payment_url")
    link = (
        text(link, 500)
        if isinstance(link, str) and link.startswith("https://")
        else None
    )
    return {
        "order_id": text(order_id, 120),
        "status": text(_first(data, "status") or "placed", 40),
        "payment_link": link,
        "eta": text(_first(data, "eta", "eta_text") or "", 60) or None,
    }


def rupees(amount_paise: int | None) -> str:
    if amount_paise is None:
        return "—"
    whole, part = divmod(int(amount_paise), 100)
    return f"₹{whole:,}" + (f".{part:02d}" if part else "")


__all__ = [
    "BadQuote",
    "addresses",
    "mask",
    "offers",
    "paise",
    "placed",
    "quote",
    "rupees",
    "search_results",
]
