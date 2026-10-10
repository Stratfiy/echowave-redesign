"""Replay Decibyl turns with each token-cut flag on and off.

    set -a && . api/.env.test && set +a
    python -m scripts.replay_token_cuts                       # offline, synthetic
    python -m scripts.replay_token_cuts --threads 8 --turns 16 --apps 30 --json
    TEST_ANTHROPIC_API_KEY=... python -m scripts.replay_token_cuts --live

What it does. It builds a fixed set of synthetic Decibyl conversations (a
mix of quick questions, tasks that call a tool, and requests that need a
tool the lean list leaves out), and runs the *real* turn code
(``decibyl.answer``) over the same conversations once per configuration: all
flags off, each of ``cache_v2`` / ``lean_tools`` / ``cheap_routing`` /
``history_cap`` alone, and all four. The database, the connected apps and the
person's data are replaced with plain values; the system prompt, the tool
list, the history window, the routing, the request the vendor would be sent
and the per-call rows are the product's own.

What it measures. Every model call writes the same row the staff caching page
reads (``billing.cache_metrics``, one row per call); the numbers below are
``billing.cache_report.summarise`` over those rows -- whole-prompt input
tokens, how much of it the cache served, what it cost at the price book. So
the figure is the one the page will show, read off the same rows.

Two modes:

* **offline (default).** The vendor is replaced by a model of Claude's prompt
  cache (:class:`CacheSim`): the render order is tools, system, messages; a
  breakpoint caches everything before it; a lookup walks back up to 20 blocks
  from each breakpoint; a prefix under the model's minimum is not cached; an
  entry lives five minutes. Token counts are the codebase's own estimate (four
  characters a token). It says what the *request shapes* do to input and cache
  reads. It cannot say what the real tokeniser or the real cache would do, so
  every figure is labelled estimated.
* **live (``--live``).** The vendor is called for real and its own usage is
  what the rows hold. It needs ``TEST_ANTHROPIC_API_KEY``: a key made for
  testing, read from that name only and never from the platform's stored keys
  or ``ANTHROPIC_API_KEY``. It refuses to run unless ``ENVIRONMENT=test``.

Both modes refuse to run outside the test environment, and nothing here
writes to a database or reads a provider key from one.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Iterator, Mapping
from unittest.mock import AsyncMock, patch

LEVERS = ("cache_v2", "lean_tools", "cheap_routing", "history_cap")

#: The configurations, in the order they are reported.
CONFIGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("baseline", ()),
    *((lever, (lever,)) for lever in LEVERS),
    ("all four", LEVERS),
)

#: Claude's prompt cache, as documented: five minutes, a lookback of twenty
#: blocks from each breakpoint.
TTL_SECONDS = 300
LOOKBACK_BLOCKS = 20

ORG = 4242


class Refused(SystemExit):
    """Not run: the environment is not the test one."""

    def __init__(self, why: str):
        super().__init__(f"replay_token_cuts refused to run: {why}")


def require_test_environment(
    *, live: bool, env: Mapping[str, str] | None = None
) -> str | None:
    """Raise :class:`Refused` unless this is the test environment.

    Returns the test API key in live mode, else None. The key is read from
    ``TEST_ANTHROPIC_API_KEY`` and from nowhere else; one equal to the
    ordinary ``ANTHROPIC_API_KEY`` is refused, because that is the
    production key under another name.
    """
    env = os.environ if env is None else env
    if (env.get("ENVIRONMENT") or "").strip().lower() != "test":
        raise Refused(
            "ENVIRONMENT is not 'test'. Source api/.env.test, never api/.env."
        )
    if not live:
        return None
    key = (env.get("TEST_ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise Refused("--live needs TEST_ANTHROPIC_API_KEY (a key made for testing).")
    if key == (env.get("ANTHROPIC_API_KEY") or "").strip():
        raise Refused("TEST_ANTHROPIC_API_KEY is the same as ANTHROPIC_API_KEY.")
    return key


# --- Claude's prompt cache, modelled -------------------------------------------


def tokens_of(text: str) -> int:
    """The codebase's own estimate (``chat_memory.tokens_of``)."""
    from api.services.workflow import chat_memory

    return chat_memory.tokens_of(text)


