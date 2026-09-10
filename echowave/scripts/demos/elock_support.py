"""Build the Elock support squad on an account, from the client's workflow document.

The Kritilabs brief describes an inbound support line for an e-lock on a
tanker truck: the caller says the lock is not opening (or locking, or that
they have no invoice), the agent verifies them by TT number and 10-digit
invoice number, walks them through the controller (6# refresh, GPS light,
3# for an OTP, 2# + OTP + # to open, 1# to lock), checks OTP status with the
backend, asks consent before triggering a manual OTP, and hands to a person
when the lock still will not open.

Run once with an API key for the account the demo runs on::

    python -m scripts.demos.elock_support \\
        --base-url https://app.decibyl.ai \\
        --api-key dk_... \\
        --validate-url https://client.example/api/validate \\
        --otp-status-url https://client.example/api/otp/status \\
        --manual-otp-url https://client.example/api/otp/manual \\
        --transfer-to +919999999999

Without ``--api-key`` it prints the workflow definition and exits, which is
also what the test uses to check every node passes the engine's validator.

Everything it makes is ordinary: three HTTP tools, one transfer tool and one
agent, all editable afterwards on the canvas or the prompts view. The
backend URLs are the client's; until they exist, point them at a mock and
the conversation still runs end to end.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

AGENT_NAME = "Elock support"

RULES = (
    "These rules apply on every turn.\n"
    "- The caller is usually a truck driver or a depot operator standing at a "
    "controller, often on a noisy line. Speak in short, plain sentences. One "
    "instruction per turn, then wait for them to confirm they have done it.\n"
    "- Follow the caller's language. If they speak Hindi, Tamil, Telugu, "
    "Kannada or Marathi, switch and stay there.\n"
    "- Read every number back digit by digit before using it: the TT number, "
    "the invoice number, the OTP.\n"
    "- Never trigger an OTP, a manual OTP or a secondary OTP without the "
    "caller saying yes first.\n"
    "- Never guess whether a lock opened. Ask, and believe the caller.\n"
    "- If the caller asks for a person, or the same step fails twice, hand "
    "the call to a support agent with everything you have learned. Do not "
    "keep trying.\n"
    "- Keypad commands are always said as digit then hash: 'press six, then "
    "hash'."
)

GREETING = (
    "Hello, welcome to Elock support. Is the lock not opening, not locking, "
    "or is it something else?"
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


def tools(
    validate_url: str, otp_status_url: str, manual_otp_url: str, transfer_to: str
) -> list[dict]:
    """The four things the agent can do that a prompt cannot."""
    tt = {
        "name": "tt_number",
        "type": "string",
        "description": "The TT (tank truck) number the caller gave, exactly as read back to them.",
        "required": True,
    }
    invoice = {
        "name": "invoice_number",
        "type": "string",
        "description": "The 10-digit invoice number the caller gave, digits only.",
        "required": True,
    }
    return [
        _tool(
            "validate_customer",
            "Check the TT number and invoice number against the Elock backend. "
            "Call it once both have been read back and confirmed.",
            method="POST",
            url=validate_url,
            parameters=[tt, invoice],
        ),
        _tool(
            "otp_status",
            "Ask the backend whether an OTP has already been triggered for this "
            "TT and invoice, and whether it was delivered. Call it after the "
            "caller has pressed 3#.",
            method="POST",
            url=otp_status_url,
            parameters=[tt, invoice],
        ),
        _tool(
            "trigger_manual_otp",
            "Generate and send a manual OTP for this TT and invoice. Only after "
            "the caller has said yes to a manual OTP.",
            method="POST",
            url=manual_otp_url,
            parameters=[
                tt,
                invoice,
                {
                    "name": "reason",
                    "type": "string",
                    "description": "One line on why the normal OTP did not arrive, in the caller's words.",
                    "required": False,
                },
            ],
        ),
        {
            "name": "transfer_to_support",
            "description": "Hand the call to a human support agent, with what has happened so far.",
            "category": "transfer_call",
            "definition": {
                "schema_version": 1,
                "type": "transfer_call",
                "config": {
                    "destination_source": "static",
                    "destination": transfer_to,
                    "messageType": "custom",
                    "customMessage": "I am connecting you to a support agent now. Please stay on the line.",
                    "timeout": 30,
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
    """The conversation, as the canvas and the runtime read it.

    ``tool_ids`` maps the tool names above to the uuids the account gave
    them. Empty when printing a dry run: the nodes are still valid, the
    agent just has no hands until the tools are attached.
    """
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
            "Find out what the caller needs. It is one of: the lock will not "
            "open, the lock will not lock, they want to open or lock it as a "
            "normal command, or they have no invoice for the trip. Remember "
            "whether the job is OPEN or LOCK for the rest of the call. Do not "
            "ask for any numbers yet.",
            0,
            is_start=True,
            greeting=GREETING,
            greeting_type="text",
            extraction_enabled=True,
            extraction_prompt="Capture the fields below from the conversation.",
            extraction_variables=[
                {
                    "name": "intent",
                    "type": "string",
                    "description": "One of: open, lock, not_opening, not_locking, invoice_missing, other",
                },
            ],
        ),
        _node(
            "agent-verify",
            "agentNode",
            "Verify TT and invoice",
            "Ask for the TT number, read it back digit by digit, and get a "
            "yes. Then ask for the 10-digit invoice number, read it back, and "
            "get a yes. Then call validate_customer. If it fails, say so "
            "plainly and ask them to check both numbers once more; try one "
            "more time. If it fails again, tell them you will connect them to "
            "a support agent.",
            step,
            tool_uuids=ids("validate_customer"),
            extraction_enabled=True,
            extraction_prompt="Capture the fields below from the conversation.",
            extraction_variables=[
                {
                    "name": "tt_number",
                    "type": "string",
                    "description": "The TT number as confirmed by the caller",
                },
                {
                    "name": "invoice_number",
                    "type": "string",
                    "description": "The 10-digit invoice number as confirmed",
                },
                {
                    "name": "validation_result",
                    "type": "string",
                    "description": "valid, invalid or error",
                },
            ],
        ),
        _node(
            "agent-refresh",
            "agentNode",
            "Refresh the device",
            "Ask the caller to press six, then hash, on the controller to "
            "refresh the device. Wait for them to say it is done. Then ask "
            "whether the GPS light is blinking. If it is blinking, move on. If "
            "it is not, ask them to wait thirty seconds and check again; if it "
            "is still not blinking, tell them a support agent needs to look "
            "and hand over.",
            step * 2,
            extraction_enabled=True,
            extraction_prompt="Capture the fields below from the conversation.",
            extraction_variables=[
                {
                    "name": "gps_blinking",
                    "type": "string",
                    "description": "yes or no, as the caller reports",
                },
            ],
        ),
        _node(
            "agent-lock",
            "agentNode",
            "Lock the lock",
            "The job is LOCK. Ask the caller to press one, then hash. Wait. "
            "Ask whether the lock is locked now. If yes, move to close. If not, "
            "ask them to try once more; if it still does not lock, hand over "
            "to a support agent.",
            step * 3,
            x=420,
            extraction_enabled=True,
            extraction_prompt="Capture the fields below from the conversation.",
            extraction_variables=[
                {
                    "name": "lock_result",
                    "type": "string",
                    "description": "locked, not_locked",
                },
            ],
        ),
        _node(
            "agent-otp",
            "agentNode",
            "Request an OTP",
            "The job is OPEN. Ask the caller to press three, then hash, to "
            "request an OTP. Wait for them to confirm. Then call otp_status. "
            "Then ask whether the OTP has arrived on their phone. If it has, "
            "move on to opening. If it has not after a minute, offer a manual "
            "OTP.",
            step * 3,
            tool_uuids=ids("otp_status"),
            extraction_enabled=True,
            extraction_prompt="Capture the fields below from the conversation.",
            extraction_variables=[
                {
                    "name": "otp_triggered",
                    "type": "string",
                    "description": "What the backend said: triggered, not_triggered, unknown",
                },
                {
                    "name": "otp_received",
                    "type": "string",
                    "description": "yes or no, as the caller reports",
                },
            ],
        ),
        _node(
            "agent-manual-otp",
            "agentNode",
            "Manual OTP with consent",
            "Explain that a manual OTP can be sent to the registered number, "
            "and ask clearly: 'Shall I send a manual OTP now?' Only if they say "
            "yes, call trigger_manual_otp. Tell them it is on its way and to "
            "say when it arrives. If they say no, ask what they would prefer, "
            "and offer a support agent.",
            step * 4,
            tool_uuids=ids("trigger_manual_otp"),
            extraction_enabled=True,
            extraction_prompt="Capture the fields below from the conversation.",
            extraction_variables=[
                {
                    "name": "manual_otp_consent",
                    "type": "string",
                    "description": "yes or no",
                },
                {
                    "name": "manual_otp_sent",
                    "type": "string",
                    "description": "yes, no, or error",
                },
            ],
        ),
        _node(
            "agent-unlock",
            "agentNode",
            "Open the lock",
            "Guide the open command one step at a time: press two, then hash; "
            "enter the OTP digits; then press hash. Wait after each. Then ask "
            "whether the lock has opened. If yes, move to close. If not, ask "
            "them to try the OTP once more carefully; if it still does not "
            "open, hand over to a support agent.",
            step * 5,
            extraction_enabled=True,
            extraction_prompt="Capture the fields below from the conversation.",
            extraction_variables=[
                {
                    "name": "unlock_result",
                    "type": "string",
                    "description": "opened, not_opened",
                },
            ],
        ),
        _node(
            "agent-escalate",
            "agentNode",
            "Hand to a person",
            "Tell the caller you are connecting them to a support agent who "
            "can see everything from this call. Then call transfer_to_support.",
            step * 6,
            x=420,
            tool_uuids=ids("transfer_to_support"),
        ),
        _node(
            "end-invoice",
            "endCall",
            "No invoice for this trip",
            "Tell the caller that no trip is available for this TT and invoice, "
            "so the lock cannot be opened over the phone. Ask them to contact "
            "their Sales Officer with the invoice details for manual trip "
            "creation by mail to helpdesk@kritilabs.com, and to make sure the "
            "registered mobile number is updated in SDMS so the invoice comes "
            "automatically next time. Close politely.",
            step * 4,
            x=-420,
            is_end=True,
        ),
        _node(
            "end-resolved",
            "endCall",
            "Resolved",
            "Confirm the outcome in one line — the lock is open, or locked — "
            "thank them for calling Elock support, and end the call.",
            step * 7,
            is_end=True,
        ),
        _node(
            "end-after-transfer",
            "endCall",
            "After transfer",
            "If the transfer connected, say nothing more. If it did not, "
            "apologise, say a support agent will call them back on this "
            "number, and end the call.",
            step * 7,
            x=420,
            is_end=True,
        ),
    ]

    edges = [
        _edge(
            "start-1",
            "agent-verify",
            "needs the lock",
            "The caller wants to open or lock the lock, or reports it is not opening or locking",
        ),
        _edge(
            "start-1",
            "agent-verify",
            "no invoice",
            "The caller says they have not received an invoice or there is no invoice for the trip",
        ),
        _edge(
            "agent-verify",
            "agent-refresh",
            "verified",
            "validate_customer confirmed the TT and invoice, and the caller's job is to open, lock or fix the lock",
        ),
        _edge(
            "agent-verify",
            "end-invoice",
            "no trip",
            "validate_customer found no trip for this TT and invoice, or the caller's issue is a missing invoice",
        ),
        _edge(
            "agent-verify",
            "agent-escalate",
            "cannot verify",
            "validate_customer failed twice, or the caller asked for a person",
        ),
        _edge(
            "agent-refresh",
            "agent-otp",
            "GPS blinking, open",
            "The GPS light is blinking and the job is OPEN or the lock is not opening",
        ),
        _edge(
            "agent-refresh",
            "agent-lock",
            "GPS blinking, lock",
            "The GPS light is blinking and the job is LOCK or the lock is not locking",
        ),
        _edge(
            "agent-refresh",
            "agent-escalate",
            "no GPS",
            "The GPS light is still not blinking after a second check, or the caller asked for a person",
        ),
        _edge(
            "agent-lock",
            "end-resolved",
            "locked",
            "The caller confirms the lock is locked",
        ),
        _edge(
            "agent-lock",
            "agent-escalate",
            "will not lock",
            "The lock still will not lock after a second try",
        ),
        _edge(
            "agent-otp",
            "agent-unlock",
            "OTP received",
            "The caller confirms the OTP arrived",
        ),
        _edge(
            "agent-otp",
            "agent-manual-otp",
            "OTP not received",
            "The OTP has not arrived, or the backend says none was triggered",
        ),
        _edge(
            "agent-manual-otp",
            "agent-unlock",
            "manual OTP received",
            "The caller consented, the manual OTP was sent, and the caller confirms it arrived",
        ),
        _edge(
            "agent-manual-otp",
            "agent-escalate",
            "no consent or no OTP",
            "The caller declined a manual OTP, it could not be sent, or it never arrived",
        ),
        _edge(
            "agent-unlock",
            "end-resolved",
            "opened",
            "The caller confirms the lock has opened",
        ),
        _edge(
            "agent-unlock",
            "agent-escalate",
            "will not open",
            "The lock still will not open after a second try, or the caller asked for a person",
        ),
        _edge(
            "agent-escalate",
            "end-after-transfer",
            "transferred",
            "The transfer was attempted",
        ),
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "viewport": {"x": 400, "y": 60, "zoom": 0.6},
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
    parser.add_argument(
        "--validate-url", default="https://example.invalid/elock/validate"
    )
    parser.add_argument(
        "--otp-status-url", default="https://example.invalid/elock/otp/status"
    )
    parser.add_argument(
        "--manual-otp-url", default="https://example.invalid/elock/otp/manual"
    )
    parser.add_argument(
        "--transfer-to", default="+910000000000", help="Support desk number, E.164."
    )
    parser.add_argument("--name", default=AGENT_NAME)
    args = parser.parse_args(argv)

    specs = tools(
        args.validate_url, args.otp_status_url, args.manual_otp_url, args.transfer_to
    )

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
