"""What the three HTTP clients share: one client factory and the reading of
a vendor's refusal into a sentence for the person.

The vendor's own words are kept where they help ("the prompt was blocked by
the safety system") and dropped where they could carry the key: a refused
key is answered with our own sentence, never the vendor's, because OpenAI's
says "Incorrect API key provided: sk-...abcd".
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from api import constants
from api.services.images.providers.base import ImageProviderError, short

#: Anything that looks like a secret, whatever vendor it is from.
_SECRETS = re.compile(
    r"(sk-[A-Za-z0-9_\-]{6,}|AIza[0-9A-Za-z_\-]{10,}|ABSK[A-Za-z0-9+/=]{10,}"
    r"|bedrock-api-key-[A-Za-z0-9+/=_\-]{6,}|Bearer\s+\S+)"
)

#: Tests swap the transport; production uses the default.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """For tests: route every provider request through ``transport``."""
    global _transport
    _transport = transport


def client(timeout: float | None = None) -> httpx.AsyncClient:
    seconds = timeout or constants.IMAGE_TIMEOUT_SECONDS
    return httpx.AsyncClient(
        timeout=httpx.Timeout(seconds, connect=10.0),
        transport=_transport,
    )


def scrub(text: str, api_key: str | None = None) -> str:
    text = str(text or "")
    if api_key:
        text = text.replace(api_key, "••••")
    return short(_SECRETS.sub("••••", text))


def vendor_message(response: httpx.Response) -> str:
    """The error text a vendor put in its JSON body, or the raw body."""
    try:
        body: Any = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if isinstance(error, str):
            return error
        for key in ("message", "Message", "detail"):
            if body.get(key):
                return str(body[key])
    return str(body)


def raise_for(
    response: httpx.Response, *, label: str, api_key: str | None, own_key: bool
) -> None:
    """Turn a vendor's non-2xx answer into an ImageProviderError."""
    if response.status_code < 400:
        return
    said = scrub(vendor_message(response), api_key)
    lowered = said.lower()
    whose = "Your" if own_key else "Decibyl's"
    if (
        response.status_code in (401, 403)
        or "api key not valid" in lowered
        or ("api_key_invalid" in lowered)
    ):
        if response.status_code == 403 and own_key and "verif" in lowered:
            raise ImageProviderError(
                "auth",
                f"{label} refused this key for images: {said} "
                "Check the account on the card.",
            )
        message = f"{whose} {label} key was refused."
        if own_key:
            message += " Check it on the card and connect it again."
        raise ImageProviderError("auth", message)
    if response.status_code == 429:
        raise ImageProviderError(
            "quota",
            f"{label} says {whose.lower()} key is over its limit right now: {said}",
        )
    if response.status_code >= 500:
        raise ImageProviderError(
            "unavailable", f"{label} could not make the image just now ({said})."
        )
    if any(word in lowered for word in ("safety", "policy", "blocked", "moderation")):
        raise ImageProviderError(
            "refused", f"{label} declined to make that image: {said}"
        )
    raise ImageProviderError(
        "bad_request", f"{label} did not accept the request: {said}"
    )


async def send(
    call: Callable[[], Awaitable[httpx.Response]], *, label: str
) -> httpx.Response:
    """Run one request, once, turning transport failures into provider errors."""
    try:
        return await call()
    except httpx.TimeoutException as exc:
        raise ImageProviderError(
            "timeout",
            f"{label} took too long to make the image. Nothing was retried; "
            "ask again when you are ready.",
        ) from exc
    except httpx.HTTPError as exc:
        raise ImageProviderError(
            "unavailable", f"{label} could not be reached ({type(exc).__name__})."
        ) from exc
