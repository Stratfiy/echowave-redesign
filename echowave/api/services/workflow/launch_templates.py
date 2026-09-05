"""The four agents an Indian SMB actually wants, prebuilt.

Twelve node types and a canvas is a platform. A clinic owner with four hundred
enquiries a month and a receptionist who quits every eight months does not want
a platform — they want the missed calls chased. These are the four moments where
that owner leaks revenue, each already a working agent:

- **Missed call** — an enquiry rang at 7pm, nobody answered, they rang the next
  clinic. Call them back within the minute and the enquiry is still warm.
- **Missed follow-up** — a quote went out, nobody chased it, it went cold.
- **Declined payment** — money already agreed, silently failing.
- **Website enquiry** — a visitor who will talk but will not fill in a form.

All four share one buyer, one motivation and one piece of arithmetic: the call
costs single-digit rupees and the thing it recovers is worth hundreds. That is
why they are one product rather than four.

Every template is a plain workflow definition built from nodes that already
exist, so nothing here is a special case at runtime — a template opens in the
editor as an ordinary agent and can be taken apart. The value is that the owner
never sees an empty canvas.

The prompts are deliberately short and written to be *edited*. A template that
tries to be complete produces an agent nobody adjusts, and an agent nobody
adjusts sounds like a template.
"""

from __future__ import annotations

from typing import Any

from api.services.workflow.qa_node import has_qa_node, qa_node
from api.services.workflow.template_generation import GLOBAL_PROMPT

#: Every template carries the same persona. It names no language: the platform
#: supports eleven Indian languages plus auto-detect, and a template that
#: hardcoded one would quietly undo that for the majority of new agents.
_PERSONA = GLOBAL_PROMPT


def _with_review(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict:
    """Every launch template ships with call review on.

    Appended here rather than written into each of the four templates, because
    a default repeated four times is a default that goes stale in three of
    them. ``has_qa_node`` keeps a template that grows its own review node from
    getting a second one, which would run the analysis twice and bill for both.
    """
    if not has_qa_node(nodes):
        nodes = [*nodes, qa_node()]
    return {"nodes": nodes, "edges": edges}


def _global_node() -> dict[str, Any]:
    return {
        "id": "global-1",
        "type": "globalNode",
        "position": {"x": -340, "y": 0},
        "data": {"name": "Persona", "prompt": _PERSONA},
    }


def _start(greeting: str, prompt: str) -> dict[str, Any]:
    return {
        "id": "start-1",
        "type": "startCall",
        "position": {"x": 0, "y": 0},
        "data": {
            "name": "Start Call",
            "is_start": True,
            "greeting": greeting,
            "greeting_type": "text",
            "prompt": prompt,
            "allow_interrupt": True,
            "add_global_prompt": True,
        },
    }


def _agent(
    node_id: str,
    name: str,
    prompt: str,
    y: int,
    *,
    extraction: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": name,
        "prompt": prompt,
        "allow_interrupt": True,
        "add_global_prompt": True,
    }
    if extraction:
        data["extraction_enabled"] = True
        data["extraction_prompt"] = "Capture the fields below from the conversation."
        data["extraction_variables"] = extraction
    return {
        "id": node_id,
        "type": "agentNode",
        "position": {"x": 0, "y": y},
        "data": data,
    }


def _end(node_id: str, name: str, prompt: str, y: int, x: int = 0) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "endCall",
        "position": {"x": x, "y": y},
        "data": {
            "name": name,
            "is_end": True,
            "prompt": prompt,
            "add_global_prompt": True,
        },
    }


def _edge(source: str, target: str, label: str, condition: str) -> dict[str, Any]:
    return {
        "id": f"{source}-{label}-{target}",
        "source": source,
        "target": target,
        "data": {"label": label, "condition": condition},
    }


