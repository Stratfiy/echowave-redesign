"""OpenAI image generation: the Images API with a GPT Image model.

Read off https://developers.openai.com/api/docs/guides/image-generation on
8 October 2026:

* ``POST https://api.openai.com/v1/images/generations`` with ``model`` and
  ``prompt`` in a JSON body; ``n`` makes several images in one request;
* ``POST https://api.openai.com/v1/images/edits`` as multipart form data,
  with reference images as repeated ``image[]`` fields -- how a logo, a
  product photo or the image being edited is sent;
* models: ``gpt-image-2.5-flare`` (fast everyday generation, the default
  here; ``IMAGE_OPENAI_MODEL`` overrides it) and ``gpt-image-2.5-sunburst``;
* ``size`` is WxH: 1024x1024, 1536x1024, 1024x1536, or custom with both
  sides multiples of 16, ratio within 1:3..3:1, 655,360 to 8,294,400 pixels;
* ``quality`` low/medium/high (and more on 2.5); ``output_format`` png by
  default;
* the response is ``data[].b64_json``, with a ``usage`` object.
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
    ProviderResult,
    made,
)

BASE_URL = "https://api.openai.com/v1"
LABEL = "OpenAI"
_EXTENSION = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


class OpenAIImages:
    name = "openai"
    label = LABEL
    takes_references = True
    max_count = 4

    def model(self) -> str:
        return constants.IMAGE_OPENAI_MODEL

    async def generate(
        self, request: ImageRequest, *, api_key: str | None, own_key: bool = True
    ) -> ProviderResult:
        if not api_key:
            raise ImageProviderError("auth", "There is no OpenAI key to use.")
        count = max(1, min(request.count, self.max_count))
        headers = {"Authorization": f"Bearer {api_key}"}
        inputs = ((request.base,) if request.base else ()) + request.references
        async with common.client() as client:
            if inputs:
                files = [
                    (
                        "image[]",
                        (
                            f"reference-{index}.{_EXTENSION.get(image.mime_type, 'png')}",
                            image.data,
                            image.mime_type,
                        ),
                    )
                    for index, image in enumerate(inputs)
                ]
                data = {
                    "model": self.model(),
                    "prompt": request.prompt,
                    "n": str(count),
                    "size": request.format.openai_size,
                    "quality": "medium",
                }
                response = await common.send(
                    lambda: client.post(
                        f"{BASE_URL}/images/edits",
                        headers=headers,
                        data=data,
                        files=files,
                    ),
                    label=LABEL,
                )
            else:
                body = {
                    "model": self.model(),
                    "prompt": request.prompt,
                    "n": count,
                    "size": request.format.openai_size,
                    "quality": "medium",
                    "output_format": "png",
                }
                response = await common.send(
                    lambda: client.post(
                        f"{BASE_URL}/images/generations", headers=headers, json=body
                    ),
                    label=LABEL,
                )
        common.raise_for(response, label=LABEL, api_key=api_key, own_key=own_key)
        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise ImageProviderError(
                "unavailable", "OpenAI answered with something that was not an image."
            ) from exc
        images = [
            made(base64.b64decode(item["b64_json"]))
            for item in payload.get("data") or []
            if isinstance(item, dict) and item.get("b64_json")
        ]
        if not images:
            raise ImageProviderError("refused", "OpenAI did not return an image.")
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return ProviderResult(
            images=images,
            model=self.model(),
            usage=usage,
            note=""
            if len(images) == count
            else f"OpenAI made {len(images)} of {count}.",
        )

    async def check_key(self, api_key: str) -> KeyCheck:
        """List the models: free, and a wrong key is a 401."""
        try:
            async with common.client(timeout=15) as client:
                response = await client.get(
                    f"{BASE_URL}/models", headers={"Authorization": f"Bearer {api_key}"}
                )
        except Exception:
            return KeyCheck("unverified", "OpenAI could not be reached to check it.")
        if response.status_code == 200:
            return KeyCheck("verified", "OpenAI accepted the key.")
        if response.status_code in (401, 403):
            return KeyCheck(
                "rejected", "OpenAI refused that key. Check you copied all of it."
            )
        return KeyCheck("unverified", "OpenAI could not check the key just now.")
