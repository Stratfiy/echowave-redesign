"""Claude and other models through AWS (stream aws-gateway).

Every AWS call here is a fake: nothing in this file reaches AWS, reads AWS
credentials, or needs an account. The three Claude backends, the fallback
brain, the cheap tier, embeddings, Nova Sonic, the needs-setup state, the
Models screen and the billing records are each held to what they promise.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from api import constants
from api.services.agent_builder import client as builder_client
from api.services.agent_builder import settings as builder_settings
from api.services.aws_gateway import bedrock, cheap, fallback
from api.services.aws_gateway import claude as aws_claude
from api.services.aws_gateway import config as aws_config
from api.services.billing import model_usage

HAIKU = "claude-haiku-4-5"
HAIKU_BEDROCK = "anthropic.claude-haiku-4-5-20251001-v1:0"
OPUS = "claude-opus-5-5"
OPUS_BEDROCK = "anthropic.claude-opus-5-5"
NOVA_PRO = "amazon.nova-pro-v1:0"
NOVA_MICRO = "amazon.nova-micro-v1:0"
EMBED = "cohere.embed-v4:0"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Today's behaviour unless a test says otherwise, and no refusal left
    over from another test."""
    for name, value in {
        "CLAUDE_BACKEND": "anthropic",
        "CLAUDE_AWS_REGION": "",
        "ANTHROPIC_AWS_WORKSPACE_ID": "",
        "BEDROCK_REGION": "",
        "BEDROCK_CLAUDE_MODEL_IDS": "",
        "BEDROCK_ENABLED_MODELS": "",
        "BEDROCK_FALLBACK_MODEL": "",
        "BEDROCK_CHEAP_MODEL": "",
        "BEDROCK_EMBEDDING_MODEL": "",
        "BEDROCK_EMBEDDING_DIMENSIONS": 1536,
        "NOVA_SONIC_MODEL": "",
        "NOVA_SONIC_REGION": "",
        "NOVA_SONIC_VOICE": "",
        "AWS_FALLBACK_BRAIN_ENABLED": False,
        "AWS_CHEAP_TIER_ENABLED": False,
        "AWS_EMBEDDINGS_ENABLED": False,
        "AWS_NOVA_SONIC_ENABLED": False,
        "LAYA_URL": "",
    }.items():
        monkeypatch.setattr(constants, name, value)
    aws_config.clear_refusals()
    aws_claude._warned.clear()
    yield
    aws_config.clear_refusals()


@pytest.fixture
def usage_rows(monkeypatch):
    rows: list[dict] = []

    async def write(row):
        rows.append(row)

    monkeypatch.setattr(model_usage, "_write", write)
    return rows


def _bedrock_account(monkeypatch, *, enabled: str = ""):
    """The account as the read-only check found it on 7 Oct 2026: Mumbai
    lists Anthropic's models, and none is authorised yet."""
    monkeypatch.setattr(constants, "BEDROCK_REGION", "ap-south-1")
    monkeypatch.setattr(
        constants,
        "BEDROCK_CLAUDE_MODEL_IDS",
        f"{HAIKU}={HAIKU_BEDROCK},{OPUS}={OPUS_BEDROCK}",
    )
    monkeypatch.setattr(constants, "BEDROCK_ENABLED_MODELS", enabled)


def _aws_platform(monkeypatch):
    monkeypatch.setattr(constants, "CLAUDE_BACKEND", "aws_platform")
    monkeypatch.setattr(constants, "CLAUDE_AWS_REGION", "ap-south-1")
    monkeypatch.setattr(constants, "ANTHROPIC_AWS_WORKSPACE_ID", "wrkspc_test")


# --- fakes ----------------------------------------------------------------------


class _Model:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


class _FakeMessages:
    def __init__(self, reply=None, events=None, error=None):
        self.reply = reply
        self.events = events or []
        self.error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if kwargs.get("stream"):
            events = self.events

            async def gen():
                for event in events:
                    yield _Model(event)

            return gen()
        return _Model(self.reply)


class _FakeSDK:
    def __init__(self, messages: _FakeMessages):
        self.messages = messages


def _use_sdk(monkeypatch, messages: _FakeMessages, seen: list | None = None):
    def factory(api_key, *, timeout, max_retries=0):
        if seen is not None:
            seen.append(api_key)
        return _FakeSDK(messages)

    monkeypatch.setattr(aws_claude, "async_client", factory)


_REPLY = {
    "content": [{"type": "text", "text": "Namaste"}],
    "usage": {"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": 3},
}


