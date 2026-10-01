"""Setting feature switches from the staff console (ADMIN-1).

The write half of ``services/features.py``: it owns the ``feature_overrides``
rows, the audit line every change leaves in ``admin_action_log``, and telling
every worker to re-read. ``features.is_on`` is the read half and never comes
here.

Every write is an upsert keyed on (feature, organisation), so repeating one
is harmless.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.feature_override_models import FeatureOverrideModel
from api.db.models import AdminActionLogModel, OrganizationModel
from api.services import features


class UnknownFeature(LookupError):
    """The name is not in ``features.FLAGS``."""


class UnknownOrganization(LookupError):
    """No organisation has that id."""


def require_known(name: str) -> None:
    if name not in features.FLAGS:
        raise UnknownFeature(name)


def org_label(org: OrganizationModel) -> str:
    return org.name or org.billing_name or f"Organization {org.id}"


async def _require_org(
    session: AsyncSession, organization_id: int
) -> OrganizationModel:
    org = await session.get(OrganizationModel, organization_id)
    if org is None:
        raise UnknownOrganization(organization_id)
    return org


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _expired(expires_at: datetime | None, now: datetime) -> bool:
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= now


def _row_dict(
    row: FeatureOverrideModel, org_name: str | None, now: datetime
) -> dict[str, Any]:
    return {
        "organization_id": row.organization_id,
        "organization_name": org_name,
        "enabled": bool(row.enabled),
        "note": row.note,
        "expires_at": _iso(row.expires_at),
        "expired": _expired(row.expires_at, now),
        "set_by_user_id": row.set_by_user_id,
        "updated_at": _iso(row.updated_at),
    }


async def registry(session: AsyncSession) -> list[dict[str, Any]]:
    """Every registered flag: what it does, what everyone gets and why, and
    every override -- console rows (with the organisation's name) and the
    read-only ``FEATURE_ORG_OVERRIDES`` ids, so nothing that decides a flag
    is invisible here."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(FeatureOverrideModel, OrganizationModel)
        .outerjoin(
            OrganizationModel,
            OrganizationModel.id == FeatureOverrideModel.organization_id,
        )
        .order_by(FeatureOverrideModel.feature, FeatureOverrideModel.organization_id)
    )
    by_flag: dict[str, list[tuple[FeatureOverrideModel, OrganizationModel | None]]] = {}
    for row, org in result.all():
        by_flag.setdefault(row.feature, []).append((row, org))

    env_orgs = features.org_overrides()
    flags = []
    for name in sorted(features.FLAGS):
        rows = by_flag.get(name, [])
        global_row = next((r for r, _ in rows if r.organization_id is None), None)
        value, source = features.global_state(name)
        flags.append(
            {
                "name": name,
                "description": features.describe(name),
                "setting": features.FLAGS[name],
                "global_enabled": value,
                "global_source": source,
                "environment_enabled": features.env_global(name),
                "global_override": (
                    _row_dict(global_row, None, now) if global_row else None
                ),
                "overrides": [
                    _row_dict(r, org_label(o) if o else None, now)
                    for r, o in rows
                    if r.organization_id is not None
                ],
                "environment_organization_ids": sorted(env_orgs.get(name, ())),
            }
        )
    return flags


async def flags_for_organization(
    session: AsyncSession, organization_id: int
) -> list[dict[str, Any]]:
    """One organisation's view: each flag's effective value for it, and the
    console row that decides it, if any."""
    await _require_org(session, organization_id)
    now = datetime.now(UTC)
    rows = {
        r.feature: r
        for r in (
            await session.scalars(
                select(FeatureOverrideModel).where(
                    FeatureOverrideModel.organization_id == organization_id
                )
            )
        ).all()
    }
    env_orgs = features.org_overrides()
    out = []
    for name in sorted(features.FLAGS):
        row = rows.get(name)
        value, source = features.global_state(name)
        out.append(
            {
                "name": name,
                "description": features.describe(name),
                "enabled": features.is_on(name, organization_id),
                "global_enabled": value,
                "global_source": source,
                "override": _row_dict(row, None, now) if row else None,
                "environment_listed": organization_id in env_orgs.get(name, ()),
            }
        )
    return out