def min_cacheable(model: str) -> int:
    """The shortest prefix the model caches: 4,096 tokens for the smallest
    tier, 1,024 for the others."""
    return 4096 if "haiku" in (model or "").lower() else 1024


@dataclass
class _Block:
    text: str
    tokens: int
    breakpoint: bool


def blocks_of(payload: dict[str, Any]) -> list[_Block]:
    """The request's blocks in render order: tools, system, messages."""
    out: list[_Block] = []

    def add(label: str, body: Any) -> None:
        marked = isinstance(body, dict) and "cache_control" in body
        plain = (
            {k: v for k, v in body.items() if k != "cache_control"}
            if isinstance(body, dict)
            else body
        )
        text = label + json.dumps(plain, sort_keys=True, ensure_ascii=False)
        out.append(_Block(text, tokens_of(text), marked))

    for tool in payload.get("tools") or []:
        add("tool:", tool)
    for block in payload.get("system") or []:
        add("system:", block)
    for message in payload.get("messages") or []:
        content = message["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for block in content:
            add(f"{message['role']}:", block)
    return out


@dataclass
class CacheSim:
    """What Claude's prompt cache would do with a sequence of requests."""

    store: dict[str, float] = field(default_factory=dict)

    def send(
        self, payload: dict[str, Any], *, model: str, now: float
    ) -> dict[str, int]:
        """Usage for one request: ``input`` (after the last breakpoint, or all
        of it when nothing is cacheable), ``read`` and ``write``."""
        blocks = blocks_of(payload)
        digests: list[str] = []
        running = hashlib.sha256()
        cumulative: list[int] = []
        total = 0
        for block in blocks:
            running.update(block.text.encode())
            digests.append(running.copy().hexdigest())
            total += block.tokens
            cumulative.append(total)
        marks = [i for i, b in enumerate(blocks) if b.breakpoint]
        floor = min_cacheable(model)
        marks = [i for i in marks if cumulative[i] >= floor]
        if not marks:
            return {"input": total, "read": 0, "write": 0}

        hit = -1
        for mark in marks:
            for j in range(mark, max(mark - LOOKBACK_BLOCKS, -1), -1):
                if self.store.get(digests[j], 0.0) > now:
                    hit = max(hit, j)
                    break
        last = max(marks)
        read = cumulative[hit] if hit >= 0 else 0
        write = max(cumulative[last] - read, 0)
        for mark in marks:
            self.store[digests[mark]] = now + TTL_SECONDS
        if hit >= 0:
            self.store[digests[hit]] = now + TTL_SECONDS
        return {"input": total - cumulative[last], "read": read, "write": write}


# --- the conversations ----------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    text: str
    #: A tool the reply calls before answering, or None for a plain answer.
    calls: str | None = None
    #: A tool the person asked for in plain words, which the model must be
    #: holding on the first round whatever the configuration.
    needs: str | None = None


QUICK = (
    "thanks",
    "ok",
    "what is the time in Mumbai?",
    "who is on shift today",
    "how many calls did we get",
    "good morning",
    "send the report to Ravi",
    "reply to Asha",
    "any update on the invoice",
)
WORK = (
    "draft a reply to the supplier's complaint and plan the follow-up",
    "summarise this week's calls and list what needs attention",
    "prepare a checklist for the new front desk agent",
    "translate the welcome message into Hindi and Tamil",
)
ASKED = (
    ("make a poster for our diwali sale", "make_images"),
    ("find leads for dentists in pune", "find_leads"),
    ("draft a purchase order for the cement", "draft_document"),
    ("rank the suppliers in that table", "rank_table"),
    ("who owes me money", "who_owes_me"),
)


def synthetic_threads(threads: int, turns: int, seed: int = 7) -> list[list[Turn]]:
    """Deterministic conversations: mostly quick, some work, some asking for
    a tool the lean list leaves out."""
    rng = random.Random(seed)
    out: list[list[Turn]] = []
    for _ in range(threads):
        thread: list[Turn] = []
        for _ in range(turns):
            roll = rng.random()
            if roll < 0.55:
                thread.append(Turn(rng.choice(QUICK)))
            elif roll < 0.8:
                thread.append(Turn(rng.choice(WORK), calls="recall"))
            else:
                text, tool = rng.choice(ASKED)
                thread.append(Turn(text, calls=tool, needs=tool))
        out.append(thread)
    return out


# --- the replay -----------------------------------------------------------------


def _drawer(full: bool) -> list[dict[str, Any]]:
    """Decibyl's own tools: the plain account's, or every optional one on."""
    from api import constants
    from api.services import features
    from api.services.workflow import decibyl

    if not full:
        return decibyl.office_tools(ORG)
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(features, "is_on", lambda name, organization_id=None: True)
        )
        stack.enter_context(patch.object(features, "on_anywhere", lambda name: True))
        for flag in (
            "DECIBYL_TOOLS_2026_09_ENABLED",
            "PROCUREMENT_DOCS_2026_09_ENABLED",
            "TABLE_TOOLS_ENABLED",
            "DECIBYL_BROWSER_ENABLED",
            "OUTREACH_ENABLED",
            "CALL_FOR_ME_ENABLED",
            "CALL_WHEN_DONE_ENABLED",
        ):
            stack.enter_context(patch.object(constants, flag, True, create=True))
        return decibyl.office_tools(ORG)


