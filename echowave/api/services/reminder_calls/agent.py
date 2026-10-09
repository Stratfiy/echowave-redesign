"""The reminder call: who is calling, is it you, and only then your words.

One small agent per workspace, made the first time a reminder call rings
and reused after (found by its ``reminder_caller`` configuration mark), the
same shape as care's and call-when-done's agents, so the call runs on the
same voice pipeline, numbers, concurrency and metering as every outbound
call.

Privacy is the shape of the graph, not only a line in a prompt:

1. **The greeting** names Decibyl and that this is the reminder they asked
   for, and asks whether it is them ("Am I speaking with Asha?"). Nothing
   else. The reminder's words are in no prompt this node can see.
2. **It is them** -> the read-out node, the only node whose prompt holds
   the reminder (``{{reminder_text}}``). It reads the words as the person
   wrote them, then accepts done, snooze, cancel or repeat. Read-out only:
   nothing said on the call approves or changes anything else.
3. **It is not them, a voicemail or an answering machine** -> a node that
   says only the content-free line (``policy.CONTENT_FREE_LINE``: decisions
   D6, D7) and ends. The reminder text is not in its prompt either.

The post-call hook (``calls.record_run_outcome``) reads what the call
recorded (``EXTRACTION``): who was reached, and what the person said to do.

The non-English greetings need a native speaker's review before launch, as
care's and call-when-done's do.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from api.db import db_client
from api.services.call_when_done import agent as done_agent
from api.services.reminder_calls import policy
from api.services.workflow.launch_templates import _agent, _edge, _end, _start

MARK = "reminder_caller"
NAME = "Reminder calls (Decibyl)"

#: "Hello, this is Decibyl with the reminder you asked for. Am I speaking
#: with {name}?" -- in each call language. Nothing about the reminder.
GREETINGS: dict[str, str] = {
    "en": "Hello, this is Decibyl with the reminder you asked for. Am I speaking with {name}?",
    "hi": "नमस्ते, यह Decibyl है, आपके माँगे हुए रिमाइंडर के साथ। क्या मेरी बात {name} से हो रही है?",
    "ta": "வணக்கம், இது Decibyl, நீங்கள் கேட்ட நினைவூட்டலுடன். நான் {name} அவர்களுடன் பேசுகிறேனா?",
    "te": "నమస్కారం, ఇది Decibyl, మీరు అడిగిన రిమైండర్‌తో. నేను {name} గారితో మాట్లాడుతున్నానా?",
    "bn": "নমস্কার, আমি Decibyl, আপনার চাওয়া রিমাইন্ডার নিয়ে। আমি কি {name}-এর সঙ্গে কথা বলছি?",
    "kn": "ನಮಸ್ಕಾರ, ಇದು Decibyl, ನೀವು ಕೇಳಿದ ಜ್ಞಾಪನೆಯೊಂದಿಗೆ. ನಾನು {name} ಅವರೊಂದಿಗೆ ಮಾತನಾಡುತ್ತಿದ್ದೇನೆಯೇ?",
    "ml": "നമസ്കാരം, ഇത് Decibyl ആണ്, നിങ്ങൾ ചോദിച്ച ഓർമ്മപ്പെടുത്തലുമായി. ഞാൻ {name}-നോടാണോ സംസാരിക്കുന്നത്?",
    "mr": "नमस्कार, हे Decibyl आहे, तुम्ही मागितलेल्या रिमाइंडरसह. मी {name} यांच्याशी बोलत आहे का?",
    "gu": "નમસ્તે, આ Decibyl છે, તમે માગેલા રિમાઇન્ડર સાથે. શું હું {name} સાથે વાત કરી રહ્યો છું?",
    "pa": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ, ਇਹ Decibyl ਹੈ, ਤੁਹਾਡੇ ਮੰਗੇ ਰਿਮਾਈਂਡਰ ਨਾਲ। ਕੀ ਮੇਰੀ ਗੱਲ {name} ਨਾਲ ਹੋ ਰਹੀ ਹੈ?",
    "od": "ନମସ୍କାର, ଏହା Decibyl, ଆପଣ ମାଗିଥିବା ରିମାଇଣ୍ଡର ସହିତ। ମୁଁ କଣ {name}ଙ୍କ ସହ କଥା ହେଉଛି?",
}

#: Without a name on file, the identity question names the ask instead.
NO_NAME = "the person who asked Decibyl for this reminder"

#: What the call records, read by ``calls.record_run_outcome``.
EXTRACTION_REACHED = {
    "name": "reached",
    "type": "string",
    "prompt": "One of: person, someone_else, voicemail. 'person' only if they "
    "clearly confirmed they are the person named. 'voicemail' for a "
    "recorded greeting, an answering machine or a beep.",
}
EXTRACTION_REPLY = {
    "name": "reminder_reply",
    "type": "string",
    "prompt": "One of: done, snooze, cancel, none. 'done' if they said they "
    "have done it or will deal with it now; 'snooze' if they asked to be "
    "reminded again later; 'cancel' if they asked to stop this reminder; "
    "otherwise 'none'.",
}
EXTRACTION_SNOOZE = {
    "name": "snooze_minutes",
    "type": "string",
    "prompt": "If they asked to be reminded again later, how many minutes "
    "from now, as a whole number; otherwise empty.",
}

_RULES = (
    "You are Decibyl, ringing someone with a reminder they asked for. Speak "
    "only in {{reminder_language_name}}, in short plain sentences, and be "
    "brief and warm.\n"
    "- You only read a reminder out. You cannot approve, send, pay, book or "
    "change anything, and nothing said on this call does.\n"
    "- Never ask for, and never accept, an OTP, PIN, password, card or bank "
    "details. If they start to say one, stop them and say Decibyl never "
    "needs it."
)

_CONTENT_FREE = policy.CONTENT_FREE_LINE.format(first_name="{{reminder_first_name}}")


def first_name(preferred: str | None) -> str:
    words = (preferred or "").split()
    return words[0] if words else ""


def greeting(language: str | None, *, name: str) -> str:
    line = GREETINGS[done_agent.language_tag(language)]
    return line.format(name=name or NO_NAME)


def call_context(*, title: str, language: str | None, name: str) -> dict[str, Any]:
    """The template variables one call carries (into ``initial_context``).
    ``reminder_text`` is used by the read-out node's prompt only."""
    tag = done_agent.language_tag(language)
    first = first_name(name)
    return {
        "reminder_language": tag,
        "reminder_language_name": done_agent.LANGUAGE_NAMES[tag],
        "reminder_first_name": first or "the person who asked for it",
        "reminder_greeting": greeting(tag, name=first),
        "reminder_text": title,
    }


