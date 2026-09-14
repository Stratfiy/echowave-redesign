"""A bot needs a key; a person types it into a form; the chat never sees it.

The wrong way is one message: "paste your Razorpay key here". The key then
sits in the timeline for everyone in the channel, goes to the model as
context on every later turn, and is exported with the thread. Three leaks
from one paste.

Same two halves as ``decisions``, in one module so they cannot drift:

``ask`` is the tool the bot calls on text and channel runs. It writes a
``needs_secret`` row whose payload carries what is wanted (a name, why, the
credential type and the fields to fill) and nothing else.

``provide`` is what the person's form does. The values go straight into the
organisation's credential store, admin only, and the row is stamped with
the credential's public uuid and the last four characters of the secret --
``payload["provided"]`` -- so the card that asked is the card that says it
was done. The bot is then handed a line naming the credential by uuid, which
is the handle its tools take. The secret itself is written to exactly one
place and read back by nothing here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional
from uuid import uuid4

from loguru import logger

from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind, WebhookCredentialType
from api.services.workflow import agent_timeline

TOOL_NAME = "ask_for_secret"
DESCRIPTION = (
    "Ask a person on the team for an API key, token or password you need to "
    "connect a tool or a service. Never ask for a key in the chat itself: "
    "this opens a secure form, and what they type goes into the account's "
    "credentials, not into this conversation. Say what the key is for in one "
    "line. You will not get the key; you will be told the credential's id "
    "once it is added. Say that you have asked, then end your reply."
)

MAX_NAME_CHARS = 80
MAX_WHY_CHARS = 300
MAX_VALUE_CHARS = 4096

#: What each credential type asks for. The secret field is the one whose
#: last four make the hint on the card; the others are plain settings.
FIELDS: dict[str, list[dict[str, Any]]] = {
    WebhookCredentialType.API_KEY.value: [
        {
            "key": "header_name",
            "label": "Header name",
            "secret": False,
            "default": "X-API-Key",
        },
        {"key": "api_key", "label": "API key", "secret": True},
    ],
    WebhookCredentialType.BEARER_TOKEN.value: [
        {"key": "token", "label": "Token", "secret": True},
    ],
    WebhookCredentialType.BASIC_AUTH.value: [
        {"key": "username", "label": "Username", "secret": False},
        {"key": "password", "label": "Password", "secret": True},
    ],
    WebhookCredentialType.CUSTOM_HEADER.value: [
        {"key": "header_name", "label": "Header name", "secret": False},
        {"key": "header_value", "label": "Header value", "secret": True},
    ],
}


def tool_properties() -> dict[str, Any]:
    return {
        "name": {
            "type": "string",
            "description": "What the key is, as a person would call it: 'Razorpay API key'.",
        },
        "why": {
            "type": "string",
            "description": "One line on what you will do with it.",
        },
        "credential_type": {
            "type": "string",
            "enum": list(FIELDS),
            "description": (
                "'api_key' for a key sent in a header, 'bearer_token' for a "
                "token, 'basic_auth' for a username and password, "
                "'custom_header' for anything else sent as a header."
            ),
        },
    }


def normalise(arguments: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The payload a card can render, or None when there is nothing to ask for."""
    name = str(arguments.get("name") or "").strip()[:MAX_NAME_CHARS]
    if not name:
        return None
    credential_type = str(arguments.get("credential_type") or "").strip()
    if credential_type not in FIELDS:
        return None
    return {
        "name": name,
        "why": str(arguments.get("why") or "").strip()[:MAX_WHY_CHARS],
        "credential_type": credential_type,
        "fields": [dict(field) for field in FIELDS[credential_type]],
    }


