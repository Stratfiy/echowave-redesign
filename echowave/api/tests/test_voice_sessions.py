"""Live voice with Decibyl: the session record (screen 05, handoff 21).

Done when: Talk refuses honestly before anything connects (needs setup,
over the daily minutes, already live); one live session per person; stale
moves lose; mute is recorded; a reconnect discloses how much audio was lost;
End always works and twice is once; minutes are settled; a lost session is
swept; and nobody else -- colleague or other workspace -- can see or touch
a person's session.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from api import constants
from api.db import db_client
from api.db.controls_models import OperationalUsageModel
from api.services import quotas
from api.services.voice import readiness, sessions
from api.tests.support.voice import all_on, clean, client_as, make_people


@pytest.fixture
async def people(test_engine):
    p = await make_people("voice-s")
    yield p
    await clean(p)


@pytest.fixture
def ready(monkeypatch):
    """Voice set up: a transcriber, a voice and the model all present."""

    async def available(**_):
        return readiness.Readiness(
            readiness.AVAILABLE,
            config={
                "stt": {"provider": "sarvam", "model": "saaras:v3"},
                "tts": {"provider": "sarvam", "model": "bulbul:v3"},
                "language": "ta-IN",
            },
        )

    monkeypatch.setattr(readiness, "live_voice", available)


async def _start(people, user=None, **kwargs):
    user = user or people.a
    org = people.org if user is not people.c else people.other
    return await sessions.start(
        organization_id=org,
        user_id=user.id,
        thread_id=kwargs.get("thread_id"),
        language="ta-IN",
        voice=None,
        config={"stt": {"provider": "sarvam"}},
    )


@pytest.mark.asyncio
class TestOffMeansAbsent:
    async def test_every_route_is_404_while_off(self, people):
        async with client_as(people.as_a) as c:
            assert (await c.get("/api/v1/voice/readiness")).status_code == 404
            assert (await c.post("/api/v1/voice/sessions", json={})).status_code == 404
            assert (await c.get("/api/v1/voice/sessions/1")).status_code == 404


@pytest.mark.asyncio
class TestHonestBeforeConnecting:
    async def test_needs_setup_without_keys_is_said_and_nothing_starts(
        self, people, monkeypatch
    ):
        all_on(monkeypatch)
        async with client_as(people.as_a) as c:
            state = (await c.get("/api/v1/voice/readiness")).json()
            # A local instance has no transcriber, voice or model key.
            assert state["live_voice"]["state"] == "needs_setup"
            assert "needs a" in state["live_voice"]["reason"]
            assert state["live_voice"]["next_step"]
            refused = await c.post("/api/v1/voice/sessions", json={})
            assert refused.status_code == 409
            assert refused.json()["detail"]["code"] == "needs_setup"
        async with db_client.async_session() as session:
            from api.db.voice_models import VoiceSessionModel

            rows = (
                await session.scalars(
                    select(VoiceSessionModel).where(
                        VoiceSessionModel.user_id == people.a.id
                    )
                )
            ).all()
        assert rows == []

    async def test_over_the_daily_minutes_is_refused_with_the_reset(
        self, people, monkeypatch, ready
    ):
        all_on(monkeypatch)
        monkeypatch.setattr(constants, "OPERATIONAL_QUOTAS_ENABLED", True)
        monkeypatch.setattr(constants, "OPERATIONAL_QUOTA_VOICE_MINUTES", 1)
        await quotas.consume(people.a.id, quotas.VOICE_MINUTES)
        async with client_as(people.as_a) as c:
            refused = await c.post("/api/v1/voice/sessions", json={})
        assert refused.status_code == 429
        detail = refused.json()["detail"]
        assert detail["code"] == "voice_limit_reached"
        assert (
            "resets" in detail["message"].lower()
            or "reset" in detail["message"].lower()
        )

    async def test_available_starts_connecting_with_the_config_fixed(
        self, people, monkeypatch, ready
    ):
        all_on(monkeypatch)
        async with client_as(people.as_a) as c:
            started = await c.post("/api/v1/voice/sessions", json={"thread_id": None})
        assert started.status_code == 201
        body = started.json()
        assert body["state"] == "connecting"
        assert body["language"] == "ta-IN"
        assert body["config"]["tts"] == {"provider": "sarvam", "model": "bulbul:v3"}
        assert body["config"]["captions"] is True


@pytest.mark.asyncio
class TestOneLivePerPerson:
    async def test_second_start_names_the_live_one(self, people, monkeypatch, ready):
        all_on(monkeypatch)
        async with client_as(people.as_a) as c:
            first = (await c.post("/api/v1/voice/sessions", json={})).json()
            second = await c.post("/api/v1/voice/sessions", json={})
        assert second.status_code == 409
        assert second.json()["detail"] == {
            "code": "already_live",
            "message": "You already have a live voice session open.",
            "session_id": first["id"],
        }

    async def test_two_tabs_racing_open_one(self, people):
        results = await asyncio.gather(
            _start(people), _start(people), return_exceptions=True
        )
        opened = [r for r in results if isinstance(r, dict)]
        refused = [r for r in results if isinstance(r, sessions.AlreadyLive)]
        assert len(opened) == 1 and len(refused) == 1

    async def test_a_colleague_has_their_own(self, people):
        await _start(people)
        theirs = await _start(people, user=people.b)
        assert theirs["state"] == "connecting"

    async def test_after_end_a_new_one_starts(self, people):
        first = await _start(people)
        await sessions.end(
            organization_id=people.org, user_id=people.a.id, session_id=first["id"]
        )
        assert (await _start(people))["id"] != first["id"]


@pytest.mark.asyncio
class TestMoves:
    async def test_live_then_mute_then_stale_loses(self, people):
        s = await _start(people)
        live = await sessions.move(
            organization_id=people.org,
            user_id=people.a.id,
            session_id=s["id"],
            expected_version=s["state_version"],
            to="live",
            phase="listening",
        )
        assert live["state"] == "live" and live["connected_at"]
        muted = await sessions.move(
            organization_id=people.org,
            user_id=people.a.id,
            session_id=s["id"],
            expected_version=live["state_version"],
            to="live",
            muted=True,
        )
        assert muted["muted"] is True
        with pytest.raises(sessions.Stale) as stale:
            await sessions.move(
                organization_id=people.org,
                user_id=people.a.id,
                session_id=s["id"],
                expected_version=live["state_version"],
                to="live",
                muted=False,
            )
        assert stale.value.current["muted"] is True

    async def test_reconnect_says_how_much_audio_was_lost(self, people):
        s = await _start(people)
        t0 = datetime.now(UTC)
        live = await sessions.move(
            organization_id=people.org,
            user_id=people.a.id,
            session_id=s["id"],
            expected_version=s["state_version"],
            to="live",
            now=t0,
        )
        dropped = await sessions.move(
            organization_id=people.org,
            user_id=people.a.id,
            session_id=s["id"],
            expected_version=live["state_version"],
            to="reconnecting",
            now=t0 + timedelta(seconds=10),
        )
        back = await sessions.move(
            organization_id=people.org,
            user_id=people.a.id,
            session_id=s["id"],
            expected_version=dropped["state_version"],
            to="live",
            now=t0 + timedelta(seconds=14),
        )
        assert back["reconnects"] == 1
        gap = back["gaps"][-1]
        assert gap["lost_ms"] == 4000 and gap["audio_lost"] is True
        assert back["lost_ms"] == 4000
        assert sessions.gap_sentence(gap) == (
            "Reconnected. About 4 seconds of audio was lost; anything you said "
            "then was not heard."
        )

    async def test_an_ended_session_never_comes_back(self, people):
        s = await _start(people)
        ended = await sessions.end(
            organization_id=people.org, user_id=people.a.id, session_id=s["id"]
        )
        with pytest.raises(sessions.NotAllowed):
            await sessions.move(
                organization_id=people.org,
                user_id=people.a.id,
                session_id=s["id"],
                expected_version=ended["state_version"],
                to="live",
            )

    async def test_dropped_audio_waits_in_reconnecting(self, people):
        s = await _start(people)
        await sessions.move(
            organization_id=people.org,
            user_id=people.a.id,
            session_id=s["id"],
            expected_version=s["state_version"],
            to="live",
        )
        await sessions.server_lost_audio(s["id"])
        now = await sessions.get(
            organization_id=people.org, user_id=people.a.id, session_id=s["id"]
        )
        assert now["state"] == "reconnecting"
        assert now["gaps"][-1]["ended_at"] is None


@pytest.mark.asyncio
class TestEnd:
    async def test_end_always_works_and_twice_is_once(self, people, monkeypatch):
        all_on(monkeypatch)
        s = await _start(people)
        async with client_as(people.as_a) as c:
            first = await c.post(f"/api/v1/voice/sessions/{s['id']}/end", json={})
            again = await c.post(f"/api/v1/voice/sessions/{s['id']}/end", json={})
        assert first.status_code == 200 and first.json()["state"] == "ended"
        assert again.json()["state_version"] == first.json()["state_version"]
        assert first.json()["end_reason"] == "user_ended"

    async def test_minutes_are_settled_after_the_first(self, people, monkeypatch):
        monkeypatch.setattr(constants, "OPERATIONAL_QUOTAS_ENABLED", True)
        s = await _start(people)
        t0 = datetime.now(UTC)
        await sessions.move(
            organization_id=people.org,
            user_id=people.a.id,
            session_id=s["id"],
            expected_version=s["state_version"],
            to="live",
            now=t0,
        )
        await sessions.end(
            organization_id=people.org,
            user_id=people.a.id,
            session_id=s["id"],
            now=t0 + timedelta(minutes=2, seconds=30),
        )
        async with db_client.async_session() as session:
            used = (
                await session.scalars(
                    select(OperationalUsageModel.used).where(
                        OperationalUsageModel.user_id == people.a.id,
                        OperationalUsageModel.kind == quotas.VOICE_MINUTES,
                    )
                )
            ).all()
        # Three minutes started; the first was taken when audio connected.
        assert sum(used) == 2

    async def test_a_lost_session_is_swept(self, people, monkeypatch):
        monkeypatch.setattr(constants, "VOICE_SESSION_STALE_SECONDS", 60)
        s = await _start(people)
        later = datetime.now(UTC) + timedelta(minutes=5)
        assert await sessions.sweep_stale(user_id=people.a.id, now=later) == 1
        now = await sessions.get(
            organization_id=people.org, user_id=people.a.id, session_id=s["id"]
        )
        assert now["state"] == "ended" and now["end_reason"] == "lost"


@pytest.mark.asyncio
class TestPrivate:
    async def test_nobody_else_sees_or_ends_it(self, people, monkeypatch):
        all_on(monkeypatch)
        s = await _start(people)
        for other in (people.as_b, people.as_c):
            async with client_as(other) as c:
                assert (
                    await c.get(f"/api/v1/voice/sessions/{s['id']}")
                ).status_code == 404
                assert (
                    await c.post(f"/api/v1/voice/sessions/{s['id']}/end", json={})
                ).status_code == 404
                assert (
                    await c.post(
                        f"/api/v1/voice/sessions/{s['id']}/move",
                        json={"expected_version": 1, "to": "ended"},
                    )
                ).status_code == 404
        still = await sessions.get(
            organization_id=people.org, user_id=people.a.id, session_id=s["id"]
        )
        assert still["state"] == "connecting"

    async def test_the_owner_reads_it(self, people, monkeypatch):
        all_on(monkeypatch)
        s = await _start(people)
        async with client_as(people.as_a) as c:
            read = await c.get(f"/api/v1/voice/sessions/{s['id']}")
            beat = await c.post(f"/api/v1/voice/sessions/{s['id']}/heartbeat")
        assert read.status_code == 200 and read.json()["id"] == s["id"]
        assert beat.status_code == 200


@pytest.mark.asyncio
class TestSignaling:
    """The voice socket reuses the workflow signaling with its own gate."""

    class _Socket:
        def __init__(self):
            self.sent = []

        async def send_json(self, message):
            self.sent.append(message)

    async def test_refuses_an_ended_session(self, people, monkeypatch, ready):
        from api.routes.webrtc_signaling import voice_signaling_manager

        all_on(monkeypatch)
        s = await _start(people)
        await sessions.end(
            organization_id=people.org, user_id=people.a.id, session_id=s["id"]
        )
        ws = self._Socket()
        ok = await voice_signaling_manager._authorize_start(
            ws, 0, s["id"], people.org, people.a
        )
        assert ok is False
        assert ws.sent[0]["payload"]["error_type"] == "session_ended"

    async def test_refuses_needs_setup(self, people, monkeypatch):
        from api.routes.webrtc_signaling import voice_signaling_manager

        all_on(monkeypatch)
        s = await _start(people)
        ws = self._Socket()
        ok = await voice_signaling_manager._authorize_start(
            ws, 0, s["id"], people.org, people.a
        )
        assert ok is False and ws.sent[0]["payload"]["error_type"] == "needs_setup"

    async def test_refuses_someone_elses_session(self, people, monkeypatch, ready):
        from api.routes.webrtc_signaling import voice_signaling_manager

        all_on(monkeypatch)
        s = await _start(people)
        ws = self._Socket()
        ok = await voice_signaling_manager._authorize_start(
            ws, 0, s["id"], people.org, people.b
        )
        assert ok is False and ws.sent[0]["payload"]["error_type"] == "session_ended"

    async def test_accepts_the_owners_live_able_session(
        self, people, monkeypatch, ready
    ):
        from api.routes.webrtc_signaling import voice_signaling_manager

        all_on(monkeypatch)
        s = await _start(people)
        ws = self._Socket()
        assert await voice_signaling_manager._authorize_start(
            ws, 0, s["id"], people.org, people.a
        )
        assert ws.sent == []

    async def test_never_touches_the_workflow_sender_registry(self, people):
        from api.routes.webrtc_signaling import voice_signaling_manager
        from api.services.pipecat import ws_sender_registry

        async def sender(_message):
            return None

        ws_sender_registry.register_ws_sender(4242, sender)
        try:
            voice_signaling_manager._on_websocket_closed(4242)
            assert ws_sender_registry.get_ws_sender(4242) is sender
        finally:
            ws_sender_registry.unregister_ws_sender(4242)


@pytest.mark.asyncio
class TestAMemberCanTalkFromTheStartScreen:
    """Phase 3, found as a plain member with private threads on: Talk from
    Chat's start screen (no thread chosen) failed with "Thread not found",
    because the original conversation was not theirs. Voice now starts in a
    new conversation of the person's own; a named thread that is somebody
    else's is still not found."""

    async def test_no_thread_starts_a_new_one_of_their_own(
        self, people, monkeypatch, ready
    ):
        all_on(monkeypatch)
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        async with client_as(people.as_b) as c:
            started = await c.post("/api/v1/voice/sessions", json={})
        assert started.status_code == 201, started.text
        minted = started.json()["thread_id"]
        assert minted and len(minted) == 36

    async def test_someone_elses_thread_is_still_not_found(
        self, people, monkeypatch, ready
    ):
        from api.services.voice import brain

        all_on(monkeypatch)
        monkeypatch.setattr(constants, "DECIBYL_PRIVATE_THREADS_ENABLED", True)
        ledger = brain.TurnLedger(
            session_id=0,
            organization_id=people.org,
            user_id=people.a.id,
            thread_id="t-asha",
        )
        await brain.record_line(ledger, "Asha's own line")
        async with client_as(people.as_b) as c:
            refused = await c.post(
                "/api/v1/voice/sessions", json={"thread_id": "t-asha"}
            )
        assert refused.status_code == 404

    async def test_with_private_threads_off_the_original_thread_is_kept(
        self, people, monkeypatch, ready
    ):
        all_on(monkeypatch)
        async with client_as(people.as_b) as c:
            started = await c.post("/api/v1/voice/sessions", json={})
        assert started.status_code == 201 and started.json()["thread_id"] is None
