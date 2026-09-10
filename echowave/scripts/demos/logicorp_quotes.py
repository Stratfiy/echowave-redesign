"""Logicorp — an international courier quote desk on the phone.

Logicorp (logicorp.in) is a logistics aggregator: one interface in front of
domestic and international courier partners. This demo is their quoting line
for DHL Express Time Definite export from India. A caller says where the
parcel is going and what it weighs; the agent works out the DHL zone, the
chargeable weight, the base rate, mentions the surcharges that apply, and
either captures the lead for a formal quote or hands to a sales person.

The 2026 DHL rate guide is uploaded to the account's knowledge base so the
agent can answer surcharge and customs-service questions from the document.
The zone map and the rate table are also spelled out in the prompts, because
a voice agent has to look a number up in under a second and get it right.

Dry run (prints the definition, needs nothing installed beyond Python)::

    python -m scripts.demos.logicorp_quotes

Build it on an account::

    python -m scripts.demos.logicorp_quotes \\
        --base-url https://app.decibyl.ai --api-key dk_... \\
        --rate-guide /path/to/Time_Definite_Export.pdf \\
        --lead-url https://logicorp.in/api/leads \\
        --transfer-to +919999999999

Until Logicorp's lead endpoint exists, point ``--lead-url`` at a mock that
returns ``{"captured": true, "reference": "LQ-2041"}``. Every rupee figure
the agent states comes from the rate guide reproduced below, never from the
model's memory.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

AGENT_NAME = "Logicorp quote desk"

COMPANY = {
    "company_name": "Logicorp",
    "tagline": "India's smartest logistics aggregator platform",
    "what_we_do": (
        "we connect businesses to premium domestic and global courier partners "
        "through a single interface, with real-time tracking and management"
    ),
    "carrier": "DHL Express",
    "product": "DHL Express Worldwide, time definite, door to door",
    "rate_year": "2026",
}

# DHL Express India 2026, Time Definite Worldwide Export. Zone by destination.
ZONES: dict[int, list[str]] = {
    1: [
        "Bangladesh",
        "Bhutan",
        "Maldives",
        "Nepal",
        "Sri Lanka",
        "United Arab Emirates",
    ],
    2: ["Hong Kong", "Malaysia", "Singapore", "Thailand"],
    3: ["China"],
    4: ["Bahrain", "Jordan", "Kuwait", "Oman", "Pakistan", "Qatar", "Saudi Arabia"],
    5: [
        "Brunei",
        "Cambodia",
        "Indonesia",
        "Japan",
        "South Korea",
        "Laos",
        "Macau",
        "Myanmar",
        "Philippines",
        "Taiwan",
        "Timor-Leste",
        "Vietnam",
    ],
    6: ["Australia", "New Zealand", "Papua New Guinea"],
    7: [
        "United Kingdom",
        "Germany",
        "France",
        "Italy",
        "Spain",
        "Netherlands",
        "Belgium",
        "Switzerland",
        "Austria",
        "Sweden",
        "Norway",
        "Denmark",
        "Finland",
        "Ireland",
        "Poland",
        "Portugal",
        "Greece",
        "Turkey",
        "Israel",
        "and the rest of Europe including Czech Republic, Hungary, Romania, "
        "Croatia, Serbia, the Baltics and Cyprus",
    ],
    8: ["USA", "Canada", "Mexico"],
    9: [
        "Brazil",
        "Argentina",
        "Chile",
        "Colombia",
        "Peru",
        "South Africa",
        "Uruguay",
        "Venezuela",
        "Ecuador",
        "Bolivia",
        "Paraguay",
    ],
    10: [
        "the rest of the world: Africa except South Africa, the Caribbean and "
        "Central America, Russia and Central Asia, Egypt, Iran, Iraq, Lebanon, "
        "Afghanistan, Kazakhstan, Mauritius, Kenya, Nigeria, Ghana, Morocco, "
        "and the Pacific islands",
    ],
}

# INR, non-documents from 0.5 kg and documents from 2.5 kg. Columns are zones 1..10.
NON_DOC_RATES: dict[str, list[int]] = {
    "0.5": [1116, 1473, 1552, 1615, 1658, 1899, 1586, 1632, 2366, 2311],
    "1": [1453, 1821, 1938, 1991, 2074, 2338, 1833, 1885, 3123, 2866],
    "2": [1891, 2379, 2534, 2599, 2694, 3034, 2327, 2393, 3963, 3756],
    "3": [2279, 2869, 3082, 3150, 3258, 3680, 2802, 2884, 4769, 4618],
    "5": [2955, 3713, 4070, 4138, 4274, 4872, 3714, 3832, 6309, 6286],
    "10": [3955, 4923, 5820, 5928, 6114, 6882, 5554, 5832, 9609, 9806],
    "15": [5125, 5763, 7190, 7268, 7474, 8502, 6854, 7522, 12379, 12316],
    "20": [6295, 6603, 8560, 8608, 8834, 10122, 8154, 9212, 15149, 14826],
    "25": [7315, 8003, 9980, 10028, 10984, 12337, 10134, 11222, 17439, 17996],
    "30": [8335, 9403, 11400, 11448, 13134, 14552, 12114, 13232, 19729, 21166],
}

# INR, documents up to 2 kg.
DOC_RATES: dict[str, list[int]] = {
    "0.5": [901, 1118, 1176, 1202, 1302, 1482, 1246, 1385, 1915, 1901],
    "1": [1150, 1456, 1539, 1554, 1710, 1929, 1619, 1598, 2610, 2438],
    "2": [1572, 2024, 2163, 2196, 2400, 2673, 2141, 2134, 3466, 3328],
}

# INR per kg for the whole shipment, from 30.1 kg. (from_kg, to_kg, per zone)
MULTIPLIER_RATES: list[tuple[str, str, list[int]]] = [
    ("30.1", "70", [269, 305, 368, 370, 433, 483, 408, 444, 636, 686]),
    ("70.1", "300", [259, 290, 350, 356, 413, 460, 403, 444, 620, 668]),
    ("300.1", "and above", [260, 292, 353, 358, 416, 464, 405, 453, 632, 673]),
]

SURCHARGES = (
    "Fuel surcharge: a percentage on the transport charge, changes monthly, "
    "see mydhl.com for the current figure. GST applies on top of everything. "
    "Remote area delivery: 50 rupees per kg, minimum 2,500. "
    "Oversize piece, longest side over 100 cm or second side over 80 cm: 1,900 per piece. "
    "Overweight piece over 70 kg: 9,200 per piece. "
    "Non-conveyable piece between 25 and 70 kg: 1,900 per piece. "
    "Shipment value protection: 1,200 or 1 percent of value, whichever is higher. "
    "Duties and taxes paid at origin: 2 percent of the fiscal charges, minimum 2,500. "
    "Saturday delivery 4,000; Saturday pickup 3,000; dedicated pickup 40 per kg, minimum 4,000. "
    "Lithium-ion batteries: 1,000 per shipment. Dry ice: 1,200. Full dangerous goods: 10,000. "
    "Elevated-risk or restricted destinations: 3,000 per shipment. "
    "Address correction 1,200; adult, direct or verified signature 500 each; "
    "residential address 500."
)


def _zone_lines() -> str:
    return "\n".join(
        f"  Zone {zone}: " + ", ".join(countries) for zone, countries in ZONES.items()
    )


def _rate_lines(table: dict[str, list[int]]) -> str:
    return "\n".join(
        f"  {kg} kg: " + ", ".join(f"Z{i + 1} {r:,}" for i, r in enumerate(rates))
        for kg, rates in table.items()
    )


def _multiplier_lines() -> str:
    return "\n".join(
        f"  {lo} to {hi} kg, per kg: "
        + ", ".join(f"Z{i + 1} {r}" for i, r in enumerate(rates))
        for lo, hi, rates in MULTIPLIER_RATES
    )


RULES = (
    "These rules apply on every turn.\n"
    "- You are the quote desk at {company_name}, {tagline}: {what_we_do}. Quotes "
    "on this line are for {product} from anywhere in India, on the {carrier} "
    "{rate_year} rate guide.\n"
    "- Speak the caller's language: English, Hindi, Tamil or Kannada, and switch "
    "if they do. Never announce which language you are using. Keep turns under "
    "three short sentences; ask one thing at a time.\n"
    "- Work out the zone and the rate silently and say only the answer. Never "
    "think aloud, never say 'wait' or 'let me check', never read the table "
    "back to the caller.\n"
    "- Say rupee amounts in words a person would use: 'about two thousand three "
    "hundred rupees', never digit strings. Round to the nearest ten.\n"
    "- Every figure you state comes from the rate guide in these rules or the "
    "uploaded DHL document. If a destination, weight band or charge is not "
    "there, say so and offer a callback rather than guessing.\n"
    "- A quote on the phone is indicative: base transport charge before fuel "
    "surcharge and GST, and before duties at destination. Say that once per "
    "quote, briefly.\n"
    "- Chargeable weight is the higher of actual weight and volumetric weight. "
    "Volumetric weight in kg is length x width x height in cm divided by 5000. "
    "Round up to the next half kilo under 20 kg and to the next kilo above.\n"
    "- Documents (paper only, no commercial value) get the document rate up to "
    "2 kg; everything else, and documents above 2 kg, is the non-document rate.\n"
    "- Read back phone numbers digit by digit and email addresses letter by "
    "letter before capturing them. If the caller asks for a person, hand off.\n"
    "\n"
    "DESTINATION ZONES\n{zones}\n"
    "\n"
    "NON-DOCUMENT RATES, INR per shipment (Z = zone)\n{non_doc}\n"
    "For weights between two rows, quote the next higher row.\n"
    "\n"
    "DOCUMENT RATES, INR per shipment, up to 2 kg\n{docs}\n"
    "\n"
    "ABOVE 30 KG, INR per kg on the whole shipment\n{multiplier}\n"
    "\n"
    "SURCHARGES AND OPTIONAL SERVICES\n{surcharges}"
).format(
    zones=_zone_lines(),
    non_doc=_rate_lines(NON_DOC_RATES),
    docs=_rate_lines(DOC_RATES),
    multiplier=_multiplier_lines(),
    surcharges=SURCHARGES,
    **COMPANY,
)

GREETING = (
    "Hello, you've reached Logicorp, international shipping quotes. "
    "Where is your parcel going, and roughly what does it weigh?"
)


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


def tools(lead_url: str, transfer_to: str) -> list[dict]:
    """Two things the agent can do that a prompt cannot."""
    return [
        _tool(
            "capture_lead",
            "Record the caller and the shipment they asked about so the sales "
            "team can send a formal quote and book the pickup. Only after the "
            "name and phone number have been read back and confirmed.",
            method="POST",
            url=lead_url,
            parameters=[
                {
                    "name": "contact_name",
                    "type": "string",
                    "description": "The caller's name as confirmed.",
                    "required": True,
                },
                {
                    "name": "phone",
                    "type": "string",
                    "description": "Ten-digit mobile number, digits only, as read back.",
                    "required": True,
                },
                {
                    "name": "email",
                    "type": "string",
                    "description": "Email address for the written quote, if given.",
                    "required": False,
                },
                {
                    "name": "company",
                    "type": "string",
                    "description": "The caller's business name, if any.",
                    "required": False,
                },
                {
                    "name": "destination_country",
                    "type": "string",
                    "description": "Destination country as confirmed.",
                    "required": True,
                },
                {
                    "name": "origin_city",
                    "type": "string",
                    "description": "Pickup city in India.",
                    "required": True,
                },
                {
                    "name": "shipment_type",
                    "type": "string",
                    "description": "'document' or 'parcel'.",
                    "required": True,
                },
                {
                    "name": "chargeable_weight_kg",
                    "type": "number",
                    "description": "The chargeable weight the quote was based on.",
                    "required": True,
                },
                {
                    "name": "contents",
                    "type": "string",
                    "description": "What is being shipped, in the caller's words.",
                    "required": False,
                },
                {
                    "name": "indicative_quote_inr",
                    "type": "number",
                    "description": "The base transport charge quoted on the call, in rupees.",
                    "required": True,
                },
                {
                    "name": "notes",
                    "type": "string",
                    "description": "Surcharges mentioned, pickup timing, anything else sales should know.",
                    "required": False,
                },
            ],
        ),
        {
            "name": "transfer_to_sales",
            "description": "Hand the call to a Logicorp sales person, with what has been gathered so far.",
            "category": "transfer_call",
            "definition": {
                "schema_version": 1,
                "type": "transfer_call",
                "config": {
                    "destination_source": "static",
                    "destination": transfer_to,
                    "messageType": "custom",
                    "message": "One moment, connecting you to our sales team.",
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


def definition(
    tool_ids: dict[str, str], document_uuids: list[str] | None = None
) -> dict[str, Any]:
    """The conversation, as the canvas and the runtime read it."""
    ids = lambda *names: [tool_ids[n] for n in names if n in tool_ids]  # noqa: E731
    docs = {"document_uuids": document_uuids} if document_uuids else {}
    step = 200

    nodes = [
        {
            "id": "global-1",
            "type": "globalNode",
            "position": {"x": -360, "y": 0},
            "data": {"name": "Rules and rate guide", "prompt": RULES},
        },
        _node(
            "start-1",
            "startCall",
            "Greet and understand",
            "Find out what the caller needs. Most want a price for sending "
            "something abroad. Some want to know a surcharge, customs service, "
            "packaging or transit rule. Some are existing customers asking "
            "about a shipment already booked, or want a person. Do not ask for "
            "their name yet.",
            0,
            greeting=GREETING,
        ),
        _node(
            "agent-shipment",
            "agentNode",
            "Take the shipment details",
            "Collect, one at a time: the destination country (city too if they "
            "offer it), whether it is documents only or a parcel, the actual "
            "weight in kg, and for parcels the box size in cm if they know it. "
            "Ask the pickup city in India. Work out the zone from the "
            "destination and the chargeable weight from the rules. If the "
            "destination is not in the zone list, say the desk will confirm and "
            "move to capture the lead.",
            step,
        ),
        _node(
            "agent-quote",
            "agentNode",
            "Give the indicative quote",
            "State the quote in one breath: the zone, the chargeable weight, "
            "and the base rate from the table, in words. Then one sentence: "
            "fuel surcharge and GST are extra and duties at destination are on "
            "the receiver unless they choose duties paid. Mention only the "
            "surcharges that clearly apply from what they said, for example a "
            "remote town or an oversize box. Then ask if they would like a "
            "formal written quote and pickup arranged.",
            2 * step,
            **docs,
        ),
        _node(
            "agent-lead",
            "agentNode",
            "Capture the lead",
            "Take their name, a ten-digit mobile number, and an email for the "
            "written quote; read each back. Ask for the company name and what "
            "is being shipped. Read back name and number in one sentence and "
            "ask 'shall I pass this to the team?'. Only on a clear yes call "
            "capture_lead. Then say the team will send the quote within the "
            "hour on working days and can book the pickup from it.",
            3 * step,
            tool_uuids=ids("capture_lead"),
        ),
        _node(
            "agent-services",
            "agentNode",
            "Surcharges, customs and services",
            "Answer from the surcharge list in the rules and from the DHL rate "
            "guide document. Give the charge and the one-line condition it "
            "applies under. For fuel surcharge say it is a monthly percentage "
            "published on mydhl.com. For anything the document does not "
            "cover, offer a callback from the team.",
            step,
            x=380,
            **docs,
        ),
        _node(
            "agent-existing",
            "agentNode",
            "Existing shipment",
            "Tracking, a delayed delivery, a customs hold or an invoice query "
            "is handled by the team, not this line. Take the airway bill or "
            "tracking number if they have it and their mobile number, read "
            "both back, and hand off to sales.",
            step,
            x=760,
        ),
        _node(
            "agent-escalate",
            "agentNode",
            "Hand to a person",
            "Say you are connecting them to the sales team, then call "
            "transfer_to_sales. Say nothing else.",
            2 * step,
            x=1140,
            tool_uuids=ids("transfer_to_sales"),
        ),
        _node(
            "end-1",
            "endCall",
            "Wrap up",
            "Thank the caller by name if you have it, mention logicorp.in for "
            "tracking and bookings, and end the call. Nothing new is "
            "introduced here.",
            4 * step,
        ),
    ]

    edges = [
        _edge(
            "start-1",
            "agent-shipment",
            "quote",
            "The caller wants a price for sending something abroad.",
        ),
        _edge(
            "start-1",
            "agent-services",
            "services",
            "The caller asks about a surcharge, customs service, packaging, "
            "insurance, dangerous goods or delivery option.",
        ),
        _edge(
            "start-1",
            "agent-existing",
            "existing",
            "The caller asks about a shipment already booked, tracking, a delay or an invoice.",
        ),
        _edge(
            "start-1",
            "agent-escalate",
            "person",
            "The caller asks to speak to a person.",
        ),
        _edge(
            "agent-shipment",
            "agent-quote",
            "details done",
            "Destination, shipment type and weight are known.",
        ),
        _edge(
            "agent-shipment",
            "agent-lead",
            "unlisted destination",
            "The destination is not in the zone list and the caller wants a callback.",
        ),
        _edge(
            "agent-quote",
            "agent-lead",
            "wants quote",
            "The caller wants a written quote or a pickup.",
        ),
        _edge(
            "agent-quote",
            "agent-services",
            "asks surcharge",
            "The caller asks about a surcharge, insurance, duties or a service.",
        ),
        _edge(
            "agent-quote",
            "agent-shipment",
            "another shipment",
            "The caller wants a price for a different destination or weight.",
        ),
        _edge(
            "agent-quote",
            "end-1",
            "just the price",
            "The caller has the price and wants nothing more.",
        ),
        _edge(
            "agent-services",
            "agent-shipment",
            "then quote",
            "After the answer the caller wants a price.",
        ),
        _edge(
            "agent-services",
            "agent-quote",
            "back to quote",
            "The caller had a quote in progress and wants to continue it.",
        ),
        _edge("agent-services", "end-1", "done", "The caller has what they needed."),
        _edge(
            "agent-lead",
            "end-1",
            "captured",
            "The lead is captured and the next step explained.",
        ),
        _edge(
            "agent-existing",
            "agent-escalate",
            "hand off",
            "The details are taken.",
        ),
        _edge("agent-escalate", "end-1", "transferred", "The transfer has been made."),
    ]
    return {"nodes": nodes, "edges": edges}


def _request(
    base_url: str, api_key: str, path: str, body: dict | None, method: str = "POST"
) -> dict:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"{path} failed with {exc.code}: {detail}") from exc


def _post(base_url: str, api_key: str, path: str, body: dict) -> dict:
    return _request(base_url, api_key, path, body)


def upload_rate_guide(base_url: str, api_key: str, path: Path) -> str:
    """Put the DHL guide in the knowledge base; returns its document UUID."""
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    ticket = _post(
        base_url,
        api_key,
        "/api/v1/knowledge-base/upload-url",
        {"filename": path.name, "mime_type": mime},
    )
    put = urllib.request.Request(
        ticket["upload_url"],
        data=path.read_bytes(),
        headers={"Content-Type": mime},
        method="PUT",
    )
    with urllib.request.urlopen(put, timeout=120) as response:
        if response.status not in (200, 201, 204):
            raise SystemExit(f"upload returned {response.status}")
    _post(
        base_url,
        api_key,
        "/api/v1/knowledge-base/process-document",
        {
            "document_uuid": ticket["document_uuid"],
            "s3_key": ticket["s3_key"],
            "retrieval_mode": "chunked",
        },
    )
    return ticket["document_uuid"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="https://app.decibyl.ai")
    parser.add_argument(
        "--api-key", help="An API key for the account. Omit to print the definition."
    )
    parser.add_argument(
        "--rate-guide",
        type=Path,
        help="The DHL rate guide PDF to upload to the knowledge base.",
    )
    parser.add_argument(
        "--document-uuid",
        action="append",
        default=[],
        help="An already-uploaded knowledge base document to attach instead.",
    )
    parser.add_argument("--lead-url", default="https://example.invalid/logicorp/leads")
    parser.add_argument(
        "--transfer-to", default="+910000000000", help="Sales desk number, E.164."
    )
    parser.add_argument("--name", default=AGENT_NAME)
    args = parser.parse_args(argv)

    specs = tools(args.lead_url, args.transfer_to)
    if not args.api_key:
        print(
            json.dumps(
                {"name": args.name, "workflow_definition": definition({})}, indent=2
            )
        )
        return 0

    document_uuids = list(args.document_uuid)
    if args.rate_guide:
        uuid = upload_rate_guide(args.base_url, args.api_key, args.rate_guide)
        document_uuids.append(uuid)
        print(f"document {args.rate_guide.name}: {uuid}", file=sys.stderr)

    tool_ids: dict[str, str] = {}
    for spec in specs:
        created = _post(args.base_url, args.api_key, "/api/v1/tools/", spec)
        tool_ids[spec["name"]] = created["tool_uuid"]
        print(f"tool {spec['name']}: {created['tool_uuid']}", file=sys.stderr)
    workflow = _post(
        args.base_url,
        args.api_key,
        "/api/v1/workflow/create/definition",
        {
            "name": args.name,
            "workflow_definition": definition(tool_ids, document_uuids),
        },
    )
    print(
        f"agent {args.name}: {args.base_url.rstrip('/')}/workflow/{workflow['id']}",
        file=sys.stderr,
    )
    print(
        json.dumps(
            {
                "workflow_id": workflow["id"],
                "tools": tool_ids,
                "documents": document_uuids,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
