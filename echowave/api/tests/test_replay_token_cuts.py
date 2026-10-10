"""The replay script (scripts/replay_token_cuts.py) says what it measures.

Its cache model must behave like Claude's prompt cache or its numbers mean
nothing; its guard must refuse anything but the test environment; and a short
replay must show the flags doing what they claim on the staff page's own rows.
"""

from __future__ import annotations

import pytest

from scripts import replay_token_cuts as replay

SYSTEM = [
    {"type": "text", "text": "system " * 800, "cache_control": {"type": "ephemeral"}}
]


def _payload(messages, *, tools=None, system=None):
    return {
        "tools": tools or [],
        "system": system or SYSTEM,
        "messages": messages,
    }


def _tool(i, mark=False):
    tool = {"name": f"t{i}", "description": "d " * 1200, "input_schema": {}}
    if mark:
        tool["cache_control"] = {"type": "ephemeral"}
    return tool


class TestTheCacheModel:
    def test_a_repeat_is_read_and_the_first_is_written(self):
        sim = replay.CacheSim()
        request = _payload([{"role": "user", "content": "hello"}])
        first = sim.send(request, model="claude-sonnet", now=0)
        again = sim.send(request, model="claude-sonnet", now=30)
        assert first["read"] == 0 and first["write"] > 0
        assert again["read"] == first["write"] and again["write"] == 0

    def test_an_entry_lives_five_minutes(self):
        sim = replay.CacheSim()
        request = _payload([{"role": "user", "content": "hello"}])
        sim.send(request, model="claude-sonnet", now=0)
        late = sim.send(request, model="claude-sonnet", now=replay.TTL_SECONDS + 1)
        assert late["read"] == 0 and late["write"] > 0

    def test_a_hit_refreshes_the_entry(self):
        sim = replay.CacheSim()
        request = _payload([{"role": "user", "content": "hello"}])
        sim.send(request, model="claude-sonnet", now=0)
        sim.send(request, model="claude-sonnet", now=250)
        later = sim.send(request, model="claude-sonnet", now=500)
        assert later["read"] > 0

    def test_a_change_breaks_everything_after_it_not_before(self):
        sim = replay.CacheSim()
        tools = [_tool(i, mark=(i == 2)) for i in range(3)]
        a = _payload([{"role": "user", "content": "q"}], tools=tools)
        sim.send(a, model="claude-sonnet", now=0)
        # Same tools, different system: the tools are still read.
        other = [
            {
                "type": "text",
                "text": "other " * 800,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        b = sim.send(
            _payload([{"role": "user", "content": "q"}], tools=tools, system=other),
            model="claude-sonnet",
            now=10,
        )
        tools_tokens = sum(
            x.tokens for x in replay.blocks_of(_payload([], tools=tools))[:3]
        )
        assert b["read"] == tools_tokens
        # A different tool list: nothing is read, though the system is the same.
        changed = [_tool(i + 10, mark=(i == 2)) for i in range(3)]
        c = sim.send(
            _payload([{"role": "user", "content": "q"}], tools=changed),
            model="claude-sonnet",
            now=20,
        )
        assert c["read"] == 0

    def test_a_prefix_under_the_minimum_is_not_cached(self):
        sim = replay.CacheSim()
        small = _payload(
            [{"role": "user", "content": "hi"}],
            system=[
                {
                    "type": "text",
                    "text": "short",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        )
        usage = sim.send(small, model="claude-sonnet", now=0)
        assert usage["write"] == 0 and usage["read"] == 0

    def test_the_smallest_tier_needs_four_thousand_tokens(self):
        assert replay.min_cacheable("claude-haiku-4-5") == 4096
        assert replay.min_cacheable("claude-sonnet-5") == 1024

    def test_a_breakpoint_on_the_thread_lets_the_next_message_read_it(self):
        sim = replay.CacheSim()
        history = [
            {"role": "user", "content": "one " * 400},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "two " * 400,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
        ]
        first = _payload(history + [{"role": "user", "content": "now A"}])
        sim.send(first, model="claude-sonnet", now=0)
        second = _payload(
            history
            + [
                {"role": "user", "content": "a follow up"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "now B"},
            ]
        )
        usage = sim.send(second, model="claude-sonnet", now=30)
        thread_tokens = sum(b.tokens for b in replay.blocks_of(_payload(history)))
        assert usage["read"] == thread_tokens


class TestTheGuard:
    def test_it_refuses_outside_the_test_environment(self):
        for env in ({}, {"ENVIRONMENT": "production"}, {"ENVIRONMENT": "dev"}):
            with pytest.raises(replay.Refused):
                replay.require_test_environment(live=False, env=env)

    def test_offline_in_test_is_fine_and_needs_no_key(self):
        assert (
            replay.require_test_environment(live=False, env={"ENVIRONMENT": "test"})
            is None
        )

    def test_live_needs_a_key_made_for_testing(self):
        env = {"ENVIRONMENT": "test"}
        with pytest.raises(replay.Refused):
            replay.require_test_environment(live=True, env=env)
        env["TEST_ANTHROPIC_API_KEY"] = "sk-test"
        assert replay.require_test_environment(live=True, env=env) == "sk-test"

    def test_the_production_key_under_another_name_is_refused(self):
        env = {
            "ENVIRONMENT": "test",
            "TEST_ANTHROPIC_API_KEY": "sk-same",
            "ANTHROPIC_API_KEY": "sk-same",
        }
        with pytest.raises(replay.Refused):
            replay.require_test_environment(live=True, env=env)

    def test_the_ordinary_key_is_never_read_for_a_live_run(self):
        env = {"ENVIRONMENT": "test", "ANTHROPIC_API_KEY": "sk-prod"}
        with pytest.raises(replay.Refused):
            replay.require_test_environment(live=True, env=env)


class TestTheConversations:
    def test_they_are_the_same_every_time(self):
        assert replay.synthetic_threads(3, 10, seed=1) == replay.synthetic_threads(
            3, 10, seed=1
        )
        assert replay.synthetic_threads(3, 10, seed=1) != replay.synthetic_threads(
            3, 10, seed=2
        )

    def test_some_ask_for_a_tool_in_plain_words(self):
        turns = [t for thread in replay.synthetic_threads(6, 20) for t in thread]
        assert any(t.needs for t in turns) and any(not t.needs for t in turns)


@pytest.mark.asyncio
async def test_a_short_replay_shows_what_each_flag_does_on_the_pages_own_rows():
    """The whole path, small: real turns, real request building, the staff
    page's own summary. A missed tool fails it."""
    args = replay.argparse.Namespace(
        threads=2,
        turns=10,
        seed=3,
        drawer="full",
        apps=4,
        context_chars=2000,
        gap=30.0,
        live=False,
        json=False,
    )
    table = {row["config"]: row for row in await replay.run(args, None)}
    assert set(table) == {"baseline", *replay.LEVERS, "all four"}
    base = table["baseline"]
    assert base["calls"] > 0 and base["prompt_tokens_per_call"] > 0
    assert all(row["asked_for_tool_missing"] == 0 for row in table.values())
    assert all(row["calls"] == base["calls"] for row in table.values())
    # cache_v2 sends the same prompt and caches more of it.
    assert table["cache_v2"]["prompt_tokens_per_call"] == base["prompt_tokens_per_call"]
    assert table["cache_v2"]["uncached_per_call"] < base["uncached_per_call"]
    # lean_tools sends fewer tools; cheap_routing the same prompt on a smaller model.
    assert table["lean_tools"]["tools_per_call"] < base["tools_per_call"]
    assert table["cheap_routing"]["cost_usd"] <= base["cost_usd"]
    assert "ESTIMATED" in replay.render(list(table.values()), live=False, args=args)