def definition() -> dict[str, Any]:
    persona = {
        "id": "global-1",
        "type": "globalNode",
        "position": {"x": -340, "y": 0},
        "data": {"name": "Persona", "prompt": _RULES},
    }
    line = f'say exactly this, in {{{{reminder_language_name}}}}: "{_CONTENT_FREE}"'
    to_someone = (
        line
        if policy.UNKNOWN_ANSWERER_HEARS == "content_free_line"
        else "say you have the wrong number and apologise"
    )
    to_machine = (
        line if policy.VOICEMAIL_HEARS == "content_free_line" else "say nothing at all"
    )
    leave_word = (
        f"If a person answered who is not {{{{reminder_first_name}}}}, "
        f"{to_someone}. If it is a voicemail, a recorded greeting or an "
        f"answering machine, {to_machine}. Say nothing else about why you "
        "called: you do not know what the reminder is about. Then end the call."
    )
    nodes = [
        persona,
        _start(
            "{{reminder_greeting}}",
            "Wait for their answer to whether they are "
            "{{reminder_first_name}}. Do not say anything about what the "
            "reminder is: you do not know yet. If they ask, say you can only "
            "tell {{reminder_first_name}}.",
        ),
        _agent(
            "agent-1",
            "Read the reminder",
            "They confirmed they are {{reminder_first_name}}. Say: your "
            'reminder is: "{{reminder_text}}". Read those words exactly as '
            "written, nothing added. Then ask whether they want to mark it "
            "done, be reminded again later (snooze, say in how many "
            "minutes), cancel it, or hear it again. If they ask to hear it "
            "again, read it again. Then say goodbye in one sentence.",
            220,
            extraction=[EXTRACTION_REACHED, EXTRACTION_REPLY, EXTRACTION_SNOOZE],
        ),
        _agent(
            "agent-2",
            "Not them",
            leave_word,
            220,
            extraction=[EXTRACTION_REACHED],
        ),
        _end("end-1", "End Call", "Say goodbye in one short, warm sentence.", 440),
    ]
    nodes[3]["position"]["x"] = 360
    edges = [
        _edge(
            "start-1",
            "agent-1",
            "it_is_them",
            "They clearly confirmed they are {{reminder_first_name}}.",
        ),
        _edge(
            "start-1",
            "agent-2",
            "not_them",
            "They said they are not {{reminder_first_name}}, or it is a "
            "voicemail, a recorded greeting, an answering machine or a beep, "
            "or they will not say who they are.",
        ),
        _edge("agent-1", "end-1", "finished", "The reminder is read and answered."),
        _edge("agent-2", "end-1", "finished", "The message is left."),
    ]
    return {"nodes": nodes, "edges": edges}


def configurations() -> dict[str, Any]:
    return {MARK: True, "follow_caller_language": True}


async def ensure_workflow(organization_id: int, *, user_id: int) -> Any:
    """The workspace's reminder-call agent, made once."""
    async with db_client.async_session() as session:
        found = (
            await session.execute(
                text(
                    "SELECT id FROM workflows WHERE organization_id = :o AND "
                    "(workflow_configurations->>:mark) = 'true' ORDER BY id LIMIT 1"
                ),
                {"o": organization_id, "mark": MARK},
            )
        ).first()
    if found:
        return await db_client.get_workflow(found[0], organization_id=organization_id)
    return await db_client.create_workflow(
        name=NAME,
        workflow_definition=definition(),
        user_id=user_id,
        organization_id=organization_id,
        workflow_configurations=configurations(),
        # Not an agent that answers anybody: it only rings with reminders.
        is_live=False,
    )
