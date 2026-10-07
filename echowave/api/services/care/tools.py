"""Care in Chat: the same scam check, tech help and reminder card, as
Decibyl's tools, while their flags are on for the workspace.

Voice first means a person can say "is this a scam?" and paste a message,
or "my phone is ringing too softly", in Chat or on WhatsApp, and get the
same answer the care screen gives. Each tool calls the care service the
screen calls; none is a second implementation.

The rules (``rules``) are added to Decibyl's system prompt only for the
tools it is actually handed, like the procurement and table rules.
"""

from __future__ import annotations

from typing import Any

from api.services.care import CareError, NeedsSetup, medicines, scam, tech_help

SCAM_TOOL = "check_for_scam"
HELP_START_TOOL = "phone_help_start"
HELP_ANSWER_TOOL = "phone_help_answer"
REMINDER_TOOL = "set_medicine_reminder"
NAMES = frozenset({SCAM_TOOL, HELP_START_TOOL, HELP_ANSWER_TOOL, REMINDER_TOOL})
#: Answered in the turn; the model keeps its tools afterwards.
READS = frozenset({SCAM_TOOL, HELP_START_TOOL, HELP_ANSWER_TOOL})

_SCAM_RULE = (
    f"- {SCAM_TOOL}: when someone pastes or forwards a message, or describes a "
    "call, and wonders if it is genuine, run it and say the headline first in "
    "one plain sentence, then the reasons and what to do. Never ask them for "
    "an OTP, PIN, password or bank details, and tell them nobody genuine will.\n"
)
_HELP_RULE = (
    f"- {HELP_START_TOOL} and {HELP_ANSWER_TOOL}: for help using their phone. "
    "Start a guide, say only the one step it gives, then ask 'Did that work?'. "
    "Pass their yes or no back with the session id and version, and say the "
    "next step it returns. One step per reply, plain words.\n"
)
_REMINDER_RULE = (
    f"- {REMINDER_TOOL}: when someone asks to be reminded by phone call to take "
    "a medicine. It puts a card in front of them showing the number, times and "
    "language; nothing rings until they confirm. Write the medicine exactly as "
    "they said it. Never suggest a dose, a time to take it, or whether to take "
    "it: you remind, the doctor advises.\n"
)


def enabled(organization_id: int | None) -> dict[str, bool]:
    return {
        SCAM_TOOL: scam.enabled(organization_id),
        HELP_START_TOOL: tech_help.enabled(organization_id),
        HELP_ANSWER_TOOL: tech_help.enabled(organization_id),
        REMINDER_TOOL: medicines.enabled(organization_id),
    }


def rules(organization_id: int | None) -> str:
    on = enabled(organization_id)
    text = ""
    if on[SCAM_TOOL]:
        text += _SCAM_RULE
    if on[HELP_START_TOOL]:
        text += _HELP_RULE
    if on[REMINDER_TOOL]:
        text += _REMINDER_RULE
    return ("\nCare (for older people and their families):\n" + text) if text else ""


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def schemas(organization_id: int | None) -> list[dict[str, Any]]:
    on = enabled(organization_id)
    out: list[dict[str, Any]] = []
    if on[SCAM_TOOL]:
        out.append(
            _fn(
                SCAM_TOOL,
                "Check a message, or a description of a call, for scam warning "
                "signs. Returns a verdict, reasons and what to do. The words are "
                "not stored.",
                {
                    "text": {
                        "type": "string",
                        "description": "The message or what the caller said.",
                    },
                    "kind": {"type": "string", "enum": ["message", "call"]},
                },
                ["text"],
            )
        )
    if on[HELP_START_TOOL]:
        out.append(
            _fn(
                HELP_START_TOOL,
                "Start step-by-step phone help for what the person wants to do. "
                "Returns the first step, or the topics available when none fits.",
                {
                    "question": {
                        "type": "string",
                        "description": "What they want to do, in their words.",
                    }
                },
                ["question"],
            )
        )
        out.append(
            _fn(
                HELP_ANSWER_TOOL,
                "Pass on whether the last step worked. Returns the next step, "
                "another way to try, or that it is done.",
                {
                    "session_id": {"type": "integer"},
                    "version": {"type": "integer"},
                    "worked": {"type": "boolean"},
                },
                ["session_id", "version", "worked"],
            )
        )
    if on[REMINDER_TOOL]:
        out.append(
            _fn(
                REMINDER_TOOL,
                "Propose reminder phone calls for a medicine. A card shows the "
                "number, times and language; nothing rings until the person "
                "confirms. Reminders only, never dosing advice.",
                {
                    "label": {
                        "type": "string",
                        "description": "The medicine as they call it.",
                    },
                    "times": {
                        "type": "array",
                        "items": {"type": "string", "description": "HH:MM, 24-hour"},
                    },
                    "phone": {"type": "string", "description": "The phone to ring."},
                    "language": {
                        "type": "string",
                        "description": "BCP 47, e.g. ta-IN. Omit for their own.",
                    },
                },
                ["label", "times", "phone"],
            )
        )
    return out


async def run(
    name: str,
    *,
    organization_id: int,
    user_id: int | None,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    on = enabled(organization_id)
    if name not in NAMES or not on.get(name):
        return {"status": "unavailable", "reason": "no such tool"}
    if not user_id:
        return {"status": "unavailable", "reason": "this needs a signed-in person"}
    try:
        if name == SCAM_TOOL:
            answer = await scam.check(
                organization_id,
                user_id,
                text=str(arguments.get("text") or ""),
                kind=str(arguments.get("kind") or "message"),
            )
            return {"status": "success", **answer}
        if name == HELP_START_TOOL:
            return {
                "status": "success",
                **await tech_help.start(
                    organization_id,
                    user_id,
                    question=str(arguments.get("question") or ""),
                ),
            }
        if name == HELP_ANSWER_TOOL:
            return {
                "status": "success",
                "session": await tech_help.answer(
                    organization_id,
                    user_id,
                    int(arguments.get("session_id") or 0),
                    worked=bool(arguments.get("worked")),
                    version=int(arguments.get("version") or 0),
                ),
            }
        made = await medicines.propose(
            organization_id,
            user_id,
            label=str(arguments.get("label") or ""),
            times=list(arguments.get("times") or []),
            phone=str(arguments.get("phone") or ""),
            language=arguments.get("language") or None,
        )
        return {
            "status": "proposed",
            "note": (
                "Proposed. A card shows the number, times and language; nothing "
                "rings until they confirm it. Say so in one sentence and end."
            ),
            "medicine_id": made["medicine"]["id"],
        }
    except NeedsSetup as exc:
        return {"status": "not_proposed", "reason": str(exc), "state": "needs_setup"}
    except CareError as exc:
        return {
            "status": "not_proposed" if name == REMINDER_TOOL else "error",
            "reason": str(exc),
        }
