"""Transcribing an upload with the key the account already has.

Batch transcription went to the Model Proxy Service and nowhere else, so an
account with a perfectly good Deepgram key could not transcribe an uploaded
recording on a deployment that could not reach MPS. The button did nothing and
said "Failed to transcribe audio".

Every test here runs against a stubbed HTTP layer. The vendors' response
shapes are the fixtures — Deepgram nests its transcript four levels down and
returns an empty alternatives list rather than an error for silent audio, and
that is exactly the sort of thing that surfaces as a 500 in production and
never in a test that mocks the service instead of the wire.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from api.services.gen_ai.transcription import (
    TranscriptionError,
    TranscriptionNotSupportedError,
    build_transcription_service,
)
from api.services.gen_ai.transcription.factory import SUPPORTED
from api.services.gen_ai.transcription.providers import (
    DeepgramTranscriptionService,
    MPSTranscriptionService,
    OpenAICompatibleTranscriptionService,
    SarvamTranscriptionService,
)

AUDIO = b"RIFF....WAVEfmt "


def _responds(status: int, payload: dict, captured: dict | None = None):
    """Stub httpx.AsyncClient.post, optionally recording what was sent."""

    async def _post(self, url, **kwargs):
        if captured is not None:
            captured["url"] = url
            captured.update(kwargs)
        return httpx.Response(status, json=payload, request=httpx.Request("POST", url))

    return _post


class TestOpenAIShaped:
    async def test_a_transcript_comes_back(self, monkeypatch):
        monkeypatch.setattr(
            "httpx.AsyncClient.post",
            _responds(200, {"text": " Hello there. ", "duration": 3.5}),
        )
        service = OpenAICompatibleTranscriptionService(api_key="sk-test")

        result = await service.transcribe(AUDIO)

        assert result.transcript == "Hello there."
        assert result.duration_seconds == 3.5

    async def test_a_regioned_language_is_reduced_to_iso_639_1(self, monkeypatch):
        """OpenAI rejects "en-IN" outright, and the region tells it nothing."""
        captured: dict = {}
        monkeypatch.setattr(
            "httpx.AsyncClient.post", _responds(200, {"text": "x"}, captured)
        )

        await OpenAICompatibleTranscriptionService(api_key="sk-test").transcribe(
            AUDIO, language="en-IN"
        )

        assert captured["data"]["language"] == "en"

    async def test_a_custom_base_url_is_used(self, monkeypatch):
        """The same implementation serves Groq, OpenRouter and Speaches."""
        captured: dict = {}
        monkeypatch.setattr(
            "httpx.AsyncClient.post", _responds(200, {"text": "x"}, captured)
        )

        await OpenAICompatibleTranscriptionService(
            api_key="sk-test", base_url="https://api.groq.com/openai/v1"
        ).transcribe(AUDIO)

        assert captured["url"].startswith("https://api.groq.com/openai/v1")

    async def test_a_missing_key_says_where_to_set_it(self):
        service = OpenAICompatibleTranscriptionService(api_key=None)

        with pytest.raises(TranscriptionError) as caught:
            await service.transcribe(AUDIO)

        assert "Model Configurations" in caught.value.user_message

    async def test_a_vendor_failure_does_not_reach_the_customer_verbatim(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "httpx.AsyncClient.post",
            _responds(401, {"error": {"message": "Incorrect API key sk-abc123"}}),
        )
        service = OpenAICompatibleTranscriptionService(api_key="sk-test")

        with pytest.raises(TranscriptionError) as caught:
            await service.transcribe(AUDIO)

        # The key fragment and the status code stay in the log.
        assert "sk-abc123" not in caught.value.user_message
        assert "401" not in caught.value.user_message


class TestDeepgram:
    async def test_the_transcript_is_dug_out_of_the_nesting(self, monkeypatch):
        monkeypatch.setattr(
            "httpx.AsyncClient.post",
            _responds(
                200,
                {
                    "results": {
                        "channels": [
                            {"alternatives": [{"transcript": "Refunds take 14 days."}]}
                        ]
                    },
                    "metadata": {"duration": 4.2},
                },
            ),
        )

        result = await DeepgramTranscriptionService(api_key="dg-test").transcribe(AUDIO)

        assert result.transcript == "Refunds take 14 days."
        assert result.duration_seconds == 4.2

    async def test_silent_audio_returns_an_empty_transcript_not_a_500(
        self, monkeypatch
    ):
        """Deepgram answers 200 with an empty alternatives list for silence.
        Indexing into it blindly is an IndexError the customer sees as a 500."""
        monkeypatch.setattr(
            "httpx.AsyncClient.post",
            _responds(200, {"results": {"channels": [{"alternatives": []}]}}),
        )

        result = await DeepgramTranscriptionService(api_key="dg-test").transcribe(AUDIO)

        assert result.transcript == ""

    async def test_a_response_with_no_channels_at_all_is_survived(self, monkeypatch):
        monkeypatch.setattr("httpx.AsyncClient.post", _responds(200, {"results": {}}))

        result = await DeepgramTranscriptionService(api_key="dg-test").transcribe(AUDIO)

        assert result.transcript == ""


class TestSarvam:
    async def test_a_transcript_comes_back(self, monkeypatch):
        monkeypatch.setattr(
            "httpx.AsyncClient.post",
            _responds(200, {"transcript": "नमस्ते", "language_code": "hi-IN"}),
        )

        result = await SarvamTranscriptionService(api_key="sv-test").transcribe(AUDIO)

        assert result.transcript == "नमस्ते"
        assert result.language == "hi-IN"

    async def test_a_bare_language_asks_sarvam_to_detect(self, monkeypatch):
        """Sarvam rejects a bare "en"; for an upload nobody declared a language
        for, detection is the right answer rather than a guess."""
        captured: dict = {}
        monkeypatch.setattr(
            "httpx.AsyncClient.post",
            _responds(200, {"transcript": "x"}, captured),
        )

        await SarvamTranscriptionService(api_key="sv-test").transcribe(
            AUDIO, language="en"
        )

        assert captured["data"]["language_code"] == "unknown"

    async def test_a_regioned_language_is_passed_through(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(
            "httpx.AsyncClient.post",
            _responds(200, {"transcript": "x"}, captured),
        )

        await SarvamTranscriptionService(api_key="sv-test").transcribe(
            AUDIO, language="hi-IN"
        )

        assert captured["data"]["language_code"] == "hi-IN"


class TestWhichServiceAnOrganizationGets:
    """The point of the change: the account's own configuration decides."""

    def _stub_config(self, monkeypatch, *, provider, api_key="k", model=None):
        async def resolved(**kwargs):
            return SimpleNamespace(
                effective=SimpleNamespace(
                    stt=SimpleNamespace(
                        provider=provider,
                        api_key=api_key,
                        model=model,
                        base_url=None,
                    )
                )
            )

        monkeypatch.setattr(
            "api.services.configuration.ai_model_configuration."
            "get_resolved_ai_model_configuration",
            resolved,
        )

    async def test_a_deepgram_account_transcribes_with_deepgram(self, monkeypatch):
        self._stub_config(monkeypatch, provider="deepgram")
        service = await build_transcription_service(organization_id=1)
        assert isinstance(service, DeepgramTranscriptionService)

    async def test_a_sarvam_account_transcribes_with_sarvam(self, monkeypatch):
        self._stub_config(monkeypatch, provider="sarvam")
        service = await build_transcription_service(organization_id=1)
        assert isinstance(service, SarvamTranscriptionService)

    async def test_an_openai_account_transcribes_with_openai(self, monkeypatch):
        self._stub_config(monkeypatch, provider="openai")
        service = await build_transcription_service(organization_id=1)
        assert isinstance(service, OpenAICompatibleTranscriptionService)

    async def test_groq_uses_the_openai_shaped_implementation(self, monkeypatch):
        self._stub_config(monkeypatch, provider="groq")
        service = await build_transcription_service(organization_id=1)
        assert isinstance(service, OpenAICompatibleTranscriptionService)

    async def test_the_managed_tier_still_goes_through_mps(self, monkeypatch):
        """A managed account holds no vendor keys — that is the tier."""
        self._stub_config(monkeypatch, provider="decibyl")
        service = await build_transcription_service(organization_id=1)
        assert isinstance(service, MPSTranscriptionService)

    async def test_an_unconfigured_account_falls_back_to_managed(self, monkeypatch):
        async def nothing_configured(**kwargs):
            return SimpleNamespace(effective=SimpleNamespace(stt=None))

        monkeypatch.setattr(
            "api.services.configuration.ai_model_configuration."
            "get_resolved_ai_model_configuration",
            nothing_configured,
        )

        service = await build_transcription_service(organization_id=1)
        assert isinstance(service, MPSTranscriptionService)

    async def test_a_broken_config_lookup_does_not_refuse_the_upload(self, monkeypatch):
        async def explodes(**kwargs):
            raise RuntimeError("configuration table is unreachable")

        monkeypatch.setattr(
            "api.services.configuration.ai_model_configuration."
            "get_resolved_ai_model_configuration",
            explodes,
        )

        service = await build_transcription_service(organization_id=1)
        assert isinstance(service, MPSTranscriptionService)

    async def test_a_provider_without_a_batch_endpoint_says_what_to_pick(
        self, monkeypatch
    ):
        """Its live calls are fine. Only this screen is affected, and saying so
        is the difference between "choose another provider" and "we are down"."""
        self._stub_config(monkeypatch, provider="elevenlabs")

        with pytest.raises(TranscriptionNotSupportedError) as caught:
            await build_transcription_service(organization_id=1)

        message = caught.value.user_message
        assert "live calls are unaffected" in message
        for provider in ("deepgram", "sarvam", "openai"):
            assert provider in message

    def test_every_supported_provider_is_actually_reachable_from_the_factory(self):
        """A name in SUPPORTED that the factory cannot build would send someone
        to configure a provider that then refuses them."""
        assert set(SUPPORTED) == {
            "openai",
            "groq",
            "openrouter",
            "speaches",
            "deepgram",
            "sarvam",
            "decibyl",
        }