def _apps(count: int) -> list[dict[str, Any]]:
    """The person's connected apps as the model is shown them: a name and a
    line each."""
    return [
        {
            "name": f"ext_app_{i:02d}_fetch_records",
            "description": (
                f"Fetch records from connected app {i} (reads; runs now). "
                "Returns the matching rows with their ids, names and dates. "
                "Call load_tool with this name first to get its arguments."
            ),
            "parameters": {"type": "object", "properties": {}},
        }
        for i in range(count)
    ]


def _context(chars: int, turn_no: int) -> str:
    body = ("Team: Front desk (live), Collections (live), Orders (paused). " * 200)[
        :chars
    ]
    return f"## Now\nTurn {turn_no}\n\n## Team\n{body}"


@contextmanager
def _environment(
    *,
    flags: tuple[str, ...],
    drawer: list[dict[str, Any]],
    apps: list[dict[str, Any]],
    rows: list[Any],
    context_chars: int,
    on_reply: Any,
    stream: Any,
    tool_result: Any,
    state: dict[str, Any],
) -> Iterator[None]:
    """Everything a Decibyl turn reads, replaced by plain values."""
    from api.enums import AgentEventActor, AgentEventKind
    from api.services import features
    from api.services.agent_builder import client, settings
    from api.services.billing import model_usage
    from api.services.workflow import chat_memory, connected_tools, decibyl

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    async def agent_events(**_: Any) -> list[Any]:
        return list(state["rows"])

    async def record(**kwargs: Any) -> int:
        if (
            kwargs.get("kind") == AgentEventKind.MESSAGE.value
            and kwargs.get("actor") == AgentEventActor.AGENT.value
        ):
            on_reply(kwargs)
        return 1

    async def resolve(_session: Any, choice: str | None, *, organization_id: Any):
        from api import constants
        from api.services.configuration import chat_presets, managed_tiers

        model = constants.AGENT_BUILDER_MODELS["anthropic"]
        if choice and choice in chat_presets.PRESETS_BY_SLUG:
            upstream = managed_tiers.resolve(
                "llm", chat_presets.PRESETS_BY_SLUG[choice].llm_tier
            )
            model = upstream.model
        return settings.BuilderModel(
            provider="anthropic", model=model, api_key=state["api_key"]
        )

    budget = chat_memory.Budget(8_000, "free", None)

    async def noop(*_: Any, **__: Any) -> None:
        return None

    with ExitStack() as stack:
        enter = stack.enter_context
        enter(
            patch.object(
                features,
                "is_on",
                lambda name, organization_id=None: name in flags,
            )
        )
        enter(patch.object(features, "on_anywhere", lambda name: name in flags))
        enter(
            patch.object(
                decibyl,
                "build_context",
                AsyncMock(
                    side_effect=lambda org, q: _context(context_chars, state["turn"])
                ),
            )
        )
        enter(patch.object(decibyl, "office_context", AsyncMock(return_value="")))
        enter(patch.object(decibyl, "office_tools", lambda org=None: list(drawer)))
        enter(patch.object(decibyl.db_client, "agent_events", agent_events))
        enter(patch.object(decibyl.db_client, "async_session", lambda: session))
        enter(patch.object(decibyl.agent_timeline, "record", record))
        enter(patch.object(decibyl.agent_timeline, "record_activity", noop))
        enter(patch.object(decibyl.reply_draft, "clear", noop))
        enter(patch.object(decibyl.reply_stop, "clear", noop))
        enter(patch.object(chat_memory, "budget", AsyncMock(return_value=budget)))
        enter(
            patch.object(
                connected_tools,
                "list_for_organization",
                AsyncMock(return_value=[]),
            )
        )
        enter(
            patch(
                "api.services.reach.outside_tools.schemas",
                AsyncMock(return_value=list(apps)),
            )
        )
        enter(
            patch(
                "api.services.sandbox.code_mode.allowed", AsyncMock(return_value=False)
            )
        )
        enter(patch.object(settings, "resolve_for_organization", resolve))
        enter(
            patch(
                "api.services.routing.brain.workspace_is_auto",
                AsyncMock(return_value=True),
            )
        )
        enter(
            patch(
                "api.services.settings.temporary.reason_for_turn",
                AsyncMock(return_value=None),
            )
        )
        enter(
            patch(
                "api.services.settings.profile.block_for_turn",
                AsyncMock(return_value=""),
            )
        )
        enter(patch("api.services.settings.profile.turn_block", lambda: ""))
        for target in (
            "api.services.knowledge_graph.spaced_recall.related_context",
            "api.services.helpers.turn.context",
        ):
            enter(patch(target, AsyncMock(return_value="")))
        enter(
            patch(
                "api.services.knowledge_graph.decisions.note",
                AsyncMock(return_value=0),
            )
        )
        enter(
            patch(
                "api.services.knowledge_graph.feed.remember_exchange",
                AsyncMock(return_value=False),
            )
        )
        enter(patch("api.services.identity.mobile_push.announce_reply", noop))
        enter(patch.object(decibyl, "_tool", AsyncMock(side_effect=tool_result)))
        enter(patch.object(model_usage, "_write", noop))
        if stream is not None:
            enter(patch.object(client, "stream", stream))
        yield


