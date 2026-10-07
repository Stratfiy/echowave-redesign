"""Connected apps and channels, as one person sees them (screen 22).

Two sections (handoff 22): **apps** Decibyl may read and act in, and
**channels** the person messages Decibyl on. The difference is said on the
screen, because it is the difference between granting access to messages
and sending some.

Scope. A person sees their own connections (Composio tenant
``decibyl_org_{org}_user_{uid}``) and the workspace's (``decibyl_org_{org}``)
-- never a colleague's: their tenant is never asked. Every consent row is
read by ``(organization_id, user_id)``.

Honest states. Apps: disconnected, authorizing, syncing, ready, limited,
expired, error, revoked. When Composio is not configured the section is
"needs setup"; when it cannot be asked, "error" -- never an empty list.
Channels are "available" only once this deployment has seen a verified
message on them (``channel_checks``), not because keys are present.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import and_, delete, select, update

from api.db import db_client
from api.db.identity_models import ChannelCheckModel, ConnectionConsentModel
from api.db.models import MemberConnectionModel
from api.services import events, features

FLAG = "identity_connections"

MINE = "mine"
WORKSPACE = "workspace"
SCOPES = (MINE, WORKSPACE)

STATES = (
    "disconnected",
    "authorizing",
    "syncing",
    "ready",
    "limited",
    "expired",
    "error",
    "revoked",
)

#: Composio's account status, in the handoff's words.
_FROM_PROVIDER = {
    "INITIALIZING": "authorizing",
    "INITIATED": "authorizing",
    "ACTIVE": "ready",
    "EXPIRED": "expired",
    "FAILED": "error",
    "INACTIVE": "limited",
}
_REASONS = {
    "expired": "The sign-in expired. Reconnect to keep using it.",
    "error": "The app refused the connection. Try connecting again.",
    "limited": "Turned off at the app's side, so Decibyl cannot use it now.",
    "authorizing": "Waiting for you to finish signing in.",
    "syncing": "Connected; checking what Decibyl can reach.",
    "revoked": "Disconnected. Decibyl can no longer use it.",
}
#: A sign-in left open longer than this was abandoned.
AUTHORIZING_FOR = timedelta(minutes=30)
_SLUG = re.compile(r"^[a-z0-9_]{2,64}$")


def enabled(organization_id: int | None) -> bool:
    return features.is_on(FLAG, organization_id)


class ConnectionError_(Exception):
    """Refused with a reason a person can read; ``status`` is the HTTP code."""

    def __init__(self, message: str, status: int = 409, code: str = "refused"):
        super().__init__(message)
        self.status = status
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_return(path: Any) -> str | None:
    """Only a path inside the app: an outside address here would make the
    sign-in a redirect to anywhere."""
    value = str(path or "").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    return value[:500]


def access_lines(app_name: str) -> list[str]:
    """What connecting grants, shown before Connect and stored with the
    consent. The provider asks for its own scopes on its own page; these
    are the rules Decibyl keeps on top of them."""
    return [
        f"Read {app_name} when a task you ask for needs it.",
        f"Draft in {app_name}. Anything that sends, changes or deletes asks you first.",
        f"You sign in on {app_name}'s own page. Decibyl never sees your password.",
        "Disconnect at any time here; Decibyl stops using it at once.",
    ]


def _per_person() -> bool:
    from api.services.integrations.composio import members

    return members.enabled()


async def _app_name(toolkit: str) -> str:
    from api.services.integrations.composio.client import toolkit_name

    try:
        return (await toolkit_name(toolkit)) or toolkit.replace("_", " ").title()
    except Exception:  # noqa: BLE001 - a name is cosmetic
        return toolkit.replace("_", " ").title()


# --- starting and finishing a connection ------------------------------------


async def preview(toolkit: str) -> dict[str, Any]:
    """The access lines a Connect would record, before anything is recorded."""
    slug = (toolkit or "").strip().lower()
    if not _SLUG.match(slug):
        raise ConnectionError_("No app by that name.", 404, "unknown_app")
    app_name = await _app_name(slug)
    return {
        "toolkit": slug,
        "app_name": app_name,
        "access": access_lines(app_name),
        "per_person": _per_person(),
    }


async def start(
    *,
    organization_id: int,
    user_id: int,
    toolkit: str,
    scope: str,
    purpose: str | None,
    return_to: str | None,
    is_admin: bool,
) -> dict[str, Any]:
    """Record the consent, then mint the provider's sign-in link."""
    from api.services.integrations.composio import client

    slug = (toolkit or "").strip().lower()
    if not _SLUG.match(slug):
        raise ConnectionError_("No app by that name.", 404, "unknown_app")
    if scope not in SCOPES:
        raise ConnectionError_("Say whose connection this is.", 422, "bad_scope")
    if scope == MINE and not _per_person():
        raise ConnectionError_(
            "Connections of your own are not switched on here yet; your "
            "workspace admin can connect it for the workspace.",
            409,
            "per_person_off",
        )
    if scope == WORKSPACE and not is_admin:
        raise ConnectionError_(
            "Only a workspace admin can connect an app for everyone.", 403, "admin_only"
        )
    if not client.is_configured():
        raise ConnectionError_(
            "Connecting apps is not set up on this deployment yet.", 503, "needs_setup"
        )
    app_name = await _app_name(slug)
    consent = ConnectionConsentModel(
        organization_id=organization_id,
        user_id=user_id,
        toolkit=slug,
        scope=scope,
        state="authorizing",
        purpose=(purpose or "").strip()[:200] or None,
        access=access_lines(app_name),
        return_to=_safe_return(return_to),
    )
    async with db_client.async_session() as session:
        session.add(consent)
        await session.commit()
        await session.refresh(consent)
    await events.emit(
        "connection_started",
        user_id=user_id,
        organization_id=organization_id,
        properties={"app": slug, "status": "authorizing"},
    )
    link = await client.connect_link(
        toolkit=slug,
        organization_id=organization_id,
        user_id=user_id if scope == MINE else None,
    )
    if "error" in link:
        await _set_state(consent.id, "error", reason_code="link_failed")
        raise ConnectionError_(str(link["error"]), 502, "link_failed")
    if scope == MINE:
        try:
            await db_client.record_member_connection(
                organization_id=organization_id, user_id=user_id, toolkit=slug
            )
        except Exception as exc:  # noqa: BLE001 - the link is still good
            logger.warning("Could not record a member connection: {}", exc)
    return {
        "consent_id": consent.id,
        "url": link["url"],
        "expires_at": link.get("expires_at"),
        "access": consent.access,
        "app_name": app_name,
    }