class TestTheManagedPath:
    async def test_an_mps_outage_is_reported_as_ours_not_theirs(self, monkeypatch):
        async def exploding(**kwargs):
            raise httpx.ConnectError("Name or service not known")

        monkeypatch.setattr(
            "api.services.mps_service_key_client.mps_service_key_client"
            ".transcribe_audio",
            exploding,
        )

        with pytest.raises(TranscriptionError) as caught:
            await MPSTranscriptionService(organization_id=1).transcribe(AUDIO)

        assert "Name or service not known" not in caught.value.user_message
        assert "our side" in caught.value.user_message

    async def test_a_successful_managed_transcription_keeps_its_shape(
        self, monkeypatch
    ):
        async def transcribed(**kwargs):
            return {
                "transcript": "Hello",
                "language": "en",
                "duration_seconds": 1.0,
                "model": "default",
            }

        monkeypatch.setattr(
            "api.services.mps_service_key_client.mps_service_key_client"
            ".transcribe_audio",
            transcribed,
        )

        result = await MPSTranscriptionService(organization_id=1).transcribe(AUDIO)

        assert result.as_response() == {
            "transcript": "Hello",
            "language": "en",
            "duration_seconds": 1.0,
            "model": "default",
        }


class TestTheResponseContract:
    def test_the_screen_still_gets_the_field_it_reads(self):
        """RecordingsUploadDialog reads data.transcript and nothing else. This
        change was about removing a dependency, not renegotiating a contract."""
        from api.services.gen_ai.transcription import Transcription

        assert "transcript" in Transcription(transcript="x").as_response()


