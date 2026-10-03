"""Photos for Studio sites, from Openverse.

A business site without pictures looks unfinished, and a model asked for a
picture invents an image URL that 404s. Openverse (openverse.org, run by
WordPress) indexes openly licensed images and needs no key, so a search here
returns real photos with the licence each one carries.

Only licences that allow commercial use and modification are asked for: a
clinic's site is commercial, and a photo cropped into a hero is modified.
Several of those licences (CC BY, CC BY-SA) still require credit, so every
result carries its attribution, and the site's footer has a place for it
(``site.credits``). A result with no attribution is dropped rather than
shown: the one thing worse than no photo is one used against its licence.
"""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

OPENVERSE_URL = "https://api.openverse.org/v1/images/"
TIMEOUT_SECS = 15.0
MAX_RESULTS = 8
#: Narrower than this looks soft as a hero or a card on a laptop.
MIN_WIDTH = 800
#: Licences that require no credit at all.
NO_CREDIT_LICENCES = frozenset({"cc0", "pdm"})


class ImageSearchError(RuntimeError):
    pass


def _result(item: dict[str, Any]) -> dict[str, Any] | None:
    url = item.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    licence = str(item.get("license") or "").lower()
    attribution = (item.get("attribution") or "").strip()
    if licence not in NO_CREDIT_LICENCES and not attribution:
        return None
    creator = item.get("creator") or "unknown"
    return {
        "url": url,
        "thumbnail": item.get("thumbnail"),
        "width": item.get("width"),
        "height": item.get("height"),
        "title": (item.get("title") or "")[:120],
        "licence": f"{licence.upper()} {item.get('license_version') or ''}".strip(),
        "needs_credit": licence not in NO_CREDIT_LICENCES,
        "credit": {
            "text": f"{(item.get('title') or 'Photo')[:80]} by {creator}",
            "url": item.get("foreign_landing_url") or url,
        },
    }


async def search(
    query: str,
    *,
    orientation: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Photos for ``query`` that may be used commercially, best first."""
    query = (query or "").strip()[:120]
    if not query:
        raise ImageSearchError("Say what the photo should show.")
    params: dict[str, Any] = {
        "q": query,
        "license_type": "commercial,modification",
        "category": "photograph",
        "mature": "false",
        "page_size": 20,
    }
    if orientation in ("wide", "tall", "square"):
        params["aspect_ratio"] = orientation
    owned = client is None
    http = client or httpx.AsyncClient(timeout=TIMEOUT_SECS)
    try:
        response = await http.get(
            OPENVERSE_URL,
            params=params,
            headers={"User-Agent": "DecibylStudio/1.0 (+https://decibyl.ai)"},
        )
    except httpx.HTTPError as exc:
        logger.warning("Openverse search failed: {}", exc)
        raise ImageSearchError("The photo library could not be reached.") from exc
    finally:
        if owned:
            await http.aclose()
    if response.status_code != 200:
        raise ImageSearchError(
            f"The photo library answered {response.status_code}; try again shortly."
        )
    items = (response.json() or {}).get("results") or []
    results = [r for r in (_result(i) for i in items if isinstance(i, dict)) if r]
    wide_enough = [r for r in results if (r.get("width") or 0) >= MIN_WIDTH]
    return (wide_enough or results)[:MAX_RESULTS]
