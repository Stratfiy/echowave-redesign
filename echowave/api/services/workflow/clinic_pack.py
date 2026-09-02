"""Clinic agents, prebuilt.

The launch templates are written for any SMB. These are the same four moments
said in a clinic's words, plus the two a clinic asks for that a generic pack
does not have: taking a booking on an inbound call, and confirming tomorrow's
list so fewer people fail to turn up.

A vertical is not a different product. It is the same graph with different
nouns — "appointment" instead of "enquiry", "doctor" instead of "the team".
Building it as new wording on the existing node helpers is deliberate: six
graphs maintained once, said in as many vernaculars as we have customers, is
the only version of this that a five-person team can keep working.

Two rules the prompts here follow, and they are safety rules rather than
style:

- **No clinical content, ever.** The agent books, reschedules, gives timings
  and fees, and hands anything medical to a human. A symptom question is a
  transfer, not an answer.
- **Never confirm a slot the agent cannot see.** Without a calendar
  integration the honest thing is to take a preference and let the desk
  confirm. An agent that invents a confirmed appointment produces a patient
  standing in a waiting room with no booking.
"""

from __future__ import annotations

from typing import Any

from api.services.workflow.launch_templates import (
    _agent,
    _edge,
    _end,
    _global_node,
    _start,
)

#: Said at the start of every clinic agent. Clinics are the one vertical where
#: an unbounded agent is a safety problem rather than an embarrassment, so the
#: boundary is stated in the persona rather than left to each node's prompt.
_CLINIC_GUARDRAIL = (
    "You work at a clinic's front desk. You never give medical advice, never "
    "discuss symptoms, diagnoses, medicines or test results, and never say "
    "whether something is serious. If the caller raises anything clinical, say "
    "the doctor's team will speak to them and offer to transfer or take a "
    "callback. You may only discuss appointments, timings, location and fees."
)


def _clinic_global() -> dict[str, Any]:
    node = _global_node()
    node["data"]["prompt"] = f"{node['data']['prompt']}\n\n{_CLINIC_GUARDRAIL}"
    return node


def appointment_booking() -> dict[str, Any]:
    """Inbound: someone rings the clinic wanting to be seen.

    The commonest call a clinic gets and the one most often missed, because it
    arrives while the desk is with a patient.
    """
    nodes = [
        _clinic_global(),
        _start(
            "Thank you for calling {{business_name}}. How can I help you today?",
            "Find out whether they want an appointment, are asking about "
            "timings or fees, or need something else. Do not ask for personal "
            "details until you know why they are calling.",
        ),
        _agent(
            "agent-1",
            "Take the booking",
            "Get their name, which doctor or treatment they want, and the day "
            "and rough time that suits them. Do NOT confirm a specific slot — "
            "say the desk will confirm by message shortly. If they ask "
            "anything medical, tell them the doctor's team will call back and "
            "move on.\n\n"
            "Our hours: {{opening_hours}}\n"
            "What we offer: {{services}}\n"
            "Fees: {{fees}}\n"
            "Where we are: {{address}}\n"
            "Only use the facts above. If you have not been given one, say "
            "the desk will confirm rather than guessing.",
            220,
            extraction=[
                {"name": "patient_name", "type": "string", "prompt": "Their name"},
                {
                    "name": "reason",
                    "type": "string",
                    "prompt": "Treatment or doctor they asked for",
                },
                {
                    "name": "preferred_time",
                    "type": "string",
                    "prompt": "Day and rough time they would like",
                },
                {
                    "name": "call_outcome",
                    "type": "string",
                    "prompt": "One of: wants_appointment, asked_info, "
                    "needs_clinical_callback, not_interested",
                },
            ],
        ),
        _end(
            "end-1",
            "End Call",
            "Say what happens next in one sentence — that the desk will "
            "confirm the time by message — thank them, and say goodbye.",
            440,
        ),
    ]
    edges = [
        _edge(
            "start-1",
            "agent-1",
            "continue",
            "They want an appointment, or they are asking about timings or fees.",
        ),
        _edge(
            "agent-1",
            "end-1",
            "finished",
            "Their request is captured, or they want to end the call.",
        ),
    ]
    return {"nodes": nodes, "edges": edges}