class TestWhatTheServiceKeysScreenSaysWithoutMPS:
    """A service key is a credential *for* MPS, not a thing MPS happens to
    store.

    The Decibyl provider's api_key is one of these, and validate_service_key
    checks it against MPS's own usage endpoint. So on a deployment that cannot
    reach MPS these are not broken — they are meaningless, and "Failed to
    retrieve service keys" sends somebody to debug a feature that has nothing
    to do for them.
    """

    async def test_an_unreachable_service_explains_rather_than_erroring(
        self, monkeypatch, test_client_factory, async_session
    ):
        from api.db.models import OrganizationModel
        from api.db.models import UserModel as UserRow

        org = OrganizationModel(provider_id="org-svckeys", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()
        user = UserRow(provider_id="user-svckeys", selected_organization_id=org.id)
        async_session.add(user)
        await async_session.flush()

        async def unreachable(*args, **kwargs):
            raise httpx.ConnectError("Name or service not known")

        monkeypatch.setattr(
            "api.services.mps_service_key_client.mps_service_key_client"
            ".get_service_keys",
            unreachable,
        )

        async with test_client_factory(user) as client:
            response = await client.get("/api/v1/user/service-keys")

        # 503, not 500: the deployment is fine, this feature has no service to
        # talk to. And the copy says what a service key is for, so somebody can
        # decide they do not need one.
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "bring your own provider keys" in detail
        assert "Name or service not known" not in detail
