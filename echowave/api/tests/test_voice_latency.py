"""Voice latency per turn (handoff 12; design screen 40).

Done when: the device's measures and the server's stages land on one row
per turn without either erasing the other; percentiles use nearest rank and
say when a sample is small; tool turns and missing measurements are kept
apart rather than averaged in; a person can only report on their own
session; and the staff view gives p50/p95 with sample sizes by language,
channel and provider.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api import constants
from api.services.voice import latency, sessions
from api.tests.support.voice import all_on, clean, client_as, make_people


@pytest.fixture
async def people(test_engine):
    p = await make_people("voice-l")
    yield p
    await clean(p)


async def _session(people):
    return await sessions.start(
        organization_id=people.org,
        user_id=people.a.id,
        thread_id=None,
        language="hi-IN",
        voice=None,
        config={},
    )


class TestPercentiles:
    def test_nearest_rank(self):
        values = list(range(1, 101))
        assert latency.percentile(values, 50) == 50
        assert latency.percentile(values, 95) == 95
        assert latency.percentile([700], 95) == 700
        assert latency.percentile([], 50) is None

    def test_tool_turns_and_missing_are_kept_apart(self):
        def row(**kw):
            base = dict(
                language="hi-IN",
                channel="web",
                tts_provider="sarvam",
                response_ms=None,
                interruption_ms=None,
                interrupted=False,
                tool_turn=False,
                stages={},
            )
            return SimpleNamespace(**{**base, **kw})

        rows = [
            row(response_ms=600, stages={"brain_first_text": 300}),
            row(response_ms=900),
            row(response_ms=5000, tool_turn=True),  # filler is not a response
            row(),  # never measured: unknown, not zero
            row(interrupted=True, interruption_ms=180),
        ]
        [group] = latency.summarise(rows)
        assert group["turns"] == 5 and group["tool_turns"] == 1
        assert group["response"]["samples"] == 2
        assert group["response"]["p50_ms"] == 600
        assert group["response"]["p95_ms"] == 900
        assert group["response"]["small_sample"] is True
        assert group["missing_response"] == 2
        assert group["interruption"] == {
            "samples": 1,
            "p95_ms": 180,
            "small_sample": True,
        }
        assert group["stages"]["brain_first_text"]["samples"] == 1
        assert group["stages"]["stt_final"] == {
            "samples": 0,
            "p50_ms": None,
            "p95_ms": None,
        }

    def test_groups_by_language_channel_and_provider(self):
        rows = [
            SimpleNamespace(
                language=lang,
                channel="web",
                tts_provider="sarvam",
                response_ms=500,
                interruption_ms=None,
                interrupted=False,
                tool_turn=False,
                stages={},
            )
            for lang in ("hi-IN", "ta-IN", "ta-IN")
        ]
        groups = {g["language"]: g for g in latency.summarise(rows)}
        assert groups["ta-IN"]["response"]["samples"] == 2
        assert groups["hi-IN"]["response"]["samples"] == 1


@pytest.mark.asyncio
class TestRecording:
    async def test_nothing_while_off(self, people):
        s = await _session(people)
        assert (
            await latency.record_client(
                organization_id=people.org,
                user_id=people.a.id,
                session_id=s["id"],
                turn_index=0,
                response_ms=700,
            )
            is None
        )

    async def test_device_and_server_merge_on_one_turn(self, people, monkeypatch):
        monkeypatch.setattr(constants, "VOICE_LATENCY_ENABLED", True)
        s = await _session(people)
        await latency.record_server(
            session_id=s["id"],
            turn_index=0,
            stages={
                "stt_final": 210.4,
                "brain_first_text": 380,
                "tts_first_audio": None,
                "made_up": 5,
            },
            tool_turn=False,
            stt_provider="sarvam",
            tts_provider="sarvam",
        )
        await latency.record_client(
            organization_id=people.org,
            user_id=people.a.id,
            session_id=s["id"],
            turn_index=0,
            response_ms=742.6,
        )
        # A late server write must not erase the device's measure.
        await latency.record_server(
            session_id=s["id"],
            turn_index=0,
            stages={"stt_final": 200},
            tool_turn=False,
        )
        summary = await latency.summary(organization_id=people.org)
        [group] = summary["groups"]
        assert group["language"] == "hi-IN"
        assert group["response"]["p50_ms"] == 743
        assert group["stages"]["stt_final"]["p50_ms"] == 200
        assert summary["targets"]["response_p95_ms"] == 1500

    async def test_bad_numbers_are_refused(self, people, monkeypatch):
        monkeypatch.setattr(constants, "VOICE_LATENCY_ENABLED", True)
        s = await _session(people)
        for bad in (-1, 10**7, "fast", True):
            with pytest.raises(latency.LatencyInvalid):
                await latency.record_client(
                    organization_id=people.org,
                    user_id=people.a.id,
                    session_id=s["id"],
                    turn_index=0,
                    response_ms=bad,
                )

    async def test_only_on_your_own_session(self, people, monkeypatch):
        all_on(monkeypatch)
        s = await _session(people)
        async with client_as(people.as_b) as c:
            refused = await c.post(
                f"/api/v1/voice/sessions/{s['id']}/turns",
                json={"turn_index": 0, "response_ms": 500},
            )
        assert refused.status_code == 404
        async with client_as(people.as_a) as c:
            ok = await c.post(
                f"/api/v1/voice/sessions/{s['id']}/turns",
                json={"turn_index": 1, "interruption_ms": 190, "interrupted": True},
            )
        assert ok.status_code == 200
        assert ok.json() == {
            "session_id": s["id"],
            "turn_index": 1,
            "response_ms": None,
            "interruption_ms": 190,
            "interrupted": True,
        }

    async def test_route_is_404_while_off(self, people, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_VOICE_ENABLED", True)
        s = await _session(people)
        async with client_as(people.as_a) as c:
            off = await c.post(
                f"/api/v1/voice/sessions/{s['id']}/turns", json={"turn_index": 0}
            )
        assert off.status_code == 404


@pytest.mark.asyncio
class TestStaffView:
    async def test_staff_only(self, people, monkeypatch):
        monkeypatch.setattr(constants, "VOICE_LATENCY_ENABLED", True)
        async with client_as(people.as_a) as c:
            denied = await c.get("/api/v1/admin/voice/latency")
        assert denied.status_code in (401, 403)

    async def test_staff_reads_the_summary(self, people, monkeypatch):
        from api.app import app
        from api.services.auth.depends import get_staff

        monkeypatch.setattr(constants, "VOICE_LATENCY_ENABLED", True)
        app.dependency_overrides[get_staff] = lambda: people.as_a
        try:
            async with client_as(people.as_a) as c:
                read = await c.get(
                    f"/api/v1/admin/voice/latency?organization_id={people.org}"
                )
        finally:
            app.dependency_overrides.pop(get_staff, None)
        assert read.status_code == 200
        body = read.json()
        assert "Filler is not a response" in body["definition"].replace(
            "filler", "Filler"
        )
        assert body["groups"] == []