async def replay_config(
    name: str,
    flags: tuple[str, ...],
    threads: list[list[Turn]],
    *,
    drawer: list[dict[str, Any]],
    apps: list[dict[str, Any]],
    context_chars: int,
    gap_seconds: float,
    live_key: str | None,
) -> dict[str, Any]:
    """Every conversation once, under one configuration. Returns the rows the
    calls wrote and the tool facts."""
    from api.enums import AgentEventActor, AgentEventKind
    from api.services.agent_builder import client
    from api.services.billing import cache_metrics, llm_usage
    from api.services.workflow import decibyl, lean_tools

    rows_out: list[dict[str, Any]] = []
    clock = {"now": 0.0}
    state: dict[str, Any] = {
        "rows": [],
        "turn": 0,
        "api_key": live_key or "sim-key",
    }
    sim = CacheSim()
    offered: list[list[str]] = []
    missing: list[str] = []
    script: dict[str, list[Any]] = {"replies": []}

    def at() -> datetime:
        return datetime(2026, 10, 10, tzinfo=UTC) + timedelta(seconds=clock["now"])

    # The thread's tool list stays put while the cache is warm; "warm" is
    # read off the replay's clock, not the machine's.
    stack_clock = patch.object(lean_tools, "_now", at)

    async def write(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            rows_out.append({**row, "created_at": at()})

    async def simulated_stream(**kwargs: Any) -> Any:
        tools = kwargs.get("tools") or []
        payload = client._anthropic_request(
            model=kwargs["model"],
            system=kwargs["system"],
            conversation=kwargs["conversation"],
            tools=tools,
        )
        usage = sim.send(payload, model=kwargs["model"], now=clock["now"])
        reply = script["replies"].pop(0)
        out_tokens = max(1, len(reply.text) // 4 + 40 * len(reply.tool_calls))
        normalised = llm_usage.NormalisedUsage(
            input_tokens=usage["input"],
            output_tokens=out_tokens,
            cache_read_tokens=usage["read"],
            cache_write_tokens=usage["write"],
            cache_outside_prompt=True,
        )
        offered.append([t["name"] for t in tools])
        await cache_metrics.record_direct(
            provider="anthropic",
            model=kwargs["model"],
            usage=normalised,
            system=kwargs["system"],
            tools=tools,
        )
        clock["now"] += 6.0
        return reply

    def on_reply(kwargs: Mapping[str, Any]) -> None:
        state["rows"].insert(
            0,
            SimpleNamespace(
                actor=AgentEventActor.AGENT.value,
                kind=AgentEventKind.MESSAGE.value,
                payload=kwargs.get("payload") or {},
                summary=kwargs.get("summary") or "",
                at=at(),
                id=len(state["rows"]) + 1,
            ),
        )

    async def tool_result(*_: Any, **__: Any) -> dict[str, Any]:
        return {"status": "success", "items": []}

    stream = None if live_key else simulated_stream
    with (
        stack_clock,
        patch.object(cache_metrics, "_write", write),
        _environment(
            flags=flags,
            drawer=drawer,
            apps=apps,
            rows=state["rows"],
            context_chars=context_chars,
            on_reply=on_reply,
            stream=stream,
            tool_result=tool_result,
            state=state,
        ),
    ):
        for t_index, thread in enumerate(threads):
            state["rows"] = []
            clock["now"] += 7 * 60
            for turn_no, turn in enumerate(thread):
                state["turn"] = turn_no
                clock["now"] += gap_seconds
                state["rows"].insert(
                    0,
                    SimpleNamespace(
                        actor=AgentEventActor.HUMAN.value,
                        kind=AgentEventKind.MESSAGE.value,
                        payload={"body": turn.text},
                        summary=turn.text,
                        at=at(),
                        id=len(state["rows"]) + 1,
                    ),
                )
                call = (
                    [
                        client.ModelReply(
                            text="",
                            tool_calls=(
                                client.ToolCall(
                                    id=f"c{t_index}{turn_no}",
                                    name=turn.calls,
                                    arguments={"query": turn.text},
                                ),
                            ),
                        )
                    ]
                    if turn.calls
                    else []
                )
                script["replies"] = [
                    *call,
                    client.ModelReply(
                        text="Here is what I found. " * 12 + f"({turn.text[:20]})"
                    ),
                ]
                before = len(offered)
                await decibyl.answer(ORG, turn.text, thread_id=f"replay-{t_index}")
                # Only a tool this account holds at all can be missed.
                if (
                    turn.needs
                    and turn.needs in {t["name"] for t in drawer}
                    and (not offered[before:] or turn.needs not in offered[before])
                ):
                    missing.append(turn.text)
    return {"rows": rows_out, "offered": offered, "missing": missing}


def summarise_config(name: str, ran: dict[str, Any]) -> dict[str, Any]:
    """The staff page's own summary over the rows the replay wrote, plus the
    tool facts."""
    from api.services.billing import cache_report, default_rates

    inr = default_rates.REFERENCE_USD_INR
    rates = {
        (r.component.value, r.provider, r.model): SimpleNamespace(
            rate_mpaise=default_rates.usd_to_mpaise(r.usd_per_unit, usd_inr=inr),
            model=r.model,
        )
        for r in default_rates.LLM_SPLIT_RATES
    }
    report = cache_report.summarise(ran["rows"], rates=rates)
    totals = report["totals"]
    whole = (
        totals["input_tokens"]
        + totals["cache_read_tokens"]
        + totals["cache_write_tokens"]
    )
    calls = max(totals["calls"], 1)
    offered = ran["offered"]
    return {
        "config": name,
        "calls": totals["calls"],
        "prompt_tokens_per_call": round(whole / calls),
        "uncached_per_call": round(totals["input_tokens"] / calls),
        "cache_read_pct": round(100 * totals["cache_read_tokens"] / whole, 1)
        if whole
        else 0.0,
        "cache_write_pct": round(100 * totals["cache_write_tokens"] / whole, 1)
        if whole
        else 0.0,
        "output_tokens": totals["output_tokens"],
        "cost_usd": round(totals["cost_paise"] / 100 / inr, 4),
        "unpriced": totals["unpriced"],
        "tools_per_call": round(sum(len(o) for o in offered) / max(len(offered), 1), 1),
        "asked_for_tool_missing": len(ran["missing"]),
    }


def with_deltas(table: list[dict[str, Any]]) -> list[dict[str, Any]]:
    base = next(r for r in table if r["config"] == "baseline")
    for row in table:
        for key, label in (
            ("prompt_tokens_per_call", "prompt_delta_pct"),
            ("cost_usd", "cost_delta_pct"),
        ):
            row[label] = (
                round(100 * (row[key] - base[key]) / base[key], 1) if base[key] else 0.0
            )
    return table


def render(table: list[dict[str, Any]], *, live: bool, args: argparse.Namespace) -> str:
    head = (
        f"{'configuration':<14}{'calls':>6}{'prompt/call':>12}{'uncached':>10}"
        f"{'read %':>8}{'write %':>9}{'cost $':>9}{'vs base':>9}"
        f"{'tools/call':>11}{'missed':>8}"
    )
    lines = [head, "-" * len(head)]
    for r in table:
        lines.append(
            f"{r['config']:<14}{r['calls']:>6}{r['prompt_tokens_per_call']:>12}"
            f"{r['uncached_per_call']:>10}{r['cache_read_pct']:>8}"
            f"{r['cache_write_pct']:>9}{r['cost_usd']:>9.4f}"
            f"{r['cost_delta_pct']:>8}%{r['tools_per_call']:>11}"
            f"{r['asked_for_tool_missing']:>8}"
        )
    kind = "measured against the vendor" if live else "ESTIMATED (offline cache model)"
    lines += [
        "",
        f"{args.threads} conversations x {args.turns} turns, {args.apps} connected-app "
        f"tools, {'full' if args.drawer == 'full' else 'plain'} drawer; {kind}.",
        "prompt/call = uncached + cache read + cache write, in tokens.",
        "missed = turns where a tool the person asked for was not offered on the "
        "first round (must be 0).",
    ]
    return "\n".join(lines)


async def run(args: argparse.Namespace, key: str | None) -> list[dict[str, Any]]:
    threads = synthetic_threads(args.threads, args.turns, args.seed)
    drawer = _drawer(args.drawer == "full")
    apps = _apps(args.apps)
    table = []
    for name, flags in CONFIGS:
        ran = await replay_config(
            name,
            flags,
            threads,
            drawer=drawer,
            apps=apps,
            context_chars=args.context_chars,
            gap_seconds=args.gap,
            live_key=key,
        )
        table.append(summarise_config(name, ran))
    return with_deltas(table)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--turns", type=int, default=12)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--drawer",
        choices=("plain", "full"),
        default="full",
        help="Decibyl's own tools: a plain account's, or every optional one on.",
    )
    parser.add_argument("--apps", type=int, default=24, help="Connected-app tools.")
    parser.add_argument("--context-chars", type=int, default=6000)
    parser.add_argument(
        "--gap", type=float, default=45.0, help="Seconds between turns."
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    key = require_test_environment(live=args.live)
    table = asyncio.run(run(args, key))
    print(
        json.dumps(table, indent=2)
        if args.json
        else render(table, live=args.live, args=args)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
