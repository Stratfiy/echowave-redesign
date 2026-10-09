"""Amazon Nova Canvas on Bedrock (``InvokeModel``).

Read off the Nova user guide on 8 October 2026
(https://docs.aws.amazon.com/nova/latest/userguide/image-gen-req-resp-structure.html
and .../image-gen-access.html):

* model ``amazon.nova-canvas-v1:0`` (``IMAGE_BEDROCK_MODEL`` overrides it),
  served in a few regions only (``IMAGE_BEDROCK_REGION``, us-east-1 default);
* ``TEXT_IMAGE`` with ``textToImageParams.text`` (1-1024 characters) and
  ``negativeText``; negations belong in ``negativeText``, not in ``text``;
* ``IMAGE_VARIATION`` with ``imageVariationParams.images`` (1-5 base64 PNG
  or JPEG) and ``similarityStrength`` 0.2-1.0 -- how a logo, a product photo
  or the image being edited is used;
* ``imageGenerationConfig``: ``width``/``height`` (multiples of 16, 320-4096,
  under 4,194,304 pixels), ``quality``, ``numberOfImages``;
* response ``{"images": [base64...], "error": "..."}``; images blocked by
  the safety filter are left out and ``error`` says so.

Two ways in. A workspace's own **Bedrock API key** is a bearer token on the
runtime's HTTPS endpoint (https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys-use.html),
so it needs no AWS SDK and no secret pair. The platform path, with
``IMAGE_BEDROCK_ENABLED``, signs with the box's own AWS role through boto3,
with retries switched off -- botocore retries throttles by default, and a
retried generation is a second charge.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any
from urllib.parse import quote

from api import constants
from api.services.images.providers import common
from api.services.images.providers.base import (
    ImageProviderError,
    ImageRequest,
    KeyCheck,
    ProviderResult,
    made,
)

LABEL = "Amazon Bedrock"
MAX_TEXT = 1024


def runtime_url() -> str:
    region = constants.IMAGE_BEDROCK_REGION
    return (
        f"https://bedrock-runtime.{region}.amazonaws.com/model/"
        f"{quote(constants.IMAGE_BEDROCK_MODEL, safe='')}/invoke"
    )


def _clip(text: str) -> str:
    text = " ".join((text or "").split())
    return text[:MAX_TEXT] if text else "A poster"


def body_for(request: ImageRequest, count: int) -> dict[str, Any]:
    width, height = request.format.nova_size
    config = {
        "width": width,
        "height": height,
        "quality": "standard",
        "numberOfImages": count,
    }
    inputs = ((request.base,) if request.base else ()) + request.references
    params: dict[str, Any] = {"text": _clip(request.prompt)}
    if request.avoid:
        params["negativeText"] = _clip(request.avoid)
    if inputs:
        return {
            "taskType": "IMAGE_VARIATION",
            "imageVariationParams": {
                **params,
                "images": [base64.b64encode(i.data).decode() for i in inputs[:5]],
                # An edit keeps the poster; a reference only guides it.
                "similarityStrength": 0.8 if request.base else 0.5,
            },
            "imageGenerationConfig": config,
        }
    return {
        "taskType": "TEXT_IMAGE",
        "textToImageParams": params,
        "imageGenerationConfig": config,
    }


def _result(payload: dict[str, Any], count: int, model: str) -> ProviderResult:
    images = [
        made(base64.b64decode(data))
        for data in payload.get("images") or []
        if isinstance(data, str) and data
    ]
    error = str(payload.get("error") or "")
    if not images:
        raise ImageProviderError(
            "refused",
            "Amazon Bedrock did not return an image"
            + (f": {common.scrub(error)}" if error else "."),
        )
    note = ""
    if len(images) < count:
        note = f"Amazon Bedrock made {len(images)} of {count}" + (
            f" ({common.scrub(error)})" if error else "."
        )
    return ProviderResult(
        images=images, model=model, usage={"images": len(images)}, note=note
    )


#: Tests swap this for a fake; production builds a boto3 client.
_boto_client_factory = None


def _boto_client():
    if _boto_client_factory is not None:
        return _boto_client_factory()
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime",
        region_name=constants.IMAGE_BEDROCK_REGION,
        config=Config(
            read_timeout=constants.IMAGE_TIMEOUT_SECONDS,
            connect_timeout=10,
            # ``total_max_attempts`` counts the first try; ``max_attempts``
            # counts retries, so 1 there would still mean two charges.
            retries={"total_max_attempts": 1, "mode": "standard"},
        ),
    )


class BedrockImages:
    name = "aws_bedrock"
    label = LABEL
    takes_references = True
    max_count = 4

    def model(self) -> str:
        return constants.IMAGE_BEDROCK_MODEL

    async def generate(
        self, request: ImageRequest, *, api_key: str | None, own_key: bool = True
    ) -> ProviderResult:
        count = max(1, min(request.count, self.max_count))
        body = body_for(request, count)
        if api_key:
            return await self._with_key(body, count, api_key, own_key)
        if not constants.IMAGE_BEDROCK_ENABLED:
            raise ImageProviderError("auth", "There is no Bedrock key to use.")
        return await self._with_role(body, count)

    async def _with_key(
        self, body: dict[str, Any], count: int, api_key: str, own_key: bool
    ) -> ProviderResult:
        async with common.client() as client:
            response = await common.send(
                lambda: client.post(
                    runtime_url(),
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Accept": "application/json",
                    },
                    json=body,
                ),
                label=LABEL,
            )
        common.raise_for(response, label=LABEL, api_key=api_key, own_key=own_key)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ImageProviderError(
                "unavailable", "Amazon Bedrock answered with something unreadable."
            ) from exc
        return _result(payload, count, self.model())

    async def _with_role(self, body: dict[str, Any], count: int) -> ProviderResult:
        def call() -> dict[str, Any]:
            response = _boto_client().invoke_model(
                modelId=self.model(),
                body=json.dumps(body),
                accept="application/json",
                contentType="application/json",
            )
            return json.loads(response["body"].read())

        try:
            payload = await asyncio.wait_for(
                asyncio.to_thread(call), timeout=constants.IMAGE_TIMEOUT_SECONDS + 5
            )
        except TimeoutError as exc:
            raise ImageProviderError(
                "timeout", "Amazon Bedrock took too long. Nothing was retried."
            ) from exc
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            said = common.scrub(str(exc))
            if code in ("AccessDeniedException", "UnrecognizedClientException"):
                raise ImageProviderError(
                    "auth", "Decibyl's Amazon Bedrock access was refused."
                ) from exc
            if code == "ThrottlingException":
                raise ImageProviderError(
                    "quota", f"Amazon Bedrock is busy right now: {said}"
                ) from exc
            if code == "ValidationException" and "content" in said.lower():
                raise ImageProviderError(
                    "refused", f"Amazon Bedrock declined to make that image: {said}"
                ) from exc
            raise ImageProviderError(
                "unavailable", f"Amazon Bedrock could not make the image: {said}"
            ) from exc
        return _result(payload, count, self.model())

    async def check_key(self, api_key: str) -> KeyCheck:
        """Ask the control plane about the model: free, and a wrong key is
        refused. API keys are good for Bedrock's control plane too."""
        region = constants.IMAGE_BEDROCK_REGION
        url = (
            f"https://bedrock.{region}.amazonaws.com/foundation-models/"
            f"{quote(constants.IMAGE_BEDROCK_MODEL, safe='')}"
        )
        try:
            async with common.client(timeout=15) as client:
                response = await client.get(
                    url, headers={"Authorization": f"Bearer {api_key}"}
                )
        except Exception:
            return KeyCheck(
                "unverified", "Amazon Bedrock could not be reached to check it."
            )
        if response.status_code == 200:
            return KeyCheck("verified", "Amazon Bedrock accepted the key.")
        if response.status_code in (401, 403):
            return KeyCheck(
                "rejected",
                "Amazon Bedrock refused that key. Use a Bedrock API key with "
                f"access to Nova Canvas in {region}.",
            )
        return KeyCheck("unverified", "Amazon Bedrock could not check the key now.")
