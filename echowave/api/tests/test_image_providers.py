"""The three image clients, against mocked HTTP (no real provider is called).

Each is held to the request shape its vendor documents (see the module
docstrings for where it was read), to reading the image out of the answer,
to turning a refusal into a sentence a person can act on without the key in
it, and to asking **once**: a retried generation is a second charge on
somebody's own key.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from api import constants
from api.services.images import formats, registry
from api.services.images.providers import bedrock, common
from api.services.images.providers.base import (
    ImageProviderError,
    ImageRequest,
    InputImage,
    image_size,
)
from api.services.images.providers.bedrock import BedrockImages
from api.services.images.providers.gemini import GeminiImages
from api.services.images.providers.openai import OpenAIImages

#: A real 2x3 PNG header (the IHDR is all image_size reads).
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x02\x00\x00\x00\x03"
    b"\x08\x06\x00\x00\x00" + b"\x00" * 16
)
B64 = base64.b64encode(PNG).decode()
SQUARE = formats.FORMATS["instagram_square"]
STORY = formats.FORMATS["story"]


class Recorder:
    """A mock transport that answers from a list and keeps every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def http():
    recorders: list[Recorder] = []

    def install(*responses) -> Recorder:
        recorder = Recorder(*responses)
        recorders.append(recorder)
        common.set_transport(httpx.MockTransport(recorder))
        return recorder

    yield install
    common.set_transport(None)


def _request(**kwargs) -> ImageRequest:
    return ImageRequest(prompt="A Diwali sale poster", format=SQUARE, **kwargs)


# --- Gemini ------------------------------------------------------------------


@pytest.mark.asyncio
class TestGemini:
    async def test_asks_the_interactions_api_once_per_option(self, http):
        body = {
            "status": "completed",
            "steps": [
                {"type": "thought", "content": [{"type": "image", "data": "eA=="}]},
                {
                    "type": "model_output",
                    "content": [
                        {"type": "image", "mime_type": "image/png", "data": B64}
                    ],
                },
            ],
            "usage": {"total_output_tokens": 1120},
        }
        recorder = http(httpx.Response(200, json=body))
        result = await GeminiImages().generate(
            ImageRequest(prompt="p", format=STORY, count=2), api_key="AIzaSECRETkey123"
        )
        assert len(recorder.requests) == 2
        sent = recorder.requests[0]
        assert sent.url.path == "/v1beta/interactions"
        assert sent.headers["x-goog-api-key"] == "AIzaSECRETkey123"
        payload = json.loads(sent.content)
        assert payload["model"] == constants.IMAGE_GEMINI_MODEL
        assert payload["response_format"] == {
            "type": "image",
            "mime_type": "image/png",
            "aspect_ratio": "9:16",
            "image_size": "1K",
        }
        assert payload["store"] is False
        # The thought image is a draft; the model_output one is the answer.
        assert [i.data for i in result.images] == [PNG, PNG]
        assert (result.images[0].width, result.images[0].height) == (2, 3)

    async def test_sends_a_reference_image_as_an_image_part(self, http):
        recorder = http(
            httpx.Response(
                200,
                json={
                    "steps": [
                        {
                            "type": "model_output",
                            "content": [{"type": "image", "data": B64}],
                        }
                    ]
                },
            )
        )
        await GeminiImages().generate(
            _request(references=(InputImage(PNG, "image/png"),)), api_key="k" * 20
        )
        parts = json.loads(recorder.requests[0].content)["input"]
        assert parts[0]["type"] == "text"
        assert parts[1] == {"type": "image", "mime_type": "image/png", "data": B64}

    async def test_a_refused_key_is_said_without_the_key_and_not_retried(self, http):
        recorder = http(
            httpx.Response(
                400,
                json={
                    "error": {
                        "message": "API key not valid. AIzaSECRETkey123",
                        "status": "INVALID_ARGUMENT",
                    }
                },
            )
        )
        with pytest.raises(ImageProviderError) as raised:
            await GeminiImages().generate(
                ImageRequest(prompt="p", format=SQUARE, count=3),
                api_key="AIzaSECRETkey123",
            )
        assert raised.value.kind == "auth"
        assert "Your Gemini key was refused" in raised.value.message
        assert "card" in raised.value.message
        assert "SECRET" not in raised.value.message
        assert len(recorder.requests) == 1

    async def test_a_timeout_is_said_and_not_retried(self, http):
        recorder = http(httpx.ReadTimeout("slow"))
        with pytest.raises(ImageProviderError) as raised:
            await GeminiImages().generate(_request(), api_key="k" * 20)
        assert raised.value.kind == "timeout"
        assert "Nothing was retried" in raised.value.message
        assert len(recorder.requests) == 1

    async def test_text_instead_of_an_image_is_a_refusal(self, http):
        http(
            httpx.Response(
                200,
                json={
                    "steps": [
                        {
                            "type": "model_output",
                            "content": [{"type": "text", "text": "I can't draw that."}],
                        }
                    ]
                },
            )
        )
        with pytest.raises(ImageProviderError) as raised:
            await GeminiImages().generate(_request(), api_key="k" * 20)
        assert raised.value.kind == "refused"
        assert "I can't draw that." in raised.value.message

    async def test_check_key(self, http):
        recorder = http(httpx.Response(200, json={"models": []}))
        assert (await GeminiImages().check_key("k" * 20)).outcome == "verified"
        assert recorder.requests[0].url.path == "/v1beta/models"
        http(httpx.Response(400, json={"error": {"message": "API key not valid"}}))
        assert (await GeminiImages().check_key("k" * 20)).outcome == "rejected"
        http(httpx.ConnectError("down"))
        assert (await GeminiImages().check_key("k" * 20)).outcome == "unverified"


