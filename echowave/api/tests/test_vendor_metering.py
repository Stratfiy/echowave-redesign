"""Every vendor unit we pay for is written down (M-1, 30 Sep 2026).

The costing audit found five kinds of platform-key spend that no table
held: Composio tool calls, Sarvam translation characters, Meta WhatsApp
messages, voice-sample characters and the knowledge graph's own OpenAI
calls. Behind ``vendor_metering`` each is now a ``model_usage`` row with a
quantity and a unit. Nothing here charges anyone.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from api import constants
from api.enums import CostComponent
from api.services.billing import model_usage, token_report
from api.services.configuration import managed_tiers
from api.services.knowledge_graph import client as graph_client
from api.services.knowledge_graph import scoping


@pytest.fixture
def metering_on(monkeypatch):
    monkeypatch.setattr(constants, "VENDOR_METERING_2026_09_ENABLED", True)


@pytest.fixture
def written(monkeypatch):
    rows: list[dict] = []

    async def fake_write(row):
        rows.append(row)

    monkeypatch.setattr(model_usage, "_write", fake_write)
    return rows


@pytest.mark.asyncio
class TestRecordUnits:
    async def test_off_by_default_writes_nothing(self, written):
        assert constants.VENDOR_METERING_2026_09_ENABLED is False
        await model_usage.record_units(
            provider="composio", model="GMAIL_SEND_EMAIL", unit="calls", quantity=1
        )
        assert written == []

    async def test_on_it_writes_the_unit_and_the_amount(self, metering_on, written):
        await model_usage.record_units(
            provider="sarvam", model="translate", unit="characters", quantity=420
        )
        assert written == [
            {
                "organization_id": None,
                "feature": model_usage.UNATTRIBUTED,
                "provider": "sarvam",
                "model": "translate",
                "quantity": 420.0,
                "unit": "characters",
            }
        ]

    async def test_an_open_scope_wins_over_what_the_caller_says(
        self, metering_on, written
    ):
        with model_usage.scope(organization_id=7, feature="decibyl"):
            await model_usage.record_units(
                provider="composio",
                model="X",
                unit="calls",
                quantity=1,
                organization_id=99,
                feature="tool_call",
            )
        assert (written[0]["organization_id"], written[0]["feature"]) == (7, "decibyl")

    async def test_with_no_scope_the_caller_may_attribute(self, metering_on, written):
        await model_usage.record_units(
            provider="composio",
            model="X",
            unit="calls",
            quantity=1,
            organization_id=99,
            feature="tool_call",
        )
        assert (written[0]["organization_id"], written[0]["feature"]) == (
            99,
            "tool_call",
        )

    @pytest.mark.parametrize("quantity", [0, -3, None, "many"])
    async def test_nothing_or_nonsense_is_not_a_row(
        self, metering_on, written, quantity
    ):
        await model_usage.record_units(
            provider="meta", model="text", unit="messages", quantity=quantity
        )
        assert written == []

    async def test_a_failed_write_never_raises(self, metering_on, monkeypatch):
        async def boom(row):
            raise RuntimeError("db away")

        monkeypatch.setattr(model_usage, "_write", boom)
        await model_usage.record_units(
            provider="meta", model="text", unit="messages", quantity=1
        )


@pytest.mark.asyncio
class TestTheCallSites:
    async def test_a_composio_call_that_reached_the_vendor_is_one_call(
        self, metering_on, written
    ):
        from api.services.integrations.composio import client as composio_client

        response = AsyncMock()
        response.status_code = 200
        response.json = lambda: {"successful": True, "data": {}}
        mock_client = AsyncMock()
        mock_client.post.return_value = response
        with (
            patch("api.services.integrations.composio.client.httpx.AsyncClient") as cls,
            patch.object(composio_client, "_headers", return_value={}),
        ):
            cls.return_value.__aenter__.return_value = mock_client
            await composio_client.execute_tool(
                tool_slug="GMAIL_SEND_EMAIL", arguments={}, organization_id=7
            )
        assert [
            (
                r["provider"],
                r["model"],
                r["unit"],
                r["quantity"],
                r["organization_id"],
                r["feature"],
            )
            for r in written
        ] == [("composio", "GMAIL_SEND_EMAIL", "calls", 1.0, 7, "tool_call")]

    async def test_a_composio_call_that_never_left_is_not_counted(
        self, metering_on, written
    ):
        from api.services.integrations.composio import client as composio_client

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("too slow")
        with (
            patch("api.services.integrations.composio.client.httpx.AsyncClient") as cls,
            patch.object(composio_client, "_headers", return_value={}),
        ):
            cls.return_value.__aenter__.return_value = mock_client
            await composio_client.execute_tool(
                tool_slug="GMAIL_SEND_EMAIL", arguments={}, organization_id=7
            )
        assert written == []

    async def test_translation_records_the_characters_sent(self, metering_on, written):
        from api.services import translation

        async def fake_post(url, key, body, client):
            return {"translated_text": "x", "source_language_code": "ta-IN"}

        with (
            patch("api.services.translation._key", new=AsyncMock(return_value="k")),
            patch("api.services.translation._post", new=fake_post),
        ):
            await translation.translate("இது ஒரு வாக்கியம்.", target="en-IN")
            await translation.transliterate("இது", target="en-IN")
        assert [
            (r["provider"], r["model"], r["unit"], r["quantity"]) for r in written
        ] == [
            ("sarvam", "translate", "characters", float(len("இது ஒரு வாக்கியம்."))),
            ("sarvam", "transliterate", "characters", 3.0),
        ]

    async def test_a_whatsapp_message_meta_accepted_is_one_message(
        self, metering_on, written
    ):
        from api.services.messaging.send import _send_meta_whatsapp

        accepted = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.1"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/x"),
        )
        refused = httpx.Response(
            400,
            json={"error": {"message": "window closed"}},
            request=httpx.Request("POST", "https://graph.facebook.com/x"),
        )
        client = AsyncMock()
        client.post.side_effect = [accepted, refused, accepted]
        creds = {"access_token": "t", "phone_number_id": "1"}
        await _send_meta_whatsapp(
            client,
            creds,
            to="+919999999999",
            body="hi",
            template={"name": "order_update", "language": "en", "params": []},
        )
        await _send_meta_whatsapp(client, creds, to="+919999999999", body="hi")
        await _send_meta_whatsapp(client, creds, to="+919999999999", body="hi")
        assert [
            (r["provider"], r["model"], r["unit"], r["feature"]) for r in written
        ] == [
            ("meta", "template:order_update", "messages", "whatsapp"),
            ("meta", "text", "messages", "whatsapp"),
        ]

    async def test_a_voice_sample_records_its_characters(self, metering_on, written):
        from api.services.configuration import voice_samples

        storage = AsyncMock()
        storage.aget_signed_url.return_value = "https://x/sample.wav"
        with (
            patch.object(voice_samples, "sample_url", new=AsyncMock(return_value=None)),
            patch.object(voice_samples, "_vendor_key", new=AsyncMock(return_value="k")),
            patch(
                "api.services.configuration.voice_synthesis.synthesise",
                new=AsyncMock(return_value=b"RIFF"),
            ),
            patch.object(voice_samples, "get_storage", return_value=storage),
        ):
            url = await voice_samples.ensure_sample_url(
                provider="sarvam", model="bulbul:v3", voice_id="anushka", language="en"
            )
        assert url == "https://x/sample.wav"
        assert [
            (r["provider"], r["model"], r["unit"], r["feature"]) for r in written
        ] == [("sarvam", "bulbul:v3", "characters", "voice_sample")]
        assert written[0]["quantity"] == float(len(voice_samples.SAMPLE_LINES["en"]))


def _openai_reply(path: str, body: dict, status: int = 200) -> httpx.Response:
    request = httpx.Request("POST", f"https://api.openai.com/v1{path}")
    return httpx.Response(status, content=json.dumps(body).encode(), request=request)


@pytest.mark.asyncio
class TestTheGraphsOwnOpenAICalls:
    async def test_a_completion_is_recorded_as_tokens(self, metering_on, written):
        reply = _openai_reply(
            "/chat/completions",
            {
                "model": "gpt-4.1-mini",
                "usage": {
                    "prompt_tokens": 1200,
                    "completion_tokens": 80,
                    "prompt_tokens_details": {"cached_tokens": 1000},
                },
            },
        )
        with model_usage.scope(organization_id=7, feature="knowledge_graph"):
            await graph_client._record_openai_reply(reply)
        assert written == [
            {
                "organization_id": 7,
                "feature": "knowledge_graph",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "prompt_tokens": 1200,
                "completion_tokens": 80,
                "cache_read_input_tokens": 1000,
                "cache_creation_input_tokens": 0,
            }
        ]

    async def test_an_embedding_is_recorded_by_its_own_unit(self, metering_on, written):
        reply = _openai_reply(
            "/embeddings",
            {"model": "text-embedding-3-small", "usage": {"prompt_tokens": 350}},
        )
        await graph_client._record_openai_reply(reply)
        assert (written[0]["unit"], written[0]["quantity"], written[0]["model"]) == (
            "embed_tokens",
            350.0,
            "text-embedding-3-small",
        )

    async def test_errors_and_other_paths_are_left_alone(self, metering_on, written):
        await graph_client._record_openai_reply(
            _openai_reply("/chat/completions", {"error": "rate"}, status=429)
        )
        await graph_client._record_openai_reply(_openai_reply("/models", {"data": []}))
        assert written == []


class TestTheGraphsClient:
    def test_the_metered_client_exists_only_when_the_flag_is_on(self, monkeypatch):
        assert graph_client.metered_openai("sk-test") is None
        monkeypatch.setattr(constants, "VENDOR_METERING_2026_09_ENABLED", True)
        client = graph_client.metered_openai("sk-test")
        assert client is not None
        hooks = client._client.event_hooks["response"]
        assert graph_client._record_openai_reply in hooks

    def test_an_episodes_organisation_is_read_back_from_its_partition(self):
        assert scoping.organization_of(scoping.group_id_for_organization(42)) == 42
        assert scoping.organization_of("somebody-elses:42") is None
        assert scoping.organization_of(None) is None


@pytest.mark.asyncio
async def test_the_token_report_counts_units_apart_from_tokens(
    db_session, async_session, metering_on
):
    await model_usage.record_units(
        provider="composio", model="GMAIL_SEND_EMAIL", unit="calls", quantity=1
    )
    await model_usage.record_units(
        provider="composio", model="GMAIL_SEND_EMAIL", unit="calls", quantity=1
    )
    await model_usage.record_units(
        provider="sarvam", model="translate", unit="characters", quantity=900
    )
    now = datetime.now(UTC)
    report = await token_report.build(
        async_session, start=now - timedelta(hours=1), end=now + timedelta(hours=1)
    )
    units = {
        (u["provider"], u["model"], u["unit"]): (u["rows"], u["quantity"])
        for u in report["units"]
    }
    assert units[("composio", "GMAIL_SEND_EMAIL", "calls")] == (2, 2.0)
    assert units[("sarvam", "translate", "characters")] == (1, 900.0)
    # A unit row is not a token call with nothing in it.
    assert not [
        l for l in report["by_model"] if l["provider"] in ("composio", "sarvam")
    ]


@pytest.mark.asyncio
async def test_the_report_for_one_account_no_longer_asks_a_run_for_its_organisation(
    db_session, async_session
):
    """``workflow_runs`` has no organization column; the filter goes through
    the workflow. This raised before 30 Sep 2026."""
    now = datetime.now(UTC)
    report = await token_report.build(
        async_session,
        start=now - timedelta(hours=1),
        end=now + timedelta(hours=1),
        organization_id=1,
    )
    assert report["organization_id"] == 1


class TestManagedRealtimeWithoutOpenAI:
    """``managed_realtime_gemini_only``: no OpenAI minute on the managed
    offering. The audit priced it at about Rs8.70 a minute before markup."""

    def test_by_default_the_premium_tier_is_openai(self):
        assert constants.MANAGED_REALTIME_GEMINI_ONLY_ENABLED is False
        up = managed_tiers.resolve(managed_tiers.REALTIME_COMPONENT, "premium")
        assert up.provider == "openai_realtime"
        assert "premium" in managed_tiers.tiers_for(managed_tiers.REALTIME_COMPONENT)

    def test_on_every_managed_realtime_tier_is_gemini_live(self, monkeypatch):
        monkeypatch.setattr(constants, "MANAGED_REALTIME_GEMINI_ONLY_ENABLED", True)
        for tier in ("default", "premium", "natural", None, "retired-name"):
            up = managed_tiers.resolve(managed_tiers.REALTIME_COMPONENT, tier)
            assert up.provider == "google_realtime", tier

    def test_on_the_premium_tier_is_not_on_sale(self, monkeypatch):
        monkeypatch.setattr(constants, "MANAGED_REALTIME_GEMINI_ONLY_ENABLED", True)
        tiers = managed_tiers.tiers_for(managed_tiers.REALTIME_COMPONENT)
        assert "premium" not in tiers and "natural" in tiers

    def test_on_readiness_no_longer_wants_an_openai_realtime_key(self, monkeypatch):
        providers = managed_tiers.upstream_providers()
        assert (managed_tiers.REALTIME_COMPONENT, "openai_realtime") in providers
        monkeypatch.setattr(constants, "MANAGED_REALTIME_GEMINI_ONLY_ENABLED", True)
        providers = managed_tiers.upstream_providers()
        assert (managed_tiers.REALTIME_COMPONENT, "openai_realtime") not in providers
        assert (managed_tiers.REALTIME_COMPONENT, "google_realtime") in providers

    def test_the_other_components_are_untouched(self, monkeypatch):
        monkeypatch.setattr(constants, "MANAGED_REALTIME_GEMINI_ONLY_ENABLED", True)
        assert managed_tiers.resolve(CostComponent.LLM, "default").provider == "openai"
        assert managed_tiers.resolve(CostComponent.TTS, "default").provider == "sarvam"