class _FakeRuntime:
    """A ``bedrock-runtime`` client: Converse and InvokeModel, recorded."""

    def __init__(self, converse=None, invoke=None, error=None):
        self._converse = converse
        self._invoke = invoke
        self.error = error
        self.requests: list[dict] = []

    async def converse(self, **request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self._converse

    async def invoke_model(self, **request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error

        class Body:
            async def read(inner):
                return json.dumps(self._invoke).encode()

        return {"body": Body()}


def _use_runtime(monkeypatch, runtime: _FakeRuntime):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def factory(region=None):
        yield runtime

    monkeypatch.setattr(bedrock, "runtime_client", factory)


class _AccessDenied(Exception):
    response = {"Error": {"Code": "AccessDeniedException"}}


def _converse_reply(text="Backup here", tool=None):
    content = [{"text": text}] if text else []
    if tool:
        content.append({"toolUse": tool})
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "usage": {"inputTokens": 20, "outputTokens": 7},
    }


# --- the backend switch and the honest states ----------------------------------


class TestTheSwitch:
    def test_anthropic_is_the_default_and_produces_no_marker(self):
        assert aws_config.claude_backend() == "anthropic"
        assert aws_claude.platform_credential(HAIKU) is None

    def test_an_unknown_backend_stays_on_anthropic(self, monkeypatch):
        monkeypatch.setattr(constants, "CLAUDE_BACKEND", "vertex")
        assert aws_config.claude_backend() == "anthropic"

    def test_claude_platform_on_aws_needs_a_region_and_a_workspace(self, monkeypatch):
        monkeypatch.setattr(constants, "CLAUDE_BACKEND", "aws_platform")
        status = aws_config.claude_backend_status(HAIKU)
        assert status.state == "needs_setup" and not status.configured
        assert aws_claude.platform_credential(HAIKU) is None

        _aws_platform(monkeypatch)
        assert aws_config.claude_backend_status(HAIKU).available
        assert aws_claude.platform_credential(HAIKU) == aws_claude.AWS_PLATFORM_KEY

    def test_claude_platform_on_aws_sends_bare_model_ids(self):
        assert aws_claude.wire_model(aws_claude.AWS_PLATFORM_KEY, HAIKU) == HAIKU

    def test_bedrock_sends_the_configured_bedrock_id(self, monkeypatch):
        _bedrock_account(monkeypatch)
        assert aws_claude.wire_model(aws_claude.BEDROCK_KEY, HAIKU) == HAIKU_BEDROCK

    def test_a_marker_is_never_a_real_key(self):
        assert not aws_claude.is_aws_key("sk-ant-api03-xyz")
        assert aws_claude.backend_for_key("sk-ant-api03-xyz") == "anthropic"


class TestNeedsSetup:
    """The account today: models listed, access NOT_AUTHORIZED. Every Bedrock
    choice must say so, never fail silently or pretend to be ready."""

    def test_every_bedrock_choice_needs_setup_until_access_is_confirmed(
        self, monkeypatch
    ):
        _bedrock_account(monkeypatch)
        monkeypatch.setattr(constants, "CLAUDE_BACKEND", "bedrock")
        monkeypatch.setattr(constants, "BEDROCK_FALLBACK_MODEL", NOVA_PRO)
        monkeypatch.setattr(constants, "BEDROCK_CHEAP_MODEL", NOVA_MICRO)
        monkeypatch.setattr(constants, "BEDROCK_EMBEDDING_MODEL", EMBED)
        monkeypatch.setattr(constants, "NOVA_SONIC_MODEL", "amazon.nova-2-sonic-v1:0")
        monkeypatch.setattr(constants, "NOVA_SONIC_REGION", "ap-northeast-1")
        monkeypatch.setattr(constants, "NOVA_SONIC_VOICE", "kiara")
        for flag in (
            "AWS_FALLBACK_BRAIN_ENABLED",
            "AWS_CHEAP_TIER_ENABLED",
            "AWS_EMBEDDINGS_ENABLED",
            "AWS_NOVA_SONIC_ENABLED",
        ):
            monkeypatch.setattr(constants, flag, True)

        states = {
            "claude": aws_config.claude_backend_status(HAIKU),
            "fallback": aws_config.fallback_status(),
            "cheap": aws_config.cheap_status(),
            "embeddings": aws_config.embeddings_status(),
            "nova_sonic": aws_config.nova_sonic_status(),
        }
        for name, status in states.items():
            assert status.state == "needs_setup", name
            assert status.configured, name
            assert "BEDROCK_ENABLED_MODELS" in status.reason, name
        # Not ready means Claude stays where it works, said in the log.
        assert aws_claude.platform_credential(HAIKU) is None

    def test_confirmed_access_makes_it_available(self, monkeypatch):
        _bedrock_account(monkeypatch, enabled=HAIKU_BEDROCK)
        monkeypatch.setattr(constants, "CLAUDE_BACKEND", "bedrock")
        assert aws_config.claude_backend_status(HAIKU).available
        assert aws_claude.platform_credential(HAIKU) == aws_claude.BEDROCK_KEY
        # Opus is listed in the region but not enabled: still needs setup.
        assert aws_config.claude_backend_status(OPUS).state == "needs_setup"

    def test_a_runtime_refusal_outranks_the_operators_list(self, monkeypatch):
        _bedrock_account(monkeypatch, enabled=HAIKU_BEDROCK)
        aws_config.mark_not_authorized(HAIKU_BEDROCK, "AccessDeniedException")
        status = aws_config.bedrock_model_status(HAIKU_BEDROCK)
        assert status.state == "needs_setup"
        assert "refused" in status.reason

    def test_a_switched_off_part_is_disabled_and_not_listed(self):
        status = aws_config.fallback_status()
        assert status.state == "disabled" and not status.configured

    def test_embeddings_that_cannot_fill_the_column_need_setup(self, monkeypatch):
        _bedrock_account(monkeypatch, enabled="amazon.titan-embed-text-v2:0")
        monkeypatch.setattr(constants, "AWS_EMBEDDINGS_ENABLED", True)
        monkeypatch.setattr(
            constants, "BEDROCK_EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0"
        )
        status = aws_config.embeddings_status()
        assert status.state == "needs_setup"
        assert "1536" in status.reason


# --- the builder client on three backends --------------------------------------


def _conversation():
    conversation = builder_client.Conversation()
    conversation.add_user("Hello")
    return conversation


@pytest.mark.asyncio
class TestTheBuilderClientOnEachBackend:
    async def test_anthropic_direct_is_unchanged(self, monkeypatch, usage_rows):
        """The default path still posts to Anthropic with the key it was given."""
        posted: list = []

        class Response:
            status_code = 200
            text = ""
            headers: dict = {}

            def json(self):
                return _REPLY

        class FakeHttp:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers, json):
                posted.append((url, headers["x-api-key"], json["model"]))
                return Response()

        monkeypatch.setattr(builder_client.httpx, "AsyncClient", FakeHttp)
        reply = await builder_client.complete(
            provider="anthropic",
            model=HAIKU,
            api_key="sk-ant-test",
            system="Be brief.",
            conversation=_conversation(),
            tools=[],
        )
        assert reply.text == "Namaste"
        assert posted == [
            ("https://api.anthropic.com/v1/messages", "sk-ant-test", HAIKU)
        ]
        assert usage_rows[0]["provider"] == "anthropic"

    async def test_claude_platform_on_aws(self, monkeypatch, usage_rows):
        _aws_platform(monkeypatch)
        messages = _FakeMessages(reply=_REPLY)
        seen: list = []
        _use_sdk(monkeypatch, messages, seen)
        reply = await builder_client.complete(
            provider="anthropic",
            model=HAIKU,
            api_key=aws_claude.AWS_PLATFORM_KEY,
            system="Be brief.",
            conversation=_conversation(),
            tools=[{"name": "t", "description": "d", "parameters": {"type": "object"}}],
        )
        assert reply.text == "Namaste"
        assert seen == [aws_claude.AWS_PLATFORM_KEY]
        sent = messages.calls[0]
        assert sent["model"] == HAIKU  # bare id on Claude Platform on AWS
        assert sent["tools"][0]["name"] == "t"
        assert usage_rows[0]["provider"] == "anthropic_aws"
        assert usage_rows[0]["model"] == HAIKU
        assert usage_rows[0]["cache_read_input_tokens"] == 3

    async def test_bedrock(self, monkeypatch, usage_rows):
        _bedrock_account(monkeypatch, enabled=HAIKU_BEDROCK)
        monkeypatch.setattr(constants, "CLAUDE_BACKEND", "bedrock")
        messages = _FakeMessages(reply=_REPLY)
        _use_sdk(monkeypatch, messages)
        reply = await builder_client.complete(
            provider="anthropic",
            model=HAIKU,
            api_key=aws_claude.BEDROCK_KEY,
            system="Be brief.",
            conversation=_conversation(),
            tools=[],
        )
        assert reply.text == "Namaste"
        assert messages.calls[0]["model"] == HAIKU_BEDROCK
        assert usage_rows[0]["provider"] == "aws_bedrock"
        assert usage_rows[0]["model"] == HAIKU_BEDROCK

    async def test_bedrock_streams(self, monkeypatch, usage_rows):
        _bedrock_account(monkeypatch, enabled=HAIKU_BEDROCK)
        monkeypatch.setattr(constants, "CLAUDE_BACKEND", "bedrock")
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 9}}},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hel"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "lo"},
            },
            {"type": "message_delta", "usage": {"output_tokens": 2}},
        ]
        _use_sdk(monkeypatch, _FakeMessages(events=events))
        shown: list[str] = []

        async def on_text(text):
            shown.append(text)

        reply = await builder_client.stream(
            provider="anthropic",
            model=HAIKU,
            api_key=aws_claude.BEDROCK_KEY,
            system="s",
            conversation=_conversation(),
            on_text=on_text,
        )
        assert reply.text == "Hello"
        assert shown == ["Hel", "Hello"]
        assert usage_rows[0]["provider"] == "aws_bedrock"
        assert usage_rows[0]["prompt_tokens"] == 9
        assert usage_rows[0]["completion_tokens"] == 2

    async def test_bedrock_drops_what_it_does_not_serve(self, monkeypatch):
        _bedrock_account(monkeypatch)
        body = aws_claude.adapt_payload(
            aws_claude.BEDROCK_KEY,
            {
                "model": HAIKU,
                "messages": [],
                "mcp_servers": [{"type": "url"}],
                "tools": [
                    {"type": "web_search_20260209", "name": "web_search"},
                    {"name": "our_web_tool", "input_schema": {}},
                ],
            },
        )
        assert "mcp_servers" not in body
        assert [t["name"] for t in body["tools"]] == ["our_web_tool"]

    async def test_a_bedrock_403_is_recorded_as_needs_setup(self, monkeypatch):
        import anthropic
        import httpx

        _bedrock_account(monkeypatch, enabled=HAIKU_BEDROCK)
        monkeypatch.setattr(constants, "CLAUDE_BACKEND", "bedrock")
        request = httpx.Request("POST", "https://bedrock-runtime.example/model")
        denied = anthropic.PermissionDeniedError(
            "denied", response=httpx.Response(403, request=request), body=None
        )
        _use_sdk(monkeypatch, _FakeMessages(error=denied))
        with pytest.raises(builder_client.BuilderClientError, match="setup"):
            await builder_client.complete(
                provider="anthropic",
                model=HAIKU,
                api_key=aws_claude.BEDROCK_KEY,
                system="s",
                conversation=_conversation(),
                tools=[],
            )
        assert aws_config.bedrock_model_status(HAIKU_BEDROCK).state == "needs_setup"


