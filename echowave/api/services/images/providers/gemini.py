"""Google Gemini image generation, on the Interactions API.

Read off https://ai.google.dev/gemini-api/docs/image-generation on
8 October 2026, which documents image generation on
``POST /v1beta/interactions`` (not ``generateContent``):

* auth: the ``x-goog-api-key`` header;
* ``model``: ``gemini-nano-banana-2.1`` is the recommended model
  (``IMAGE_GEMINI_MODEL`` overrides it);
* ``input``: an array of parts, ``{"type": "text", "text": ...}`` and
  ``{"type": "image", "mime_type": ..., "data": <base64>}`` -- up to 14
  reference images may be mixed in, which is how a logo or a product photo
  is sent;
* ``response_format``: ``{"type": "image", "mime_type", "aspect_ratio",
  "image_size"}`` with ``image_size`` one of ``1K``/``2K``/``4K``;
* the response's ``steps`` hold ``model_output`` steps whose ``content``
  blocks of ``"type": "image"`` carry base64 ``data``; the SDK's
  ``output_image`` is the last of them, and that is the one taken here.

One image per request, so options are separate requests.
"""

from __future__ import annotations

import base64
from typing import Any

from api import constants
from api.services.images.providers import common
from api.services.images.providers.base import (
    ImageProviderError,
    ImageRequest,
    KeyCheck,
    MadeImage,
    ProviderResult,
    made,
)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
LABEL = "Gemini"


def _images_in(body: dict[str, Any]) -> list[MadeImage]:
    """Every final image block in a response, last one last.

    Read from ``model_output`` steps only: a thinking model may show interim
    "thought images", which are drafts, not the answer. A response in the
    older ``candidates`` shape is read too, so a deployment pointed at a
    model served that way still gets its image rather than an empty card.
    """
    found: list[MadeImage] = []
    output_image = body.get("output_image")
    if isinstance(output_image, dict) and output_image.get("data"):
        found.append(
            made(
                base64.b64decode(output_image["data"]),
                output_image.get("mime_type"),
            )
        )
        return found
    for step in body.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for block in step.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "image":
                data = block.get("data")
                if data:
                    found.append(made(base64.b64decode(data), block.get("mime_type")))
    if found:
        return found
    for candidate in body.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline, dict) and inline.get("data"):
                found.append(
                    made(
                        base64.b64decode(inline["data"]),
                        inline.get("mimeType") or inline.get("mime_type"),
                    )
                )
    return found


def _text_in(body: dict[str, Any]) -> str:
    """What the model said instead of drawing, for the person."""
    for step in body.get("steps") or []:
        for block in (step or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text") or "")
    return ""


class GeminiImages:
    name = "google"
    label = LABEL
    takes_references = True
    max_count = 4

    def model(self) -> str:
        return constants.IMAGE_GEMINI_MODEL

    def _body(self, request: ImageRequest) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
        for image in ((request.base,) if request.base else ()) + request.references:
            parts.append(
                {
                    "type": "image",
                    "mime_type": image.mime_type,
                    "data": base64.b64encode(image.data).decode(),
                }
            )
        return {
            "model": self.model(),
            "input": parts,
            "response_format": {
                "type": "image",
                "mime_type": "image/png",
                "aspect_ratio": request.format.aspect,
                "image_size": "1K",
            },
            # A poster brief is the business's, not something to keep on
            # the vendor's side for later retrieval.
            "store": False,
        }

    async def _one(
        self, client, request: ImageRequest, api_key: str, own_key: bool
    ) -> tuple[MadeImage | None, dict[str, Any], str]:
        response = await common.send(
            lambda: client.post(
                f"{BASE_URL}/interactions",
                headers={"x-goog-api-key": api_key},
                json=self._body(request),
            ),
            label=LABEL,
        )
        common.raise_for(response, label=LABEL, api_key=api_key, own_key=own_key)
        try:
            body = response.json()
        except ValueError as exc:
            raise ImageProviderError(
                "unavailable", "Gemini answered with something that was not an image."
            ) from exc
        if body.get("status") == "failed":
            raise ImageProviderError(
                "refused",
                "Gemini did not make the image: "
                + common.scrub(str(body.get("errors") or "no reason given"), api_key),
            )
        images = _images_in(body)
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return (images[-1] if images else None), usage, _text_in(body)

    async def generate(
        self, request: ImageRequest, *, api_key: str | None, own_key: bool = True
    ) -> ProviderResult:
        if not api_key:
            raise ImageProviderError("auth", "There is no Gemini key to use.")
        results: list[MadeImage] = []
        usages: list[dict[str, Any]] = []
        said = ""
        async with common.client() as client:
            # One at a time: each is a charge, and if the first fails for a
            # reason the rest would share (a refused key), stop there rather
            # than spend the same refusal three more times.
            for _ in range(max(1, min(request.count, self.max_count))):
                try:
                    image, usage, text = await self._one(
                        client, request, api_key, own_key
                    )
                except ImageProviderError:
                    if results:
                        break
                    raise
                usages.append(usage)
                if image is not None:
                    results.append(image)
                elif text:
                    said = text
        if not results:
            raise ImageProviderError(
                "refused",
                "Gemini did not return an image"
                + (f": {common.scrub(said, api_key)}" if said else "."),
            )
        return ProviderResult(
            images=results,
            model=self.model(),
            usage={"requests": usages},
            note=""
            if len(results) == request.count
            else f"Gemini made {len(results)} of {request.count}.",
        )

    async def check_key(self, api_key: str) -> KeyCheck:
        """List one model: free, and a wrong key is refused."""
        try:
            async with common.client(timeout=15) as client:
                response = await client.get(
                    f"{BASE_URL}/models",
                    params={"pageSize": 1},
                    headers={"x-goog-api-key": api_key},
                )
        except Exception:
            return KeyCheck("unverified", "Gemini could not be reached to check it.")
        if response.status_code == 200:
            return KeyCheck("verified", "Gemini accepted the key.")
        if response.status_code in (400, 401, 403):
            return KeyCheck(
                "rejected", "Gemini refused that key. Check you copied all of it."
            )
        return KeyCheck("unverified", "Gemini could not check the key just now.")
