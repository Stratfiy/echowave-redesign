"""Instagram messages, answered by an agent.

A business connects its Instagram professional account with Instagram's own
sign-in ("Instagram API with Instagram Login" -- no Facebook Page needed),
picks the agent that answers, and from then on a customer who messages the
account gets that agent's reply, in the same thread on Instagram.

**Connecting.** ``authorize_url`` sends the owner to Instagram with a signed
``state`` that names the workspace and the person; nothing else in the
round trip is trusted. ``complete_login`` swaps the code for a short-lived
token, the short-lived token for a long-lived one (sixty days), reads the
account's id and username, subscribes the app to its messages, and stores
the token sealed with the platform key. A token close to expiring is
refreshed the next time it is used.

**A message.** Meta POSTs it to ``/api/v1/public/instagram/webhook``,
signed with the app secret (``X-Hub-Signature-256``); a bad signature is a
403 before the body is read. ``parse`` keeps customers' text messages --
not our own echoes, not reactions -- and each is handled in the background
(``handle``): the account is found by the id Instagram sent it to, the
conversation is the customer's run for the day (one text-chat run per
customer per day, so the agent remembers what was said), the agent takes
its turn, the turn is billed like any chat reply, and the reply goes back.

**What it never does.** It never writes first: Instagram allows a business
to answer within 24 hours of the customer's last message, and every reply
here is an answer. A message no agent is set to answer is left for a
person, and the owner is told once a day on their thread rather than the
customer getting silence dressed as a reply. An account that cannot pay
does not answer; the customer is not told why.

**Twice is once.** Message ids dedupe for a day in Redis.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from loguru import logger

from api import constants
from api.db import db_client
from api.services import features

FLAG = "instagram_channel"
TOOL_NAME = "connect_instagram"
EVENT_KIND = "instagram_connect_offered"  # AgentEventKind.INSTAGRAM_CONNECT_OFFERED

SCOPES = "instagram_business_basic,instagram_business_manage_messages"
AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH = "https://graph.instagram.com"

#: How long a sign-in link stays good.
STATE_TTL_SECONDS = 30 * 60
#: Refresh a long-lived token when it has less than this left.
REFRESH_BEFORE = timedelta(days=7)
#: Instagram's limit on one text message, in characters.
MAX_MESSAGE_CHARS = 1000
#: A customer's conversation is one run for this long after their last word.
CONVERSATION_TTL_SECONDS = 24 * 60 * 60
DEDUPE_TTL_SECONDS = 24 * 60 * 60
#: How often an owner is told that messages are waiting with no agent.
NO_AGENT_NOTICE_SECONDS = 24 * 60 * 60
TIMEOUT_SECS = 20.0
#: Answers one customer can get in an hour (see ``_within_rate``).
PER_SENDER_PER_HOUR = 30

_SEEN = "instagram:seen:"
_CONVERSATION = "instagram:conv:"
_NO_AGENT = "instagram:no_agent:"


class InstagramError(ValueError):
    """What went wrong, in words a person (or the model) can act on."""


def is_configured() -> bool:
    return bool(
        constants.INSTAGRAM_APP_ID.strip() and constants.INSTAGRAM_APP_SECRET.strip()
    )


def enabled(organization_id: int | None = None) -> bool:
    """Offered at all: switched on. Whether it can connect is ``is_configured``."""
    return features.is_on(FLAG, organization_id)


def redirect_uri() -> str:
    return (
        f"{constants.BACKEND_API_ENDPOINT.rstrip('/')}/api/v1/public/instagram/callback"
    )


def _secret() -> bytes:
    return constants.INSTAGRAM_APP_SECRET.strip().encode()


# --- the sign-in ------------------------------------------------------------


def make_state(organization_id: int, user_id: int, *, now: float | None = None) -> str:
    body = json.dumps(
        {
            "o": int(organization_id),
            "u": int(user_id),
            "e": int((now or time.time()) + STATE_TTL_SECONDS),
        },
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(body).decode().rstrip("=")
    signature = hmac.new(_secret(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def read_state(state: str, *, now: float | None = None) -> tuple[int, int]:
    """(organization id, user id) from a state this module signed."""
    try:
        encoded, signature = (state or "").split(".", 1)
    except ValueError as exc:
        raise InstagramError("That sign-in link is not one of ours.") from exc
    expected = hmac.new(_secret(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise InstagramError("That sign-in link is not one of ours.")
    padded = encoded + "=" * (-len(encoded) % 4)
    data = json.loads(base64.urlsafe_b64decode(padded))
    if int(data["e"]) < (now or time.time()):
        raise InstagramError("That sign-in link has expired; start again from Chat.")
    return int(data["o"]), int(data["u"])


def authorize_url(organization_id: int, user_id: int) -> str:
    if not is_configured():
        raise InstagramError("Instagram is not set up on this deployment yet.")
    query = urlencode(
        {
            "client_id": constants.INSTAGRAM_APP_ID.strip(),
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": SCOPES,
            "state": make_state(organization_id, user_id),
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _client(client: httpx.AsyncClient | None) -> httpx.AsyncClient:
    from api.services.workflow import web_tools

    if client is not None:
        return web_tools._Borrowed(client)  # type: ignore[return-value]
    return httpx.AsyncClient(timeout=TIMEOUT_SECS)


def _said(response: httpx.Response) -> str:
    """Meta's own words for an error, short."""
    try:
        error = response.json().get("error") or {}
        if isinstance(error, dict):
            return str(error.get("message") or error.get("error_user_msg") or "")[:200]
        return str(error)[:200]
    except Exception:  # noqa: BLE001
        return response.text[:200]


