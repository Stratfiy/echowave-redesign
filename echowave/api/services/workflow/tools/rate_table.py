"""Look a price up in an operator's rate card, instead of reading it off a prompt.

A rate card is a grid: a destination across the top, a weight or a quantity down
the side, and a number where they meet. Pasting that grid into an agent's prompt
and asking a language model to read a cell out of it does not work, and the way
it fails is the expensive way -- a confident, well-formed, wrong number.

Two different models made the same mistake on the same card within an hour: one
quoted a parcel to Dubai from the Zone 4 column when Dubai is Zone 1, and the
other did it again on the documents table, quoting Rs1,202 where the card says
Rs901. Both found the right row. Both slid a column. Nothing in the transcript
looks wrong, which is why a caller would have paid it.

So the number stops being something the model reads and becomes something it is
told. The model supplies what the caller said -- a destination, a weight, a kind
of shipment -- and this returns one figure, or refuses. It never interpolates,
never rounds down, and never picks a nearby cell when the exact one is missing.

**Generic on purpose.** Nothing here knows about couriers. A card is a set of
grids keyed by variant, a band down the side and a series across the top, and
that shape fits freight, insurance bands, tiered subscriptions and room rates
just as well. Everything specific -- what the columns are called, which
destinations map to which column, what happens past the last row -- comes from
the operator's own configuration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


class RateLookupError(Exception):
    """The card cannot answer this. Raised rather than returning a near miss."""


def normalise(text: Any) -> str:
    """Fold a caller's wording to something a lookup key can match.

    Lowercase, strip anything that is not a letter or a digit, collapse spaces.
    "U.A.E.", "uae" and "UAE " are one destination; a card that distinguished
    them would be a card nobody could hit from speech.
    """
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


@dataclass(frozen=True)
class RateResult:
    """One figure, and enough provenance to check it without the card."""

    amount: float
    currency: str
    series: str
    band: float
    variant: str
    #: True when the amount is a per-unit rate multiplied out, rather than a
    #: cell read straight from the grid. The caller says it differently.
    metered: bool = False
    per_unit: float | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        out = {
            "amount": self.amount,
            "currency": self.currency,
            "series": self.series,
            "band": self.band,
            "variant": self.variant,
            "metered": self.metered,
        }
        if self.per_unit is not None:
            out["per_unit"] = self.per_unit
        if self.notes:
            out["notes"] = list(self.notes)
        return out


def _grid_bands(grid: dict) -> list[float]:
    try:
        return sorted(float(band) for band in grid)
    except (TypeError, ValueError) as exc:
        raise RateLookupError(f"Rate card has a non-numeric band: {exc}") from exc


def resolve_series(config: dict, destination: str) -> str:
    """Which column of the card this destination sits in.

    Tried in the order an operator would expect: the destination as written, the
    alias table, then the destination as a bare column name. A miss raises --
    guessing a column is the exact defect this module exists to remove.
    """
    wanted = normalise(destination)
    if not wanted:
        raise RateLookupError("No destination given.")

    # Two spellings per key: the folded one, and the same with spaces closed up.
    # Speech recognition renders an acronym half a dozen ways -- "UAE", "U.A.E.",
    # "U A E" -- and folding punctuation alone turns the last two into "u a e",
    # which matches nothing. Closing the gaps makes all three one key.
    aliases: dict[str, str] = {}
    for key, value in (config.get("aliases") or {}).items():
        folded = normalise(key)
        aliases.setdefault(folded, str(value))
        aliases.setdefault(folded.replace(" ", ""), str(value))

    for candidate in (wanted, wanted.replace(" ", "")):
        if candidate in aliases:
            return aliases[candidate]

    known: dict[str, str] = {}
    for name in _series_names(config):
        folded = normalise(name)
        known.setdefault(folded, str(name))
        known.setdefault(folded.replace(" ", ""), str(name))

    for candidate in (wanted, wanted.replace(" ", "")):
        if candidate in known:
            return known[candidate]

    raise RateLookupError(
        f"'{destination}' is not on this rate card. "
        "Ask the caller to confirm the destination."
    )


def _series_names(config: dict) -> set[str]:
    names: set[str] = set()
    for grid in (config.get("grids") or {}).values():
        for row in grid.values():
            names.update(str(key) for key in row)
    return names


def _resolve_variant(
    config: dict, variant: str | None, band: float
) -> tuple[str, tuple]:
    """Which grid to read, after the card's own crossover rules.

    A courier's document rate stops at 2 kg and heavier documents price as
    parcels. That rule lived in a prompt and every model had to remember it on
    every call; here it is data, applied before anything is read.
    """
    grids = config.get("grids") or {}
    if not grids:
        raise RateLookupError("Rate card has no grids configured.")

    if variant is None:
        chosen = config.get("default_variant") or next(iter(grids))
    else:
        wanted = normalise(variant)
        matches = [name for name in grids if normalise(name) == wanted]
        if not matches:
            raise RateLookupError(
                f"'{variant}' is not a kind this rate card prices. "
                f"It has: {', '.join(sorted(grids))}."
            )
        chosen = matches[0]

    notes: tuple[str, ...] = ()
    crossover = (config.get("crossovers") or {}).get(chosen)
    if crossover:
        above = float(crossover["above_band"])
        target = str(crossover["use"])
        if band > above and target in grids:
            notes = (f"Above {above:g}, {chosen} is priced as {target} on this card.",)
            chosen = target

    return chosen, notes


def _overflow_amount(config: dict, series: str, band: float) -> RateResult | None:
    """Past the last row, a card usually meters per unit rather than per band."""
    overflow = config.get("overflow")
    if not overflow:
        return None
    if band <= float(overflow.get("from", 0)):
        return None

    for tier in overflow.get("tiers") or []:
        upto = tier.get("upto")
        if upto is None or band <= float(upto):
            rates = tier.get("per_unit") or {}
            if series not in rates:
                raise RateLookupError(
                    f"This card has no per-unit rate for {series} above "
                    f"{overflow.get('from')}."
                )
            per_unit = float(rates[series])
            return RateResult(
                amount=round(per_unit * band, 2),
                currency=str(config.get("currency") or ""),
                series=series,
                band=band,
                variant="overflow",
                metered=True,
                per_unit=per_unit,
                notes=(
                    f"Charged per unit on the whole amount at {per_unit:g} "
                    f"a unit, not from the banded table.",
                ),
            )
    raise RateLookupError(f"{band:g} is beyond every tier on this rate card.")


def lookup_rate(
    config: dict,
    *,
    destination: str,
    band: float,
    variant: str | None = None,
) -> RateResult:
    """The one figure this card gives for this destination, size and kind.

    ``band`` always rounds **up** to the next row the card actually lists. A card
    that rounds down under-quotes, and an under-quote is the error a customer
    only finds on the invoice.
    """
    try:
        band = float(band)
    except (TypeError, ValueError):
        raise RateLookupError(f"'{band}' is not a number.") from None
    if band <= 0:
        raise RateLookupError("Weight or quantity has to be above zero.")

    series = resolve_series(config, destination)
    variant_name, notes = _resolve_variant(config, variant, band)
    grid = (config.get("grids") or {})[variant_name]

    bands = _grid_bands(grid)
    if not bands:
        raise RateLookupError(f"The {variant_name} grid on this card is empty.")

    if band > bands[-1]:
        metered = _overflow_amount(config, series, band)
        if metered is not None:
            return metered
        raise RateLookupError(
            f"{band:g} is above the heaviest row on this card ({bands[-1]:g})."
        )

    row_band = next(candidate for candidate in bands if candidate >= band)
    row = grid[_band_key(grid, row_band)]
    if series not in row:
        raise RateLookupError(f"This card has no column for {series} at {row_band:g}.")

    if row_band != band:
        notes = notes + (f"Rounded up to the {row_band:g} band, as the card lists it.",)

    return RateResult(
        amount=float(row[series]),
        currency=str(config.get("currency") or ""),
        series=series,
        band=row_band,
        variant=variant_name,
        notes=notes,
    )


def _band_key(grid: dict, band: float) -> str:
    """The key this grid stores that band under, whatever it looks like."""
    for key in grid:
        if float(key) == band:
            return key
    raise RateLookupError(f"Band {band:g} vanished from the grid.")


def get_rate_table_tools(config: dict) -> list[dict[str, Any]]:
    """The function the model sees, described in the operator's own words."""
    labels = config.get("labels") or {}
    variants = sorted((config.get("grids") or {}).keys())
    name = str(labels.get("function_name") or "lookup_rate")

    variant_description = labels.get("variant") or "Which kind of item to price"
    if variants:
        variant_description = f"{variant_description}. One of: {', '.join(variants)}."

    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": str(
                    labels.get("description")
                    or "Look up the exact price on the rate card. Always call this "
                    "before quoting a price. Never state a price from memory."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "destination": {
                            "type": "string",
                            "description": str(
                                labels.get("destination")
                                or "Where it is going, as the caller said it"
                            ),
                        },
                        "band": {
                            "type": "number",
                            "description": str(
                                labels.get("band")
                                or "The weight or quantity, as a number"
                            ),
                        },
                        "variant": {
                            "type": "string",
                            "description": str(variant_description),
                        },
                    },
                    "required": ["destination", "band"],
                },
            },
        }
    ]
