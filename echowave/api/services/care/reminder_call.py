"""The reminder call: what Decibyl says when it rings for a medicine.

One small agent per workspace, made the first time a reminder rings and
reused after (found by its ``care_reminder_caller`` configuration mark), so
the call runs on the same voice pipeline, numbers, concurrency and costs as
every other call. Built from the launch templates' node helpers.

Three rules the prompt states, because they are safety rules:

* **Say who is calling.** The greeting names Decibyl and why it rings
  before anything is asked.
* **Reminders only.** The medicine is named exactly as the person wrote it.
  Never a dose, never whether to take it, never anything about symptoms:
  "please ask your doctor" and end kindly.
* **Never ask for anything secret.** No OTP, no PIN, no password, no bank
  details -- and if the person offers one, stop them.

The greeting is written per language (``GREETINGS``) so the first words are
in the person's own language; the rest of the call follows it. The non-English
lines need a native speaker's review before launch (CARE.md).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from api.db import db_client
from api.services.workflow.launch_templates import _agent, _edge, _end, _start

MARK = "care_reminder_caller"
NAME = "Medicine reminder calls (Decibyl care)"

#: "Hello {person}, this is Decibyl, calling with your medicine reminder. It
#: is time for {medicine}. Have you taken it?" -- in each call language.
GREETINGS: dict[str, str] = {
    "en": "Hello{person}, this is Decibyl, calling with your medicine reminder. "
    "It is time for {medicine}. Have you taken it?",
    "hi": "नमस्ते{person}, यह Decibyl है। आपकी दवा की याद दिलाने के लिए फ़ोन किया "
    "है। {medicine} लेने का समय हो गया है। क्या आपने ले ली है?",
    "ta": "வணக்கம்{person}, இது Decibyl. உங்கள் மருந்தை நினைவூட்ட அழைக்கிறோம். "
    "{medicine} எடுத்துக்கொள்ளும் நேரம் ஆகிவிட்டது. நீங்கள் எடுத்துக்கொண்டீர்களா?",
    "te": "నమస్కారం{person}, ఇది Decibyl. మీ మందు గుర్తు చేయడానికి కాల్ చేస్తున్నాము. "
    "{medicine} వేసుకునే సమయం అయింది. మీరు వేసుకున్నారా?",
    "bn": "নমস্কার{person}, আমি Decibyl। আপনার ওষুধের কথা মনে করিয়ে দিতে ফোন করছি। "
    "{medicine} খাওয়ার সময় হয়েছে। আপনি কি খেয়েছেন?",
    "kn": "ನಮಸ್ಕಾರ{person}, ಇದು Decibyl. ನಿಮ್ಮ ಔಷಧಿಯನ್ನು ನೆನಪಿಸಲು ಕರೆ ಮಾಡುತ್ತಿದ್ದೇವೆ. "
    "{medicine} ತೆಗೆದುಕೊಳ್ಳುವ ಸಮಯವಾಗಿದೆ. ನೀವು ತೆಗೆದುಕೊಂಡಿದ್ದೀರಾ?",
    "ml": "നമസ്കാരം{person}, ഇത് Decibyl ആണ്. നിങ്ങളുടെ മരുന്ന് ഓർമ്മിപ്പിക്കാൻ "
    "വിളിക്കുകയാണ്. {medicine} കഴിക്കാനുള്ള സമയമായി. നിങ്ങൾ കഴിച്ചോ?",
    "mr": "नमस्कार{person}, हे Decibyl आहे. तुमच्या औषधाची आठवण करून देण्यासाठी फोन "
    "केला आहे. {medicine} घेण्याची वेळ झाली आहे. तुम्ही घेतले का?",
    "gu": "નમસ્તે{person}, આ Decibyl છે. તમારી દવાની યાદ અપાવવા ફોન કર્યો છે. "
    "{medicine} લેવાનો સમય થઈ ગયો છે. શું તમે લીધી?",
    "pa": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ{person}, ਇਹ Decibyl ਹੈ। ਤੁਹਾਡੀ ਦਵਾਈ ਯਾਦ ਕਰਾਉਣ ਲਈ ਫ਼ੋਨ ਕੀਤਾ "
    "ਹੈ। {medicine} ਲੈਣ ਦਾ ਸਮਾਂ ਹੋ ਗਿਆ ਹੈ। ਕੀ ਤੁਸੀਂ ਲੈ ਲਈ ਹੈ?",
    "od": "ନମସ୍କାର{person}, ଏହା Decibyl। ଆପଣଙ୍କ ଔଷଧ ମନେ ପକାଇବା ପାଇଁ ଫୋନ କରୁଛୁ। "
    "{medicine} ଖାଇବା ସମୟ ହୋଇଗଲାଣି। ଆପଣ ଖାଇଲେଣି କି?",
}

_RULES = (
    "You are Decibyl, ringing {{care_person}} with a medicine reminder they "
    "asked for. Speak only in {{care_language_name}}, slowly, in short plain "
    "sentences, and be warm and patient.\n"
    "- You only remind. The medicine is {{care_medicine}}, exactly as they "
    "wrote it. Never say how much to take, whether to take it, what it is "
    "for, or anything about symptoms or side effects. If they ask, say "
    "kindly that their doctor or family is the right person to ask.\n"
    "- Never ask for, and never accept, an OTP, PIN, password, card or bank "
    "details. If they start to say one, stop them and say Decibyl never "
    "needs it.\n"
    "- If they say they feel unwell, tell them to call their family or a "
    "doctor now, and end the call kindly."
)

#: What the call records, read by services/care/calls.outcome_of.
EXTRACTION = [
    {
        "name": "dose_taken",
        "type": "string",
        "prompt": "One of: taken, not_yet, not_answered. 'taken' only if they "
        "clearly said they have taken it.",
    }
]


def greeting(language: str, *, medicine: str, person: str = "") -> str:
    tag = (language or "en").split("-")[0].lower()
    line = GREETINGS.get(tag) or GREETINGS["en"]
    return line.format(person=f" {person}" if person else "", medicine=medicine)


def call_context(*, medicine: str, language: str, person: str) -> dict[str, Any]:
    """The template variables one call carries (into ``initial_context``)."""
    from api.services.care.medicines import LANGUAGE_NAMES

    return {
        "care_medicine": medicine,
        "care_language": language,
        "care_language_name": LANGUAGE_NAMES.get(language, "English"),
        "care_person": person or "the person",
        "care_greeting": greeting(language, medicine=medicine, person=person),
    }


def definition() -> dict[str, Any]:
    persona = {
        "id": "global-1",
        "type": "globalNode",
        "position": {"x": -340, "y": 0},
        "data": {"name": "Persona", "prompt": _RULES},
    }
    nodes = [
        persona,
        _start(
            "{{care_greeting}}",
            "Wait for their answer. If they have taken it, thank them. If not "
            "yet, remind them gently once and say Decibyl will let them get "
            "on with it. Do not ask anything else.",
        ),
        _agent(
            "agent-1",
            "Note the answer",
            "Record whether they have taken {{care_medicine}}. Then say goodbye "
            "warmly in one sentence.",
            220,
            extraction=EXTRACTION,
        ),
        _end("end-1", "End Call", "Say goodbye in one short, warm sentence.", 440),
    ]
    edges = [
        _edge("start-1", "agent-1", "answered", "They have answered the question."),
        _edge("agent-1", "end-1", "finished", "Their answer is recorded."),
    ]
    return {"nodes": nodes, "edges": edges}


def configurations() -> dict[str, Any]:
    return {
        MARK: True,
        # The greeting is in the person's language; the voice follows them.
        "follow_caller_language": True,
    }


async def ensure_workflow(organization_id: int, *, user_id: int) -> Any:
    """The workspace's reminder agent, made once."""
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
        # Not an agent that answers anybody: it only rings for reminders.
        is_live=False,
    )