def _seal(token: str) -> str:
    from api.services.configuration import organization_credentials as creds

    try:
        return creds.seal(token)
    except creds.OrganizationCredentialError as exc:
        raise InstagramError(
            "Instagram cannot be connected until the platform's credential "
            "key is set (PLATFORM_CREDENTIAL_SECRET)."
        ) from exc


async def complete_login(
    *, code: str, state: str, client: httpx.AsyncClient | None = None
) -> Any:
    """Finish the sign-in: store the account and return its row."""
    organization_id, user_id = read_state(state)
    graph = f"{GRAPH}/{constants.INSTAGRAM_GRAPH_VERSION}"
    async with _client(client) as http:
        short = await http.post(
            TOKEN_URL,
            data={
                "client_id": constants.INSTAGRAM_APP_ID.strip(),
                "client_secret": constants.INSTAGRAM_APP_SECRET.strip(),
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri(),
                "code": code,
            },
        )
        if short.status_code >= 400:
            raise InstagramError(
                f"Instagram did not accept the sign-in: {_said(short)}"
            )
        short_token = str(short.json().get("access_token") or "")
        long = await http.get(
            f"{GRAPH}/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": constants.INSTAGRAM_APP_SECRET.strip(),
                "access_token": short_token,
            },
        )
        if long.status_code >= 400:
            raise InstagramError(
                f"Instagram did not give a lasting token: {_said(long)}"
            )
        token = str(long.json().get("access_token") or "")
        expires_in = int(long.json().get("expires_in") or 0)
        me = await http.get(
            f"{graph}/me",
            params={"fields": "user_id,username", "access_token": token},
        )
        if me.status_code >= 400:
            raise InstagramError(f"Instagram did not say which account: {_said(me)}")
        profile = me.json()
        ig_user_id = str(profile.get("user_id") or profile.get("id") or "")
        if not ig_user_id:
            raise InstagramError("Instagram did not say which account this is.")
        subscribed = await http.post(
            f"{graph}/me/subscribed_apps",
            params={"subscribed_fields": "messages", "access_token": token},
        )
        if subscribed.status_code >= 400:
            raise InstagramError(
                "The account was found but its messages could not be turned "
                f"on: {_said(subscribed)}"
            )
    return await db_client.save_instagram_account(
        organization_id=organization_id,
        ig_user_id=ig_user_id,
        username=str(profile.get("username") or ""),
        encrypted_token=_seal(token),
        token_expires_at=(
            datetime.now(UTC) + timedelta(seconds=expires_in) if expires_in else None
        ),
        connected_by=user_id,
    )


async def token_for(account: Any, *, client: httpx.AsyncClient | None = None) -> str:
    """The account's token, refreshed first when it is close to expiring."""
    from api.services.configuration import organization_credentials as creds

    token = creds.unseal(account.encrypted_token)
    expires = account.token_expires_at
    if expires is None or expires - datetime.now(UTC) > REFRESH_BEFORE:
        return token
    try:
        async with _client(client) as http:
            response = await http.get(
                f"{GRAPH}/refresh_access_token",
                params={"grant_type": "ig_refresh_token", "access_token": token},
            )
        if response.status_code < 400:
            body = response.json()
            fresh = str(body.get("access_token") or "")
            if fresh:
                await db_client.update_instagram_token(
                    account_id=account.id,
                    encrypted_token=_seal(fresh),
                    token_expires_at=datetime.now(UTC)
                    + timedelta(seconds=int(body.get("expires_in") or 0)),
                )
                return fresh
        logger.warning(
            "Instagram token for account {} was not refreshed: {}",
            account.id,
            _said(response),
        )
    except Exception as exc:  # noqa: BLE001 - the old token still works today
        logger.warning("Instagram token refresh failed for {}: {}", account.id, exc)
    return token


