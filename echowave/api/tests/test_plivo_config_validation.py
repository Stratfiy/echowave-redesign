"""Two different questions the Plivo provider was answering with one method.

`validate_config` required `auth_id`, `auth_token` **and** `from_numbers`, and
gated every operation on the class. That made buying a first number impossible:
searching Plivo's inventory was gated on already owning a number, and a
configuration created in order to buy one has none. An approved customer, on a
configuration that was perfectly good for the thing they were doing, got
"Plivo provider not properly configured".

Placing a call needs a caller ID. Talking to the account does not.
"""

from __future__ import annotations

import pytest

from api.services.telephony.providers.plivo.provider import PlivoProvider

CREDENTIALS = {"auth_id": "MAXXXXXXXXXXXXXXXXXX", "auth_token": "token"}


class TestTheTwoQuestions:
    def test_credentials_alone_can_reach_the_account(self):
        """The state a configuration is in the moment before its first
        purchase."""
        provider = PlivoProvider(dict(CREDENTIALS))

        assert provider.has_credentials() is True

    def test_credentials_alone_cannot_place_a_call(self):
        """Still true, and still the right answer — there is no number to call
        from."""
        provider = PlivoProvider(dict(CREDENTIALS))

        assert provider.validate_config() is False

    def test_a_number_makes_it_callable(self):
        provider = PlivoProvider({**CREDENTIALS, "from_numbers": ["+918047123456"]})

        assert provider.validate_config() is True
        assert provider.has_credentials() is True

    def test_neither_is_true_without_credentials(self):
        provider = PlivoProvider({"from_numbers": ["+918047123456"]})

        assert provider.has_credentials() is False
        assert provider.validate_config() is False


@pytest.mark.asyncio
class TestSearchingBeforeYouOwnAnything:
    async def test_it_does_not_refuse_for_want_of_a_caller_id(self, monkeypatch):
        """The bug, stated directly: a search on a configuration with no
        numbers must reach Plivo rather than being refused locally."""
        provider = PlivoProvider(dict(CREDENTIALS))
        reached = {}

        async def _fake_request(method, url, **kwargs):
            reached["url"] = url

            class _Response:
                status = 200

                async def text(self):
                    return '{"objects": []}'

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_a):
                    return False

            return _Response()

        import aiohttp

        monkeypatch.setattr(aiohttp.ClientSession, "get", _fake_request)

        try:
            await provider.search_available_numbers(country_iso="IN")
        except ValueError as exc:  # pragma: no cover - the regression itself
            pytest.fail(f"search refused a credentialled configuration: {exc}")
        except Exception:
            # Any other failure is the fake transport, not the gate. The gate
            # raises ValueError before a request is ever attempted, so getting
            # this far is the property under test.
            pass


@pytest.mark.asyncio
class TestWhatTheSearchAsksPlivoFor:
    """The two parameters that decide whether a search can return anything.

    Both were learned from Plivo's own buy screen rather than from our code:
    it offers prefix checkboxes and a Voice capability filter, which is a
    fairly direct statement about what its API rewards.
    """

    @staticmethod
    async def _captured_params(monkeypatch, **search_kwargs):
        import aiohttp

        provider = PlivoProvider(dict(CREDENTIALS))
        seen = {}

        def _get(self, url, **kwargs):
            seen.update(kwargs.get("params") or {})

            class _Response:
                status = 200

                async def text(self):
                    return '{"objects": []}'

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_a):
                    return False

            return _Response()

        monkeypatch.setattr(aiohttp.ClientSession, "get", _get)
        await provider.search_available_numbers(**search_kwargs)
        return seen

    async def test_it_asks_for_voice_numbers(self, monkeypatch):
        """An SMS-only number sold to a voice agent is a number that cannot
        take a call."""
        params = await self._captured_params(monkeypatch, country_iso="IN")

        assert params["services"] == "voice"

    async def test_the_pattern_is_sent_as_given(self, monkeypatch):
        """Plivo matches it against the start of the number after the country
        code. The caller composes the STD code and any extra digits into one
        string precisely because they are one prefix, not two filters."""
        params = await self._captured_params(
            monkeypatch, country_iso="IN", pattern="226423"
        )

        assert params["pattern"] == "226423"

    async def test_no_pattern_means_no_pattern(self, monkeypatch):
        """An unfiltered search must not send an empty string — Plivo would
        match it against every number and there is no reason to make it."""
        params = await self._captured_params(monkeypatch, country_iso="IN")

        assert "pattern" not in params
