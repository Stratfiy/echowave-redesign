"""The telecaller call coach (promoted 22 Sept 2026, CR-3).

The draft listened to "the day's recorded calls on {{phone_number}}" --
calls a human team makes on its own dialer, which never touch a Decibyl
number. It stayed a draft until the dialer import (CR-1) gave it something
to read; its one tool, ``read_team_calls``, returns those calls transcribed
and grouped by caller, for the hiring organization only.

What else promotion changed: the register check is optional (a business
may keep no register), a caller's name comes from the roster the business
gives rather than from a dialer that does not know it, and the three
per-message placeholders the draft asked at hire (``{{one_strength}}``,
``{{caller_count}}``, ``{{time}}``) are read from the calls instead.
"""

from __future__ import annotations

from api.services.agent_templates._base import (
    AgentTemplate,
    CallDirection,
    ScheduleShape,
    TemplateEdge,
    TemplateNode,
)


def template() -> AgentTemplate:
    from api.services.agent_templates.catalogue import _QUIET, _QUIET_GUARDRAILS

    return AgentTemplate(
        id="telecaller_call_coach",
        name="Human telecaller call coach",
        vertical="Businesses with a telecalling team on Exotel or Tata Smartflo",
        industry="Any business",
        function="Coach the team",
        direction=CallDirection.scheduled,
        summary=(
            "Reads the day's recorded calls from your dialer, sends each "
            "telecaller a private two-line note every evening, and sends you "
            "one board for the whole team each week."
        ),
        languages=["English", "Hindi", "Tamil", "Telugu", "Kannada", "Marathi"],
        stack=_QUIET,
        schedule_shape=ScheduleShape(
            runs="every evening, after the calling day",
            typical_items_per_run=40,
            typical_runs_per_month=26,
        ),
        template_variables={
            "business_name": "The business, as the team knows it",
            "team_roster": "Each telecaller's name and dialer number",
            "owner_contact": "Who gets the weekly board and any same-day flag",
            "board_day": "The day the weekly board goes out",
            "register_sheet": "Where callers write each call's outcome, if anywhere",
        },
        apps=["whatsapp", "googlesheets", "gmail"],
        needs_team_calls=True,
        nodes=[
            TemplateNode(
                type="startCall",
                name="Score the day's calls",
                prompt=(
                    "You are the call coach for {{business_name}}'s telecalling "
                    "team. You are not a manager and you never decide anyone's "
                    "pay, role or hours.\n\n"
                    "1. Read today's calls with read_team_calls (days: 1). Name "
                    "each caller from {{team_roster}} by their number; a number "
                    "not on it is named by its number.\n"
                    "2. Score only calls you have read in full, on four things: "
                    "the greeting, the questions asked, the close, and -- when "
                    "{{register_sheet}} is named -- whether the outcome was "
                    "written there. If no register is named, skip that check.\n"
                    "3. For each caller, pick one thing done well and one thing "
                    "to try tomorrow -- never more than one of each. The note "
                    "always opens with what went well, even on a poor day, and a "
                    "missing register entry is the thing to try, not a reason to "
                    "skip the strength.\n"
                    "4. Anything the owner must see today -- a serious "
                    "complaint, a promise beyond policy -- is flagged to "
                    "{{owner_contact}} now, separately from the notes.\n\n"
                    "The transcripts have no speaker labels. Tell the telecaller "
                    "from the customer by what is said, and where you cannot "
                    "tell, do not score that part. No calls today is a complete "
                    "answer: send nothing and say so."
                ),
                extract={
                    "callers": "How many callers were scored",
                    "calls": "How many calls were read in full",
                    "flagged": "Any same-day flag, or none",
                },
            ),
            TemplateNode(
                type="agentNode",
                name="Send the notes",
                prompt=(
                    "Send each caller their own note as a private WhatsApp "
                    "message, two lines: what went well, then one thing to try "
                    "tomorrow. Never in a group, and never mentioning anyone "
                    "else.\n\n"
                    "Never share one caller's score, note or recording with "
                    "another, in any form. A caller who asks how they compare "
                    "with a colleague is told that scores stay private to each "
                    "person, and offered their own note instead.\n\n"
                    "If today is {{board_day}}, read the week with "
                    "read_team_calls (days: 7) and send {{owner_contact}} one "
                    "board: every caller's scores side by side, how many calls "
                    "each. It never singles out who is lowest, and adds no "
                    "written comment on any one caller, unless {{owner_contact}} "
                    "has asked for that view."
                ),
                extract={"sent": "How many notes went out, and whether the board did"},
            ),
            TemplateNode(
                type="endCall",
                name="Close",
                prompt=(
                    "Record the run in one line: callers scored, notes sent, "
                    "board sent or not, any flag raised. 'No calls today' "
                    "qualifies; 'completed' does not."
                ),
            ),
        ],
        edges=[
            TemplateEdge(
                source="Score the day's calls",
                target="Send the notes",
                label="scored",
                condition="At least one call was read and scored",
            ),
            TemplateEdge(
                source="Score the day's calls",
                target="Close",
                label="no calls",
                condition="There were no calls to read today",
            ),
            TemplateEdge(
                source="Send the notes",
                target="Close",
                label="sent",
                condition="Every caller's note has gone, and the board if it was due",
            ),
        ],
        guardrails=_QUIET_GUARDRAILS
        + [
            "Never share one caller's score, note or recording with another caller.",
            "Never score a call you have not read in full, and never invent a "
            "call, a caller or a score.",
            "Never suggest a change to anyone's pay, role or hours. That is the "
            "owner's decision.",
        ],
        compliance_notes=[
            "It reads calls imported from your own dialer. Telling customers "
            "and staff that calls are recorded stays with the business, as the "
            "dialer's greeting does today.",
            "Tell the team what the coach reads and who sees the weekly board "
            "before turning it on.",
            "The evening run happens when a routine runs it. Set one up after "
            "hiring; without it the coach sends nothing.",
        ],
        example_requests=[
            "coach my telecallers from their recorded calls",
            "score my sales team's calls on Exotel every evening",
            "send each telecaller feedback on their calls",
        ],
    )


__all__ = ["template"]
