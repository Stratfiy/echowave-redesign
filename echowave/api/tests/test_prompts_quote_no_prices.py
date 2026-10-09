"""No price reaches a user through what a model says aloud.

The founder decided on 9 Oct 2026 that no pricing is shown to users
(AGENTS.md, "Product decisions that are already settled";
``constants.PRICES_SHOWN``). The UI has its own guard
(ui/src/__tests__/pricingGuard.test.ts); this one covers the prompts:
Decibyl's persona, the agent builder's system prompt, every tool
description the builder (and Studio, which reuses them) hands the model, and
the developer tool server (MCP): its instructions and every registered
tool's description.
A model told "four credits a run" or "Lite, Normal or Smart with its price a
minute" says it, so the words fail here.
"""

from __future__ import annotations

import json
import re

import pytest

from api import constants
from api.mcp_server.instructions import DECIBYL_MCP_INSTRUCTIONS
from api.mcp_server.server import mcp
from api.services.agent_builder import session as builder_session
from api.services.agent_builder import tools as builder_tools
from api.services.studio import tools as studio_tools
from api.services.workflow import decibyl

PRICE_WORDS: dict[str, re.Pattern[str]] = {
    "₹ / rupees": re.compile(r"₹|\brupees?\b|\bRs\.?\s?\d", re.I),
    "credit": re.compile(r"\bcredits?\b", re.I),
    "per minute": re.compile(r"\bper[- ]minute\b|\bprice a minute\b", re.I),
    "/min": re.compile(r"/min\b", re.I),
    # "plan" as a pricing word, not "make a plan" or "plan my day".
    "plan": re.compile(
        r"\b(your|their|on the|this|that|which) plans?\b|\bplans that\b|\bpaid plan\b",
        re.I,
    ),
    "upgrade": re.compile(r"\bupgrade", re.I),
}

#: (prompt, exact fragment) pairs allowed to carry a word above, with why.
ALLOWED: dict[tuple[str, str], str] = {}


def _prompts() -> dict[str, str]:
    return {
        "decibyl.SYSTEM": decibyl.SYSTEM,
        "agent_builder.SYSTEM_PROMPT": builder_session.SYSTEM_PROMPT,
        "agent_builder.tool_schemas": json.dumps(
            builder_tools.tool_schemas(), ensure_ascii=False
        ),
    }


def _hits(name: str, text: str) -> list[str]:
    found = []
    for word, pattern in PRICE_WORDS.items():
        for match in pattern.finditer(text):
            fragment = text[max(0, match.start() - 60) : match.end() + 40]
            if any(
                prompt == name
                and allowed in text[match.start() - 200 : match.end() + 200]
                for prompt, allowed in ALLOWED
            ):
                continue
            found.append(f"{name} [{word}]: …{fragment}…")
    return found


def test_the_switch_is_off():
    assert constants.PRICES_SHOWN is False


@pytest.mark.parametrize("name", list(_prompts()))
def test_no_prompt_tells_the_model_a_price(name):
    assert _hits(name, _prompts()[name]) == []


def test_the_mcp_instructions_tell_no_price():
    assert _hits("mcp.instructions", DECIBYL_MCP_INSTRUCTIONS) == []


@pytest.mark.asyncio
async def test_no_mcp_tool_description_tells_a_price():
    found = []
    for tool in await mcp.list_tools():
        found += _hits(f"mcp.{tool.name}", tool.description or "")
    assert found == []


@pytest.mark.asyncio
async def test_the_mcp_server_offers_no_priced_tool():
    names = {tool.name for tool in await mcp.list_tools()}
    assert "estimate_agent_cost" not in names
    assert "get_billing_summary" not in names
    assert "estimate_agent_cost" not in DECIBYL_MCP_INSTRUCTIONS


def test_the_guard_catches_what_it_is_meant_to():
    """A guard that matches nothing passes for ever."""
    for sample in (
        "Four credits a run; offered only on plans that have it.",
        "the brain as Lite, Normal or Smart with its price a minute",
        "Included in your plan. It uses credit by the minute",
        "two rupees a minute",
        "or upgrade for a larger allowance",
        "12 credits/min",
    ):
        assert _hits("sample", sample), sample
    for sample in (
        "Make a plan for the week",
        "plan my revision",
        "Lite, Normal or Smart",
    ):
        assert not _hits("sample", sample), sample


def test_the_cost_estimate_is_not_offered():
    names = {schema["name"] for schema in builder_tools.tool_schemas()}
    assert "estimate_agent_cost" not in names
    assert "estimate_agent_cost" not in builder_tools.TOOL_NAMES
    assert "estimate_agent_cost" not in studio_tools.AGENT_TOOLS
    assert "estimate_agent_cost" not in builder_session.SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_a_stray_cost_estimate_call_returns_no_money():
    """A model that remembers the tool from an older thread gets no figure."""
    result = await builder_tools.dispatch(
        "estimate_agent_cost",
        {"template_id": "anything"},
        session=None,
        organization_id=1,
        user_id=1,
    )
    text = json.dumps(result)
    assert "rupees" not in text and "₹" not in text
