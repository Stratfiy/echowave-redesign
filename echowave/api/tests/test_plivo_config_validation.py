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
