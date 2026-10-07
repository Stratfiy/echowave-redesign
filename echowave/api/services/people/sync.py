"""Keeping a person's contacts in step with Google and Microsoft.

``start`` claims the sync (one at a time per person and provider) and hands
it to the worker; ``run`` reads page after page from the saved cursor, so a
re-sync reads only what changed. ``resync_due`` (hourly) re-runs every sync
that last finished more than ``RESYNC_HOURS`` ago, which is how contacts stay
current without anybody pressing anything.

States the screen shows, never guessed: ``not_connected`` (with the connect
chip), ``needs_setup`` (connections are not per person here, or Composio is
not configured), ``syncing``, ``ok`` with when and how many, ``error`` with
the provider's reason. A failed read is an error, never "0 contacts".
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db import db_client
from api.db.people_models import PeopleSyncModel
from api.services.people import enabled, providers, store

RESYNC_HOURS = 6
STALE_MINUTES = 30
MAX_PAGES = 200

NOT_CONNECTED = "not_connected"
NEEDS_SETUP = "needs_setup"


class SyncRefused(RuntimeError):
    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state


def _setup_reason(user_id: int) -> str | None:
    if providers.fake_base():
        return None
    from api.services.integrations.composio import client, members

    if not client.is_configured():
        return "Connecting apps is not set up on this deployment yet."
    if members.member_scope(user_id) is None:
        return (
            "Contacts sync from your own connection, and connections here are "
            "the workspace's. An admin can turn on per-person connections."
        )
    return None


async def _row(
    organization_id: int, user_id: int, provider: str
) -> PeopleSyncModel | None:
    async with db_client.async_session() as session:
        return (
            await session.execute(
                select(PeopleSyncModel).where(
                    PeopleSyncModel.organization_id == organization_id,
                    PeopleSyncModel.user_id == user_id,
                    PeopleSyncModel.provider == provider,
                )
            )
        ).scalar_one_or_none()


async def status(organization_id: int, user_id: int) -> list[dict[str, Any]]:
    """One row per provider, as the screen shows it."""
    out = []
    for provider in providers.PROVIDERS:
        row = await _row(organization_id, user_id, provider)
        reason = _setup_reason(user_id)
        if reason:
            state, detail = NEEDS_SETUP, reason
        else:
            try:
                account = await providers.account_for(
                    organization_id, user_id, provider
                )
            except Exception as exc:  # noqa: BLE001 - say we could not ask
                logger.warning("Could not read connections for people: {}", exc)
                account, state, detail = (
                    None,
                    "error",
                    "Could not check the connection just now.",
                )
            else:
                state, detail = (
                    (NOT_CONNECTED, None) if account is None else ("idle", None)
                )
            if account is not None and row is not None:
                state = row.status
                detail = row.last_error
                if (
                    state == "syncing"
                    and row.started_at
                    and (
                        store.now() - row.started_at > timedelta(minutes=STALE_MINUTES)
                    )
                ):
                    state, detail = "error", "The last sync stopped before it finished."
        out.append(
            {
                "provider": provider,
                "name": providers.NAMES[provider],
                "toolkit": providers.toolkit(provider),
                "state": state,
                "detail": detail,
                "last_synced_at": row.last_synced_at.isoformat()
                if row and row.last_synced_at
                else None,
                "counts": dict(row.counts or {}) if row else {},
            }
        )
    return out


async def start(organization_id: int, user_id: int, provider: str) -> str:
    """Claim the sync and queue it. Returns ``syncing``; raises SyncRefused
    with the state when it cannot start."""
    if provider not in providers.PROVIDERS:
        raise SyncRefused("error", "No such provider.")
    reason = _setup_reason(user_id)
    if reason:
        raise SyncRefused(NEEDS_SETUP, reason)
    if await providers.account_for(organization_id, user_id, provider) is None:
        raise SyncRefused(NOT_CONNECTED, f"Connect {providers.NAMES[provider]} first.")
    now = store.now()
    stale = now - timedelta(minutes=STALE_MINUTES)
    async with db_client.async_session() as session:
        await session.execute(
            pg_insert(PeopleSyncModel)
            .values(
                organization_id=organization_id,
                user_id=user_id,
                provider=provider,
                status="idle",
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["organization_id", "user_id", "provider"]
            )
        )
        claimed = await session.execute(
            update(PeopleSyncModel)
            .where(
                PeopleSyncModel.organization_id == organization_id,
                PeopleSyncModel.user_id == user_id,
                PeopleSyncModel.provider == provider,
                (PeopleSyncModel.status != "syncing")
                | (PeopleSyncModel.started_at < stale),
            )
            .values(status="syncing", started_at=now, last_error=None, updated_at=now)
        )
        await session.commit()
    if not claimed.rowcount:
        return "syncing"  # already running: the same answer, nothing queued twice
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    try:
        await enqueue_job(
            FunctionNames.SYNC_PEOPLE,
            organization_id=organization_id,
            user_id=user_id,
            provider=provider,
        )
    except Exception as exc:
        await _finish(
            organization_id, user_id, provider, error="Could not start the sync."
        )
        raise SyncRefused("error", "Could not start the sync just now.") from exc
    return "syncing"


async def _finish(
    organization_id: int,
    user_id: int,
    provider: str,
    *,
    cursor: str | None = None,
    counts: dict[str, int] | None = None,
    error: str | None = None,
    keep_cursor: bool = True,
) -> None:
    values: dict[str, Any] = {"updated_at": store.now()}
    if error:
        values.update(status="error", last_error=error[:300])
    else:
        values.update(
            status="ok",
            last_error=None,
            last_synced_at=store.now(),
            counts=counts or {},
        )
    if cursor is not None or not keep_cursor:
        values["cursor"] = cursor
    async with db_client.async_session() as session:
        await session.execute(
            update(PeopleSyncModel)
            .where(
                PeopleSyncModel.organization_id == organization_id,
                PeopleSyncModel.user_id == user_id,
                PeopleSyncModel.provider == provider,
            )
            .values(**values)
        )
        await session.commit()


async def run(organization_id: int, user_id: int, provider: str) -> dict[str, int]:
    """Read every page since the saved cursor and store what changed."""
    counts = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0}
    if not enabled(organization_id):
        await _finish(
            organization_id, user_id, provider, error="People is switched off."
        )
        return counts
    row = await _row(organization_id, user_id, provider)
    cursor = row.cursor if row else None
    try:
        account = await providers.account_for(organization_id, user_id, provider)
        if account is None:
            await _finish(
                organization_id,
                user_id,
                provider,
                error=f"{providers.NAMES[provider]} is not connected.",
            )
            return counts
        try:
            new_cursor = await _read_all(
                provider, organization_id, user_id, account, cursor, counts
            )
        except providers.CursorExpired:
            logger.info("People sync cursor expired for user {}; reading all", user_id)
            new_cursor = await _read_all(
                provider, organization_id, user_id, account, None, counts
            )
    except providers.ProviderError as exc:
        await _finish(organization_id, user_id, provider, error=str(exc))
        return counts
    except Exception as exc:  # noqa: BLE001 - the screen must say something
        logger.exception("People sync broke for user {}: {}", user_id, exc)
        await _finish(
            organization_id,
            user_id,
            provider,
            error="Something went wrong on our side.",
        )
        return counts
    await _finish(organization_id, user_id, provider, cursor=new_cursor, counts=counts)
    return counts


async def _read_all(
    provider: str,
    organization_id: int,
    user_id: int,
    account: str,
    cursor: str | None,
    counts: dict[str, int],
) -> str | None:
    page_token: str | None = None
    new_cursor: str | None = None
    for _ in range(MAX_PAGES):
        page = await providers.read_page(
            provider, organization_id, user_id, account, cursor=cursor, page=page_token
        )
        for contact in page.contacts:
            clean = contact.clean()
            if clean is None:
                continue
            result = await store.upsert(
                organization_id, user_id, clean, source=provider, provider=provider
            )
            key = (
                "added"
                if result.created
                else ("updated" if result.changed else "unchanged")
            )
            counts[key] += 1
        for external_id in page.removed:
            if await store.forget_source(
                organization_id, user_id, provider, external_id
            ):
                counts["removed"] += 1
        new_cursor = page.cursor or new_cursor
        page_token = page.next_page
        if not page_token:
            break
    return new_cursor


async def resync_due() -> int:
    """Re-run syncs that last finished ``RESYNC_HOURS`` ago or more."""
    cutoff = store.now() - timedelta(hours=RESYNC_HOURS)
    async with db_client.async_session() as session:
        rows = (
            (
                await session.execute(
                    select(PeopleSyncModel)
                    .where(
                        PeopleSyncModel.status == "ok",
                        PeopleSyncModel.last_synced_at < cutoff,
                    )
                    .limit(200)
                )
            )
            .scalars()
            .all()
        )
    started = 0
    for row in rows:
        if not enabled(row.organization_id):
            continue
        try:
            await start(row.organization_id, row.user_id, row.provider)
            started += 1
        except SyncRefused as exc:
            await _finish(
                row.organization_id, row.user_id, row.provider, error=str(exc)
            )
    return started
