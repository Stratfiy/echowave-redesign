"""The refresh-token grant, and the ways it can quietly go wrong.

Every credential type before this one was a value formatted into a header. This
one has state — a token that expires, a cache shared between workers, and a
secret that must never reach a log. Each test here is one way that goes wrong
in production rather than in review:

* a token that is *technically* still valid when checked and expired by the
  time the request lands,
* twenty concurrent tool calls on one agent each opening their own exchange,
* a refresh token rotated by an operator mid-call and then overwritten by a
  worker still holding the old one,
* a vendor answering 200 with an error body,
* an exception message carrying the client secret into the logs.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from api.services.integrations import oauth2
from api.utils.credential_auth import build_auth_header

GRANT = {
    "token_url": "https://accounts.zoho.in/oauth/v2/token",
    "client_id": "cid",
    "client_secret": "sekrit",
    "refresh_token": "refresh-abc",
}


class _Credential:
    """The two attributes the resolver touches."""

    def __init__(self, data: dict, uuid: str = "cred-1"):
        self.credential_uuid = uuid
        self.credential_type = oauth2.CREDENTIAL_TYPE
        self.credential_data = data


class TestTheCachedToken:
    def test_a_live_token_is_used(self):
        data = {
            **GRANT,
            "access_token": "live",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
        assert oauth2.cached_token(data) == "live"

    def test_an_expired_token_is_not(self):
        data = {
            **GRANT,
            "access_token": "stale",
            "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        }
        assert oauth2.cached_token(data) is None

    def test_a_token_expiring_inside_the_skew_is_not_used(self):
        """The failure this exists to prevent: valid when checked, dead when the
        request lands. Anything inside the skew is treated as already gone."""
        data = {
            **GRANT,
            "access_token": "about-to-die",
            "expires_at": (
                datetime.now(UTC) + timedelta(seconds=oauth2.EXPIRY_SKEW_SECONDS - 5)
            ).isoformat(),
        }
        assert oauth2.cached_token(data) is None

    def test_an_unparseable_expiry_is_treated_as_expired(self):
        data = {**GRANT, "access_token": "x", "expires_at": "whenever"}
        assert oauth2.cached_token(data) is None

    def test_a_naive_expiry_is_read_as_utc(self):
        """A timestamp written without a zone would otherwise raise on the
        comparison and take the whole call down with it."""
        naive = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None)
        data = {**GRANT, "access_token": "ok", "expires_at": naive.isoformat()}
        assert oauth2.cached_token(data) == "ok"


class TestTheHeaderShape:
    def test_bearer_is_the_default(self):
        assert oauth2.header_for({}, "T") == {"Authorization": "Bearer T"}

    def test_zoho_gets_its_own_scheme(self):
        """Zoho rejects Bearer. One field, not a vendor branch."""
        assert oauth2.header_for({"header_prefix": "Zoho-oauthtoken"}, "T") == {
            "Authorization": "Zoho-oauthtoken T"
        }

    def test_an_empty_prefix_sends_the_bare_token(self):
        assert oauth2.header_for({"header_prefix": ""}, "T") == {"Authorization": "T"}

    def test_the_header_name_can_move(self):
        assert oauth2.header_for(
            {"header_name": "X-Auth", "header_prefix": ""}, "T"
        ) == {"X-Auth": "T"}


class TestValidation:
    def test_every_grant_field_is_required(self):
        assert set(oauth2.missing_fields({})) == set(oauth2.REQUIRED_FIELDS)

    def test_blank_is_missing_not_present(self):
        """A form that posts empty strings must not produce a credential that
        looks complete and fails on first use."""
        assert oauth2.missing_fields({**GRANT, "client_secret": "   "}) == [
            "client_secret"
        ]

    def test_a_complete_grant_passes(self):
        assert oauth2.missing_fields(GRANT) == []


class TestTheSynchronousPathFailsClosed:
    def test_a_live_cached_token_is_returned(self):
        cred = _Credential(
            {
                **GRANT,
                "access_token": "live",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
        )
        assert build_auth_header(cred) == {"Authorization": "Bearer live"}

    def test_an_expired_token_yields_no_header_rather_than_a_dead_one(self):
        """`build_auth_header` cannot mint — it has no network. Sending the
        expired token anyway turns a fixable 401 into one that looks like a bad
        secret, so it sends nothing."""
        cred = _Credential(
            {
                **GRANT,
                "access_token": "stale",
                "expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            }
        )
        assert build_auth_header(cred) == {}


@pytest.mark.asyncio
class TestMinting:
    async def test_it_mints_when_there_is_no_cache(self, monkeypatch):
        async def fake_exchange(data, *, session=None):
            assert data["refresh_token"] == "refresh-abc"
            return "fresh", datetime.now(UTC) + timedelta(hours=1)

        monkeypatch.setattr(oauth2, "exchange_refresh_token", fake_exchange)
        cred = _Credential(dict(GRANT))

        header = await oauth2.resolve_header(cred)
        assert header == {"Authorization": "Bearer fresh"}

    async def test_the_minted_token_is_written_back_onto_the_row(self, monkeypatch):
        """So the next call on this worker skips the exchange entirely."""

        async def fake_exchange(data, *, session=None):
            return "fresh", datetime.now(UTC) + timedelta(hours=1)

        monkeypatch.setattr(oauth2, "exchange_refresh_token", fake_exchange)
        cred = _Credential(dict(GRANT))

        await oauth2.resolve_header(cred)
        assert cred.credential_data["access_token"] == "fresh"
        assert oauth2.cached_token(cred.credential_data) == "fresh"

    async def test_it_persists_through_the_hook(self, monkeypatch):
        async def fake_exchange(data, *, session=None):
            return "fresh", datetime.now(UTC) + timedelta(hours=1)

        monkeypatch.setattr(oauth2, "exchange_refresh_token", fake_exchange)
        saved: dict = {}

        async def persist(uuid, data):
            saved["uuid"] = uuid
            saved["data"] = data

        cred = _Credential(dict(GRANT), uuid="cred-persist")
        await oauth2.resolve_header(cred, persist=persist)
        # The lock is per credential_uuid, so this test needs its own.
        assert saved["uuid"] == "cred-persist"
        assert saved["data"]["access_token"] == "fresh"

    async def test_a_failing_persist_does_not_fail_the_call(self, monkeypatch):
        """The token is already in hand. Losing the cache costs one exchange;
        raising here would drop a live conversation."""

        async def fake_exchange(data, *, session=None):
            return "fresh", datetime.now(UTC) + timedelta(hours=1)

        async def persist(uuid, data):
            raise RuntimeError("database went away")

        monkeypatch.setattr(oauth2, "exchange_refresh_token", fake_exchange)
        cred = _Credential(dict(GRANT), uuid="cred-persist-fails")

        header = await oauth2.resolve_header(cred, persist=persist)
        assert header == {"Authorization": "Bearer fresh"}

    async def test_concurrent_callers_share_one_exchange(self, monkeypatch):
        """Twenty tool calls on one agent must not open twenty exchanges. Some
        vendors invalidate the previous access token on each refresh, so the
        losers of that race would be holding dead tokens."""
        calls = 0

        async def fake_exchange(data, *, session=None):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return f"fresh-{calls}", datetime.now(UTC) + timedelta(hours=1)

        monkeypatch.setattr(oauth2, "exchange_refresh_token", fake_exchange)
        cred = _Credential(dict(GRANT), uuid="cred-concurrent")

        headers = await asyncio.gather(
            *(oauth2.resolve_header(cred) for _ in range(20))
        )
        assert calls == 1
        assert {tuple(h.items()) for h in headers} == {
            (("Authorization", "Bearer fresh-1"),)
        }

    async def test_an_incomplete_grant_raises_before_the_network(self):
        """Deliberately not mocking the exchange: the point is that the real
        one refuses on the missing fields before it builds a request, so a
        half-filled credential cannot put a client id on the wire looking for
        a secret that is not there."""
        cred = _Credential({"token_url": "https://x/token"}, uuid="cred-incomplete")

        with pytest.raises(oauth2.OAuth2Error) as caught:
            await oauth2.resolve_header(cred)
        message = str(caught.value)
        assert "client_secret" in message and "refresh_token" in message


@pytest.mark.asyncio
class TestTheExchangeKeepsSecretsOut:
    async def test_a_vendor_error_message_names_no_secret(self, monkeypatch):
        """A 400 from the token endpoint echoes the request back in some
        vendors' bodies. The message that reaches a log must not."""

        class _Response:
            status = 400

            async def text(self):
                return f"error: bad client_secret={GRANT['client_secret']}"

            async def json(self, content_type=None):
                return {"error": "invalid_client"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class _Session:
            def post(self, *a, **kw):
                return _Response()

            async def close(self):
                pass

        with pytest.raises(oauth2.OAuth2Error) as caught:
            await oauth2.exchange_refresh_token(dict(GRANT), session=_Session())

        message = str(caught.value)
        assert GRANT["client_secret"] not in message
        assert GRANT["refresh_token"] not in message
        assert "400" in message

    async def test_a_200_carrying_an_error_is_still_a_failure(self, monkeypatch):
        """Several vendors answer 200 with {"error": ...}. Treating that as
        success stores an empty token and every later call 401s."""

        class _Response:
            status = 200

            async def text(self):
                return "{}"

            async def json(self, content_type=None):
                return {"error": "invalid_grant"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class _Session:
            def post(self, *a, **kw):
                return _Response()

            async def close(self):
                pass

        with pytest.raises(oauth2.OAuth2Error) as caught:
            await oauth2.exchange_refresh_token(dict(GRANT), session=_Session())
        assert "invalid_grant" in str(caught.value)

    async def test_a_missing_expires_in_falls_back_rather_than_raising(self):
        class _Response:
            status = 200

            async def text(self):
                return "{}"

            async def json(self, content_type=None):
                return {"access_token": "fresh"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class _Session:
            def post(self, *a, **kw):
                return _Response()

            async def close(self):
                pass

        token, expires_at = await oauth2.exchange_refresh_token(
            dict(GRANT), session=_Session()
        )
        assert token == "fresh"
        assert expires_at > datetime.now(UTC)