async def _set_state(consent_id: int, state: str, **fields: Any) -> None:
    async with db_client.async_session() as session:
        await session.execute(
            update(ConnectionConsentModel)
            .where(ConnectionConsentModel.id == consent_id)
            .values(state=state, updated_at=_now(), **fields)
        )
        await session.commit()


async def _consent(organization_id: int, user_id: int, consent_id: int):
    """The person's consent in this workspace. Another person's consent is
    not found; the person's own, started in another workspace, is the
    wrong-workspace case and says so (handoff 22: "wrong-workspace OAuth is
    rejected")."""
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(ConnectionConsentModel).where(
                ConnectionConsentModel.id == consent_id,
                ConnectionConsentModel.user_id == user_id,
            )
        )
    if row is None:
        raise ConnectionError_("Not found.", 404, "not_found")
    if row.organization_id != organization_id:
        raise ConnectionError_(
            "You started connecting this in another workspace. Switch back to "
            "that workspace to finish, or start again here.",
            409,
            "wrong_workspace",
        )
    return row


async def complete(
    *, organization_id: int, user_id: int, consent_id: int
) -> dict[str, Any]:
    """After the provider's page: see what actually happened, and say where
    to go back to. Never claims ready without the provider saying so."""
    from api.services.integrations.composio import client

    consent = await _consent(organization_id, user_id, consent_id)
    if consent.state in ("ready", "revoked"):
        return _consent_view(consent)
    try:
        accounts = await client.accounts_with_status(
            organization_id, user_id=user_id if consent.scope == MINE else None
        )
    except client.ComposioUnavailable:
        # Not known yet; the consent stays as it was and the screen says so.
        view = _consent_view(consent)
        view["reason"] = "We could not reach the app's sign-in service. Try again."
        return view
    ours = [a for a in accounts if a.get("app") == consent.toolkit]
    ours.sort(key=lambda a: str(a.get("connected_at") or ""), reverse=True)
    if not ours:
        if _now() - consent.started_at > AUTHORIZING_FOR:
            await _set_state(consent.id, "error", reason_code="not_finished")
            consent.state, consent.reason_code = "error", "not_finished"
        return _consent_view(consent)
    account = ours[0]
    state = _FROM_PROVIDER.get(account["status"], "error")
    fields: dict[str, Any] = {"connected_account_id": account["connected_account_id"]}
    if state == "ready":
        fields.update(ready_at=_now(), last_success_at=_now(), reason_code=None)
    await _set_state(consent.id, state, **fields)
    if state == "ready" and consent.state != "ready":
        await events.emit(
            "connection_ready",
            user_id=user_id,
            organization_id=organization_id,
            properties={"app": consent.toolkit, "status": "ready"},
        )
    async with db_client.async_session() as session:
        fresh = await session.get(ConnectionConsentModel, consent.id)
    return _consent_view(fresh)


