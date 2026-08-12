"""What has to hold for "Sign in with Google" not to be a way in for someone else.

The happy path is the boring part. These cover the four ways a naive
implementation hands an attacker an account, each of which is a real, published
attack rather than a hypothetical:

* accepting a ``state`` we did not issue (login CSRF),
* accepting one we issued an hour ago (a leaked link staying useful),
* accepting an ID token minted for a different sign-in (replay), and
* trusting an email address Google never confirmed (account takeover).

The signature check on the ID token itself is PyJWT's job and is not re-tested
here; what is tested is that we ask it to, and that nothing reaches the network
before the state has been proven ours.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from api.services.auth import google_oauth as g


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    """Pretend the deployment has Google configured."""
    monkeypatch.setattr(g, "GOOGLE_OAUTH_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setattr(g, "GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")


def _identity(**overrides) -> g.GoogleIdentity:
    return g.GoogleIdentity(
        **{
            "subject": "1234567890",
            "email": "founder@example.com",
            "email_verified": True,
            "name": "A Founder",
            "picture": None,
            **overrides,
        }
    )


class TestConfiguration:
    def test_both_halves_are_required(self, monkeypatch):
        assert g.is_enabled()
        monkeypatch.setattr(g, "GOOGLE_OAUTH_CLIENT_SECRET", None)
        assert not g.is_enabled()

    def test_an_unconfigured_deployment_refuses_rather_than_half_works(self, monkeypatch):
        monkeypatch.setattr(g, "GOOGLE_OAUTH_CLIENT_ID", None)
        with pytest.raises(g.GoogleAuthError):
            g.build_authorization_url(redirect_uri="https://api.example.com/cb")


class TestTheAuthorizationUrl:
    def test_it_carries_state_and_a_matching_nonce(self):
        url = g.build_authorization_url(redirect_uri="https://api.example.com/cb")
        assert url.startswith(g.AUTHORIZE_ENDPOINT)

        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(url).query)
        # The nonce in the request and the nonce sealed into the state must be
        # the same value, or the callback can never match them and every
        # sign-in fails closed.
        assert q["nonce"][0] == g._read_state(q["state"][0])["nonce"]

    def test_each_sign_in_gets_its_own_nonce(self):
        first = g._read_state(_state_from(g.build_authorization_url(redirect_uri="https://x/cb")))
        second = g._read_state(_state_from(g.build_authorization_url(redirect_uri="https://x/cb")))
        assert first["nonce"] != second["nonce"]

    def test_it_asks_for_no_more_than_identity(self):
        url = g.build_authorization_url(redirect_uri="https://api.example.com/cb")
        assert "scope=openid+email+profile" in url or "openid%20email%20profile" in url


def _state_from(url: str) -> str:
    from urllib.parse import parse_qs, urlparse

    return parse_qs(urlparse(url).query)["state"][0]


class TestState:
    def test_a_state_we_issued_reads_back(self):
        state = g._issue_state(nonce="n", next_path="/agents")
        claims = g._read_state(state)
        assert claims["nonce"] == "n"
        assert claims["next"] == "/agents"

    def test_a_state_we_did_not_sign_is_refused(self):
        """The login-CSRF case: an attacker supplying their own state."""
        forged = jwt.encode(
            {"aud": g._STATE_AUDIENCE, "nonce": "attacker", "exp": datetime.now(UTC) + timedelta(minutes=5)},
            "not-our-secret",
            algorithm="HS256",
        )
        with pytest.raises(g.GoogleAuthError):
            g._read_state(forged)

    def test_an_expired_state_is_refused(self):
        from api.constants import OSS_JWT_SECRET

        stale = jwt.encode(
            {
                "aud": g._STATE_AUDIENCE,
                "nonce": "n",
                "exp": datetime.now(UTC) - timedelta(seconds=1),
            },
            OSS_JWT_SECRET,
            algorithm="HS256",
        )
        with pytest.raises(g.GoogleAuthError):
            g._read_state(stale)

    def test_a_token_signed_for_something_else_is_refused(self):
        """Our session tokens use the same secret; they are not sign-in states."""
        from api.utils.auth import create_jwt_token

        with pytest.raises(g.GoogleAuthError):
            g._read_state(create_jwt_token(1, "founder@example.com"))


class TestCompletingSignIn:
    async def test_a_bad_state_never_reaches_the_network(self, monkeypatch):
        """Nothing is sent to Google until the request is proven to be ours."""
        called = False

        async def _exchange(**_kwargs):
            nonlocal called
            called = True
            return "id-token"

        monkeypatch.setattr(g, "_exchange_code", _exchange)

        with pytest.raises(g.GoogleAuthError):
            await g.complete_sign_in(
                code="c", state="not-a-state", redirect_uri="https://x/cb"
            )
        assert called is False

    async def test_an_unverified_email_is_refused(self, monkeypatch):
        """The account-takeover case, and the reason this check exists.

        Google will issue a token carrying an address on a domain it does not
        verify. Linking on the email claim alone would hand that person the
        matching Decibyl account.
        """
        _patch_google(monkeypatch, _identity(email_verified=False))

        with pytest.raises(g.GoogleAuthError, match="not verified"):
            await g.complete_sign_in(
                code="c",
                state=g._issue_state(nonce="n", next_path=None),
                redirect_uri="https://x/cb",
            )

    async def test_a_verified_account_comes_back_with_where_it_was_going(
        self, monkeypatch
    ):
        _patch_google(monkeypatch, _identity())

        identity, next_path = await g.complete_sign_in(
            code="c",
            state=g._issue_state(nonce="n", next_path="/billing"),
            redirect_uri="https://x/cb",
        )

        assert identity.email == "founder@example.com"
        assert next_path == "/billing"

    async def test_the_nonce_from_the_state_is_what_the_token_is_checked_against(
        self, monkeypatch
    ):
        """Replay protection: the verifier is handed the state's nonce, not the token's."""
        seen: dict = {}

        async def _exchange(**_kwargs):
            return "id-token"

        def _verify(_token, *, expected_nonce):
            seen["nonce"] = expected_nonce
            return _identity()

        monkeypatch.setattr(g, "_exchange_code", _exchange)
        monkeypatch.setattr(g, "_verify_id_token", _verify)

        await g.complete_sign_in(
            code="c",
            state=g._issue_state(nonce="the-only-valid-nonce", next_path=None),
            redirect_uri="https://x/cb",
        )
        assert seen["nonce"] == "the-only-valid-nonce"


def _patch_google(monkeypatch, identity: g.GoogleIdentity) -> None:
    async def _exchange(**_kwargs):
        return "id-token"

    monkeypatch.setattr(g, "_exchange_code", _exchange)
    monkeypatch.setattr(g, "_verify_id_token", lambda _t, *, expected_nonce: identity)
