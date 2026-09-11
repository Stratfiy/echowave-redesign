"""The validator that used to be ``return True``.

A live dental clinic went dark for the length of a test because an ElevenLabs
model the account has no entitlement to is selectable in the editor, saves
without complaint, reports ``last_check_ok``, and then refuses the websocket
with a bare ``HTTP 403`` on the first call. Nothing in the interface said why.
The voice half of the same failure looks identical from inside a call.

So the tests that matter here are not "does a good key pass". They are: does a
bad combination get caught *at save time*, and -- just as important -- does an
unreachable vendor stay silent rather than blaming the customer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from api.services.configuration.check_validity import UserConfigurationValidator


def _config(model="eleven_flash_v2_5", voice="abc123", base_url=None):
    return SimpleNamespace(model=model, voice=voice, base_url=base_url)


def _response(status, payload=None):
    return SimpleNamespace(
        status_code=status, json=lambda: payload if payload is not None else {}
    )


MODELS_OK = {
    "models": [
        {"model_id": "eleven_flash_v2_5"},
        {"model_id": "eleven_multilingual_v2"},
    ]
}


def _get(models_response, voice_response=None):
    """Route the two GETs the validator makes."""

    def side_effect(url, **_kwargs):
        if "/v1/models" in url:
            return models_response
        return voice_response if voice_response is not None else _response(200)

    return side_effect


class TestTheOutageThatCausedThis:
    def test_a_model_the_plan_cannot_use_is_refused(self):
        validator = UserConfigurationValidator()
        with patch("httpx.get", side_effect=_get(_response(200, MODELS_OK))):
            with pytest.raises(ValueError) as caught:
                validator._validate_elevenlabs_api_key(
                    "elevenlabs", "k", _config(model="eleven_v3_conversational")
                )
        message = str(caught.value)
        assert "eleven_v3_conversational" in message
        assert "eleven_flash_v2_5" in message, "it must say what IS available"

    def test_a_voice_not_in_the_account_is_refused(self):
        validator = UserConfigurationValidator()
        with patch(
            "httpx.get",
            side_effect=_get(_response(200, MODELS_OK), _response(404)),
        ):
            with pytest.raises(ValueError) as caught:
                validator._validate_elevenlabs_api_key(
                    "elevenlabs", "k", _config(voice="HBlqQDCBvQxsEK8OFtEZ")
                )
        assert "HBlqQDCBvQxsEK8OFtEZ" in str(caught.value)

    def test_a_good_combination_passes(self):
        validator = UserConfigurationValidator()
        with patch("httpx.get", side_effect=_get(_response(200, MODELS_OK))):
            assert validator._validate_elevenlabs_api_key("elevenlabs", "k", _config())

    def test_a_rejected_key_says_so(self):
        validator = UserConfigurationValidator()
        with patch("httpx.get", side_effect=_get(_response(401))):
            with pytest.raises(ValueError) as caught:
                validator._validate_elevenlabs_api_key("elevenlabs", "k", _config())
        assert "Invalid ElevenLabs API key" in str(caught.value)

    def test_an_empty_key_says_so(self):
        validator = UserConfigurationValidator()
        with pytest.raises(ValueError):
            validator._validate_elevenlabs_api_key("elevenlabs", "", _config())


class TestSilenceIsNotEvidenceAgainstTheCustomer:
    """A validator that guesses wrong blames the customer for our mistake."""

    def test_a_vendor_that_does_not_answer_passes(self):
        validator = UserConfigurationValidator()
        with patch("httpx.get", side_effect=OSError("connection reset")):
            assert validator._validate_elevenlabs_api_key(
                "elevenlabs", "k", _config(model="anything")
            )

    def test_an_unexpected_status_passes(self):
        validator = UserConfigurationValidator()
        with patch("httpx.get", side_effect=_get(_response(500))):
            assert validator._validate_elevenlabs_api_key(
                "elevenlabs", "k", _config(model="anything")
            )

    def test_a_response_shape_we_do_not_recognise_passes(self):
        validator = UserConfigurationValidator()
        with patch("httpx.get", side_effect=_get(_response(200, {"data": []}))):
            assert validator._validate_elevenlabs_api_key(
                "elevenlabs", "k", _config(model="anything")
            )

    def test_a_voice_endpoint_that_errors_does_not_fail_the_save(self):
        validator = UserConfigurationValidator()
        with patch(
            "httpx.get",
            side_effect=_get(_response(200, MODELS_OK), _response(500)),
        ):
            assert validator._validate_elevenlabs_api_key("elevenlabs", "k", _config())


class TestThePlatformSweep:
    """The credential health check has a key but no agent configuration."""

    def test_without_a_configuration_the_key_is_still_checked(self):
        validator = UserConfigurationValidator()
        with patch("httpx.get", side_effect=_get(_response(401))):
            with pytest.raises(ValueError):
                validator._validate_elevenlabs_api_key("elevenlabs", "k", None)

    def test_without_a_configuration_a_working_key_passes(self):
        validator = UserConfigurationValidator()
        with patch("httpx.get", side_effect=_get(_response(200, MODELS_OK))):
            assert validator._validate_elevenlabs_api_key("elevenlabs", "k", None)


class TestItIsWiredIn:
    def test_the_dispatcher_hands_elevenlabs_the_configuration(self):
        """Without this the model and voice can never be seen."""
        validator = UserConfigurationValidator()
        with patch("httpx.get", side_effect=_get(_response(200, MODELS_OK))):
            with pytest.raises(ValueError):
                validator._check_api_key(
                    "elevenlabs", "k", _config(model="eleven_v3_conversational")
                )


class TestOldConfigurationsStillWork:
    def test_a_name_dash_id_voice_is_understood(self):
        """Older configurations stored "Name - voice_id"."""
        seen = {}

        def capture(url, **_kwargs):
            if "/v1/models" in url:
                return _response(200, MODELS_OK)
            seen["url"] = url
            return _response(200)

        validator = UserConfigurationValidator()
        with patch("httpx.get", side_effect=capture):
            validator._validate_elevenlabs_api_key(
                "elevenlabs", "k", _config(voice="Meera - gHu9GtaHOXcSqFTK06ux")
            )
        assert seen["url"].endswith("/v1/voices/gHu9GtaHOXcSqFTK06ux")