def _consent_view(row: ConnectionConsentModel) -> dict[str, Any]:
    return {
        "consent_id": row.id,
        "toolkit": row.toolkit,
        "scope": row.scope,
        "state": row.state,
        "reason": _REASONS.get(row.state),
        "reason_code": row.reason_code,
        "purpose": row.purpose,
        "access": list(row.access or []),
        "return_to": row.return_to,
        "connected_account_id": row.connected_account_id,
        "started_at": row.started_at,
        "ready_at": row.ready_at,
    }


# --- what one person sees ---------------------------------------------------


async def _consents(organization_id: int, user_id: int) -> list[ConnectionConsentModel]:
    async with db_client.async_session() as session:
        rows = await session.scalars(
            select(ConnectionConsentModel)
            .where(
                ConnectionConsentModel.organization_id == organization_id,
                ConnectionConsentModel.user_id == user_id,
            )
            .order_by(ConnectionConsentModel.id.desc())
            .limit(200)
        )
        return list(rows)


async def dependents(
    organization_id: int, user_id: int, toolkit: str, scope: str
) -> list[str]:
    """What stops working without this connection, in words: the
    workspace's agents' tools from this app, and the person's own waiting
    cards that would run in it."""
    from api.services.workflow import connected_tools

    found: list[str] = []
    if scope == WORKSPACE:
        for tool in await connected_tools.list_for_organization(organization_id):
            if (connected_tools.toolkit_of(tool) or "").lower() == toolkit:
                found.append(f"the tool {tool.name}")
    try:
        from sqlalchemy import text

        from api.db.models import AgentEventModel
        from api.enums import AgentEventKind

        async with db_client.async_session() as session:
            waiting = await session.scalar(
                select(text("count(*)"))
                .select_from(AgentEventModel)
                .where(
                    AgentEventModel.organization_id == organization_id,
                    AgentEventModel.kind == AgentEventKind.ACTION_PROPOSED.value,
                    text("agent_events.payload->>'action' = 'run_tool'"),
                    text("lower(agent_events.payload->'args'->>'toolkit') = :toolkit"),
                    text("agent_events.payload->>'state' IN ('proposed', 'armed')"),
                    text(
                        "(agent_events.payload->'confirmed'->>'by' IS NULL "
                        "OR (agent_events.payload->'confirmed'->>'by')::int = :uid)"
                    ),
                )
                .params(toolkit=toolkit, uid=user_id)
            )
        if waiting:
            found.append(f"{waiting} waiting card{'s' if waiting != 1 else ''}")
    except Exception as exc:  # noqa: BLE001 - a partial list is still a list
        logger.warning("Could not count cards waiting on {}: {}", toolkit, exc)
    return found


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


