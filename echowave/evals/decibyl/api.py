"""The HTTP side: Decibyl exactly as a person reaches it.

Standard library only, like ``scripts/staging_check.py``, so the decibyl
suite runs on any runner without installing the API's dependencies.

``Transport`` is the seam: ``HttpTransport`` talks to a running instance,
and the unit tests hand in an in-memory one. Nothing above this module knows
which it has.

Credentials are read from the environment by the caller and passed in; this
module never prints, logs or stores a password or a token.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

#: What Decibyl writes when the turn never reached a model. An agent message
#: like any other, so a check that only looks for "a reply" passes on it.
FAILURE_REPLY = "I could not think that through"

#: Card kinds Decibyl's tools can put on the thread (decibyl.thread_filter).
CARD_KINDS = frozenset({"action_proposed", "edit_proposed"})
CHIP_KINDS = frozenset({"connector_offered", "reach_connect_offered"})


class Transport(Protocol):
    def request(
        self, method: str, path: str, *, token: str | None = None, body: Any = None
    ) -> tuple[int, Any]: ...


@dataclass
class HttpTransport:
    """JSON over HTTP to ``{base}/api/v1``."""

    base: str
    timeout: float = 30.0

    def request(
        self, method: str, path: str, *, token: str | None = None, body: Any = None
    ) -> tuple[int, Any]:
        url = f"{self.base.rstrip('/')}/api/v1{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("content-type", "application/json")
        if token:
            req.add_header("authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"null")
            except ValueError:
                return exc.code, None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            return 0, {"detail": type(exc).__name__}


class ApiError(RuntimeError):
    """A request the run cannot continue without. The message never carries
    a credential."""


@dataclass
class Account:
    """One signed-in test account."""

    label: str
    token: str
    user_id: int | None = None
    organization_id: int | None = None


@dataclass
class Turn:
    """One line said and what came back on the thread for it."""

    said: str
    reply: str = ""
    #: Every event the thread gained after the line, oldest first.
    events: list[dict[str, Any]] = field(default_factory=list)
    timed_out: bool = False
    failed: bool = False
    #: The line was refused because the person's turns for today are used.
    quota: bool = False
    model: str | None = None
    seconds: float = 0.0


@dataclass
class Thread:
    """A fresh conversation with Decibyl, and everything that happened in it."""

    account: str
    thread_id: str
    turns: list[Turn] = field(default_factory=list)

    @property
    def events(self) -> list[dict[str, Any]]:
        return [e for t in self.turns for e in t.events]

    @property
    def cards(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("kind") in CARD_KINDS]

    @property
    def chips(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("kind") in CHIP_KINDS]

    @property
    def replies(self) -> list[str]:
        return [t.reply for t in self.turns]

    @property
    def last_reply(self) -> str:
        return self.turns[-1].reply if self.turns else ""


def login(transport: Transport, label: str, email: str, password: str) -> Account:
    status, body = transport.request(
        "POST", "/auth/login", body={"email": email, "password": password}
    )
    token = (body or {}).get("token") or (body or {}).get("access_token")
    if status != 200 or not token:
        # The email is the operator's own test account, not a secret; the
        # password is never echoed.
        raise ApiError(f"Could not sign in as account {label} (HTTP {status}).")
    user = (body or {}).get("user") or {}
    return Account(
        label=label,
        token=token,
        user_id=user.get("id"),
        organization_id=user.get("organization_id"),
    )


def _is_reply(event: dict[str, Any]) -> bool:
    payload = event.get("payload") or {}
    return (
        event.get("kind") == "message"
        and event.get("actor") == "agent"
        and payload.get("from") == "Decibyl"
    )


def read_thread(
    transport: Transport, account: Account, thread_id: str
) -> list[dict[str, Any]]:
    """The thread oldest first, as the app reads it."""
    query = urllib.parse.urlencode(
        {"assistant": "true", "thread_id": thread_id, "limit": 200}
    )
    status, page = transport.request("GET", f"/timeline?{query}", token=account.token)
    if status != 200:
        raise ApiError(f"Could not read the thread (HTTP {status}).")
    events = list((page or {}).get("events") or [])
    events.sort(key=lambda e: (str(e.get("at") or ""), int(e.get("id") or 0)))
    return events


def say(
    transport: Transport,
    account: Account,
    thread_id: str,
    text: str,
    *,
    wait_seconds: float = 150.0,
    poll_seconds: float = 3.0,
    sleep=time.sleep,
    clock=time.monotonic,
    helper: str | None = None,
) -> Turn:
    """Post one line to Decibyl and wait for its reply on the same thread.

    The same request the composer sends (``assistant`` with a thread id).
    The reply is enqueued server-side, so this polls the thread the way the
    screen does until a Decibyl message newer than every row seen before the
    line arrives -- then reads once more, so cards written just before the
    reply are not missed.
    """
    seen = {e.get("id") for e in read_thread(transport, account, thread_id)}
    started = clock()
    status, body = transport.request(
        "POST",
        "/timeline/message",
        token=account.token,
        body={
            "assistant": True,
            "thread_id": thread_id,
            "text": text,
            # A helper chosen in the picker (screen 06); None is Automatic.
            **({"helper": helper} if helper else {}),
        },
    )
    turn = Turn(said=text)
    if status != 200:
        turn.failed = True
        turn.reply = f"(the message was refused: HTTP {status} {_detail(body)})"
        return turn
    while True:
        events = read_thread(transport, account, thread_id)
        fresh = [e for e in events if e.get("id") not in seen]
        mine = [e for e in fresh if _is_reply(e)]
        # The person's own line comes back too; it is not news.
        fresh = [e for e in fresh if e.get("actor") != "human"]
        if mine:
            reply = mine[-1]
            payload = reply.get("payload") or {}
            turn.events = fresh
            turn.reply = str(payload.get("body") or reply.get("summary") or "")
            turn.model = payload.get("model")
            turn.quota = "quota" in payload
            turn.failed = (
                bool(payload.get("failed"))
                or turn.quota
                or turn.reply.startswith(FAILURE_REPLY)
            )
            break
        if clock() - started > wait_seconds:
            turn.events = fresh
            turn.timed_out = True
            turn.failed = True
            # A quota line or a could-not row is the answer the person saw.
            said = [
                str((e.get("payload") or {}).get("body") or e.get("summary") or "")
                for e in fresh
            ]
            turn.reply = said[-1] if said else ""
            break
        sleep(poll_seconds)
    turn.seconds = round(clock() - started, 2)
    return turn


def converse(
    transport: Transport,
    account: Account,
    lines: list[str],
    *,
    thread_id: str | None = None,
    **wait: Any,
) -> Thread:
    """A fresh thread, every line in order, each waiting for its reply."""
    thread = Thread(account=account.label, thread_id=thread_id or str(uuid.uuid4()))
    for line in lines:
        turn = say(transport, account, thread.thread_id, line, **wait)
        thread.turns.append(turn)
        if turn.quota or (turn.failed and not turn.reply):
            break
    return thread


def _detail(body: Any) -> str:
    if isinstance(body, dict) and body.get("detail"):
        return str(body["detail"])[:200]
    return ""


# --- what the account has ----------------------------------------------------


@dataclass
class AccountState:
    """What a case's ``requires`` is checked against. Read once per run."""

    features: dict[str, bool] = field(default_factory=dict)
    connected: set[str] = field(default_factory=set)
    helpers: dict[str, str] = field(default_factory=dict)
    same_workspace: bool = False
    #: Whether this deployment can connect outside apps at all (a Composio
    #: key). Without it there is no connect chip to offer.
    apps: bool = False


