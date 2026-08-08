"""Google Calendar: the connect flow, token refresh, and the booking tool.

Three things are tested, in order of how much would go wrong silently if
they broke:

1. **The state parameter is tamper-evident.** It is the only thing telling
   an unauthenticated callback which organization a grant belongs to, so a
   forged or expired one must be rejected outright.
2. **Tokens round-trip through encryption**, and a stale cached access
   token triggers a refresh rather than being handed out expired.
3. **The booking tool** builds a correct event body and reports a clear
   error — never a stack trace — when Calendar isn't connected or Google
   rejects the request.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from cryptography.fernet import Fernet

from api.db.models import GoogleCalendarConnectionModel, OrganizationModel, UserModel
from api.services.integrations.google_calendar import client as gcal_client
from api.services.integrations.google_calendar import oauth as gcal_oauth

TEST_SECRET = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    """A real Fernet key, generated per run — never a fixed literal.

    See test_organization_credentials.py for why: a committed key that also
    happens to work in a real deployment is how a fixture becomes a leaked
    production secret.
    """
    monkeypatch.setattr(gcal_oauth, "PLATFORM_CREDENTIAL_SECRET", TEST_SECRET)


@pytest.fixture(autouse=True)
def oauth_client_credentials(monkeypatch):
    monkeypatch.setattr(gcal_oauth, "GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(gcal_oauth, "GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(gcal_oauth, "BACKEND_API_ENDPOINT", "https://app.decibyl.ai")


async def _org(async_session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


async def _user(async_session, slug: str) -> UserModel:
    """connected_by is a real foreign key to users.id -- a bare int like 99
    passes every check that doesn't touch the database, then fails on the
    one query that actually writes the row."""
    user = UserModel(provider_id=f"user-{slug}")
    async_session.add(user)
    await async_session.flush()
    return user


def _mock_response(status_code: int, json_body: dict):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


class TestStateSigning:
    def test_a_signed_state_round_trips(self):
        state = gcal_oauth._sign_state(organization_id=42, user_id=7)
        payload = gcal_oauth._verify_state(state)
        assert payload["organization_id"] == 42
        assert payload["user_id"] == 7

    def test_a_tampered_state_is_rejected(self):
        """Changing even one byte of the signed payload must fail verification.

        This is the entire security of the callback: it has no other way to
        know which organization a grant belongs to.
        """
        state = gcal_oauth._sign_state(organization_id=42, user_id=7)
        tampered = state[:-2] + ("xx" if not state.endswith("xx") else "yy")
        with pytest.raises(gcal_oauth.GoogleCalendarError):
            gcal_oauth._verify_state(tampered)

    def test_a_state_signed_for_one_org_cannot_be_edited_to_claim_another(self):
        """Editing the organization_id inside the payload must invalidate the
        signature -- the field itself, not just gross corruption, is protected."""
        import base64
        import json

        state = gcal_oauth._sign_state(organization_id=42, user_id=7)
        body_b64, sig_b64 = state.split(".", 1)
        padded = body_b64 + "=" * (-len(body_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        payload["organization_id"] = 999  # attacker tries to steal another org's grant
        forged_body = json.dumps(payload, separators=(",", ":")).encode()
        forged_body_b64 = base64.urlsafe_b64encode(forged_body).decode().rstrip("=")
        forged_state = f"{forged_body_b64}.{sig_b64}"  # old signature, new body

        with pytest.raises(gcal_oauth.GoogleCalendarError):
            gcal_oauth._verify_state(forged_state)

    def test_an_expired_state_is_rejected(self):
        state = gcal_oauth._sign_state(organization_id=1, user_id=1)
        with patch("api.services.integrations.google_calendar.oauth.time") as mock_time:
            mock_time.time.return_value = (
                time.time() + gcal_oauth._STATE_MAX_AGE_SECONDS + 1
            )
            with pytest.raises(gcal_oauth.GoogleCalendarError):
                gcal_oauth._verify_state(state)


@pytest.mark.asyncio
class TestHandleCallback:
    async def test_a_successful_callback_stores_an_encrypted_connection(
        self, async_session
    ):
        org = await _org(async_session, "callback-happy")
        user = await _user(async_session, "callback-happy")
        state = gcal_oauth._sign_state(organization_id=org.id, user_id=user.id)

        with patch(
            "api.services.integrations.google_calendar.oauth.httpx.AsyncClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(
                200,
                {
                    "access_token": "at-123",
                    "refresh_token": "rt-456",
                    "expires_in": 3600,
                },
            )
            mock_client.get.return_value = _mock_response(
                200, {"email": "ops@nautomationlabs.com"}
            )
            mock_cls.return_value.__aenter__.return_value = mock_client

            result = await gcal_oauth.handle_callback(
                async_session, code="auth-code-xyz", state=state
            )

        assert result.organization_id == org.id
        assert result.user_id == user.id

        from sqlalchemy import select

        row = await async_session.scalar(
            select(GoogleCalendarConnectionModel).where(
                GoogleCalendarConnectionModel.organization_id == org.id
            )
        )
        assert row is not None
        assert row.connected_email == "ops@nautomationlabs.com"
        assert row.encrypted_refresh_token != "rt-456"  # never plaintext
        cipher = Fernet(TEST_SECRET.encode())
        assert cipher.decrypt(row.encrypted_refresh_token.encode()) == b"rt-456"

    async def test_a_callback_without_a_refresh_token_fails_loudly(self, async_session):
        """Google can omit the refresh token on a repeat consent. Storing an
        access-token-only connection would look connected and silently stop
        working an hour later, so this must raise rather than half-succeed."""
        org = await _org(async_session, "callback-norefresh")
        state = gcal_oauth._sign_state(organization_id=org.id, user_id=1)

        with patch(
            "api.services.integrations.google_calendar.oauth.httpx.AsyncClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(
                200, {"access_token": "at-123", "expires_in": 3600}
            )
            mock_cls.return_value.__aenter__.return_value = mock_client

            with pytest.raises(gcal_oauth.GoogleCalendarError):
                await gcal_oauth.handle_callback(
                    async_session, code="auth-code-xyz", state=state
                )

    async def test_a_forged_state_never_reaches_the_token_exchange(self, async_session):
        with patch(
            "api.services.integrations.google_calendar.oauth.httpx.AsyncClient"
        ) as mock_cls:
            with pytest.raises(gcal_oauth.GoogleCalendarError):
                await gcal_oauth.handle_callback(
                    async_session, code="auth-code-xyz", state="not-a-real-state"
                )
            mock_cls.assert_not_called()

    async def test_reconnecting_replaces_the_existing_row_rather_than_adding_one(
        self, async_session
    ):
        """One organization, one connection -- see the model docstring."""
        org = await _org(async_session, "callback-reconnect")
        user = await _user(async_session, "callback-reconnect")

        async def _connect(refresh_token: str, email: str):
            state = gcal_oauth._sign_state(organization_id=org.id, user_id=user.id)
            with patch(
                "api.services.integrations.google_calendar.oauth.httpx.AsyncClient"
            ) as mock_cls:
                mock_client = AsyncMock()
                mock_client.post.return_value = _mock_response(
                    200,
                    {
                        "access_token": "at",
                        "refresh_token": refresh_token,
                        "expires_in": 3600,
                    },
                )
                mock_client.get.return_value = _mock_response(200, {"email": email})
                mock_cls.return_value.__aenter__.return_value = mock_client
                await gcal_oauth.handle_callback(async_session, code="c", state=state)

        await _connect("rt-first", "first@example.com")
        await _connect("rt-second", "second@example.com")

        from sqlalchemy import select

        rows = (
            await async_session.scalars(
                select(GoogleCalendarConnectionModel).where(
                    GoogleCalendarConnectionModel.organization_id == org.id
                )
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].connected_email == "second@example.com"


@pytest.mark.asyncio
class TestGetValidAccessToken:
    async def test_no_connection_raises_not_connected(self, async_session):
        org = await _org(async_session, "token-none")
        with pytest.raises(gcal_oauth.GoogleCalendarNotConnectedError):
            await gcal_oauth.get_valid_access_token(
                async_session, organization_id=org.id
            )

    async def test_a_cached_unexpired_token_is_returned_without_a_refresh_call(
        self, async_session
    ):
        org = await _org(async_session, "token-cached")
        cipher = Fernet(TEST_SECRET.encode())
        row = GoogleCalendarConnectionModel(
            organization_id=org.id,
            encrypted_refresh_token=cipher.encrypt(b"rt").decode(),
            encrypted_access_token=cipher.encrypt(b"cached-at").decode(),
            access_token_expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        async_session.add(row)
        await async_session.flush()

        with patch(
            "api.services.integrations.google_calendar.oauth.httpx.AsyncClient"
        ) as mock_cls:
            token = await gcal_oauth.get_valid_access_token(
                async_session, organization_id=org.id
            )
            mock_cls.assert_not_called()
        assert token == "cached-at"

    async def test_an_expiring_token_is_refreshed_and_recached(self, async_session):
        org = await _org(async_session, "token-refresh")
        cipher = Fernet(TEST_SECRET.encode())
        row = GoogleCalendarConnectionModel(
            organization_id=org.id,
            encrypted_refresh_token=cipher.encrypt(b"rt-durable").decode(),
            encrypted_access_token=cipher.encrypt(b"stale-at").decode(),
            # inside the refresh skew -- must be treated as needing a refresh
            access_token_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )
        async_session.add(row)
        await async_session.flush()

        with patch(
            "api.services.integrations.google_calendar.oauth.httpx.AsyncClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(
                200, {"access_token": "fresh-at", "expires_in": 3600}
            )
            mock_cls.return_value.__aenter__.return_value = mock_client

            token = await gcal_oauth.get_valid_access_token(
                async_session, organization_id=org.id
            )

        assert token == "fresh-at"
        # refresh_token used, not the stale access token
        call_kwargs = mock_client.post.call_args.kwargs
        assert call_kwargs["data"]["refresh_token"] == "rt-durable"
        assert call_kwargs["data"]["grant_type"] == "refresh_token"

        from sqlalchemy import select

        refreshed = await async_session.scalar(
            select(GoogleCalendarConnectionModel).where(
                GoogleCalendarConnectionModel.organization_id == org.id
            )
        )
        assert cipher.decrypt(refreshed.encrypted_access_token.encode()) == b"fresh-at"

    async def test_google_refusing_the_refresh_raises_a_clear_error(
        self, async_session
    ):
        """The most likely real-world cause: the customer revoked access from
        their Google Account settings. Must not look like a transient failure."""
        org = await _org(async_session, "token-revoked")
        cipher = Fernet(TEST_SECRET.encode())
        row = GoogleCalendarConnectionModel(
            organization_id=org.id,
            encrypted_refresh_token=cipher.encrypt(b"rt-revoked").decode(),
        )
        async_session.add(row)
        await async_session.flush()

        with patch(
            "api.services.integrations.google_calendar.oauth.httpx.AsyncClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(
                400, {"error": "invalid_grant"}
            )
            mock_cls.return_value.__aenter__.return_value = mock_client

            with pytest.raises(gcal_oauth.GoogleCalendarTokenError):
                await gcal_oauth.get_valid_access_token(
                    async_session, organization_id=org.id
                )

    async def test_a_secret_rotation_invalidates_the_cached_token_and_falls_through(
        self, async_session
    ):
        """Rotating PLATFORM_CREDENTIAL_SECRET must break every stored token
        the same way it breaks provider keys -- not silently keep serving a
        token encrypted under a key that no longer exists."""
        org = await _org(async_session, "token-rotated-secret")
        old_cipher = Fernet(Fernet.generate_key())
        row = GoogleCalendarConnectionModel(
            organization_id=org.id,
            encrypted_refresh_token=old_cipher.encrypt(b"rt").decode(),
            encrypted_access_token=old_cipher.encrypt(b"at").decode(),
            access_token_expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        async_session.add(row)
        await async_session.flush()

        with pytest.raises(gcal_oauth.GoogleCalendarTokenError):
            await gcal_oauth.get_valid_access_token(
                async_session, organization_id=org.id
            )


@pytest.mark.asyncio
class TestDisconnect:
    async def test_disconnect_removes_the_row_even_if_googles_revoke_call_fails(
        self, async_session
    ):
        org = await _org(async_session, "disconnect")
        cipher = Fernet(TEST_SECRET.encode())
        row = GoogleCalendarConnectionModel(
            organization_id=org.id,
            encrypted_refresh_token=cipher.encrypt(b"rt").decode(),
        )
        async_session.add(row)
        await async_session.flush()

        with patch(
            "api.services.integrations.google_calendar.oauth.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__.side_effect = Exception("network down")
            await gcal_oauth.disconnect(async_session, organization_id=org.id)

        status = await gcal_oauth.get_status(async_session, organization_id=org.id)
        assert status.connected is False

    async def test_disconnecting_an_already_disconnected_org_is_a_no_op(
        self, async_session
    ):
        org = await _org(async_session, "disconnect-noop")
        await gcal_oauth.disconnect(
            async_session, organization_id=org.id
        )  # must not raise


class TestFunctionSchema:
    def test_the_tool_name_is_sanitized_into_a_valid_function_name(self):
        tool = SimpleNamespace(
            name="Book a Demo!! (Sales)", description="d", tool_uuid="u1"
        )
        schema = gcal_client.google_calendar_function_schema(tool)
        assert schema["function"]["name"] == "book_a_demo_sales"

    def test_the_parameters_require_summary_and_date_and_time_only(self):
        tool = SimpleNamespace(name="Book", description="d", tool_uuid="u2")
        schema = gcal_client.google_calendar_function_schema(tool)
        params = schema["function"]["parameters"]
        assert set(params["required"]) == {"summary", "start_date", "start_time"}
        assert "attendee_email" in params["properties"]
        assert "duration_minutes" in params["properties"]


class TestCombineStartEnd:
    def test_a_valid_date_and_time_combine_correctly(self):
        start, end = gcal_client._combine_start_end("2026-08-15", "15:00", 45)
        assert start == "2026-08-15T15:00:00"
        assert end == "2026-08-15T15:45:00"

    def test_a_missing_or_zero_duration_falls_back_to_thirty_minutes(self):
        _, end = gcal_client._combine_start_end("2026-08-15", "15:00", 0)
        assert end == "2026-08-15T15:30:00"

    def test_unparseable_input_raises_rather_than_silently_producing_garbage(self):
        with pytest.raises(ValueError):
            gcal_client._combine_start_end("next tuesday", "3pm", 30)


@pytest.mark.asyncio
class TestExecuteGoogleCalendarTool:
    async def test_no_organization_id_is_reported_as_an_error_not_a_crash(self):
        tool = SimpleNamespace(name="Book", tool_uuid="u3")
        result = await gcal_client.execute_google_calendar_tool(
            tool,
            {"summary": "x", "start_date": "2026-08-15", "start_time": "15:00"},
            None,
        )
        assert result["status"] == "error"

    async def test_not_connected_is_reported_as_a_clear_actionable_error(self):
        tool = SimpleNamespace(name="Book", tool_uuid="u4")
        with patch(
            "api.services.integrations.google_calendar.client.get_valid_access_token",
            new=AsyncMock(
                side_effect=gcal_oauth.GoogleCalendarNotConnectedError("nope")
            ),
        ):
            result = await gcal_client.execute_google_calendar_tool(
                tool,
                {"summary": "Demo", "start_date": "2026-08-15", "start_time": "15:00"},
                organization_id=1,
            )
        assert result["status"] == "error"
        assert "not connected" in result["error"].lower()

    async def test_missing_summary_is_rejected_before_any_network_call(self):
        tool = SimpleNamespace(name="Book", tool_uuid="u5")
        with patch(
            "api.services.integrations.google_calendar.client.get_valid_access_token",
            new=AsyncMock(side_effect=AssertionError("should not be called")),
        ):
            result = await gcal_client.execute_google_calendar_tool(
                tool,
                {"start_date": "2026-08-15", "start_time": "15:00"},
                organization_id=1,
            )
        assert result["status"] == "error"
        assert "summary" in result["error"].lower()

    async def test_a_successful_booking_sends_the_expected_event_body(self):
        tool = SimpleNamespace(name="Book a Demo", tool_uuid="u6")

        with (
            patch(
                "api.services.integrations.google_calendar.client.get_valid_access_token",
                new=AsyncMock(return_value="access-token-xyz"),
            ),
            patch(
                "api.services.integrations.google_calendar.client.get_status",
                new=AsyncMock(
                    return_value=gcal_oauth.ConnectionStatus(
                        connected=True,
                        connected_email="ops@nautomationlabs.com",
                        calendar_id="primary",
                        updated_at=None,
                    )
                ),
            ),
            patch(
                "api.services.integrations.google_calendar.client.db_client"
            ) as mock_db,
            patch(
                "api.services.integrations.google_calendar.client.httpx.AsyncClient"
            ) as mock_cls,
        ):
            mock_db.async_session.return_value.__aenter__.return_value = AsyncMock()
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(
                200, {"id": "evt-1", "htmlLink": "https://calendar.google.com/evt-1"}
            )
            mock_cls.return_value.__aenter__.return_value = mock_client

            result = await gcal_client.execute_google_calendar_tool(
                tool,
                {
                    "summary": "Demo call with Rahul (Acme Corp)",
                    "start_date": "2026-08-15",
                    "start_time": "15:00",
                    "duration_minutes": 30,
                    "attendee_email": "rahul@acme.example",
                },
                organization_id=7,
            )

        assert result["status"] == "success"
        assert result["data"]["event_id"] == "evt-1"

        call_kwargs = mock_client.post.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer access-token-xyz"
        body = call_kwargs["json"]
        assert body["summary"] == "Demo call with Rahul (Acme Corp)"
        assert body["start"]["dateTime"] == "2026-08-15T15:00:00"
        assert body["end"]["dateTime"] == "2026-08-15T15:30:00"
        assert body["attendees"] == [{"email": "rahul@acme.example"}]
        # Sending an invite only when an attendee was actually given.
        assert call_kwargs["params"] == {"sendUpdates": "all"}

    async def test_google_rejecting_the_request_is_reported_not_raised(self):
        tool = SimpleNamespace(name="Book", tool_uuid="u7")

        with (
            patch(
                "api.services.integrations.google_calendar.client.get_valid_access_token",
                new=AsyncMock(return_value="access-token-xyz"),
            ),
            patch(
                "api.services.integrations.google_calendar.client.get_status",
                new=AsyncMock(
                    return_value=gcal_oauth.ConnectionStatus(
                        connected=True,
                        connected_email=None,
                        calendar_id="primary",
                        updated_at=None,
                    )
                ),
            ),
            patch(
                "api.services.integrations.google_calendar.client.db_client"
            ) as mock_db,
            patch(
                "api.services.integrations.google_calendar.client.httpx.AsyncClient"
            ) as mock_cls,
        ):
            mock_db.async_session.return_value.__aenter__.return_value = AsyncMock()
            mock_client = AsyncMock()
            mock_client.post.return_value = _mock_response(403, {"error": "forbidden"})
            mock_cls.return_value.__aenter__.return_value = mock_client

            result = await gcal_client.execute_google_calendar_tool(
                tool,
                {"summary": "Demo", "start_date": "2026-08-15", "start_time": "15:00"},
                organization_id=7,
            )

        assert result["status"] == "error"

    async def test_a_conflicting_event_blocks_the_booking_and_names_it(self):
        """Two callers asking for the same slot must not both get an event --
        the tool has to refuse the second one rather than silently double-book."""
        tool = SimpleNamespace(name="Book", tool_uuid="u8")

        with (
            patch(
                "api.services.integrations.google_calendar.client.get_valid_access_token",
                new=AsyncMock(return_value="access-token-xyz"),
            ),
            patch(
                "api.services.integrations.google_calendar.client.get_status",
                new=AsyncMock(
                    return_value=gcal_oauth.ConnectionStatus(
                        connected=True,
                        connected_email=None,
                        calendar_id="primary",
                        updated_at=None,
                    )
                ),
            ),
            patch(
                "api.services.integrations.google_calendar.client.db_client"
            ) as mock_db,
            patch(
                "api.services.integrations.google_calendar.client.httpx.AsyncClient"
            ) as mock_cls,
        ):
            mock_db.async_session.return_value.__aenter__.return_value = AsyncMock()
            mock_client = AsyncMock()
            mock_client.get.return_value = _mock_response(
                200,
                {
                    "items": [
                        {
                            "status": "confirmed",
                            "summary": "Demo call with Nitesh (Reliance)",
                        }
                    ]
                },
            )
            mock_cls.return_value.__aenter__.return_value = mock_client

            result = await gcal_client.execute_google_calendar_tool(
                tool,
                {
                    "summary": "Demo call with Nitish",
                    "start_date": "2026-08-10",
                    "start_time": "12:00",
                },
                organization_id=7,
            )

        assert result["status"] == "error"
        assert "Demo call with Nitesh (Reliance)" in result["error"]
        mock_client.post.assert_not_called()  # never creates the double-booking

    async def test_a_cancelled_event_at_the_same_time_is_not_a_conflict(self):
        tool = SimpleNamespace(name="Book", tool_uuid="u9")

        with (
            patch(
                "api.services.integrations.google_calendar.client.get_valid_access_token",
                new=AsyncMock(return_value="access-token-xyz"),
            ),
            patch(
                "api.services.integrations.google_calendar.client.get_status",
                new=AsyncMock(
                    return_value=gcal_oauth.ConnectionStatus(
                        connected=True,
                        connected_email=None,
                        calendar_id="primary",
                        updated_at=None,
                    )
                ),
            ),
            patch(
                "api.services.integrations.google_calendar.client.db_client"
            ) as mock_db,
            patch(
                "api.services.integrations.google_calendar.client.httpx.AsyncClient"
            ) as mock_cls,
        ):
            mock_db.async_session.return_value.__aenter__.return_value = AsyncMock()
            mock_client = AsyncMock()
            mock_client.get.return_value = _mock_response(
                200, {"items": [{"status": "cancelled", "summary": "Old demo"}]}
            )
            mock_client.post.return_value = _mock_response(
                200, {"id": "evt-2", "htmlLink": "https://calendar.google.com/evt-2"}
            )
            mock_cls.return_value.__aenter__.return_value = mock_client

            result = await gcal_client.execute_google_calendar_tool(
                tool,
                {
                    "summary": "New demo",
                    "start_date": "2026-08-10",
                    "start_time": "12:00",
                },
                organization_id=7,
            )

        assert result["status"] == "success"

    async def test_an_availability_check_failure_does_not_block_booking(self):
        """A broken read must not turn into no bookings getting made at all --
        the write is what matters, same trade-off as the receipt-voucher path."""
        tool = SimpleNamespace(name="Book", tool_uuid="u10")

        with (
            patch(
                "api.services.integrations.google_calendar.client.get_valid_access_token",
                new=AsyncMock(return_value="access-token-xyz"),
            ),
            patch(
                "api.services.integrations.google_calendar.client.get_status",
                new=AsyncMock(
                    return_value=gcal_oauth.ConnectionStatus(
                        connected=True,
                        connected_email=None,
                        calendar_id="primary",
                        updated_at=None,
                    )
                ),
            ),
            patch(
                "api.services.integrations.google_calendar.client.db_client"
            ) as mock_db,
            patch(
                "api.services.integrations.google_calendar.client.httpx.AsyncClient"
            ) as mock_cls,
        ):
            mock_db.async_session.return_value.__aenter__.return_value = AsyncMock()
            mock_client = AsyncMock()
            mock_client.get.return_value = _mock_response(500, {"error": "boom"})
            mock_client.post.return_value = _mock_response(
                200, {"id": "evt-3", "htmlLink": "https://calendar.google.com/evt-3"}
            )
            mock_cls.return_value.__aenter__.return_value = mock_client

            result = await gcal_client.execute_google_calendar_tool(
                tool,
                {
                    "summary": "New demo",
                    "start_date": "2026-08-10",
                    "start_time": "12:00",
                },
                organization_id=7,
            )

        assert result["status"] == "success"


class TestLocalize:
    def test_attaches_the_deployment_calendar_timezone(self):
        localized = gcal_client._localize("2026-08-10T12:00:00")
        assert localized.startswith("2026-08-10T12:00:00")
        assert localized != "2026-08-10T12:00:00"  # an offset was actually added


class TestToolCreationSchema:
    """The Pydantic contract used by the /tools REST route and the MCP
    authoring surface -- see api/schemas/tool.py."""

    def test_a_google_calendar_tool_definition_validates_and_defaults_its_category(
        self,
    ):
        from api.schemas.tool import CreateToolRequest

        request = CreateToolRequest.model_validate(
            {
                "name": "Book a Demo",
                "description": "Book a demo once the caller confirms name, date and time.",
                "definition": {"type": "google_calendar"},
            }
        )
        assert request.category == "google_calendar"

    def test_category_must_match_definition_type(self):
        from pydantic import ValidationError

        from api.schemas.tool import CreateToolRequest

        with pytest.raises(ValidationError):
            CreateToolRequest.model_validate(
                {
                    "name": "bad",
                    "category": "http_api",
                    "definition": {"type": "google_calendar"},
                }
            )