def appointment_reminder() -> dict[str, Any]:
    """Outbound: confirm tomorrow's list so fewer people fail to turn up.

    The one clinic use case with a number the owner already knows. They can
    count the empty chairs.
    """
    nodes = [
        _clinic_global(),
        _start(
            "Hello, this is {{business_name}} calling about your appointment. "
            "Is that {{patient_name}}?",
            "Confirm you are speaking to the right person before saying "
            "anything about their appointment. If it is the wrong person or a "
            "bad time, apologise and end without giving details.",
        ),
        _agent(
            "agent-1",
            "Confirm or reschedule",
            "Tell them the day and time of their appointment and ask whether "
            "they can still come. If they cannot, get a day and rough time "
            "that suits them instead — do not confirm a new slot yourself, say "
            "the desk will confirm. Do not discuss why they are coming in.",
            220,
            extraction=[
                {
                    "name": "call_outcome",
                    "type": "string",
                    "prompt": "One of: confirmed, rescheduling, cancelled, no_answer",
                },
                {
                    "name": "new_preferred_time",
                    "type": "string",
                    "prompt": "Day and rough time they would prefer instead, if any",
                },
            ],
        ),
        _end(
            "end-1",
            "End Call",
            "Confirm in one sentence what will happen, thank them, and say "
            "goodbye.",
            440,
        ),
    ]
    edges = [
        _edge(
            "start-1",
            "agent-1",
            "continue",
            "They have confirmed they are the right person and can talk.",
        ),
        _edge(
            "agent-1",
            "end-1",
            "finished",
            "They have confirmed, rescheduled or cancelled.",
        ),
    ]
    return {"nodes": nodes, "edges": edges}


def missed_call_clinic() -> dict[str, Any]:
    """Outbound: an enquiry rang, nobody answered, ring them back.

    The generic missed-call template in a clinic's words. Paired with a number
    in callback mode this is the whole missed-call product.
    """
    nodes = [
        _clinic_global(),
        _start(
            "Hello, this is {{business_name}} returning your call. "
            "I believe you tried to reach us a short while ago — is now a good time?",
            "Say who you are and that you are returning their call, before "
            "asking for anything. A caller from an hour ago may not remember "
            "ringing. If it is a bad time, offer to call back and end politely.",
        ),
        _agent(
            "agent-1",
            "What they needed",
            "Find out what they were calling about. If they want to be seen, "
            "get their name and a day and rough time that suits them. Do not "
            "confirm a slot — you cannot see the diary; say the desk will "
            "confirm by message. Keep it to two or three questions. Anything "
            "clinical goes to the doctor's team, not to you.\n\n"
            "Our hours: {{opening_hours}}\n"
            "What we offer: {{services}}\n"
            "Only use the facts above; do not guess.",
            220,
            extraction=[
                {"name": "patient_name", "type": "string", "prompt": "Their name"},
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
                    "prompt": "One of: wants_appointment, asked_info, "
                    "needs_clinical_callback, not_interested",
                },
            ],
        ),
        _end(
            "end-1",
            "End Call",
            "Confirm in one sentence what happens next, thank them, and say "
            "goodbye.",
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
            "Their enquiry is understood, or they want to end the call.",
        ),
    ]
    return {"nodes": nodes, "edges": edges}


#: Named "Clinic — ..." so the four generic templates and these sit together in
#: one list and still read as two packs. A `vertical` column would group them
#: properly; the prefix is what that costs nothing today.
CLINIC_TEMPLATES: tuple[tuple[str, str, Any], ...] = (
    (
        "Clinic — Appointment booking",
        "Answers the clinic's phone, takes booking requests, and passes "
        "anything clinical to your team.",
        appointment_booking,
    ),
    (
        "Clinic — Appointment reminder",
        "Calls tomorrow's patients to confirm, and takes a new time from "
        "anyone who cannot come.",
        appointment_reminder,
    ),
    (
        "Clinic — Missed call callback",
        "Rings back an enquiry nobody answered, while it is still warm.",
        missed_call_clinic,
    ),
)