async def apps(organization_id: int, user_id: int, *, is_admin: bool) -> dict[str, Any]:
    """The apps section: ``{state, reason, items}``. ``state`` is the
    section's own: ok, needs_setup, or error (with whatever could be read)."""
    from api.services.integrations.composio import client

    consents = await _consents(organization_id, user_id)
    if not client.is_configured():
        return {
            "state": "needs_setup",
            "reason": "Connecting apps is not set up on this deployment yet.",
            "per_person": _per_person(),
            "items": [],
        }
    items: list[dict[str, Any]] = []
    failed: list[str] = []
    scopes = [WORKSPACE] + ([MINE] if _per_person() else [])
    seen_accounts: set[str] = set()
    for scope in scopes:
        try:
            accounts = await client.accounts_with_status(
                organization_id, user_id=user_id if scope == MINE else None
            )
        except client.ComposioUnavailable as exc:
            logger.warning("Connections: could not list {} accounts: {}", scope, exc)
            failed.append(scope)
            continue
        for account in accounts:
            toolkit = account.get("app") or "unknown"
            seen_accounts.add(account["connected_account_id"])
            consent = next(
                (
                    c
                    for c in consents
                    if c.connected_account_id == account["connected_account_id"]
                ),
                None,
            )
            state = _FROM_PROVIDER.get(account.get("status") or "", "error")
            app_name = await _app_name(toolkit)
            items.append(
                {
                    "id": account["connected_account_id"],
                    "toolkit": toolkit,
                    "app_name": app_name,
                    "account": account.get("label"),
                    "owner": "you" if scope == MINE else "workspace",
                    "scope": scope,
                    "state": state,
                    "reason": _REASONS.get(state),
                    "connected_at": _iso(account.get("connected_at")),
                    "last_success_at": _iso(consent.last_success_at)
                    if consent
                    else None,
                    "access": list(consent.access)
                    if consent
                    else access_lines(app_name),
                    "purpose": consent.purpose if consent else None,
                    "can_disconnect": scope == MINE or is_admin,
                }
            )
    # Sign-ins started and not finished, and recent disconnections: the
    # person's own history, so a cancelled sign-in is not silently gone.
    recent = _now() - timedelta(days=30)
    for consent in consents:
        if consent.connected_account_id in seen_accounts:
            continue
        if consent.state == "ready":
            # Ready by our record and absent at the provider: revoked there.
            state = "revoked"
        elif consent.state in ("authorizing", "error", "revoked"):
            state = consent.state
            if state == "authorizing" and _now() - consent.started_at > AUTHORIZING_FOR:
                state = "error"
        else:
            continue
        if (consent.updated_at or consent.started_at) < recent:
            continue
        app_name = await _app_name(consent.toolkit)
        items.append(
            {
                "id": f"consent:{consent.id}",
                "toolkit": consent.toolkit,
                "app_name": app_name,
                "account": None,
                "owner": "you" if consent.scope == MINE else "workspace",
                "scope": consent.scope,
                "state": state,
                "reason": _REASONS.get(state)
                if not (state == "error" and consent.reason_code == "not_finished")
                else "Signing in was not finished. Connect again when you are ready.",
                "connected_at": None,
                "last_success_at": _iso(consent.last_success_at),
                "access": list(consent.access or []),
                "purpose": consent.purpose,
                "can_disconnect": False,
                "consent_id": consent.id,
            }
        )
        # One row per app and scope for the history: the newest.
        seen_accounts.add(consent.connected_account_id or f"c{consent.id}")
    deduped: list[dict[str, Any]] = []
    history: set[tuple[str, str]] = set()
    for item in items:
        if item["id"].startswith("consent:"):
            key = (item["toolkit"], item["scope"])
            live = any(
                i["toolkit"] == item["toolkit"]
                and i["scope"] == item["scope"]
                and not i["id"].startswith("consent:")
                for i in items
            )
            if live or key in history:
                continue
            history.add(key)
        deduped.append(item)
    section_state = "error" if failed else "ok"
    reason = None
    if failed:
        reason = (
            "Some connections could not be checked just now, so this list may be "
            "incomplete. Try again."
        )
    return {
        "state": section_state,
        "reason": reason,
        "per_person": _per_person(),
        "items": deduped,
    }