# --- OpenAI ------------------------------------------------------------------


@pytest.mark.asyncio
class TestOpenAI:
    async def test_generations_with_n_and_the_formats_size(self, http):
        recorder = http(
            httpx.Response(
                200,
                json={
                    "data": [{"b64_json": B64}, {"b64_json": B64}],
                    "usage": {"output_tokens": 2000},
                },
            )
        )
        result = await OpenAIImages().generate(
            ImageRequest(prompt="p", format=STORY, count=2), api_key="sk-test-123456789"
        )
        sent = recorder.requests[0]
        assert sent.url.path == "/v1/images/generations"
        assert sent.headers["authorization"] == "Bearer sk-test-123456789"
        payload = json.loads(sent.content)
        assert payload["model"] == constants.IMAGE_OPENAI_MODEL
        assert payload["n"] == 2
        assert payload["size"] == "1008x1792"
        assert len(result.images) == 2
        assert result.usage == {"output_tokens": 2000}
        assert len(recorder.requests) == 1

    async def test_a_reference_goes_to_edits_as_multipart(self, http):
        recorder = http(httpx.Response(200, json={"data": [{"b64_json": B64}]}))
        await OpenAIImages().generate(
            _request(references=(InputImage(PNG, "image/png"),)),
            api_key="sk-test-123456789",
        )
        sent = recorder.requests[0]
        assert sent.url.path == "/v1/images/edits"
        assert sent.headers["content-type"].startswith("multipart/form-data")
        assert b'name="image[]"' in sent.content
        assert b'name="model"' in sent.content

    async def test_a_refused_key_never_echoes_it(self, http):
        http(
            httpx.Response(
                401,
                json={
                    "error": {
                        "message": "Incorrect API key provided: sk-test-****6789."
                    }
                },
            )
        )
        with pytest.raises(ImageProviderError) as raised:
            await OpenAIImages().generate(_request(), api_key="sk-test-123456789")
        assert raised.value.kind == "auth"
        assert raised.value.message.startswith("Your OpenAI key was refused")
        assert "sk-" not in raised.value.message

    async def test_the_platform_key_is_not_called_yours(self, http):
        http(httpx.Response(401, json={"error": {"message": "bad"}}))
        with pytest.raises(ImageProviderError) as raised:
            await OpenAIImages().generate(
                _request(), api_key="sk-test-123456789", own_key=False
            )
        assert raised.value.message.startswith("Decibyl's OpenAI key")

    async def test_a_safety_refusal(self, http):
        http(
            httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Your request was rejected by the safety system."
                    }
                },
            )
        )
        with pytest.raises(ImageProviderError) as raised:
            await OpenAIImages().generate(_request(), api_key="sk-test-123456789")
        assert raised.value.kind == "refused"

    async def test_quota(self, http):
        http(httpx.Response(429, json={"error": {"message": "Rate limit reached"}}))
        with pytest.raises(ImageProviderError) as raised:
            await OpenAIImages().generate(_request(), api_key="sk-test-123456789")
        assert raised.value.kind == "quota"


# --- Bedrock -----------------------------------------------------------------