def missed_call() -> dict[str, Any]:
    """Ring back an enquiry nobody answered.

    Outbound, and the first ten seconds decide everything: a stranger who rang
    you an hour ago does not remember doing it. Say who is calling and why
    before asking for anything.
    """
    nodes = [
        _global_node(),
        _start(
            "Hello, this is calling back from {{business_name}}. "
            "I believe you tried to reach us a short while ago — is now a good time?",
            "Say who you are and that you are returning their call. If it is a "
            "bad time, offer to call back later and end politely. Do not ask "
            "them to confirm anything before you have said why you are calling.",
        ),
        _agent(
            "agent-1",
            "What they needed",
            "Find out what they were calling about and how urgent it is. Keep "
            "it to two or three questions. If they want an appointment, get a "
            "day and a rough time — do not try to confirm an exact slot you "
            "cannot see.",
            220,
            extraction=[
                {
                    "name": "enquiry_about",
                    "type": "string",
                    "prompt": "What they were calling about",
                },
                {
                    "name": "preferred_time",
                    "type": "string",
                    "prompt": "Day and rough time they would like, if any",
                },
                {
                    "name": "call_outcome",
                    "type": "string",
                    "prompt": "One of: booked, wants_callback, not_interested",
                },
            ],
        ),
        _end(
            "end-1",
            "End Call",
            "Confirm in one sentence what will happen next, thank them, and "
            "say goodbye.",
            440,
        ),
    ]
    edges = [
        _edge(
            "start-1",
            "agent-1",
            "continue",
            "They have confirmed it is a good time to talk.",
        ),
        _edge(
            "agent-1",
            "end-1",
            "finished",
            "Their enquiry is understood and next steps are agreed, or they "
            "want to end the call.",
        ),
    ]
    return _with_review(nodes, edges)


def missed_follow_up() -> dict[str, Any]:
    """Chase a quote that went quiet.

    Branches on a fact rather than asking the model twice: whether they
    remember the quote decides the whole rest of the call.
    """
    nodes = [
        _global_node(),
        _start(
            "Hello, this is {{business_name}} calling about the quote we sent "
            "you. Do you have two minutes?",
            "Introduce yourself and say you are following up on a quote. "
            "Confirm they have a moment before continuing.",
        ),
        _agent(
            "agent-1",
            "Do they remember it",
            "Ask whether they had a chance to look at the quote we sent. "
            "Capture whether they remember receiving it.",
            220,
            extraction=[
                {
                    "name": "saw_quote",
                    "type": "boolean",
                    "prompt": "True if they remember receiving the quote",
                }
            ],
        ),
        {
            "id": "branch-1",
            "type": "branch",
            "position": {"x": 0, "y": 440},
            "data": {
                "name": "Did they see it",
                "rules": [
                    {
                        "label": "seen",
                        "variable": "saw_quote",
                        "operator": "is_true",
                        "value": "",
                    }
                ],
                "default_label": "not_seen",
            },
        },
        _agent(
            "agent-seen",
            "Their decision",
            "They have seen the quote. Find out what is holding them back and "
            "whether they want to go ahead. Do not negotiate on price — note "
            "the objection and say someone will call.",
            660,
            extraction=[
                {
                    "name": "objection",
                    "type": "string",
                    "prompt": "What is stopping them going ahead",
                },
                {
                    "name": "call_outcome",
                    "type": "string",
                    "prompt": "One of: agreed, thinking, not_interested",
                },
            ],
        ),
        _agent(
            "agent-unseen",
            "Resend it",
            "They have not seen the quote. Confirm the best email or WhatsApp "
            "number to resend it to, and offer to call back in two days.",
            660,
            extraction=[
                {
                    "name": "resend_to",
                    "type": "string",
                    "prompt": "Email or number to resend the quote to",
                },
                {
                    "name": "call_outcome",
                    "type": "string",
                    "prompt": "One of: resend, not_interested",
                },
            ],
        ),
        _end(
            "end-1",
            "End Call",
            "Confirm what happens next in one sentence, thank them, and say goodbye.",
            880,
        ),
    ]
    edges = [
        _edge("start-1", "agent-1", "continue", "They have a moment to talk."),
        _edge(
            "agent-1",
            "branch-1",
            "checked",
            "It is clear whether they received the quote.",
        ),
        # A branch routes on its rule labels, not on these conditions — the
        # text is documentation for whoever opens the canvas next.
        _edge(
            "branch-1",
            "agent-seen",
            "seen",
            "Taken when saw_quote is true. Decided by the branch rule, not the model.",
        ),
        _edge(
            "branch-1",
            "agent-unseen",
            "not_seen",
            "Taken when saw_quote is false or was never captured.",
        ),
        _edge("agent-seen", "end-1", "finished", "Their position is understood."),
        _edge("agent-unseen", "end-1", "finished", "A resend has been agreed."),
    ]
    return _with_review(nodes, edges)


