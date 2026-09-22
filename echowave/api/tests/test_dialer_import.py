"""CR-1: a business's own dialer, connected, and its team's calls imported.

Three things are tested, in the order they can go wrong:

1. **Each dialer is read the way its API documents** -- Exotel's bulk Call
   Details with basic auth and an IST ``DateCreated`` filter, followed page
   by page; Smartflo's call records with a bearer token, paged against
   ``count``. A refused credential says, in words, what to do.
2. **The vault holds**: credentials encrypted, never readable back but for
   the last four, and one account can neither see nor remove another's.
3. **The import is safe to run every night**: only calls a person answered
   and recorded, each once; a failure retried rather than lost; a refused
   token turning the connection to "needs attention" rather than going
   quiet; the customer's number reduced to four digits; and everything
   deleted when its 30 days are up.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from api import constants
from api.db.models import ImportedCallModel, OrganizationModel
from api.services.configuration import organization_credentials
from api.services.dialer_import import connections, exotel, importer, smartflo
from api.services.dialer_import._base import DialerAuthError, DialerCall
from api.services.dialer_import.times import IST, last_two_days_ist, yesterday_ist

SINCE = datetime(2026, 9, 21, 0, 0, 0, tzinfo=IST)
UNTIL = datetime(2026, 9, 21, 23, 59, 59, tzinfo=IST)

EXOTEL = {
    "api_key": "exo-key",
    "api_token": "exo-token-9876",
    "account_sid": "acme1",
    "subdomain": "api.in.exotel.com",
}


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setattr(
        organization_credentials,
        "PLATFORM_CREDENTIAL_SECRET",
        Fernet.generate_key().decode(),
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- 1. each dialer, read the way it documents --------------------------------


@pytest.mark.asyncio
class TestExotel:
    async def test_it_lists_a_day_page_by_page_with_basic_auth(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if "Before" not in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "Metadata": {
                            "NextPageUri": "/v1/Accounts/acme1/Calls.json?Before=abc"
                        },
                        "Calls": [
                            {
                                "Sid": "c1",
                                "Direction": "outbound-api",
                                "From": "09840012345",
                                "To": "+919876543210",
                                "Status": "completed",
                                "StartTime": "2026-09-21 10:00:05",
                                "Duration": "134",
                                "RecordingUrl": "https://rec/c1.mp3",
                            }
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "Metadata": {"NextPageUri": None},
                    "Calls": [
                        {
                            "Sid": "c2",
                            "Direction": "inbound",
                            "From": "+919812345678",
                            "Status": "completed",
                            "StartTime": "2026-09-21 11:00:00",
                            "Duration": "90",
                            "RecordingUrl": "https://rec/c2.mp3",
                            "Details": {
                                "Leg2To": "09840055555",
                                "ConversationDuration": "70",
                            },
                        }
                    ],
                },
            )

        async with _client(handler) as client:
            calls = await exotel.list_calls(EXOTEL, SINCE, UNTIL, client=client)

        first = seen[0]
        assert first.url.host == "api.in.exotel.com"
        assert first.url.path == "/v1/Accounts/acme1/Calls.json"
        assert first.url.params["DateCreated"] == (
            "gte:2026-09-21 00:00:00;lte:2026-09-21 23:59:59"
        )
        assert first.headers["Authorization"].startswith("Basic ")
        assert len(seen) == 2 and "Before=abc" in str(seen[1].url)

        outbound, inbound = calls
        # Click-to-call rings the agent first: From is the telecaller.
        assert outbound.agent_number == "09840012345"
        assert outbound.customer_number == "+919876543210"
        assert outbound.duration_seconds == 134
        assert outbound.started_at == datetime(2026, 9, 21, 10, 0, 5, tzinfo=IST)
        # Inbound: the customer calls, the second leg is the agent, and the
        # talk time is the conversation, not the ringing.
        assert inbound.agent_number == "09840055555"
        assert inbound.customer_number == "+919812345678"
        assert inbound.duration_seconds == 70

    async def test_a_refused_key_says_what_to_do(self):
        async with _client(lambda r: httpx.Response(401)) as client:
            with pytest.raises(DialerAuthError, match="Exotel refused"):
                await exotel.list_calls(EXOTEL, SINCE, UNTIL, client=client)

    async def test_the_recording_is_fetched_with_the_same_credentials(self):
        def handler(request):
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, content=b"mp3-bytes")

        call = _call("c1")
        async with _client(handler) as client:
            assert await exotel.fetch_recording(EXOTEL, call, client=client) == (
                b"mp3-bytes"
            )


@pytest.mark.asyncio
class TestSmartflo:
    async def test_it_pages_against_count_with_a_bearer_token(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            page = int(request.url.params["page"])
            results = [
                {
                    "call_id": f"s{page}-{i}",
                    "direction": "outbound",
                    "status": "answered",
                    "date": "2026-09-21",
                    "time": "12:00:00",
                    "call_duration": 95,
                    "answered_seconds": 80,
                    "recording_url": "https://cloudphone/rec?token=x",
                    "agent_name": "Divya",
                    "agent_number": "9840012345",
                    "client_number": "9876543210",
                }
                for i in range(100 if page == 1 else 20)
            ]
            return httpx.Response(200, json={"count": 120, "results": results})

        async with _client(handler) as client:
            calls = await smartflo.list_calls(
                {"api_token": "tok-1234"}, SINCE, UNTIL, client=client
            )

        assert len(seen) == 2 and len(calls) == 120
        assert seen[0].url.path == "/v1/call/records"
        assert seen[0].headers["Authorization"] == "Bearer tok-1234"
        assert seen[0].url.params["from_date"] == "2026-09-21 00:00:00"
        assert seen[0].url.params["to_date"] == "2026-09-21 23:59:59"
        first = calls[0]
        assert first.agent_name == "Divya" and first.duration_seconds == 80
        assert first.answered

    async def test_an_expired_token_asks_for_a_new_one(self):
        async with _client(lambda r: httpx.Response(401)) as client:
            with pytest.raises(DialerAuthError, match="new token"):
                await smartflo.list_calls(
                    {"api_token": "old"}, SINCE, UNTIL, client=client
                )


def test_the_nightly_window_is_the_last_two_indian_days():
    now = datetime(2026, 9, 22, 21, 0, tzinfo=timezone.utc)  # 02:30 IST, 23rd
    assert yesterday_ist(now) == (
        datetime(2026, 9, 22, 0, 0, 0, tzinfo=IST),
        datetime(2026, 9, 22, 23, 59, 59, tzinfo=IST),
    )
    since, until = last_two_days_ist(now)
    assert since == datetime(2026, 9, 21, 0, 0, 0, tzinfo=IST)
    assert until == datetime(2026, 9, 22, 23, 59, 59, tzinfo=IST)


# --- 2. the vault -------------------------------------------------------------


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


@pytest.mark.asyncio
class TestTheVault:
    async def test_credentials_are_encrypted_and_only_four_characters_show(
        self, async_session
    ):
        org = await _org(async_session, "vault")
        made = await connections.create(
            async_session,
            organization_id=org.id,
            actor_user_id=None,
            vendor="exotel",
            credentials=EXOTEL,
        )
        row = await connections.get_row(
            async_session, organization_id=org.id, connection_id=made.id
        )
        assert "exo-token-9876" not in row.encrypted_credentials
        assert made.key_last_four == "9876"
        assert "api_token" not in json.dumps(made.__dict__, default=str)
        assert connections.credentials_of(row)["api_token"] == "exo-token-9876"

    async def test_one_account_cannot_see_or_remove_anothers(self, async_session):
        mine = await _org(async_session, "mine")
        theirs = await _org(async_session, "theirs")
        made = await connections.create(
            async_session,
            organization_id=theirs.id,
            actor_user_id=None,
            vendor="smartflo",
            credentials={"api_token": "their-token"},
        )
        assert await connections.list_for(async_session, organization_id=mine.id) == []
        assert (
            await connections.get_row(
                async_session, organization_id=mine.id, connection_id=made.id
            )
            is None
        )
        assert not await connections.remove(
            async_session, organization_id=mine.id, connection_id=made.id
        )
        assert (
            len(await connections.list_for(async_session, organization_id=theirs.id))
            == 1
        )

    async def test_a_missing_field_or_an_unknown_dialer_is_refused_plainly(self):
        with pytest.raises(connections.ConnectionError_, match="account sid"):
            connections.clean_credentials("exotel", {"api_key": "k", "api_token": "t"})
        with pytest.raises(connections.ConnectionError_, match="Exotel and Tata"):
            connections.clean_credentials("knowlarity", {})
        # Exotel's subdomain is optional: most accounts are on the default.
        assert "subdomain" not in connections.clean_credentials(
            "exotel", {"api_key": "k", "api_token": "t", "account_sid": "s"}
        )


# --- 3. the nightly import ----------------------------------------------------


def _call(external_id: str, **overrides) -> DialerCall:
    values = dict(
        external_id=external_id,
        started_at=datetime(2026, 9, 21, 10, 0, tzinfo=IST),
        duration_seconds=120,
        direction="outbound",
        status="completed",
        recording_url=f"https://rec/{external_id}.mp3",
        agent_name="Divya",
        agent_number="9840012345",
        customer_number="+91 98765 43210",
    )
    values.update(overrides)
    return DialerCall(**values)


class FakeAdapter:
    def __init__(self, calls, *, fail_list=None, fail_fetch=()):
        self.calls, self.fail_list, self.fail_fetch = calls, fail_list, set(fail_fetch)
        self.fetched = []

    async def list_calls(self, credentials, since, until):
        if self.fail_list:
            raise self.fail_list
        return self.calls

    async def fetch_recording(self, credentials, call):
        self.fetched.append(call.external_id)
        if call.external_id in self.fail_fetch:
            raise RuntimeError("network blip")
        return b"audio-" + call.external_id.encode()


class FakeStorage:
    def __init__(self):
        self.files = {}

    async def acreate_file_from_bytes(self, key, data):
        self.files[key] = data
        return True

    async def adelete_file(self, key):
        self.files.pop(key, None)
        return True


class FakeTranscriber:
    async def transcribe(self, audio, *, filename, content_type, language):
        from api.services.gen_ai.transcription.base import Transcription

        assert language == ""  # let the model hear Hindi, Tamil or both
        return Transcription(
            transcript=f"transcript of {audio.decode()}",
            language="hi",
            duration_seconds=120,
            model="saaras:v2",
        )


async def _connection(session, org_id, vendor="smartflo"):
    made = await connections.create(
        session,
        organization_id=org_id,
        actor_user_id=None,
        vendor=vendor,
        credentials={"api_token": "tok"} if vendor == "smartflo" else EXOTEL,
    )
    return await connections.get_row(
        session, organization_id=org_id, connection_id=made.id
    )


async def _imported(session, org_id):
    from sqlalchemy import select

    return list(
        await session.scalars(
            select(ImportedCallModel)
            .where(ImportedCallModel.organization_id == org_id)
            .order_by(ImportedCallModel.external_id)
        )
    )


NOW = datetime(2026, 9, 22, 21, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
class TestTheNightlyImport:
    async def test_only_answered_recorded_real_conversations_are_imported(
        self, async_session
    ):
        org = await _org(async_session, "filter")
        conn = await _connection(async_session, org.id)
        adapter = FakeAdapter(
            [
                _call("keep"),
                _call("missed", status="no-answer"),
                _call("unrecorded", recording_url=None),
                _call("wrong-number", duration_seconds=8),
            ]
        )
        storage = FakeStorage()
        result = await importer.import_window(
            async_session,
            conn,
            SINCE,
            UNTIL,
            adapter=adapter,
            storage=storage,
            transcriber=FakeTranscriber(),
            now=NOW,
        )
        assert (result.listed, result.imported, result.skipped) == (4, 1, 3)
        (row,) = await _imported(async_session, org.id)
        assert row.external_id == "keep" and row.status == "transcribed"
        assert row.transcript == "transcript of audio-keep"
        assert row.transcription_model == "saaras:v2"
        assert storage.files[row.recording_key] == b"audio-keep"
        assert row.expires_at == NOW + timedelta(days=30)

    async def test_the_customers_number_is_kept_as_four_digits(self, async_session):
        org = await _org(async_session, "privacy")
        conn = await _connection(async_session, org.id)
        await importer.import_window(
            async_session,
            conn,
            SINCE,
            UNTIL,
            adapter=FakeAdapter([_call("c1")]),
            storage=FakeStorage(),
            transcriber=FakeTranscriber(),
            now=NOW,
        )
        (row,) = await _imported(async_session, org.id)
        assert row.customer_last_four == "3210"
        assert not hasattr(row, "customer_number")

    async def test_a_second_run_imports_nothing_twice(self, async_session):
        org = await _org(async_session, "twice")
        conn = await _connection(async_session, org.id)
        adapter = FakeAdapter([_call("c1"), _call("c2")])
        kwargs = dict(
            adapter=adapter,
            storage=FakeStorage(),
            transcriber=FakeTranscriber(),
            now=NOW,
        )
        await importer.import_window(async_session, conn, SINCE, UNTIL, **kwargs)
        again = await importer.import_window(
            async_session, conn, SINCE, UNTIL, **kwargs
        )
        assert again.imported == 0 and again.skipped == 2
        assert adapter.fetched == ["c1", "c2"]
        assert len(await _imported(async_session, org.id)) == 2

    async def test_a_failed_call_fails_alone_and_is_retried_next_night(
        self, async_session
    ):
        org = await _org(async_session, "retry")
        conn = await _connection(async_session, org.id)
        first = await importer.import_window(
            async_session,
            conn,
            SINCE,
            UNTIL,
            adapter=FakeAdapter([_call("ok"), _call("blip")], fail_fetch={"blip"}),
            storage=FakeStorage(),
            transcriber=FakeTranscriber(),
            now=NOW,
        )
        assert (first.imported, first.failed) == (1, 1)
        rows = {r.external_id: r for r in await _imported(async_session, org.id)}
        assert rows["blip"].status == "failed" and rows["blip"].error
        assert conn.status == connections.CONNECTED

        second = await importer.import_window(
            async_session,
            conn,
            SINCE,
            UNTIL,
            adapter=FakeAdapter([_call("ok"), _call("blip")]),
            storage=FakeStorage(),
            transcriber=FakeTranscriber(),
            now=NOW,
        )
        assert second.imported == 1
        rows = {r.external_id: r for r in await _imported(async_session, org.id)}
        assert rows["blip"].status == "transcribed" and rows["blip"].error is None
        assert len(rows) == 2

    async def test_a_refused_token_turns_the_connection_to_needs_attention(
        self, async_session
    ):
        org = await _org(async_session, "expired")
        conn = await _connection(async_session, org.id)
        result = await importer.import_window(
            async_session,
            conn,
            SINCE,
            UNTIL,
            adapter=FakeAdapter([], fail_list=DialerAuthError("Generate a new token")),
            storage=FakeStorage(),
            transcriber=FakeTranscriber(),
            now=NOW,
        )
        assert result.errors == ["Generate a new token"]
        assert conn.status == connections.NEEDS_ATTENTION
        assert conn.last_error == "Generate a new token"
        assert conn.last_synced_at is None

        # A new token that works clears it, on the next run.
        await importer.import_window(
            async_session,
            conn,
            SINCE,
            UNTIL,
            adapter=FakeAdapter([]),
            storage=FakeStorage(),
            transcriber=FakeTranscriber(),
            now=NOW,
        )
        assert conn.status == connections.CONNECTED and conn.last_error is None
        assert conn.last_synced_at == NOW

    async def test_expired_calls_are_deleted_recording_first(self, async_session):
        org = await _org(async_session, "purge")
        conn = await _connection(async_session, org.id)
        storage = FakeStorage()
        await importer.import_window(
            async_session,
            conn,
            SINCE,
            UNTIL,
            adapter=FakeAdapter([_call("old")]),
            storage=storage,
            transcriber=FakeTranscriber(),
            now=NOW - timedelta(days=31),
        )
        await importer.import_window(
            async_session,
            conn,
            SINCE,
            UNTIL,
            adapter=FakeAdapter([_call("new")]),
            storage=storage,
            transcriber=FakeTranscriber(),
            now=NOW,
        )
        purged = await importer.purge_expired(async_session, now=NOW, storage=storage)
        assert purged == 1
        assert [r.external_id for r in await _imported(async_session, org.id)] == [
            "new"
        ]
        assert list(storage.files) == [importer.recording_key(org.id, conn.id, "new")]

    async def test_disconnecting_deletes_the_recordings_too(
        self, async_session, monkeypatch
    ):
        org = await _org(async_session, "disconnect")
        conn = await _connection(async_session, org.id)
        storage = FakeStorage()
        await importer.import_window(
            async_session,
            conn,
            SINCE,
            UNTIL,
            adapter=FakeAdapter([_call("c1")]),
            storage=storage,
            transcriber=FakeTranscriber(),
            now=NOW,
        )
        import api.services.storage as storage_module

        monkeypatch.setattr(storage_module, "storage_fs", storage)
        assert await connections.remove(
            async_session, organization_id=org.id, connection_id=conn.id
        )
        assert storage.files == {}


# --- the flag -----------------------------------------------------------------


def test_every_route_is_hidden_while_the_flag_is_off(monkeypatch):
    from api.routes import dialer_connections

    monkeypatch.setattr(constants, "DIALER_IMPORT_ENABLED", False)
    with pytest.raises(HTTPException) as caught:
        dialer_connections._enabled()
    assert caught.value.status_code == 404
    for route in dialer_connections.router.routes:
        assert any(
            dep.call is dialer_connections._enabled
            for dep in route.dependant.dependencies
        ), route.path


@pytest.mark.asyncio
async def test_the_nightly_job_touches_no_dialer_while_the_flag_is_off(monkeypatch):
    from api.tasks import dialer_import

    monkeypatch.setattr(constants, "DIALER_IMPORT_ENABLED", False)

    async def boom(*args, **kwargs):
        raise AssertionError("a dialer was called with the import off")

    monkeypatch.setattr(importer, "import_window", boom)
    await dialer_import.import_dialer_calls({})


def test_the_import_and_its_purge_are_scheduled():
    from api.tasks.arq import WorkerSettings

    scheduled = {job.coroutine.__name__ for job in WorkerSettings.cron_jobs}
    assert {"import_dialer_calls", "purge_imported_calls"} <= scheduled


@pytest.mark.asyncio
async def test_a_dialer_is_not_connected_without_the_consent_terms(monkeypatch):
    from api.routes import dialer_connections

    monkeypatch.setattr(constants, "DIALER_IMPORT_ENABLED", True)
    request = dialer_connections.ConnectRequest(
        vendor="smartflo", credentials={"api_token": "tok"}
    )
    with pytest.raises(HTTPException) as caught:
        await dialer_connections.connect_dialer(request, user=object())
    assert caught.value.status_code == 400
    assert "accept" in caught.value.detail


# --- CR-3: the coach's tool ---------------------------------------------------


@pytest.mark.asyncio
class TestTheTeamCallsTool:
    async def test_it_reads_one_organizations_calls_grouped_by_caller(
        self, async_session
    ):
        from api.services.dialer_import import team_calls_tool

        mine = await _org(async_session, "coach-mine")
        theirs = await _org(async_session, "coach-theirs")
        for org, calls in (
            (
                mine,
                [_call("a1"), _call("a2", agent_name=None, agent_number="9840099999")],
            ),
            (theirs, [_call("x1", agent_name="Someone else")]),
        ):
            await importer.import_window(
                async_session,
                await _connection(async_session, org.id),
                SINCE,
                UNTIL,
                adapter=FakeAdapter(calls),
                storage=FakeStorage(),
                transcriber=FakeTranscriber(),
                now=NOW,
            )
        result = await team_calls_tool.read(
            async_session,
            organization_id=mine.id,
            arguments={"days": 7},
            now=datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
        )
        callers = {c["caller"]: c for c in result["callers"]}
        assert set(callers) == {"Divya", "9840099999"}
        assert "Someone else" not in callers
        assert callers["Divya"]["calls"][0]["transcript"] == "transcript of audio-a1"

    async def test_no_calls_is_said_rather_than_invented(self, async_session):
        from api.services.dialer_import import team_calls_tool

        org = await _org(async_session, "coach-empty")
        result = await team_calls_tool.read(
            async_session, organization_id=org.id, arguments={"days": 99}
        )
        assert result["callers"] == [] and "never invent" in result["note"]
        assert result["days"] == team_calls_tool.MAX_DAYS

    async def test_the_tool_is_made_only_while_the_import_is_on(self, monkeypatch):
        from api.services.dialer_import import team_calls_tool

        monkeypatch.setattr(constants, "DIALER_IMPORT_ENABLED", False)
        assert await team_calls_tool.ensure_tool(organization_id=1, user_id=1) is None


def test_the_coach_is_hired_with_the_team_calls_tool():
    from api.services.agent_templates import get_template

    coach = get_template("telecaller_call_coach")
    assert coach.needs_team_calls
    assert "read_team_calls" in " ".join(n.prompt for n in coach.nodes)


def test_the_dispatcher_offers_the_tool_only_while_the_import_is_on():
    """Both branch points name it, and both check the flag, so a tool row
    made while the import was on goes quiet when it is switched off."""
    import inspect

    from api.services.workflow import pipecat_engine_custom_tools as tools

    source = inspect.getsource(tools.CustomToolManager)
    assert source.count("team_calls_tool.is_team_calls_tool(tool)") == 2
    assert source.count("team_calls_tool.enabled()") == 2