def read_state(transport: Transport, a: Account, b: Account | None) -> AccountState:
    state = AccountState()
    status, health = transport.request("GET", "/health")
    if status == 200:
        state.features = {
            k: bool(v) for k, v in ((health or {}).get("features") or {}).items()
        }
    status, accounts = transport.request("GET", "/connectors/accounts", token=a.token)
    if status == 200:
        state.connected = {
            str(row.get("app") or "").lower()
            for row in (accounts or {}).get("accounts") or []
            if row.get("app")
        }
    status, catalogue = transport.request("GET", "/connectors?q=gmail", token=a.token)
    state.apps = status == 200 and bool((catalogue or {}).get("available"))
    status, helpers = transport.request("GET", "/helpers", token=a.token)
    if status == 200:
        state.helpers = {
            str(h.get("key")): str(h.get("state"))
            for h in (helpers or {}).get("helpers") or []
        }
    if b is not None:
        _, me_a = transport.request("GET", "/auth/me", token=a.token)
        _, me_b = transport.request("GET", "/auth/me", token=b.token)
        state.same_workspace = bool(
            me_a
            and me_b
            and me_a.get("organization_id")
            and me_a.get("organization_id") == me_b.get("organization_id")
        )
    return state


# --- preferences a case needs (care mode, language) ---------------------------


def get_preferences(transport: Transport, account: Account) -> dict[str, Any] | None:
    status, body = transport.request("GET", "/me/preferences", token=account.token)
    return body if status == 200 and isinstance(body, dict) else None


def set_preferences(
    transport: Transport, account: Account, changes: dict[str, Any]
) -> dict[str, Any]:
    """Save ``changes`` on the person's own preferences. Returns what was
    there before, so the caller can put it back."""
    current = get_preferences(transport, account)
    if current is None:
        raise ApiError("Personal preferences are not switched on here.")
    before = {k: current.get(k) for k in changes}
    status, body = transport.request(
        "PUT",
        "/me/preferences",
        token=account.token,
        body={**changes, "revision": current.get("revision", 0)},
    )
    if status != 200:
        raise ApiError(f"Could not save preferences (HTTP {status} {_detail(body)}).")
    return before


def decline_open_cards(transport: Transport, account: Account, thread: Thread) -> int:
    """Decline every card a case left waiting, so a later run (or a person
    looking at the test account) never finds one to confirm by mistake.
    Declining is the one press that can never send anything."""
    declined = 0
    for card in thread.cards:
        if card.get("kind") != "action_proposed":
            continue
        if (card.get("payload") or {}).get("state") not in (None, "proposed"):
            continue
        status, _ = transport.request(
            "POST",
            "/timeline/actions/settle",
            token=account.token,
            body={"event_id": card.get("id"), "verb": "decline"},
        )
        declined += int(status == 200)
    return declined


def turns_left(transport: Transport, account: Account) -> int | None:
    """Model turns this person has left today, or None when operational
    quotas are off (nothing to run out of) or unreadable."""
    status, body = transport.request("GET", "/me/quotas", token=account.token)
    if status != 200:
        return None
    for row in (body or {}).get("allowances") or []:
        if row.get("kind") == "model_turns":
            return int(row.get("remaining") or 0)
    return None