def chunks(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """``text`` in pieces Instagram accepts, split at a paragraph, a line or
    a space where it can be."""
    text = (text or "").strip()
    out: list[str] = []
    while len(text) > limit:
        cut = max(text.rfind("\n\n", 0, limit), text.rfind("\n", 0, limit))
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        out.append(text)
    return out


async def send_text(
    account: Any,
    *,
    to: str,
    text: str,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Send ``text`` to a customer; returns the message ids Instagram gave."""
    token = await token_for(account, client=client)
    sent: list[str] = []
    async with _client(client) as http:
        for piece in chunks(text):
            response = await http.post(
                f"{GRAPH}/{constants.INSTAGRAM_GRAPH_VERSION}/me/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"recipient": {"id": to}, "message": {"text": piece}},
            )
            if response.status_code >= 400:
                raise InstagramError(f"Instagram refused the reply: {_said(response)}")
            sent.append(str(response.json().get("message_id") or ""))
    return sent


# --- the webhook ------------------------------------------------------------


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    if not is_configured() or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(_secret(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def challenge_for(
    mode: str | None, verify_token: str | None, challenge: str | None
) -> str | None:
    wanted = constants.INSTAGRAM_WEBHOOK_VERIFY_TOKEN.strip()
    if mode == "subscribe" and wanted and verify_token == wanted and challenge:
        return challenge
    return None


@dataclass(frozen=True)
class Incoming:
    """One customer's text message to a connected account."""

    account_ig_id: str
    sender_id: str
    message_id: str
    text: str


def parse(payload: Any) -> list[Incoming]:
    """Customers' text messages in a webhook body. Echoes of our own
    replies, reactions, read receipts and attachments are not messages to
    answer and are left out."""
    if not isinstance(payload, dict) or payload.get("object") != "instagram":
        return []
    out: list[Incoming] = []
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for event in entry.get("messaging") or []:
            if not isinstance(event, dict):
                continue
            message = event.get("message") or {}
            if not isinstance(message, dict) or message.get("is_echo"):
                continue
            text = str(message.get("text") or "").strip()
            sender = str((event.get("sender") or {}).get("id") or "")
            account = str(
                (event.get("recipient") or {}).get("id") or entry.get("id") or ""
            )
            mid = str(message.get("mid") or "")
            if not text or not sender or not account or not mid or sender == account:
                continue
            out.append(Incoming(account, sender, mid, text[:4000]))
    return out


async def _redis():
    import redis.asyncio as aioredis

    return aioredis.from_url(constants.REDIS_URL, decode_responses=True)


async def first_time(key: str, ttl: int) -> bool:
    """True the first time ``key`` is claimed within ``ttl``. Redis down
    reads as first time: answering twice is better than never."""
    try:
        client = await _redis()
        try:
            return bool(await client.set(key, "1", nx=True, ex=ttl))
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Instagram could not claim {}: {}", key, exc)
        return True


async def _remembered_run(key: str) -> int | None:
    try:
        client = await _redis()
        try:
            value = await client.get(key)
        finally:
            await client.aclose()
        return int(value) if value else None
    except Exception:  # noqa: BLE001
        return None


async def _remember_run(key: str, run_id: int) -> None:
    try:
        client = await _redis()
        try:
            await client.set(key, str(run_id), ex=CONVERSATION_TTL_SECONDS)
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Instagram could not remember run {}: {}", run_id, exc)


async def _conversation(account: Any, sender: str, workflow: Any) -> int | None:
    """The customer's run for today: the one remembered, or a new one.
    None when the account cannot start one (no credit)."""
    from api.enums import WorkflowRunMode
    from api.services.quota_service import authorize_workflow_run_start
    from api.services.workflow import one_shot_run
    from api.services.workflow.text_chat_runner import default_text_chat_checkpoint
    from api.services.workflow.text_chat_session_service import (
        default_text_chat_session_data,
    )

    key = f"{_CONVERSATION}{account.id}:{workflow.id}:{sender}"
    remembered = await _remembered_run(key)
    if remembered:
        run = await db_client.get_workflow_run(
            remembered, organization_id=account.organization_id
        )
        if run is not None and not run.is_completed:
            await _remember_run(key, remembered)
            return remembered
    run = await db_client.create_workflow_run(
        name=f"Instagram - {datetime.now(UTC).date().isoformat()}",
        workflow_id=workflow.id,
        mode=WorkflowRunMode.TEXTCHAT.value,
        user_id=account.connected_by or workflow.user_id,
        organization_id=account.organization_id,
        initial_context={
            "provider": "instagram",
            "channel": "instagram",
            "instagram_user_id": sender,
        },
    )
    quota = await authorize_workflow_run_start(
        workflow_id=workflow.id,
        organization_id=account.organization_id,
        workflow_run_id=run.id,
    )
    if not quota.has_quota:
        logger.warning(
            "Instagram message for org {} not answered: {}",
            account.organization_id,
            quota.error_code or quota.error_message,
        )
        await one_shot_run.close(run.id)
        return None
    await db_client.ensure_workflow_run_text_session(
        run.id,
        session_data=default_text_chat_session_data(),
        checkpoint=default_text_chat_checkpoint(),
    )
    await _remember_run(key, run.id)
    return run.id


async def _within_rate(account_id: int, sender: str) -> bool:
    """One customer gets at most ``PER_SENDER_PER_HOUR`` answers an hour:
    every reply is paid by the business, and a person (or a script) sending
    a message a second should not be able to spend its balance."""
    from api.services.rate_limit import rate_limiter

    try:
        allowed, _ = await rate_limiter.check(
            bucket="instagram_sender",
            identity=f"{account_id}:{sender}",
            limit=PER_SENDER_PER_HOUR,
            window_secs=60 * 60,
        )
        return bool(allowed)
    except Exception as exc:  # noqa: BLE001 - unknown is allowed, as elsewhere
        logger.warning("Instagram rate check failed: {}", exc)
        return True


async def _tell_owner_no_agent(account: Any, incoming: Incoming) -> None:
    if not await first_time(f"{_NO_AGENT}{account.id}", NO_AGENT_NOTICE_SECONDS):
        return
    from api.enums import AgentEventActor, AgentEventKind
    from api.services.workflow import agent_timeline

    await agent_timeline.record(
        organization_id=account.organization_id,
        kind=AgentEventKind.MESSAGE.value,
        actor=AgentEventActor.AGENT.value,
        summary=(
            f"Instagram messages to @{account.username or 'your account'} are "
            "waiting and no agent is set to answer them. Ask me to choose one."
        ),
        payload={"channel": "instagram", "first_message": incoming.text[:200]},
        in_channel=False,
    )


async def handle(incoming: Incoming, *, client: httpx.AsyncClient | None = None) -> str:
    """Answer one message. Returns what happened, in a word. Never raises."""
    try:
        return await _handle(incoming, client=client)
    except Exception as exc:  # noqa: BLE001 - one message, not the worker
        logger.exception("Instagram message {} failed: {}", incoming.message_id, exc)
        return "error"


async def _handle(incoming: Incoming, *, client: httpx.AsyncClient | None) -> str:
    from api.services.billing import events as billing_events
    from api.services.billing import reservations
    from api.services.workflow.text_chat_session_service import (
        TextChatSessionRevisionConflictError,
        append_text_chat_user_message,
        execute_pending_text_chat_turn,
    )

    account = await db_client.get_instagram_account_by_ig_user(incoming.account_ig_id)
    if account is None:
        return "unknown_account"
    if not enabled(account.organization_id):
        return "switched_off"
    if not await first_time(f"{_SEEN}{incoming.message_id}", DEDUPE_TTL_SECONDS):
        return "duplicate"
    if not await _within_rate(account.id, incoming.sender_id):
        return "rate_limited"
    workflow = (
        await db_client.get_workflow(
            account.workflow_id, organization_id=account.organization_id
        )
        if account.workflow_id
        else None
    )
    if workflow is None:
        await _tell_owner_no_agent(account, incoming)
        return "no_agent"
    run_id = await _conversation(account, incoming.sender_id, workflow)
    if run_id is None:
        return "no_credit"
    async with db_client.async_session() as session:
        if not await reservations.has_credit(
            session, organization_id=account.organization_id, workflow_run_id=run_id
        ):
            return "no_credit"

    text_session = None
    for attempt in range(2):
        text_session = await db_client.get_workflow_run_text_session(
            run_id, organization_id=account.organization_id
        )
        try:
            text_session = await append_text_chat_user_message(
                run_id=run_id,
                text_session=text_session,
                user_text=incoming.text,
                expected_revision=text_session.revision,
            )
            break
        except TextChatSessionRevisionConflictError:
            if attempt:
                raise
    text_session = await execute_pending_text_chat_turn(
        workflow_id=workflow.id, run_id=run_id, text_session=text_session
    )
    last_turn = billing_events.last_turn_of(text_session)
    reply = str(
        ((last_turn or {}).get("assistant_message") or {}).get("text") or ""
    ).strip()
    await billing_events.charge_in_own_session(
        organization_id=account.organization_id,
        event=billing_events.event_for_turn(last_turn),
        ref_id=f"{run_id}:{(last_turn or {}).get('id') or incoming.message_id}",
        note="Instagram reply",
        workflow_id=workflow.id,
        usage=billing_events.turn_usages(text_session, last_only=True),
    )
    if not reply:
        return "no_reply"
    await send_text(account, to=incoming.sender_id, text=reply, client=client)
    return "answered"


# --- for Decibyl --------------------------------------------------------------


def tool_schema() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Instagram messages answered by an agent. Without an agent: puts "
            "a 'Connect Instagram' button on the thread (or says the account "
            "connected and which agent answers). With an agent: proposes a "
            "card that makes that agent answer the account's messages."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "The agent to answer, by @handle or name.",
                },
                "why": {"type": "string", "description": "One line, for the card."},
            },
        },
    }