@pytest.mark.asyncio
class TestTheBuilderPicksTheBackend:
    async def test_no_anthropic_key_is_needed_on_aws(self, monkeypatch):
        _aws_platform(monkeypatch)
        monkeypatch.setattr(constants, "AGENT_BUILDER_ENABLED", True)
        monkeypatch.setattr(constants, "AGENT_BUILDER_PROVIDER", "")
        monkeypatch.setattr(
            constants, "AGENT_BUILDER_PROVIDER_PREFERENCE", ("anthropic",)
        )

        async def no_key(session, *, component, provider):
            return None

        monkeypatch.setattr(
            builder_settings.platform_credentials, "resolve_api_key", no_key
        )
        chosen = await builder_settings.resolve_model(session=None)
        assert chosen.provider == "anthropic"
        assert chosen.api_key == aws_claude.AWS_PLATFORM_KEY

    async def test_on_anthropic_the_stored_key_is_used(self, monkeypatch):
        async def key(session, *, component, provider):
            return "sk-ant-stored"

        monkeypatch.setattr(
            builder_settings.platform_credentials, "resolve_api_key", key
        )
        assert await builder_settings.platform_key(None, "anthropic", HAIKU) == (
            "sk-ant-stored"
        )


# --- the fallback brain ----------------------------------------------------------


