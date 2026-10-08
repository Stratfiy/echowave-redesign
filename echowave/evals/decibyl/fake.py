"""Fakes, so the whole suite is built and tested without spending anything.

* ``FakeJudge`` -- a ``judge.Model`` that answers from rules, and counts.
* ``FakeAssistant`` -- a scripted stand-in for Decibyl's model: given the
  system prompt and the person's question it returns text and tool calls
  the way ``agent_builder.client.stream`` does. ``install`` patches it into
  the real API process, so the real routes, worker, tools, cards and
  thread run around a model that costs nothing (see ``local_stack.py``).

Neither is clever. Their job is to exercise every path of the runner, the
checks and the report -- a card, a chip, a refusal, a reply in Devanagari,
a timeout -- not to pass the cases.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from evals.decibyl.judge import Completion


@dataclass
class FakeJudge:
    """Passes unless the conversation contains ``fail_on``; counts its calls."""

    name: str = "claude-sonnet-5-5"
    fail_on: str = "JUDGE-FAIL"
    calls: list[str] = field(default_factory=list)

    def complete(self, system: str, user: str) -> Completion:
        self.calls.append(user)
        passed = self.fail_on not in user
        verdict = {
            "passed": passed,
            "reason": "Looks right." if passed else f"It said {self.fail_on}.",
        }
        return Completion(
            text=json.dumps(verdict), input_tokens=len(user) // 4, output_tokens=20
        )


# --- the assistant's model ---------------------------------------------------

_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+?\d[\d\s-]{8,}\d")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_TAMIL = re.compile(r"[஀-௿]")


def _question(conversation: Any) -> tuple[str, bool]:
    """The person's latest line, and whether a tool already answered this
    turn (the last message is a tool result)."""
    messages = list(getattr(conversation, "messages", []) or [])
    after_tool = bool(messages) and messages[-1].get("role") == "tool"
    for message in reversed(messages):
        if message.get("role") == "user":
            content = str(message.get("content") or "")
            return content.split("## Question", 1)[-1].strip(), after_tool
    return "", after_tool


def _has(tools: list[dict[str, Any]] | None, name: str) -> bool:
    return any(t.get("name") == name for t in tools or [])


def respond(system: str, conversation: Any, tools: list[dict[str, Any]] | None):
    """(text, [(tool_name, arguments)]) for one round."""
    question, after_tool = _question(conversation)
    low = question.lower()
    if after_tool:
        last = (getattr(conversation, "messages", None) or [{}])[-1]
        result = last.get("content")
        status = result.get("status") if isinstance(result, dict) else None
        if status in ("proposed", "offered", None) or "event_id" in (result or {}):
            return (
                "I have put it on a card for you to confirm; nothing happens until you do.",
                [],
            )
        return (
            f"I could not do that here: {(result or {}).get('reason', 'it is not available')}.",
            [],
        )
    if _TAMIL.search(question):
        return "வணக்கம்! நான் உதவ தயாராக இருக்கிறேன். என்ன செய்ய வேண்டும்?", []
    if _DEVANAGARI.search(question):
        return "नमस्ते! मैं मदद के लिए तैयार हूँ। बताइए क्या करना है?", []
    phone = _PHONE.search(question)
    if "call" in low and phone and _has(tools, "call_for_me"):
        return "", [
            (
                "call_for_me",
                {
                    "phone_number": phone.group(0),
                    "callee": "them",
                    "purpose": question[:200],
                },
            )
        ]
    if re.search(r"\bevery (weekday|day|morning|monday|tuesday|week)", low) and _has(
        tools, "schedule_routine"
    ):
        when = re.search(r"every [^,.]*", low).group(0)
        return "", [
            (
                "schedule_routine",
                {"name": "Routine", "instruction": question[:200], "when": when},
            )
        ]
    if ("scam" in low or "genuine" in low or "otp" in low) and _has(
        tools, "check_for_scam"
    ):
        return "", [("check_for_scam", {"text": question[:2000], "kind": "message"})]
    for app in ("gmail", "calendar", "slack", "outlook"):
        if app in low and _has(tools, "offer_connector"):
            slug = "googlecalendar" if app == "calendar" else app
            return f"{app.title()} is not connected yet.", [
                ("offer_connector", {"app": slug, "why": "to do what you asked"})
            ]
    if _EMAIL.search(question) and re.search(r"\b(email|send|mail|write)\b", low):
        return "I cannot send email from here because no email app is connected.", []
    if "simple mode" in system.lower() or "one thing at a time" in system.lower():
        return "Let us do one step. Open your phone settings. Did that work?", []
    return "Here is a short answer to that.", []


def install(log_path: str | None = None) -> None:
    """Patch the model client of *this process* with the fake. Used only by
    ``local_stack.py``; never imported by the product."""
    from api.services.agent_builder import client

    log_path = log_path or os.environ.get("FAKE_MODEL_LOG")

    async def stream(
        *, provider, model, api_key, system, conversation, on_text, tools=None
    ):
        text, calls = respond(system, conversation, tools)
        if log_path:
            with open(log_path, "a", encoding="utf-8") as handle:  # noqa: ASYNC230 - a local log line
                handle.write(
                    json.dumps(
                        {
                            "system_chars": len(system),
                            "system_tail": system[-1500:],
                            "question": _question(conversation)[0][:500],
                            "tools": [t.get("name") for t in tools or []],
                            "text": text,
                            "calls": calls,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        if text:
            await on_text(text)
        return client.ModelReply(
            text=text,
            tool_calls=tuple(
                client.ToolCall(id=f"fake-{i}", name=name, arguments=args)
                for i, (name, args) in enumerate(calls)
            ),
            usage={
                "prompt_tokens": len(system) // 4,
                "completion_tokens": len(text) // 4,
            },
        )

    async def complete(*, provider, model, api_key, system, conversation, tools):
        async def ignore(_: str) -> None:
            return None

        return await stream(
            provider=provider,
            model=model,
            api_key=api_key,
            system=system,
            conversation=conversation,
            on_text=ignore,
            tools=tools,
        )

    client.stream = stream
    client.complete = complete

    # The platform's model key lives in the database (provider keys screen).
    # A local database has none, and the fake needs none: any turn that
    # asks for one gets a placeholder that never leaves this process.
    from api.services.agent_builder import settings

    real_platform_key = settings.platform_key

    async def platform_key(session, provider, model=None):
        return await real_platform_key(session, provider, model) or "fake-local-key"

    settings.platform_key = platform_key