async def ask(
    *,
    organization_id: Optional[int],
    workflow_id: Optional[int],
    workflow_run_id: Optional[int],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Record the request. Returns what the model is told."""
    payload = normalise(arguments)
    if payload is None:
        return {
            "status": "not_asked",
            "reason": "A name and a credential type (api_key, bearer_token, basic_auth, custom_header) are needed.",
        }
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.NEEDS_SECRET.value,
        summary=f"Needs {payload['name']}",
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        payload=payload,
    )
    return {
        "status": "asked",
        "note": (
            "A secure form has been shown to the team. You will be told the "
            "credential id once it is added. Say that you have asked, then "
            "end your reply."
        ),
    }


class SecretError(ValueError):
    """The values cannot be stored; the message says why, for the screen."""


def _hint(values: dict[str, str], fields: list[dict[str, Any]]) -> str:
    for field in fields:
        if field.get("secret"):
            value = values.get(field["key"], "")
            return value[-4:] if len(value) >= 4 else ""
    return ""


async def provide(
    *,
    organization_id: int,
    event_id: int,
    values: dict[str, str],
    user_id: int,
) -> dict[str, Any]:
    """Store the values as a credential; stamp the card; hand the bot the id.

    Returns the updated payload, which never contains a value. Raises
    SecretError for the screen to show.
    """
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    if event is None or event.kind != AgentEventKind.NEEDS_SECRET.value:
        raise SecretError("That request is not here to answer.")
    payload = dict(event.payload or {})
    if payload.get("provided"):
        raise SecretError("Already added.")

    credential_type = payload.get("credential_type")
    fields = FIELDS.get(str(credential_type)) or []
    if not fields:
        raise SecretError("That request has no form to fill.")

    data: dict[str, str] = {}
    for field in fields:
        raw = values.get(field["key"])
        value = str(raw).strip() if raw is not None else ""
        if not value and field.get("default"):
            value = str(field["default"])
        if not value:
            raise SecretError(f"{field['label']} is needed.")
        if len(value) > MAX_VALUE_CHARS:
            raise SecretError(f"{field['label']} is too long.")
        data[field["key"]] = value

    name = str(payload.get("name") or "Credential")
    credential = None
    for attempt in (name, f"{name} ({uuid4().hex[:4]})"):
        try:
            credential = await db_client.create_credential(
                organization_id=organization_id,
                user_id=user_id,
                name=attempt,
                description="Added from a bot's secure form",
                credential_type=str(credential_type),
                credential_data=data,
            )
            break
        except Exception as exc:  # noqa: BLE001 - only the name clash is retried
            if "unique_org_credential_name" not in str(exc):
                raise
    if credential is None:
        raise SecretError("A credential with that name already exists.")

    provided = {
        "credential_uuid": credential.credential_uuid,
        "credential_name": credential.name,
        "hint": _hint(data, fields),
        "by": user_id,
        "at": datetime.now(UTC).isoformat(),
    }
    payload["provided"] = provided
    if not await db_client.set_agent_event_payload(
        event_id, organization_id=organization_id, payload=payload
    ):
        raise SecretError("That request is not here to answer.")

    # The bot is told the handle, never the value. The line is what everyone
    # in the channel sees too: enough to know it was done and by which key.
    line = (
        f"{credential.name} has been added as credential "
        f"{credential.credential_uuid}"
        + (f" (ends in {provided['hint']})" if provided["hint"] else "")
        + ". Use that credential id for the tool."
    )
    folder_id = event.folder_id
    if folder_id is None and event.workflow_id is not None:
        folder_id = await agent_timeline._folder_for(event.workflow_id, organization_id)
    await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.HUMAN.value,
        summary=line,
        workflow_id=event.workflow_id,
        folder_id=folder_id,
        payload={"body": line, "author_id": user_id, "secret_event_id": event_id},
        in_channel=folder_id is not None,
    )
    if event.workflow_id is not None:
        try:
            from api.tasks.arq import enqueue_job
            from api.tasks.function_names import FunctionNames

            await enqueue_job(
                FunctionNames.ANSWER_CHANNEL_MESSAGE,
                event.workflow_id,
                folder_id,
                line,
            )
        except Exception as exc:  # noqa: BLE001 - the credential is stored regardless
            logger.error(
                "Secret for event {} stored but workflow {} could not be asked to continue: {}",
                event_id,
                event.workflow_id,
                exc,
            )
    return payload