def _fallback_ready(monkeypatch):
    _bedrock_account(monkeypatch, enabled=NOVA_PRO)
    monkeypatch.setattr(constants, "AWS_FALLBACK_BRAIN_ENABLED", True)
    monkeypatch.setattr(constants, "BEDROCK_FALLBACK_MODEL", NOVA_PRO)


def _claude_fails(monkeypatch):
    async def fails(**kwargs):
        raise builder_client.BuilderClientError("The assistant hit an error.")

    monkeypatch.setattr(builder_client, "_complete_once", fails)
    monkeypatch.setattr(builder_client, "_stream_once", fails)

    async def nowhere(provider, api_key):
        return None

    monkeypatch.setattr(builder_client, "_fallback_model", nowhere)


@pytest.mark.asyncio
class TestTheFallbackBrain:
    async def test_it_answers_says_so_and_is_recorded(self, monkeypatch, usage_rows):
        _fallback_ready(monkeypatch)
        _claude_fails(monkeypatch)
        runtime = _FakeRuntime(converse=_converse_reply("Your order ships today."))
        _use_runtime(monkeypatch, runtime)

        with model_usage.scope(organization_id=7, feature="decibyl"):
            reply = await builder_client.complete(
                provider="anthropic",
                model=HAIKU,
                api_key=aws_claude.BEDROCK_KEY,
                system="Be brief.",
                conversation=_conversation(),
                tools=[],
            )
        # The client hands back the text as written and names the backup
        # model; the surfaces a person reads add the note (next test), so a
        # caller that parses the text is never handed a sentence after it.
        assert reply.text == "Your order ships today."
        assert reply.fallback_model == NOVA_PRO
        assert runtime.requests[0]["modelId"] == NOVA_PRO
        assert runtime.requests[0]["system"] == [{"text": "Be brief."}]
        assert usage_rows == [
            {
                "organization_id": 7,
                "feature": "decibyl:fallback",
                "provider": "aws_bedrock",
                "model": NOVA_PRO,
                "prompt_tokens": 20,
                "completion_tokens": 7,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
        ]

    async def test_it_streams_the_answer_with_the_note(self, monkeypatch, usage_rows):
        _fallback_ready(monkeypatch)
        _claude_fails(monkeypatch)
        _use_runtime(monkeypatch, _FakeRuntime(converse=_converse_reply("Hi")))
        shown: list[str] = []

        async def on_text(text):
            shown.append(text)

        reply = await builder_client.stream(
            provider="anthropic",
            model=HAIKU,
            api_key=aws_claude.BEDROCK_KEY,
            system="s",
            conversation=_conversation(),
            on_text=on_text,
        )
        assert shown == ["Hi"]
        assert reply.fallback_model == NOVA_PRO

    async def test_the_builder_says_a_backup_model_answered(
        self, monkeypatch, usage_rows
    ):
        from api.services.agent_builder import session as builder_session

        _fallback_ready(monkeypatch)
        _claude_fails(monkeypatch)
        _use_runtime(monkeypatch, _FakeRuntime(converse=_converse_reply("Done.")))
        model = builder_settings.BuilderModel(
            provider="anthropic", model=HAIKU, api_key=aws_claude.BEDROCK_KEY
        )
        result = await builder_session.run_turn(
            session=None,
            model=model,
            organization_id=7,
            user_id=1,
            message="Build me a reminder bot",
        )
        assert result.reply == f"Done.\n\n{fallback.NOTE}"
        # The transcript keeps the model's own words, not the note.
        assert result.conversation[-1]["content"] == "Done."

    async def test_tool_calls_come_back_as_tool_calls(self, monkeypatch, usage_rows):
        _fallback_ready(monkeypatch)
        _claude_fails(monkeypatch)
        tool = {"toolUseId": "tu_1", "name": "lookup", "input": {"q": "x"}}
        _use_runtime(monkeypatch, _FakeRuntime(converse=_converse_reply("", tool)))
        reply = await builder_client.complete(
            provider="anthropic",
            model=HAIKU,
            api_key=aws_claude.BEDROCK_KEY,
            system="s",
            conversation=_conversation(),
            tools=[{"name": "lookup", "description": "d", "parameters": {}}],
        )
        assert reply.tool_calls[0].name == "lookup"
        assert reply.tool_calls[0].arguments == {"q": "x"}

    async def test_switched_off_the_original_error_stands(self, monkeypatch):
        _claude_fails(monkeypatch)
        with pytest.raises(builder_client.BuilderClientError):
            await builder_client.complete(
                provider="anthropic",
                model=HAIKU,
                api_key=aws_claude.BEDROCK_KEY,
                system="s",
                conversation=_conversation(),
                tools=[],
            )

    async def test_not_enabled_in_aws_the_original_error_stands(self, monkeypatch):
        _fallback_ready(monkeypatch)
        monkeypatch.setattr(constants, "BEDROCK_ENABLED_MODELS", "")
        _claude_fails(monkeypatch)
        runtime = _FakeRuntime(converse=_converse_reply())
        _use_runtime(monkeypatch, runtime)
        with pytest.raises(builder_client.BuilderClientError):
            await builder_client.complete(
                provider="anthropic",
                model=HAIKU,
                api_key=aws_claude.BEDROCK_KEY,
                system="s",
                conversation=_conversation(),
                tools=[],
            )
        assert runtime.requests == []

    async def test_a_workspace_on_its_own_key_is_never_moved(self, monkeypatch):
        _fallback_ready(monkeypatch)
        _claude_fails(monkeypatch)
        runtime = _FakeRuntime(converse=_converse_reply())
        _use_runtime(monkeypatch, runtime)

        async def platform(session, *, component, provider):
            return "sk-ant-platform"

        from api.services.configuration import platform_credentials

        monkeypatch.setattr(platform_credentials, "resolve_api_key", platform)
        with pytest.raises(builder_client.BuilderClientError):
            await builder_client.complete(
                provider="anthropic",
                model=HAIKU,
                api_key="sk-ant-the-workspaces-own",
                system="s",
                conversation=_conversation(),
                tools=[],
            )
        assert runtime.requests == []

    async def test_a_refusal_from_aws_keeps_the_error_and_marks_needs_setup(
        self, monkeypatch
    ):
        _fallback_ready(monkeypatch)
        _claude_fails(monkeypatch)
        _use_runtime(monkeypatch, _FakeRuntime(error=_AccessDenied()))
        with pytest.raises(builder_client.BuilderClientError):
            await builder_client.complete(
                provider="anthropic",
                model=HAIKU,
                api_key=aws_claude.BEDROCK_KEY,
                system="s",
                conversation=_conversation(),
                tools=[],
            )
        assert aws_config.fallback_status().state == "needs_setup"


class TestTheTranscriptForConverse:
    def test_roles_alternate_and_tool_results_ride_in_a_user_turn(self):
        conversation = builder_client.Conversation()
        conversation.add_user("Find it")
        call = builder_client.ToolCall(id="c1", name="search", arguments={"q": "a"})
        conversation.add_assistant(
            builder_client.ModelReply(text="Looking", tool_calls=(call,))
        )
        conversation.add_tool_result(call, {"found": 1})
        conversation.add_user("Thanks")
        out = fallback.to_converse(conversation.messages)
        assert [m["role"] for m in out] == ["user", "assistant", "user"]
        assert out[1]["content"][1]["toolUse"]["toolUseId"] == "c1"
        assert out[2]["content"][0]["toolResult"]["toolUseId"] == "c1"
        assert out[2]["content"][1] == {"text": "Thanks"}


# --- the cheap tier ----------------------------------------------------------------


@pytest.mark.asyncio
class TestTheCheapTier:
    async def test_off_by_default_routing_is_rules_only(self):
        from api.services.routing import brain, decision

        assert not decision.enabled()
        route = await brain.route("hi")
        assert route.source == "rules"

    async def test_it_sorts_work_when_on(self, monkeypatch, usage_rows):
        from api.services.routing import brain, decision

        _bedrock_account(monkeypatch, enabled=NOVA_MICRO)
        monkeypatch.setattr(constants, "AWS_CHEAP_TIER_ENABLED", True)
        monkeypatch.setattr(constants, "BEDROCK_CHEAP_MODEL", NOVA_MICRO)
        monkeypatch.setattr(constants, "LAYA_ROUTING", "on")
        _use_runtime(
            monkeypatch,
            _FakeRuntime(
                converse=_converse_reply('{"choice": "deep", "confidence": 0.9}')
            ),
        )
        assert decision.enabled()
        route = await brain.route("hi")
        assert route.kind == "deep" and route.source == "laya"
        assert usage_rows[0]["provider"] == "aws_bedrock"
        assert usage_rows[0]["feature"] == "routing"

    async def test_a_malformed_answer_is_an_abstention(self, monkeypatch, usage_rows):
        from api.services.routing import decision

        _bedrock_account(monkeypatch, enabled=NOVA_MICRO)
        monkeypatch.setattr(constants, "AWS_CHEAP_TIER_ENABLED", True)
        monkeypatch.setattr(constants, "BEDROCK_CHEAP_MODEL", NOVA_MICRO)
        _use_runtime(
            monkeypatch, _FakeRuntime(converse=_converse_reply("probably deep"))
        )
        answer = await decision.choose("q", {"quick": "", "deep": ""}, "text")
        assert answer.label is None and answer.abstained == "malformed"

    async def test_needs_setup_is_never_asked(self, monkeypatch):
        _bedrock_account(monkeypatch)  # not enabled
        monkeypatch.setattr(constants, "AWS_CHEAP_TIER_ENABLED", True)
        monkeypatch.setattr(constants, "BEDROCK_CHEAP_MODEL", NOVA_MICRO)
        assert not cheap.available()


# --- embeddings ---------------------------------------------------------------------


def _embeddings_ready(monkeypatch):
    _bedrock_account(monkeypatch, enabled=EMBED)
    monkeypatch.setattr(constants, "AWS_EMBEDDINGS_ENABLED", True)
    monkeypatch.setattr(constants, "BEDROCK_EMBEDDING_MODEL", EMBED)


@pytest.mark.asyncio
class TestBedrockEmbeddings:
    async def test_the_factory_builds_it_only_on_the_managed_marker(self, monkeypatch):
        from api.services.gen_ai.embedding.bedrock_service import (
            BedrockEmbeddingService,
        )
        from api.services.gen_ai.embedding.factory import build_embedding_service

        service = await build_embedding_service(
            db_client=None,
            provider="aws_bedrock",
            api_key=aws_claude.BEDROCK_KEY,
            model=EMBED,
        )
        assert isinstance(service, BedrockEmbeddingService)
        with pytest.raises(ValueError):
            await build_embedding_service(
                db_client=None, provider="aws_bedrock", api_key="AKIA...", model=EMBED
            )

    async def test_it_embeds_with_cohere_v4_at_the_column_width(self, monkeypatch):
        from api.services.gen_ai.embedding.bedrock_service import (
            BedrockEmbeddingService,
        )

        _embeddings_ready(monkeypatch)
        runtime = _FakeRuntime(
            invoke={
                "embeddings": {"float": [[0.1] * 1536]},
                "meta": {"billed_units": {"input_tokens": 5}},
            }
        )
        _use_runtime(monkeypatch, runtime)
        service = BedrockEmbeddingService(db_client=None, model_id=EMBED)
        vector = await service.embed_query("kitna hua?")
        assert len(vector) == 1536
        assert service.last_usage_tokens == 5
        body = json.loads(runtime.requests[0]["body"])
        assert body["input_type"] == "search_query"
        assert body["output_dimension"] == 1536

    async def test_managed_resolution_hands_over_the_marker(self, monkeypatch):
        from api.services.configuration import managed_resolution
        from api.services.configuration.registry import DecibylEmbeddingsConfiguration

        _embeddings_ready(monkeypatch)
        section = DecibylEmbeddingsConfiguration(model="aws", use_platform_key=True)
        effective = SimpleNamespace(
            llm=None, stt=None, tts=None, realtime=None, embeddings=section
        )
        await managed_resolution.apply(effective)
        assert effective.embeddings.provider == "aws_bedrock"
        assert effective.embeddings.model == EMBED
        assert effective.embeddings.api_key == aws_claude.BEDROCK_KEY


# --- the call pipeline's managed tier ----------------------------------------------


@pytest.mark.asyncio
class TestThePipelinesManagedBrain:
    async def _resolve(self, tier="default"):
        from api.services.configuration import managed_resolution
        from api.services.configuration.registry import DecibylLLMService

        section = DecibylLLMService(provider="decibyl", model=tier, api_key="")
        effective = SimpleNamespace(
            llm=section, stt=None, tts=None, realtime=None, embeddings=None
        )
        await managed_resolution.apply(effective)
        return effective.llm

    async def test_bedrock_reuses_the_bedrock_provider(self, monkeypatch):
        _bedrock_account(monkeypatch, enabled=HAIKU_BEDROCK)
        monkeypatch.setattr(constants, "CLAUDE_BACKEND", "bedrock")
        llm = await self._resolve()
        assert llm.provider == "aws_bedrock"
        assert llm.model == HAIKU_BEDROCK
        assert llm.api_key == aws_claude.BEDROCK_KEY

        from pipecat.services.aws.llm import AWSBedrockLLMService

        from api.services.pipecat.service_factory import (
            create_llm_service_from_provider,
        )

        service = create_llm_service_from_provider(llm.provider, llm.model, llm.api_key)
        assert isinstance(service, AWSBedrockLLMService)

    async def test_claude_platform_on_aws_keeps_our_claude_service(self, monkeypatch):
        _aws_platform(monkeypatch)
        llm = await self._resolve()
        assert llm.provider == "anthropic" and llm.model == HAIKU
        assert llm.api_key == aws_claude.AWS_PLATFORM_KEY

        from api.services.pipecat.anthropic_llm import DecibylAnthropicAWSLLMService
        from api.services.pipecat.service_factory import (
            create_llm_service_from_provider,
        )

        built: list = []

        def factory(api_key, *, timeout, max_retries=0):
            built.append(api_key)
            from anthropic import AsyncAnthropic

            return AsyncAnthropic(api_key="not-used")

        monkeypatch.setattr(aws_claude, "async_client", factory)
        service = create_llm_service_from_provider(llm.provider, llm.model, llm.api_key)
        assert isinstance(service, DecibylAnthropicAWSLLMService)
        assert built == [aws_claude.AWS_PLATFORM_KEY]

    async def test_not_ready_stays_on_anthropic(self, monkeypatch):
        monkeypatch.setattr(constants, "CLAUDE_BACKEND", "bedrock")
        _bedrock_account(monkeypatch)  # access not granted

        from api.services.configuration import managed_resolution

        async def key(session, *, component, provider):
            return "sk-ant-platform"

        monkeypatch.setattr(
            managed_resolution.platform_credentials, "resolve_api_key", key
        )
        llm = await self._resolve()
        assert llm.provider == "anthropic" and llm.api_key == "sk-ant-platform"


# --- Nova Sonic ---------------------------------------------------------------------


class TestNovaSonic:
    def _ready(self, monkeypatch):
        monkeypatch.setattr(constants, "AWS_NOVA_SONIC_ENABLED", True)
        monkeypatch.setattr(constants, "NOVA_SONIC_MODEL", "amazon.nova-2-sonic-v1:0")
        monkeypatch.setattr(constants, "NOVA_SONIC_REGION", "ap-northeast-1")
        monkeypatch.setattr(constants, "NOVA_SONIC_VOICE", "kiara")

    def test_not_on_sale_until_switched_on_and_configured(self, monkeypatch):
        from api.services.configuration import managed_tiers

        assert "nova" not in managed_tiers.tiers_for("realtime")
        self._ready(monkeypatch)
        assert "nova" in managed_tiers.tiers_for("realtime")
        # Listed while access is pending, and says so.
        assert aws_config.nova_sonic_status().state == "needs_setup"
        monkeypatch.setattr(
            constants, "BEDROCK_ENABLED_MODELS", "amazon.nova-2-sonic-v1:0"
        )
        assert aws_config.nova_sonic_status().available

    @pytest.mark.parametrize("language", ["hi", "hi-IN", "en-IN"])
    def test_hindi_and_indian_english_run_on_it(self, language):
        from api.services.configuration import managed_tiers

        assert managed_tiers.realtime_tier_for_language("nova", language) == "nova"

    @pytest.mark.parametrize("language", ["ta", "te-IN", "en-US", "multi", None])
    def test_any_other_language_runs_on_the_natural_tier(self, language):
        from api.services.configuration import managed_tiers

        assert managed_tiers.realtime_tier_for_language("nova", language) == "natural"

    def test_the_compiled_configuration_honours_the_gate(self):
        from api.schemas.ai_model_configuration import (
            DecibylManagedAIModelConfiguration,
            _compile_decibyl_configuration,
        )

        tamil = DecibylManagedAIModelConfiguration(language="ta", realtime_tier="nova")
        hindi = DecibylManagedAIModelConfiguration(language="hi", realtime_tier="nova")
        assert _compile_decibyl_configuration(tamil).realtime.model == "natural"
        assert _compile_decibyl_configuration(hindi).realtime.model == "nova"


# --- Settings -> Models -----------------------------------------------------------


class TestTheModelsScreen:
    def _view(self):
        from api.services.configuration import workspace_models

        return workspace_models.view(None, platform_providers={}, keys_held={})

    def _embeddings(self, view):
        return next(s for s in view["slots"] if s["key"] == "embeddings")

    def test_nothing_new_is_listed_until_configured(self):
        values = [o["value"] for o in self._embeddings(self._view())["ours"]]
        assert values == ["tier:default"]

    def test_configured_but_not_enabled_is_listed_as_needs_setup(self, monkeypatch):
        from api.services.configuration import workspace_models

        _embeddings_ready(monkeypatch)
        monkeypatch.setattr(constants, "BEDROCK_ENABLED_MODELS", "")
        view = self._view()
        aws = next(
            o for o in self._embeddings(view)["ours"] if o["value"] == "tier:aws"
        )
        assert aws["status"] == "needs_setup"
        assert aws["status_note"] == workspace_models.NEEDS_SETUP_NOTE
        with pytest.raises(workspace_models.NotReady):
            workspace_models.choose(
                None, slot="embeddings", value="tier:aws", offered=view
            )

    def test_ready_it_can_be_chosen_and_compiles_to_the_managed_tier(self, monkeypatch):
        from api.schemas.ai_model_configuration import _compile_decibyl_configuration
        from api.services.configuration import workspace_models

        _embeddings_ready(monkeypatch)
        view = self._view()
        stored = workspace_models.choose(
            None, slot="embeddings", value="tier:aws", offered=view
        )
        assert stored.decibyl.slots["embeddings"] == "decibyl/aws"
        after = workspace_models.view(stored, platform_providers={}, keys_held={})
        assert self._embeddings(after)["current"] == "tier:aws"
        compiled = _compile_decibyl_configuration(stored.decibyl)
        assert compiled.embeddings.model == "aws"
        assert compiled.embeddings.is_managed

    def test_the_brain_says_who_serves_it_once_aws_is_ready(self, monkeypatch):
        llm = next(s for s in self._view()["slots"] if s["key"] == "llm")
        everyday = next(o for o in llm["ours"] if o["value"] == "tier:default")
        assert everyday["serves"].startswith("Anthropic")

        _aws_platform(monkeypatch)
        llm = next(s for s in self._view()["slots"] if s["key"] == "llm")
        everyday = next(o for o in llm["ours"] if o["value"] == "tier:default")
        assert everyday["serves"].startswith("Claude on AWS")


# --- billing ------------------------------------------------------------------------


class TestBilling:
    def test_pipeline_services_are_priced_under_the_door_they_used(self):
        from api.services.billing.usage import provider_from_processor

        assert (
            provider_from_processor("DecibylAnthropicAWSLLMService#0")
            == "anthropic_aws"
        )
        assert provider_from_processor("AWSBedrockLLMService#1") == "aws_bedrock"
        assert provider_from_processor("AWSNovaSonicLLMService#0") == "aws_bedrock"
        assert provider_from_processor("DecibylAnthropicLLMService#0") == "anthropic"

    def test_every_model_this_stream_can_run_has_a_rate(self):
        from api.enums import CostComponent
        from api.services.billing.default_rates import DEFAULT_RATES

        priced = {(r.component, r.provider, r.model) for r in DEFAULT_RATES}
        for provider, model in [
            ("anthropic_aws", HAIKU),
            ("anthropic_aws", OPUS),
            ("aws_bedrock", HAIKU_BEDROCK),
            ("aws_bedrock", OPUS_BEDROCK),
            ("aws_bedrock", NOVA_PRO),
            ("aws_bedrock", NOVA_MICRO),
            ("aws_bedrock", "amazon.nova-lite-v1:0"),
            ("aws_bedrock", "amazon.nova-2-sonic-v1:0"),
        ]:
            assert (CostComponent.LLM, provider, model) in priced, (provider, model)
        assert (CostComponent.EMBEDDING, "aws_bedrock", EMBED) in priced

    def test_aws_rows_are_marked_provisional(self):
        from api.services.billing.default_rates import DEFAULT_RATES

        for rate in DEFAULT_RATES:
            if rate.provider in ("anthropic_aws", "aws_bedrock"):
                assert rate.provisional, (rate.provider, rate.model)

    def test_claude_on_aws_is_split_with_its_cache_outside_the_prompt(self):
        from api.services.billing.usage import llm_split_items

        items = llm_split_items(
            {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "cache_read_input_tokens": 50,
                "cache_creation_input_tokens": 20,
            },
            provider="anthropic_aws",
            model=HAIKU,
        )
        by = {getattr(i.component, "value", i.component): i.quantity for i in items}
        assert by == {
            "llm_input": 100,
            "llm_cached": 50,
            "llm_cache_write": 20,
            "llm_output": 10,
        }


# --- the staff view -------------------------------------------------------------------


@pytest.mark.asyncio
class TestTheStaffView:
    async def test_staff_see_each_part_and_the_step_that_readies_it(self, monkeypatch):
        from httpx import ASGITransport, AsyncClient

        from api.app import app
        from api.services.auth.depends import get_superuser

        _bedrock_account(monkeypatch)
        monkeypatch.setattr(constants, "CLAUDE_BACKEND", "bedrock")
        app.dependency_overrides[get_superuser] = lambda: SimpleNamespace(id=1)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/superuser/aws-gateway")
        finally:
            app.dependency_overrides.pop(get_superuser, None)
        assert response.status_code == 200
        body = response.json()
        assert body["claude_backend"]["backend"] == "bedrock"
        # Mapped, but access is not granted: needs setup, per model too.
        assert body["claude_backend"]["state"] == "needs_setup"
        assert body["claude_backend"]["models"][HAIKU]["state"] == "needs_setup"
        assert body["fallback_brain"]["state"] == "disabled"

    async def test_nobody_else_does(self):
        from httpx import ASGITransport, AsyncClient

        from api.app import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/superuser/aws-gateway")
        assert response.status_code in (401, 403)


class TestRollback:
    """Switching a part off must not strand a workspace that chose it."""

    def test_a_stored_aws_embeddings_choice_falls_back_to_default(self, monkeypatch):
        from api.services.configuration import managed_tiers

        _embeddings_ready(monkeypatch)
        assert managed_tiers.resolve("embeddings", "aws").provider == "aws_bedrock"
        monkeypatch.setattr(constants, "AWS_EMBEDDINGS_ENABLED", False)
        upstream = managed_tiers.resolve("embeddings", "aws")
        assert upstream == managed_tiers.resolve("embeddings", "default")

    def test_a_stored_nova_tier_falls_back_to_natural(self):
        from api.services.configuration import managed_tiers

        assert managed_tiers.resolve("realtime", "nova") == managed_tiers.resolve(
            "realtime", "natural"
        )

    def test_backend_back_to_anthropic_needs_no_marker(self, monkeypatch):
        _aws_platform(monkeypatch)
        assert aws_claude.platform_credential(HAIKU) == aws_claude.AWS_PLATFORM_KEY
        monkeypatch.setattr(constants, "CLAUDE_BACKEND", "anthropic")
        assert aws_claude.platform_credential(HAIKU) is None


@pytest.mark.asyncio
class TestAutoSaysSo:
    """Decibyl's reply on Auto names the stand-in, and the timeline row
    records which model it was."""

    async def test_the_chat_reply_carries_the_note_and_the_record(self):
        from unittest.mock import AsyncMock, patch

        from api.services.agent_builder.client import ModelReply
        from api.services.workflow import connected_tools, decibyl
        from tests.test_decibyl_connected_tools import _thread

        reply = ModelReply(text="Your order ships today.", fallback_model=NOVA_PRO)
        with (
            _thread(),
            patch(
                "api.services.agent_builder.client.stream",
                new=AsyncMock(return_value=reply),
            ),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[])
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ) as record,
        ):
            body = await decibyl.answer(7, "where is my order?")
        assert body == f"Your order ships today.\n\n{fallback.NOTE}"
        payload = record.await_args_list[-1].kwargs["payload"]
        assert payload["backup_model"] == NOVA_PRO

    async def test_a_claude_reply_is_untouched(self):
        from unittest.mock import AsyncMock, patch

        from api.services.agent_builder.client import ModelReply
        from api.services.workflow import connected_tools, decibyl
        from tests.test_decibyl_connected_tools import _thread

        with (
            _thread(),
            patch(
                "api.services.agent_builder.client.stream",
                new=AsyncMock(return_value=ModelReply(text="Ships today.")),
            ),
            patch.object(
                connected_tools, "list_for_organization", AsyncMock(return_value=[])
            ),
        ):
            body = await decibyl.answer(7, "where is my order?")
        assert body == "Ships today."