def _audit(
    session: AsyncSession,
    *,
    actor_user_id: int,
    action: str,
    name: str,
    organization_id: int | None,
    detail: str,
) -> None:
    scope = f"org={organization_id}" if organization_id is not None else "global"
    session.add(
        AdminActionLogModel(
            actor_user_id=actor_user_id,
            action=action,
            target_organization_id=organization_id,
            note=f"feature={name}; {scope}; {detail}"[:500],
        )
    )


async def set_override(
    session: AsyncSession,
    *,
    name: str,
    organization_id: int | None,
    enabled: bool,
    note: str | None,
    expires_at: datetime | None,
    actor_user_id: int,
) -> None:
    """Insert or replace the row for (name, organisation); ``None`` is the
    global switch. Adds the audit row to the same transaction; the caller
    commits."""
    require_known(name)
    if organization_id is not None:
        await _require_org(session, organization_id)
    now = datetime.now(UTC)
    values = {
        "feature": name,
        "organization_id": organization_id,
        "enabled": enabled,
        "note": (note or "").strip()[:300] or None,
        "expires_at": expires_at,
        "set_by_user_id": actor_user_id,
        "created_at": now,
        "updated_at": now,
    }
    stmt = insert(FeatureOverrideModel).values(**values)
    if organization_id is None:
        target = {
            "index_elements": ["feature"],
            "index_where": FeatureOverrideModel.organization_id.is_(None),
        }
    else:
        target = {
            "index_elements": ["feature", "organization_id"],
            "index_where": FeatureOverrideModel.organization_id.isnot(None),
        }
    stmt = stmt.on_conflict_do_update(
        **target,
        set_={
            "enabled": stmt.excluded.enabled,
            "note": stmt.excluded.note,
            "expires_at": stmt.excluded.expires_at,
            "set_by_user_id": stmt.excluded.set_by_user_id,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await session.execute(stmt)
    _audit(
        session,
        actor_user_id=actor_user_id,
        action="flag_set",
        name=name,
        organization_id=organization_id,
        detail=(
            f"enabled={str(enabled).lower()}; "
            f"expires_at={_iso(expires_at) or 'never'}; {values['note'] or ''}"
        ),
    )


async def clear_override(
    session: AsyncSession,
    *,
    name: str,
    organization_id: int | None,
    actor_user_id: int,
) -> bool:
    """Delete the row, so the next rule down decides. Returns whether there
    was one; the audit row is written either way, since someone asked."""
    require_known(name)
    if organization_id is not None:
        await _require_org(session, organization_id)
    condition = (
        FeatureOverrideModel.organization_id.is_(None)
        if organization_id is None
        else FeatureOverrideModel.organization_id == organization_id
    )
    result = await session.execute(
        delete(FeatureOverrideModel).where(
            FeatureOverrideModel.feature == name, condition
        )
    )
    removed = bool(result.rowcount)
    _audit(
        session,
        actor_user_id=actor_user_id,
        action="flag_cleared",
        name=name,
        organization_id=organization_id,
        detail="removed" if removed else "no row",
    )
    return removed


async def publish_change(organization_id: int | None) -> None:
    """Reload this worker, then tell the others.

    In that order so the request that made the change sees it even when
    Redis is down. A worker that misses the broadcast still catches up on its
    30-second refresh, which is why a failed broadcast is logged, not raised.
    """
    from api.services.worker_sync.manager import get_worker_sync_manager
    from api.services.worker_sync.protocol import WorkerSyncEventType

    await features.refresh_overrides()
    try:
        manager = get_worker_sync_manager()
    except RuntimeError:
        logger.warning(
            "Feature override changed with no worker sync manager; other "
            "workers will pick it up on their next refresh"
        )
        return
    try:
        await manager.broadcast(
            WorkerSyncEventType.FEATURE_OVERRIDES.value,
            "update",
            org_id=str(organization_id or ""),
        )
    except Exception as exc:  # noqa: BLE001 -- the write already committed
        logger.warning("Could not broadcast feature override change: {}", exc)
