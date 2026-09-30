"""Connections per person: whose Gmail an agent uses (WS-1, KAN-196).

The core promise of PRD v2 §5.9: Decibyl uses the connections of the member
talking to it. Two members share one agent; each send goes from the
requester's own mailbox, and neither can reach the other's.

Composio holds the credentials and decides which mailbox a call touches from
the ``user_id`` sent with it. Today that identity is one per organization
(``decibyl_org_7``), so every member's agent sends from whichever account
an admin connected. Behind ``connections_per_person`` a member gets their
own identity (``decibyl_org_7_user_3``) and their own connections under it.

Three things live here and nowhere else:

* **The member scope.** Which member a call is for. Set once per Decibyl
  turn or card confirmation with :func:`acting_as`, read by the connected
  tool executor, so no caller has to thread a user id through six layers.
* **The resolver.** Member's own connection first; the workspace's when the
  member has none; a connect card when neither exists. Never an exception:
  a missing connection is a wall the thread names, not a crash.
* **The ownership registry.** Which member a Composio connected-account id
  belongs to, kept locally so a tool configured with somebody else's
  account is refused before a network call, not after Composio says no.

Off (the default) nothing here changes a byte of what is sent: every helper
answers "the workspace" and the tenant id is what it always was.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from api.services import acting as _acting
from api.services import features

FLAG = "connections_per_person"

#: Where a resolved connection came from. Written to the ledger note so
#: "sent from the workspace account" and "sent as Priya" read differently.
MEMBER = "member"
WORKSPACE = "workspace"


def enabled() -> bool:
    return features.is_on(FLAG)


def member_scope(user_id: Any) -> Optional[int]:
    """The member a call should be scoped to, or None for the workspace.

    None whenever the flag is off, so every path behaves as today, and for
    anything that is not a real user id -- a bool, a zero, a string --
    because ``decibyl_org_7_user_True`` is a silent wrong answer.
    """
    if not enabled():
        return None
    return _acting.valid_member(user_id)


# The member a turn runs as is shared with personal memory (MEM-1), so it
# lives in one place: api/services/acting.py. Re-exported here because the
# connected-tool executor and the Decibyl turn already import it from here.
acting_as = _acting.acting_as
acting_user = _acting.acting_user


@dataclass(frozen=True)
class Resolved:
    """Which identity a tool call should carry."""

    scope: str  # MEMBER or WORKSPACE
    #: The member whose tenant to send under; None sends as the workspace.
    user_id: Optional[int]


async def resolve(
    *, organization_id: int, toolkit: Optional[str], user_id: int
) -> Optional[Resolved]:
    """Member → workspace → None.

    The member's own connection wins: it is the one they authorised and the
    one a send should come from. The workspace's is the fallback an admin
    set up for everyone. None means a connect card, and the caller says so
    rather than raising -- the task waits on the person, it does not fail.
    """
    from api.services.integrations.composio.client import connected_toolkits

    wanted = (toolkit or "").strip().upper()
    if not wanted:
        return Resolved(WORKSPACE, None)
    if wanted in set(await connected_toolkits(organization_id, user_id=user_id)):
        return Resolved(MEMBER, user_id)
    if wanted in set(await connected_toolkits(organization_id)):
        return Resolved(WORKSPACE, None)
    return None


async def needs_connection(
    *, organization_id: int, toolkit: Optional[str], tool_name: str
) -> dict[str, Any]:
    """The envelope a tool call answers with when nobody has connected the app.

    Posts the connect card on the thread (the same card Decibyl offers when
    asked to connect something), so the person can fix it where they are,
    and tells the model the app is not connected in words it can repeat.
    """
    app = (toolkit or "").strip().lower() or tool_name
    try:
        from api.services.workflow import connector_offer

        await connector_offer.offer(
            organization_id=organization_id,
            arguments={"app": app, "why": f"{tool_name} needs it to run as you."},
        )
    except Exception as exc:  # noqa: BLE001 - the answer below still stands
        logger.warning("Could not post a connect card for {}: {}", app, exc)
    return {
        "status": "needs_connection",
        "app": app,
        "error": (
            f"{app} is not connected for this person or the workspace. A "
            "connect card is on the thread; nothing was done."
        ),
    }


async def owner_of(organization_id: int, connected_account_id: str) -> Optional[int]:
    """Which member a connected account was authorised by, when we know.

    None for an account the registry has not seen: the workspace's own, or
    one connected before this shipped. Composio still refuses an id that is
    not under the tenant the call carries, so an unknown id is never a way
    across a boundary -- only a slower refusal.
    """
    if not enabled() or not connected_account_id:
        return None
    from api.db import db_client

    try:
        return await db_client.member_connection_owner(
            organization_id=organization_id,
            connected_account_id=connected_account_id,
        )
    except Exception as exc:  # noqa: BLE001 - read failure is not a grant
        logger.warning(
            "Could not read the connection registry for org {}: {}",
            organization_id,
            exc,
        )
        return None


async def learn(
    *, organization_id: int, user_id: int, accounts: list[dict[str, Any]]
) -> None:
    """Remember which connected-account ids this member's tenant lists.

    Composio never calls us back when an authorisation completes, so the
    registry fills in the next time the member's accounts are read: the
    Settings screen, the accounts endpoint, a resolver run. Idempotent.
    """
    if not enabled():
        return
    rows = [
        (str(a.get("app") or "").lower(), str(a["connected_account_id"]))
        for a in accounts
        if isinstance(a, dict) and a.get("connected_account_id")
    ]
    if not rows:
        return
    from api.db import db_client

    try:
        await db_client.learn_member_connections(
            organization_id=organization_id, user_id=user_id, accounts=rows
        )
    except Exception as exc:  # noqa: BLE001 - a registry miss is a slower refusal
        logger.warning(
            "Could not record member connections for org {} user {}: {}",
            organization_id,
            user_id,
            exc,
        )


__all__ = [
    "FLAG",
    "MEMBER",
    "WORKSPACE",
    "Resolved",
    "acting_as",
    "acting_user",
    "enabled",
    "learn",
    "member_scope",
    "needs_connection",
    "owner_of",
    "resolve",
]