def declined_payment() -> dict[str, Any]:
    """Recover a payment that failed after the customer already agreed.

    The most delicate of the four. The money was agreed, so this is a service
    call, not a chase — and the agent must never take card details on the
    phone. It sends a link instead.
    """
    nodes = [
        _global_node(),
        _start(
            "Hello, this is {{business_name}}. Your recent payment did not go "
            "through, and I wanted to let you know so it does not cause you "
            "any trouble. Is now a good time?",
            "Be reassuring. This is a service call, not a demand. Never ask "
            "for card numbers, CVV, UPI PIN or an OTP — say a secure payment "
            "link will be sent instead.",
        ),
        _agent(
            "agent-1",
            "Sort out the payment",
            "Explain that the payment failed and offer to send a secure "
            "payment link. Confirm the best number to send it to. If they say "
            "they will handle it themselves, accept that and do not press. "
            "Never take payment details over the phone under any circumstances.",
            220,
            extraction=[
                {
                    "name": "send_link_to",
                    "type": "string",
                    "prompt": "Number to send the payment link to",
                },
                {
                    "name": "call_outcome",
                    "type": "string",
                    "prompt": "One of: link_requested, will_handle_it, disputed",
                },
            ],
        ),
        _end(
            "end-1",
            "End Call",
            "Confirm the link is on its way, thank them for their time, and "
            "say goodbye.",
            440,
        ),
    ]
    edges = [
        _edge("start-1", "agent-1", "continue", "They have a moment to talk."),
        _edge(
            "agent-1",
            "end-1",
            "finished",
            "The payment is resolved or they have said how they will handle it.",
        ),
    ]
    return _with_review(nodes, edges)


def website_enquiry() -> dict[str, Any]:
    """Answer a visitor who clicked talk on the website.

    Inbound, and the caller is already interested — they pressed a button. The
    job is to qualify quickly and hand over, not to sell.
    """
    nodes = [
        _global_node(),
        _start(
            "Hello, thanks for getting in touch. What can I help you with?",
            "Find out what they need. They came from the website, so they are "
            "already interested — do not re-sell the business to them.",
        ),
        _agent(
            "agent-1",
            "Qualify",
            "Find out what they want, roughly when they need it, and how to "
            "reach them. Three questions at most. If they ask something you do "
            "not know, say a colleague will call rather than guessing.",
            220,
            extraction=[
                {
                    "name": "enquiry_about",
                    "type": "string",
                    "prompt": "What they are asking about",
                },
                {
                    "name": "contact_number",
                    "type": "string",
                    "prompt": "Best number to reach them on",
                },
                {
                    "name": "call_outcome",
                    "type": "string",
                    "prompt": "One of: qualified, browsing, wrong_fit",
                },
            ],
        ),
        _end(
            "end-1",
            "End Call",
            "Tell them who will follow up and roughly when, thank them, and "
            "say goodbye.",
            440,
        ),
    ]
    edges = [
        _edge("start-1", "agent-1", "continue", "They have said what they need."),
        _edge(
            "agent-1",
            "end-1",
            "finished",
            "Their enquiry and contact details are captured, or they want to go.",
        ),
    ]
    return _with_review(nodes, edges)


#: name, description, builder. The description is what an owner reads on the
#: picker, so it names the money rather than the mechanism.
LAUNCH_TEMPLATES: tuple[tuple[str, str, Any], ...] = (
    (
        "Missed call callback",
        "Rings back every caller you did not answer, in their language, and "
        "tells you what they wanted.",
        missed_call,
    ),
    (
        "Follow up a quote",
        "Chases quotes that went quiet and finds out what is holding them up.",
        missed_follow_up,
    ),
    (
        "Recover a failed payment",
        "Tells a customer their payment failed and sends them a secure link. "
        "Never takes card details on the phone.",
        declined_payment,
    ),
    (
        "Website enquiry",
        "Answers the talk button on your site, qualifies the visitor, and "
        "captures how to reach them.",
        website_enquiry,
    ),
)


def build_all() -> list[tuple[str, str, dict[str, Any]]]:
    """Every launch template as ``(name, description, definition)``.

    Includes the vertical packs. Imported here rather than at module scope
    because a pack imports the node helpers from this module, and importing it
    back at the top would be a cycle.
    """
    from api.services.workflow.clinic_pack import CLINIC_TEMPLATES

    return [
        (name, description, build())
        for name, description, build in (*LAUNCH_TEMPLATES, *CLINIC_TEMPLATES)
    ]
