"""Every model call outside a pipeline leaves a line saying what it used.

Found on 22 September 2026, auditing where tokens are recorded before
deciding how credits charge for models. Pipeline runs -- calls, text
replies, routines -- and post-call QA and classification all put their
tokens on the run's receipt. The builder client did not record anything.
It is the one door every *other* model call goes through, over raw HTTP
to Anthropic, OpenAI and Google: Decibyl's own assistant, the builder,
triggers, Decibyl's tasks, document fields, the acceptable-use check and
the knowledge-graph reviews. Probably the largest spend we have, and
invisible.

So the client now reads each vendor's usage block into the same shape the
pipeline writes (``prompt_tokens``, ``completion_tokens``,
``cache_read_input_tokens``, ``cache_creation_input_tokens``), so the one
vendor rule in ``billing/usage.llm_split_items`` splits both, and records
it -- every call, whether or not the caller said who it was for. A call
nobody attributed is written as ``unattributed``, never dropped: an absence
cannot be reviewed.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from api.db.models import ModelUsageModel
from api.services.agent_builder import client
from api.services.billing import model_usage
from api.services.billing.usage import llm_split_items

ANTHROPIC_BODY = {
    "content": [{"type": "text", "text": "Done."}],
    "usage": {
        "input_tokens": 120,
        "output_tokens": 40,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 60,
    },
}
OPENAI_BODY = {
    "choices": [{"message": {"content": "Done.", "role": "assistant"}}],
    "usage": {
        "prompt_tokens": 1000,
        "completion_tokens": 40,
        "prompt_tokens_details": {"cached_tokens": 800},
    },
}
GEMINI_BODY = {
    "candidates": [{"content": {"parts": [{"text": "Done."}]}}],
    "usageMetadata": {
        "promptTokenCount": 1000,
        "candidatesTokenCount": 30,
        "thoughtsTokenCount": 10,
        "cachedContentTokenCount": 700,
    },
}


class TestEachVendorsUsageIsRead:
    def test_anthropic(self):
        reply = client._anthropic_parse(ANTHROPIC_BODY)
        assert reply.usage == {
            "prompt_tokens": 120,
            "completion_tokens": 40,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 60,
        }

    def test_openai(self):
        reply = client._openai_parse(OPENAI_BODY)
        assert reply.usage == {
            "prompt_tokens": 1000,
            "completion_tokens": 40,
            "cache_read_input_tokens": 800,
        }

    def test_gemini_counts_thinking_as_output(self):
        # Google bills thinking tokens at the output rate and reports them
        # beside the candidates, not inside them.
        reply = client._gemini_parse(GEMINI_BODY)
        assert reply.usage == {
            "prompt_tokens": 1000,
            "completion_tokens": 40,
            "cache_read_input_tokens": 700,
        }

    def test_no_usage_block_is_none_not_zero(self):
        # "The vendor did not say" and "the vendor said nothing was used"
        # are different facts; only the second is a zero.
        body = {"content": [{"type": "text", "text": "x"}]}
        assert client._anthropic_parse(body).usage is None

    def test_the_split_rule_reads_it_the_same_way_as_the_pipeline(self):
        # Anthropic reports input net of the cache, OpenAI includes it; the
        # one rule in usage.py must see the same four numbers both ways.
        a = {
            i.component: i.quantity
            for i in llm_split_items(
                client._anthropic_parse(ANTHROPIC_BODY).usage,
                provider="anthropic",
                model="m",
            )
        }
        o = {
            i.component: i.quantity
            for i in llm_split_items(
                client._openai_parse(OPENAI_BODY).usage, provider="openai", model="m"
            )
        }
        assert a == {
            "llm_input": 120,
            "llm_cached": 900,
            "llm_cache_write": 60,
            "llm_output": 40,
        }
        assert o == {"llm_input": 200, "llm_cached": 800, "llm_output": 40}


class TestStreamedUsageIsRead:
    def test_anthropic_usage_arrives_in_two_events(self):
        state = client._StreamState()
        state.anthropic(
            {
                "type": "message_start",
                "message": {
                    "usage": {
                        "input_tokens": 50,
                        "cache_read_input_tokens": 400,
                        "cache_creation_input_tokens": 0,
                        "output_tokens": 1,
                    }
                },
            }
        )
        state.anthropic({"type": "message_delta", "usage": {"output_tokens": 77}})
        assert state.usage() == {
            "prompt_tokens": 50,
            "completion_tokens": 77,
            "cache_read_input_tokens": 400,
        }

    def test_openai_usage_is_the_last_chunk_with_no_choices(self):
        state = client._StreamState()
        state.openai({"choices": [{"delta": {"content": "hi"}}]})
        state.openai(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 300,
                    "completion_tokens": 9,
                    "prompt_tokens_details": {"cached_tokens": 256},
                },
            }
        )
        assert state.text() == "hi"
        assert state.usage() == {
            "prompt_tokens": 300,
            "completion_tokens": 9,
            "cache_read_input_tokens": 256,
        }

    def test_an_openai_stream_asks_for_its_usage(self):
        # Without stream_options OpenAI never sends the usage chunk.
        payload = client._stream_payload(
            client.OPENAI, {"model": "gpt", "messages": []}
        )
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}


def _response(body: dict) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(body).encode())


@pytest.mark.asyncio
class TestEveryCallIsRecorded:
    async def _complete(self, body):
        with patch.object(
            httpx.AsyncClient, "post", AsyncMock(return_value=_response(body))
        ):
            return await client.complete(
                provider=client.ANTHROPIC,
                model="claude-x",
                api_key="k",
                system="s",
                conversation=client.Conversation(),
                tools=[],
            )

    async def test_a_scoped_call_is_attributed(self):
        with patch.object(model_usage, "_write", AsyncMock()) as write:
            with model_usage.scope(organization_id=7, feature="decibyl"):
                await self._complete(ANTHROPIC_BODY)
        row = write.await_args.args[0]
        assert row["organization_id"] == 7 and row["feature"] == "decibyl"
        assert row["provider"] == "anthropic" and row["model"] == "claude-x"
        assert row["cache_read_input_tokens"] == 900

    async def test_an_unscoped_call_is_recorded_as_unattributed(self):
        with patch.object(model_usage, "_write", AsyncMock()) as write:
            await self._complete(ANTHROPIC_BODY)
        row = write.await_args.args[0]
        assert row["organization_id"] is None
        assert row["feature"] == model_usage.UNATTRIBUTED

    async def test_the_scope_ends_with_its_block(self):
        with model_usage.scope(organization_id=7, feature="builder"):
            pass
        assert model_usage.current() == (None, model_usage.UNATTRIBUTED)

    async def test_a_recording_failure_never_costs_the_reply(self):
        with patch.object(
            model_usage, "_write", AsyncMock(side_effect=RuntimeError("db down"))
        ):
            reply = await self._complete(ANTHROPIC_BODY)
        assert reply.text == "Done."

    async def test_a_reply_without_usage_writes_nothing(self):
        with patch.object(model_usage, "_write", AsyncMock()) as write:
            await self._complete({"content": [{"type": "text", "text": "x"}]})
        write.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_row_lands_in_the_table(db_session, async_session):
    await model_usage.record(
        provider="openai",
        model="gpt-5",
        usage={"prompt_tokens": 10, "completion_tokens": 2},
    )
    rows = (await async_session.execute(select(ModelUsageModel))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert (row.provider, row.model, row.feature) == ("openai", "gpt-5", "unattributed")
    assert (row.prompt_tokens, row.completion_tokens, row.cache_read_input_tokens) == (
        10,
        2,
        0,
    )


class TestEveryCallerSaysWhatItIs:
    """An unlabelled call is still recorded, as ``unattributed``; this keeps
    the unattributed share at zero for the code we own, so a non-zero figure
    on the report means something new, not something forgotten."""

    def test_every_call_into_the_builder_client_is_inside_a_scope(self):
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        offenders: list[str] = []
        for path in (root / "services").rglob("*.py"):
            if path.name == "client.py" and path.parent.name == "agent_builder":
                continue
            source = path.read_text(encoding="utf-8")
            if "agent_builder" not in source:
                continue
            tree = ast.parse(source)
            parents: dict[ast.AST, ast.AST] = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = (
                    fn.attr
                    if isinstance(fn, ast.Attribute)
                    else fn.id
                    if isinstance(fn, ast.Name)
                    else ""
                )
                owner = (
                    fn.value.id
                    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                    else ""
                )
                is_builder = (
                    owner in ("client", "builder_client")
                    and name in ("complete", "stream")
                ) or (
                    isinstance(fn, ast.Name)
                    and name == "complete"
                    and "from api.services.agent_builder.client import" in source
                )
                if not is_builder:
                    continue
                cursor, scoped = node, False
                while cursor in parents:
                    cursor = parents[cursor]
                    if isinstance(cursor, (ast.With, ast.AsyncWith)) and any(
                        "model_usage" in ast.unparse(item.context_expr)
                        for item in cursor.items
                    ):
                        scoped = True
                        break
                if not scoped:
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
        assert offenders == [], offenders