async def find_account(
    organization_id: int, user_id: int, *, scope: str, connected_account_id: str
) -> dict[str, Any]:
    """The account, from the tenant's own listing -- the proof that it is
    this person's (or this workspace's) to revoke. Raises CardError."""
    from api.services.identity.cards import CardError
    from api.services.integrations.composio import client

    if scope not in SCOPES or not connected_account_id:
        raise CardError("Say which connection.")
    if scope == MINE and not _per_person():
        raise CardError("That connection is not here.")
    try:
        accounts = await client.accounts_with_status(
            organization_id, user_id=user_id if scope == MINE else None
        )
    except client.ComposioNotConfigured as exc:
        raise CardError("Connecting apps is not set up on this deployment.") from exc
    except client.ComposioUnavailable as exc:
        raise CardError("Could not check the connection just now. Try again.") from exc
    for account in accounts:
        if account["connected_account_id"] == connected_account_id:
            toolkit = account.get("app") or "unknown"
            return {
                "toolkit": toolkit,
                "app_name": await _app_name(toolkit),
                "status": account.get("status"),
            }
    raise CardError("That connection is not here.")


async def disconnect(
    organization_id: int, user_id: int, *, scope: str, connected_account_id: str
) -> str:
    """Revoke at the provider, then record it. Run by the card only."""
    from api.services.identity.cards import CardError
    from api.services.integrations.composio import client

    found = await find_account(
        organization_id,
        user_id,
        scope=scope,
        connected_account_id=connected_account_id,
    )
    try:
        await client.delete_connected_account(connected_account_id)
    except client.ComposioExecutionError as exc:
        raise CardError(
            f"{found['app_name']} refused to disconnect. Nothing changed."
        ) from exc
    # ComposioUnavailable propagates: whether it went is not known, and the
    # card says so (outcome unknown; reconciled by reconcile.py).
    async with db_client.async_session() as session:
        await session.execute(
            update(ConnectionConsentModel)
            .where(
                ConnectionConsentModel.organization_id == organization_id,
                ConnectionConsentModel.connected_account_id == connected_account_id,
            )
            .values(
                state="revoked",
                revoked_at=_now(),
                revoked_by=user_id,
                updated_at=_now(),
            )
        )
        if scope == MINE:
            await session.execute(
                delete(MemberConnectionModel).where(
                    and_(
                        MemberConnectionModel.organization_id == organization_id,
                        MemberConnectionModel.user_id == user_id,
                        MemberConnectionModel.toolkit == found["toolkit"],
                    )
                )
            )
        await session.commit()
    await events.emit(
        "connection_revoked",
        user_id=user_id,
        organization_id=organization_id,
        properties={"app": found["toolkit"], "status": "revoked"},
    )
    return f"Disconnected {found['app_name']}. Decibyl can no longer use it."


