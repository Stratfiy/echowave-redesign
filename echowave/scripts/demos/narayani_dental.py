"""Narayani Dental Clinic, Hosur — the front desk that never puts you on hold.

What a dental front desk does on the phone, as an agent: books, reschedules
and cancels appointments, answers timings and directions, sends anything
clinical or urgent to a person. Hosur sits on the Tamil Nadu–Karnataka
border, so callers open in Tamil, Kannada, English or Hindi and switch
mid-sentence; the agent follows.

Dry run (prints the definition, needs nothing installed beyond Python)::

    python -m scripts.demos.narayani_dental

Build it on an account::

    python -m scripts.demos.narayani_dental \\
        --base-url https://app.decibyl.ai --api-key dk_... \\
        --slots-url https://clinic.example/api/slots \\
        --book-url  https://clinic.example/api/book \\
        --transfer-to +919999999999

Until the clinic's endpoints exist, point both URLs at a mock that returns
``{"slots": ["Tuesday 5:30 pm", "Wednesday 11:00 am"]}`` and
``{"booked": true, "reference": "ND-1042"}`` and the conversation runs end
to end. Every detail the agent states — doctors, hours, address, prices —
comes from the variables below, never from the model's imagination.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

AGENT_NAME = "Narayani Dental front desk"

CLINIC = {
    "clinic_name": "Narayani Dental Clinic",
    "city": "Hosur",
    # Placeholders until the clinic confirms; the demo says these out loud.
    "doctor_names": "Dr. Narayani (general and cosmetic dentistry), Dr. Karthik (orthodontics)",
    "opening_hours": "Monday to Saturday, 9:30 am to 1:00 pm and 4:00 pm to 8:00 pm; Sunday closed",
    "clinic_address": "Narayani Dental Clinic, near the bus stand, Hosur, Tamil Nadu",
    "treatments": (
        "check-ups and cleaning, fillings, root canal, tooth removal, braces and "
        "aligners consultation, children's dentistry, whitening, dental implants consultation"
    ),
    "consultation_fee": "a consultation is 300 rupees; treatment costs are quoted after the doctor sees you",
}

RULES = (
    "These rules apply on every turn.\n"
    "- You are the front desk of {clinic_name} in {city}. Warm, brief, "
    "efficient: the way a good receptionist is at a busy clinic.\n"
    "- Follow the caller's language. Tamil, Kannada, English or Hindi — switch "
    "to whatever they use and stay there, including mid-sentence mixes.\n"
    "- Ask one question per turn. Keep every turn under three short sentences.\n"
    "- Read back names, phone numbers (digit by digit), dates and times before "
    "treating them as confirmed.\n"
    "- Never give clinical advice. Anything about pain, medicines, bleeding, "
    "swelling, or what a treatment involves gets a callback from the doctor, "
    "or a transfer if it sounds urgent.\n"
    "- Never invent a doctor, a time, a price or a promise. The facts you may "
    "state are exactly these: doctors — {doctor_names}; hours — {opening_hours}; "
    "address — {clinic_address}; treatments — {treatments}; fees — {consultation_fee}.\n"
    "- If the caller asks for a person, hand off at once."
).format(**CLINIC)

GREETING = "Vanakkam, Narayani Dental Clinic, Hosur. How can I help you today?"


def _tool(
    name: str, description: str, *, method: str, url: str, parameters: list[dict]
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "category": "http_api",
        "definition": {
            "schema_version": 1,
            "type": "http_api",
            "config": {
                "method": method,
                "url": url,
                "parameters": parameters,
                "timeout_ms": 8000,
            },
        },
    }


def tools(slots_url: str, book_url: str, transfer_to: str) -> list[dict]:
    """Three things the agent can do that a prompt cannot."""
    treatment = {
        "name": "treatment",
        "type": "string",
        "description": "What the caller wants done, in a few words: cleaning, root canal, braces consultation, child check-up, and so on.",
        "required": True,
    }
    preferred = {
        "name": "preferred_time",
        "type": "string",
        "description": "The day and time window the caller asked for, as they said it.",
        "required": True,
    }
    return [
        _tool(
            "check_slots",
            "Ask the clinic's diary for free slots near the caller's preferred day "
            "and time. Call it once the treatment and a preferred time are known.",
            method="POST",
            url=slots_url,
            parameters=[treatment, preferred],
        ),
        _tool(
            "book_appointment",
            "Book the slot the caller chose. Only after the name, phone number, "
            "treatment and slot have all been read back and confirmed.",
            method="POST",
            url=book_url,
            parameters=[
                {
                    "name": "patient_name",
                    "type": "string",
                    "description": "The patient's name as confirmed.",
                    "required": True,
                },
                {
                    "name": "phone",
                    "type": "string",
                    "description": "Ten-digit mobile number, digits only, as read back.",
                    "required": True,
                },
                treatment,
                {
                    "name": "slot",
                    "type": "string",
                    "description": "The exact slot the caller chose from check_slots.",
                    "required": True,
                },
                {
                    "name": "notes",
                    "type": "string",
                    "description": "Anything the doctor should know before the visit, in the caller's words.",
                    "required": False,
                },
            ],
        ),
        {
            "name": "transfer_to_reception",
            "description": "Hand the call to the reception desk, with what has been gathered so far.",
            "category": "transfer_call",
            "definition": {
                "schema_version": 1,
                "type": "transfer_call",
                "config": {
                    "destination_source": "static",
                    "destination": transfer_to,
                    "messageType": "custom",
                    "message": "One moment, connecting you to the reception desk.",
                },
            },
        },
    ]


def _node(
    node_id: str,
    node_type: str,
    name: str,
    prompt: str,
    y: int,
    x: int = 0,
    **data: Any,
) -> dict:
    payload: dict[str, Any] = {
        "name": name,
        "prompt": prompt,
        "allow_interrupt": True,
        "add_global_prompt": True,
        **data,
    }
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": x, "y": y},
        "data": payload,
    }


def _edge(source: str, target: str, label: str, condition: str) -> dict:
    return {
        "id": f"{source}-{label.replace(' ', '_')}-{target}",
        "source": source,
        "target": target,
        "data": {"label": label, "condition": condition},
    }


def definition(tool_ids: dict[str, str]) -> dict[str, Any]:
    """The conversation, as the canvas and the runtime read it."""
    ids = lambda *names: [tool_ids[n] for n in names if n in tool_ids]  # noqa: E731
    step = 200

    nodes = [
        {
            "id": "global-1",
            "type": "globalNode",
            "position": {"x": -360, "y": 0},
            "data": {"name": "Rules", "prompt": RULES},
        },
        _node(
            "start-1",
            "startCall",
            "Greet and understand",
            "Find out what the caller needs. Almost everyone wants one of: to "
            "book an appointment, to reschedule or cancel one, to ask timings, "
            "directions or fees, or something about a tooth or a treatment. If "
            "they describe pain, swelling, bleeding or an accident, treat it as "
            "urgent. Do not ask for their name yet.",
            0,
            greeting=GREETING,
        ),
        _node(
            "agent-details",
            "agentNode",
            "Take the booking details",
            "Collect, one at a time and reading each back: the patient's name, "
            "a ten-digit mobile number, what they want done (from the "
            "treatments list, or 'check-up' if unsure), and a preferred day and "
            "time. If they ask which doctor, name the doctors and what each "
            "does. Once all four are confirmed, move on.",
            step,
        ),
        _node(
            "agent-slots",
            "agentNode",
            "Find a slot",
            "Call check_slots with the treatment and preferred time. Offer at "
            "most two of the returned slots, nearest first, and ask which suits. "
            "If none suit, ask for another day and call check_slots again. If "
            "the diary returns nothing at all, apologise and offer a callback "
            "from reception.",
            2 * step,
            tool_uuids=ids("check_slots"),
        ),
        _node(
            "agent-book",
            "agentNode",
            "Confirm and book",
            "Read back the name, number, treatment and slot in one short "
            "sentence and ask 'shall I confirm?'. Only on a clear yes call "
            "book_appointment. Then tell them the reference, that a WhatsApp "
            "confirmation will follow, to arrive ten minutes early, and to "
            "bring any earlier X-rays or reports.",
            3 * step,
            tool_uuids=ids("book_appointment"),
        ),
        _node(
            "agent-change",
            "agentNode",
            "Reschedule or cancel",
            "Ask for the mobile number the booking was made with and the "
            "current appointment day. Read both back. For a reschedule, ask "
            "for the new preferred time and move on to find a slot. For a "
            "cancellation, confirm they want it cancelled and say reception "
            "will send a confirmation; do not ask why.",
            step,
            x=380,
        ),
        _node(
            "agent-info",
            "agentNode",
            "Timings, directions and fees",
            "Answer from the facts in the rules only: hours, address, doctors, "
            "treatments offered, and the consultation fee. For any other price "
            "say it is quoted after the doctor sees them, and offer to book a "
            "consultation.",
            step,
            x=760,
        ),
        _node(
            "agent-clinical",
            "agentNode",
            "Clinical question",
            "Do not answer the medical question. Say the doctor will call them "
            "back, take their name and number (read back), and ask in one line "
            "what it is about. If it sounds urgent — severe pain, swelling of "
            "the face, bleeding that will not stop, a knocked-out tooth — hand "
            "off to reception immediately instead.",
            step,
            x=1140,
        ),
        _node(
            "agent-escalate",
            "agentNode",
            "Hand to a person",
            "Say you are connecting them to the reception desk, then call "
            "transfer_to_reception. Say nothing else.",
            2 * step,
            x=1140,
            tool_uuids=ids("transfer_to_reception"),
        ),
        _node(
            "end-1",
            "endCall",
            "Wrap up",
            "Thank the caller by name if you have it, wish them well, and end "
            "the call. Nothing new is introduced here.",
            4 * step,
        ),
    ]

    edges = [
        _edge(
            "start-1",
            "agent-details",
            "book",
            "The caller wants to book an appointment.",
        ),
        _edge(
            "start-1",
            "agent-change",
            "change",
            "The caller wants to reschedule or cancel an existing appointment.",
        ),
        _edge(
            "start-1",
            "agent-info",
            "info",
            "The caller asks about timings, directions, doctors, treatments or fees.",
        ),
        _edge(
            "start-1",
            "agent-clinical",
            "clinical",
            "The caller asks something medical, or describes pain, swelling, bleeding or an injury.",
        ),
        _edge(
            "start-1",
            "agent-escalate",
            "person",
            "The caller asks to speak to a person.",
        ),
        _edge(
            "agent-details",
            "agent-slots",
            "details done",
            "Name, number, treatment and preferred time are all confirmed.",
        ),
        _edge(
            "agent-slots", "agent-book", "slot chosen", "The caller has chosen a slot."
        ),
        _edge(
            "agent-slots",
            "agent-escalate",
            "no slots",
            "The diary returned nothing and the caller wants a person.",
        ),
        _edge(
            "agent-book",
            "end-1",
            "booked",
            "The appointment is booked and the reference given.",
        ),
        _edge(
            "agent-change", "agent-slots", "reschedule", "The caller wants a new time."
        ),
        _edge(
            "agent-change",
            "end-1",
            "cancelled",
            "The caller confirmed the cancellation.",
        ),
        _edge(
            "agent-info",
            "agent-details",
            "then book",
            "After the information the caller wants to book.",
        ),
        _edge("agent-info", "end-1", "done", "The caller has what they needed."),
        _edge("agent-clinical", "agent-escalate", "urgent", "It sounds urgent."),
        _edge(
            "agent-clinical",
            "end-1",
            "callback taken",
            "The callback details are taken.",
        ),
        _edge("agent-escalate", "end-1", "transferred", "The transfer has been made."),
    ]
    nodes.append(whatsapp_step())
    return {"nodes": nodes, "edges": edges}


def whatsapp_step() -> dict:
    """The artefact the caller keeps: one WhatsApp message after the call.

    Detached from the conversation — no edges — it fires when the run ends,
    to the number that was on the call, on Decibyl's WhatsApp sender. The
    text is what a receptionist would send by hand; the clinic's own
    reference and time come in once the booking tool returns them.
    """
    return {
        "id": "whatsapp-1",
        "type": "sms",
        "position": {"x": -360, "y": 400},
        "data": {
            "name": "WhatsApp confirmation",
            "enabled": True,
            "channel": "whatsapp",
            "body": (
                "Thank you for calling {clinic_name}, {city}. Our reception "
                "will confirm your appointment on this number shortly. Please "
                "arrive ten minutes early and bring any earlier X-rays or "
                "reports."
            ).format(**CLINIC),
        },
    }


def _post(base_url: str, api_key: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"{path} failed with {exc.code}: {detail}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="https://app.decibyl.ai")
    parser.add_argument(
        "--api-key", help="An API key for the account. Omit to print the definition."
    )
    parser.add_argument("--slots-url", default="https://example.invalid/narayani/slots")
    parser.add_argument("--book-url", default="https://example.invalid/narayani/book")
    parser.add_argument(
        "--transfer-to", default="+910000000000", help="Reception desk number, E.164."
    )
    parser.add_argument("--name", default=AGENT_NAME)
    args = parser.parse_args(argv)

    specs = tools(args.slots_url, args.book_url, args.transfer_to)
    if not args.api_key:
        print(
            json.dumps(
                {"name": args.name, "workflow_definition": definition({})}, indent=2
            )
        )
        return 0

    tool_ids: dict[str, str] = {}
    for spec in specs:
        created = _post(args.base_url, args.api_key, "/api/v1/tools/", spec)
        tool_ids[spec["name"]] = created["tool_uuid"]
        print(f"tool {spec['name']}: {created['tool_uuid']}", file=sys.stderr)
    workflow = _post(
        args.base_url,
        args.api_key,
        "/api/v1/workflow/create/definition",
        {"name": args.name, "workflow_definition": definition(tool_ids)},
    )
    print(
        f"agent {args.name}: {args.base_url.rstrip('/')}/workflow/{workflow['id']}",
        file=sys.stderr,
    )
    print(json.dumps({"workflow_id": workflow["id"], "tools": tool_ids}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