@pytest.mark.asyncio
class TestBedrock:
    async def test_a_bedrock_api_key_is_a_bearer_on_invoke(self, http, monkeypatch):
        monkeypatch.setattr(constants, "IMAGE_BEDROCK_REGION", "us-east-1")
        recorder = http(httpx.Response(200, json={"images": [B64, B64]}))
        result = await BedrockImages().generate(
            ImageRequest(prompt="p" * 2000, format=STORY, count=2, avoid="extra text"),
            api_key="ABSKbedrockkey1234567890",
        )
        sent = recorder.requests[0]
        assert sent.url.host == "bedrock-runtime.us-east-1.amazonaws.com"
        assert sent.url.raw_path == b"/model/amazon.nova-canvas-v1%3A0/invoke"
        assert sent.headers["authorization"] == "Bearer ABSKbedrockkey1234567890"
        body = json.loads(sent.content)
        assert body["taskType"] == "TEXT_IMAGE"
        assert len(body["textToImageParams"]["text"]) == 1024
        assert body["textToImageParams"]["negativeText"] == "extra text"
        assert body["imageGenerationConfig"] == {
            "width": 720,
            "height": 1280,
            "quality": "standard",
            "numberOfImages": 2,
        }
        assert len(result.images) == 2

    async def test_a_reference_is_an_image_variation(self, http):
        recorder = http(httpx.Response(200, json={"images": [B64]}))
        await BedrockImages().generate(
            _request(references=(InputImage(PNG, "image/png"),)),
            api_key="ABSKbedrockkey1234567890",
        )
        body = json.loads(recorder.requests[0].content)
        assert body["taskType"] == "IMAGE_VARIATION"
        assert body["imageVariationParams"]["images"] == [B64]
        assert 0.2 <= body["imageVariationParams"]["similarityStrength"] <= 1.0

    async def test_fewer_images_than_asked_says_why(self, http):
        http(
            httpx.Response(
                200,
                json={"images": [B64], "error": "1 image blocked by content filters"},
            )
        )
        result = await BedrockImages().generate(
            ImageRequest(prompt="p", format=SQUARE, count=3),
            api_key="ABSKbedrockkey1234567890",
        )
        assert len(result.images) == 1
        assert "1 of 3" in result.note

    async def test_the_platform_role_through_boto3_without_retries(self, monkeypatch):
        monkeypatch.setattr(constants, "IMAGE_BEDROCK_ENABLED", True)
        calls = []

        class Body:
            def read(self):
                return json.dumps({"images": [B64]}).encode()

        class Client:
            def invoke_model(self, **kwargs):
                calls.append(kwargs)
                return {"body": Body()}

        monkeypatch.setattr(bedrock, "_boto_client_factory", lambda: Client())
        result = await BedrockImages().generate(_request(), api_key=None)
        assert len(calls) == 1
        assert calls[0]["modelId"] == constants.IMAGE_BEDROCK_MODEL
        assert json.loads(calls[0]["body"])["taskType"] == "TEXT_IMAGE"
        assert len(result.images) == 1

    async def test_the_real_boto3_client_is_built_with_one_attempt(self, monkeypatch):
        monkeypatch.setattr(bedrock, "_boto_client_factory", None)
        client = bedrock._boto_client()
        assert client.meta.config.retries["total_max_attempts"] == 1

    async def test_no_key_and_no_platform_role(self, monkeypatch):
        monkeypatch.setattr(constants, "IMAGE_BEDROCK_ENABLED", False)
        with pytest.raises(ImageProviderError) as raised:
            await BedrockImages().generate(_request(), api_key=None)
        assert raised.value.kind == "auth"


# --- the registry and the bits --------------------------------------------


class TestRegistry:
    def test_three_providers_in_card_order(self):
        assert registry.names() == ["google", "openai", "aws_bedrock"]
        for name in registry.names():
            assert registry.get(name).name == name
            assert registry.INFO[name].label
        assert registry.get("dall-e") is None

    def test_platform_keys_make_a_provider_ready(self, monkeypatch):
        monkeypatch.setattr(constants, "IMAGE_GEMINI_API_KEY", "")
        monkeypatch.setattr(constants, "IMAGE_OPENAI_API_KEY", "")
        monkeypatch.setattr(constants, "IMAGE_BEDROCK_ENABLED", False)
        assert not any(registry.platform_ready(n) for n in registry.names())
        monkeypatch.setattr(constants, "IMAGE_GEMINI_API_KEY", "AIza-platform")
        monkeypatch.setattr(constants, "IMAGE_BEDROCK_ENABLED", True)
        assert registry.platform_key("google") == "AIza-platform"
        # Bedrock on the box's role: ready, with no key to send.
        assert registry.platform_key("aws_bedrock") == ""
        assert registry.platform_ready("aws_bedrock")
        assert not registry.platform_ready("openai")


def test_image_size_reads_png_and_jpeg_headers():
    assert image_size(PNG) == (2, 3)
    jpeg = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xc0\x00\x11\x08\x00\x05\x00\x07\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    assert image_size(jpeg) == (7, 5)
    assert image_size(b"not an image") == (None, None)


def test_scrub_removes_anything_key_shaped():
    said = common.scrub("bad key sk-abcdefghijk and AIzaSyABCDEFGHIJKLMN, Bearer xyz")
    assert "sk-abc" not in said and "AIza" not in said and "xyz" not in said


def test_every_format_fits_every_providers_rules():
    for fmt in formats.FORMATS.values():
        width, height = (int(v) for v in fmt.openai_size.split("x"))
        assert width % 16 == 0 and height % 16 == 0
        assert 655_360 <= width * height <= 8_294_400
        assert 1 / 3 <= width / height <= 3
        nova_w, nova_h = fmt.nova_size
        assert nova_w % 16 == 0 and nova_h % 16 == 0
        assert 320 <= nova_w <= 4096 and 320 <= nova_h <= 4096
        assert nova_w * nova_h < 4_194_304
        assert 1 / 4 <= nova_w / nova_h <= 4
        assert fmt.aspect in {
            "1:1",
            "3:2",
            "2:3",
            "3:4",
            "4:3",
            "4:5",
            "5:4",
            "9:16",
            "16:9",
            "21:9",
        }
    assert formats.resolve("A4 poster")[0].key == "a4_poster"
    fmt, known = formats.resolve("billboard")
    assert fmt.key == formats.DEFAULT_FORMAT and not known