async def note_success(
    organization_id: int, user_id: int | None, toolkit: str | None
) -> None:
    """A tool call in this app succeeded: the "last success" on the screen.
    Never raises; nothing while the flag is off."""
    if not toolkit or not enabled(organization_id):
        return
    try:
        async with db_client.async_session() as session:
            conditions = [
                ConnectionConsentModel.organization_id == organization_id,
                ConnectionConsentModel.toolkit == toolkit.lower(),
                ConnectionConsentModel.state == "ready",
            ]
            if user_id is not None:
                conditions.append(ConnectionConsentModel.user_id == user_id)
            else:
                conditions.append(ConnectionConsentModel.scope == WORKSPACE)
            await session.execute(
                update(ConnectionConsentModel)
                .where(and_(*conditions))
                .values(last_success_at=_now())
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not note a success for {}: {}", toolkit, exc)


# --- channels ---------------------------------------------------------------

_CHANNEL_NAMES = {
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "slack": "Slack",
    "teams": "Microsoft Teams",
}
#: What each platform lets Decibyl do, as the platforms state it. Checked
#: against their rules for business messaging; not the same for all four
#: (handoff 7: "do not assume all channels allow identical proactive
#: messaging").
_PROACTIVE = {
    "whatsapp": (
        "Replies freely for 24 hours after your last message. After that, "
        "WhatsApp only allows approved message templates."
    ),
    "telegram": "Can message you first once you have linked it.",
    "slack": "Can message you first in a direct message in a workspace that added Decibyl.",
    "teams": "Can message you after you have messaged it once.",
}
#: A verified inbound older than this no longer counts as current proof.
VERIFIED_FOR = timedelta(days=30)


async def channel_checks() -> dict[str, ChannelCheckModel]:
    async with db_client.async_session() as session:
        rows = await session.scalars(select(ChannelCheckModel))
        return {row.channel: row for row in rows}


async def channels(organization_id: int, user_id: int) -> list[dict[str, Any]]:
    """One row per platform: capability (with its reason), what it can do,
    and whether this person has linked it. Linking and unlinking stay on
    the existing /channel-links routes."""
    from api.services.messaging.channels import dispatch, identities

    checks = await channel_checks()
    linked = await identities.for_member(
        organization_id=organization_id, user_id=user_id
    )
    rows: list[dict[str, Any]] = []
    now = _now()
    for channel in ("whatsapp", "telegram", "slack", "teams"):
        try:
            adapter = dispatch.adapter_for(channel)
            configured = bool(adapter.enabled())
        except Exception:  # noqa: BLE001 - an adapter that cannot load is unavailable
            configured = False
        check = checks.get(channel)
        verified_at = check.verified_inbound_at if check else None
        verified = bool(verified_at and now - verified_at <= VERIFIED_FOR)
        delivery_failing = bool(
            check
            and check.delivery_failed_at
            and (
                not check.delivery_ok_at
                or check.delivery_failed_at > check.delivery_ok_at
            )
        )
        if not dispatch.channel_on(channel, organization_id):
            capability, reason = (
                "disabled_by_policy",
                "Not switched on for this workspace.",
            )
        elif not configured:
            capability, reason = "needs_setup", "Not set up on this deployment yet."
        elif not verified:
            capability, reason = (
                "needs_setup",
                "Set up, and not yet proven with a real message. Linking it sends the first one.",
            )
        else:
            capability, reason = "available", None
        mine = [i for i in linked if i.channel == channel]
        if not mine:
            state = "disconnected"
        elif capability != "available":
            state = "limited"
        elif delivery_failing:
            state = "error"
        else:
            state = "ready"
        rows.append(
            {
                "channel": channel,
                "name": _CHANNEL_NAMES[channel],
                "capability": capability,
                "reason": reason,
                "verified_at": _iso(verified_at),
                "last_delivery_ok_at": _iso(check.delivery_ok_at) if check else None,
                "delivery_failing": delivery_failing,
                "proactive": _PROACTIVE[channel],
                # Messaging Decibyl on a channel is not access to the
                # person's other messages there (handoff 25).
                "reads_other_messages": False,
                "state": state,
                "linked": [
                    {
                        "id": i.id,
                        "display_name": i.display_name,
                        "handle": (i.external_id or "")[-4:],
                        "linked_at": _iso(getattr(i, "verified_at", None)),
                    }
                    for i in mine
                ],
            }
        )
    return rows
