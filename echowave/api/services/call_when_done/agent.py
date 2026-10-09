"""The "it's done" call: what Decibyl says when it rings about finished work.

One small agent per workspace, made the first time such a call rings and
reused after (found by its ``task_done_caller`` configuration mark) -- the
same shape as care's reminder agent (services/care/reminder_call), so the
call runs on the same voice pipeline, numbers, concurrency and metering as
every other outbound call.

What the call does, in order:

1. **Says who is calling and what finished**, in the person's language,
   before anything else (``GREETINGS``).
2. **The result in one or two sentences**, and whether anything needs them.
3. **Offers "say 'tell me more'" or "check the app"**, and answers a couple
   of follow-up questions from the task's own output (``done_results``),
   nothing else. It never asks for anything secret.

**Read-out only.** A call never approves a send, a payment or any other
action by voice, whatever is said on it. The agent has no tools at all (no
tool nodes, nothing in its context that names a card), so there is nothing
on the call that could change a card; for anything that needs approval it
says "I've put it in your app for you to confirm" (``APPROVE_IN_APP``), and
the card waits there for a Confirm in the app.

The non-English greetings need a native speaker's review before launch, as
care's do.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from api.db import db_client
from api.services.workflow.launch_templates import _agent, _edge, _end, _start

MARK = "task_done_caller"
NAME = "Task finished calls (Decibyl)"

#: "Hello {person}, this is Decibyl. The task you handed me is done:
#: {title}." -- in each call language.
GREETINGS: dict[str, str] = {
    "en": "Hello{person}, this is Decibyl. The task you handed me is done: {title}.",
    "hi": "नमस्ते{person}, यह Decibyl है। आपने जो काम सौंपा था, वह पूरा हो गया है: {title}।",
    "ta": "வணக்கம்{person}, இது Decibyl. நீங்கள் கொடுத்த வேலை முடிந்துவிட்டது: {title}.",
    "te": "నమస్కారం{person}, ఇది Decibyl. మీరు అప్పగించిన పని పూర్తయింది: {title}.",
    "bn": "নমস্কার{person}, আমি Decibyl। আপনি যে কাজটি দিয়েছিলেন তা শেষ হয়েছে: {title}।",
    "kn": "ನಮಸ್ಕಾರ{person}, ಇದು Decibyl. ನೀವು ಕೊಟ್ಟ ಕೆಲಸ ಮುಗಿದಿದೆ: {title}.",
    "ml": "നമസ്കാരം{person}, ഇത് Decibyl ആണ്. നിങ്ങൾ ഏൽപ്പിച്ച ജോലി പൂർത്തിയായി: {title}.",
    "mr": "नमस्कार{person}, हे Decibyl आहे. तुम्ही दिलेले काम पूर्ण झाले आहे: {title}.",
    "gu": "નમસ્તે{person}, આ Decibyl છે. તમે સોંપેલું કામ પૂરું થઈ ગયું છે: {title}.",
    "pa": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ{person}, ਇਹ Decibyl ਹੈ। ਤੁਹਾਡਾ ਸੌਂਪਿਆ ਕੰਮ ਪੂਰਾ ਹੋ ਗਿਆ ਹੈ: {title}।",
    "od": "ନମସ୍କାର{person}, ଏହା Decibyl। ଆପଣ ଦେଇଥିବା କାମ ସରିଗଲା: {title}।",
}

LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
    "bn": "Bengali",
    "kn": "Kannada",
    "ml": "Malayalam",
    "mr": "Marathi",
    "gu": "Gujarati",
    "pa": "Punjabi",
    "od": "Odia",
}

#: The most of the tasks' output the call carries for follow-up questions.
MAX_RESULTS = 4000

#: What the call says about anything that needs the person's approval.
APPROVE_IN_APP = "I've put it in your app for you to confirm."

_RULES = (
    "You are Decibyl, ringing {{done_person}} because work they handed you "
    "has finished. Speak only in {{done_language_name}}, in short plain "
    "sentences, and be brief: this is a quick update, not a meeting.\n"
    "- After the greeting, give the result in one or two sentences: "
    "{{done_summary}}\n"
    "- Then say whether anything needs them: {{done_needs_you}}\n"
    "- Then offer: they can say 'tell me more', or check the Decibyl app.\n"
    "- If they ask, answer at most a couple of questions, using ONLY the "
    "results below. If the answer is not there, say it is in the app. "
    "Never invent a detail.\n"
    "- You only read the result out. You cannot approve, send, pay, book, "
    "cancel or change anything, and nothing said on this call does. If they "
    "ask you to approve or do something, or something needs their approval, "
    'say exactly: "I\'ve put it in your app for you to confirm." Never say '
    "it is done or approved.\n"
    "- Never ask for or accept an OTP, PIN, password, card or bank details.\n"
    "RESULTS:\n{{done_results}}"
)


def language_tag(language: str | None) -> str:
    tag = (language or "en").split("-")[0].lower()
    return tag if tag in GREETINGS else "en"


def greeting(language: str | None, *, title: str, person: str = "") -> str:
    line = GREETINGS[language_tag(language)]
    return line.format(person=f" {person}" if person else "", title=title)


def titles_line(titles: list[str]) -> str:
    """ "A", "A and B", "A, B and C" -- the finished tasks as one phrase."""
    names = [t for t in titles if t] or ["your task"]
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def call_context(
    *,
    items: list[dict[str, Any]],
    language: str | None,
    person: str,
) -> dict[str, Any]:
    """The template variables one call carries (into ``initial_context``).

    ``items`` are the finished tasks: title, summary, output, needs_you."""
    tag = language_tag(language)
    title = titles_line([str(i.get("title") or "") for i in items])
    summary = " ".join(str(i.get("summary") or "").strip() for i in items).strip()
    needing = [str(i.get("title") or "") for i in items if i.get("needs_you")]
    needs = (
        f"Yes: {titles_line(needing)} needs them. Say: {APPROVE_IN_APP}"
        if needing
        else "Nothing needs them right now."
    )
    results = "\n\n".join(
        f"{i.get('title') or 'Task'}:\n{(i.get('output') or i.get('summary') or '').strip()}"
        for i in items
    )[:MAX_RESULTS]
    return {
        "done_language": tag,
        "done_language_name": LANGUAGE_NAMES[tag],
        "done_person": person or "the person",
        "done_title": title,
        "done_summary": summary or "It has finished.",
        "done_needs_you": needs,
        "done_results": results or "(nothing more was recorded)",
        "done_greeting": greeting(tag, title=title, person=person),
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
            "{{done_greeting}}",
            "Give the result in one or two sentences, say whether anything "
            "needs them, then offer 'tell me more' or the app. Wait for them.",
        ),
        _agent(
            "agent-1",
            "Questions",
            "Answer at most a couple of questions from RESULTS only. When they "
            "have nothing more, say goodbye warmly in one sentence.",
            220,
        ),
        _end("end-1", "End Call", "Say goodbye in one short, warm sentence.", 440),
    ]
    edges = [
        _edge("start-1", "agent-1", "heard", "They have heard the update."),
        _edge("agent-1", "end-1", "finished", "They have nothing more to ask."),
    ]
    return {"nodes": nodes, "edges": edges}


def configurations() -> dict[str, Any]:
    return {MARK: True, "follow_caller_language": True}


async def ensure_workflow(organization_id: int, *, user_id: int) -> Any:
    """The workspace's "it's done" agent, made once."""
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
        # Not an agent that answers anybody: it only rings with updates.
        is_live=False,
    )