async def for_thread(organization_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
    """The tool call: a connect button, the state of things, or a card."""
    from api.enums import AgentEventActor
    from api.services.workflow import actions, agent_timeline

    if not is_configured():
        return {
            "status": "unavailable",
            "reason": (
                "Instagram is not set up on this deployment yet (the Meta app "
                "is not configured). Say so plainly; do not offer a button."
            ),
        }
    account = await db_client.get_instagram_account(organization_id)
    agent = str(arguments.get("agent") or "").strip()
    if account is None:
        await agent_timeline.record(
            organization_id=organization_id,
            kind=EVENT_KIND,
            actor=AgentEventActor.AGENT.value,
            summary="Connect Instagram",
            payload={"why": str(arguments.get("why") or "")[:200], "agent": agent},
            in_channel=False,
        )
        return {
            "status": "offered",
            "note": (
                "A 'Connect Instagram' button is on the thread. Say signing "
                "in is theirs to do on Instagram's screen (a professional "
                "account), then end your reply. It is NOT connected yet; "
                "once it is, propose the agent with this tool again."
            ),
        }
    if not agent:
        current = (
            await db_client.get_workflow(
                account.workflow_id, organization_id=organization_id
            )
            if account.workflow_id
            else None
        )
        return {
            "status": "connected",
            "username": account.username,
            "answered_by": current.name if current else None,
            "note": (
                "Connected. "
                + (
                    f"{current.name} answers its messages."
                    if current
                    else "No agent answers yet: ask which one should."
                )
            ),
        }
    return await actions.propose(
        organization_id=organization_id,
        workflow_id=None,
        workflow_run_id=None,
        arguments={**arguments, "action": actions.ANSWER_INSTAGRAM},
        in_channel=False,
    )


async def resolve_card(*, organization_id: int, arguments: dict[str, Any]) -> dict:
    from api.services.workflow import office

    account = await db_client.get_instagram_account(organization_id)
    if account is None:
        raise InstagramError("Instagram is not connected yet; offer the button first.")
    roster = list(
        await db_client.get_all_workflows_for_listing(organization_id=organization_id)
    )
    wanted = str(arguments.get("agent") or "")
    agent = office._match_bot(wanted, roster)
    if agent is None:
        raise InstagramError(f"No agent called {wanted!r} here; use its exact @handle.")
    return {
        "args": {"account_id": int(account.id), "workflow_id": int(agent.id)},
        "label": f"{agent.name} answers Instagram messages",
        "preview": "\n".join(
            [
                f"Account: @{account.username}",
                f"Answered by: {agent.name}",
                "When: as each message arrives; Instagram allows replies "
                "within 24 hours of the customer's last message",
                "Never writes first, and each reply is billed like any chat reply",
            ]
        ),
        "effect": f"{agent.name} starts answering @{account.username}'s messages.",
        "reversible": True,
    }


async def execute_card(*, organization_id: int, payload: dict[str, Any]) -> str:
    args = payload.get("args") or {}
    workflow = await db_client.get_workflow(
        int(args["workflow_id"]), organization_id=organization_id
    )
    if workflow is None:
        raise InstagramError("That agent is not here any more.")
    if not await db_client.set_instagram_agent(
        organization_id=organization_id,
        account_id=int(args["account_id"]),
        workflow_id=workflow.id,
    ):
        raise InstagramError("That Instagram account is not connected any more.")
    return f"{workflow.name} now answers Instagram messages."


async def undo_card(*, organization_id: int, payload: dict[str, Any]) -> None:
    args = payload.get("args") or {}
    await db_client.set_instagram_agent(
        organization_id=organization_id,
        account_id=int(args["account_id"]),
        workflow_id=None,
    )
