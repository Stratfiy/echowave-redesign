"""Provider key lifecycle: stage, validate, activate, refresh consumers,
verify, revoke -- and revert while that is still possible (handoff 34).

The existing provider-keys screen replaces a key in place: paste, save, and
every call from that moment uses the new value, tested or not, with the old
one gone. That is fine for a first key and wrong for a rotation, where the
safe order is the handoff's:

1. **stage**    -- the new key is sealed and held beside the live one. Nothing
                   uses it yet.
2. **validate** -- the vendor is asked whether the staged key works. A
                   rejection stops here; "could not ask" is recorded as such
                   and may be activated only with ``force``.
3. **activate** -- the staged key becomes the live one. The old ciphertext is
                   kept on the rotation row, which is what makes revert
                   possible.
4. **refresh**  -- consumers are told: every worker re-reads managed tier
                   availability (``MANAGED_TIERS`` sync), and the key is
                   mirrored to the configured secret store. The pipeline
                   reads keys per call, never per audio frame, so the next
                   call uses the new key.
5. **verify**   -- the live key is checked again, now that it is live.
6. **revoke**   -- the old ciphertext is destroyed. From here revert is
                   impossible. Revoking the old key *at the provider* is a
                   separate human step on the vendor's dashboard; this module
                   says so rather than pretending it did it.

**Never returned.** No function here returns key material: views carry the
last four characters only. Request bodies carrying a key are excluded from
logs (nothing here logs a value) and from Sentry (``sentry_scrub`` drops
bodies). Every step leaves an ``admin_action_log`` row with the actor,
the slot and the reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db.models import AdminActionLogModel, PlatformProviderCredentialModel
from api.db.ops_models import PlatformCredentialRotationModel
from api.services.configuration import key_validation, platform_credentials
from api.services.ops import redaction, secret_store

STAGED = "staged"
VALIDATED = "validated"
VALIDATION_FAILED = "validation_failed"
UNVERIFIED = "unverified"
ACTIVE = "active"
REFRESHED = "refreshed"
VERIFIED = "verified"
REVOKED = "revoked"
REVERTED = "reverted"
CANCELLED = "cancelled"

#: Which steps may follow which. Anything else is refused with the reason.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "validate": (STAGED, VALIDATION_FAILED, UNVERIFIED),
    "activate": (VALIDATED, UNVERIFIED),
    "refresh": (ACTIVE, REFRESHED),
    "verify": (ACTIVE, REFRESHED, VERIFIED),
    "revoke": (VERIFIED,),
    "revert": (ACTIVE, REFRESHED, VERIFIED),
    "cancel": (STAGED, VALIDATED, VALIDATION_FAILED, UNVERIFIED),
}
OPEN_STATES = (
    STAGED,
    VALIDATED,
    VALIDATION_FAILED,
    UNVERIFIED,
    ACTIVE,
    REFRESHED,
    VERIFIED,
)


class LifecycleError(ValueError):
    """A step that cannot run now, with words an operator can act on."""


class StepNotAllowed(LifecycleError):
    pass


@dataclass(frozen=True)
class RotationView:
    id: int
    component: str
    provider: str
    environment: str
    state: str
    staged_key: str
    previous_key: str | None
    can_revert: bool
    label: str | None
    reason: str
    reason_code: str | None
    staged_by: int | None
    timeline: dict[str, str | None]
    next_steps: list[str]
    provider_revocation_reminder: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "component": self.component,
            "provider": self.provider,
            "environment": self.environment,
            "state": self.state,
            "staged_key": self.staged_key,
            "previous_key": self.previous_key,
            "can_revert": self.can_revert,
            "label": self.label,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "staged_by": self.staged_by,
            "timeline": self.timeline,
            "next_steps": self.next_steps,
            "provider_revocation_reminder": self.provider_revocation_reminder,
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _mask(last_four: str | None) -> str | None:
    return f"••••{last_four}" if last_four else None


def _view(row: PlatformCredentialRotationModel) -> RotationView:
    steps = [step for step, allowed in TRANSITIONS.items() if row.state in allowed]
    reminder = None
    if row.state == REVOKED:
        reminder = (
            f"Decibyl no longer holds the old key ending {row.previous_last_four or '????'}. "
            "Revoke it on the provider's dashboard too; until then it still works for "
            "anyone who has it."
        )
    return RotationView(
        id=row.id,
        component=row.component,
        provider=row.provider,
        environment=row.environment,
        state=row.state,
        staged_key=_mask(row.staged_last_four) or "",
        previous_key=_mask(row.previous_last_four),
        # With no previous key, revert takes the slot out of service instead.
        can_revert=row.state in TRANSITIONS["revert"],
        label=row.label,
        reason=row.reason,
        reason_code=row.reason_code,
        staged_by=row.staged_by,
        timeline={
            "staged_at": _iso(row.staged_at),
            "validated_at": _iso(row.validated_at),
            "activated_at": _iso(row.activated_at),
            "consumers_refreshed_at": _iso(row.consumers_refreshed_at),
            "verified_at": _iso(row.verified_at),
            "revoked_at": _iso(row.revoked_at),
            "reverted_at": _iso(row.reverted_at),
        },
        next_steps=steps,
        provider_revocation_reminder=reminder,
    )


def _audit(
    session: AsyncSession,
    *,
    actor_user_id: int,
    step: str,
    row,
    reason: str | None = None,
) -> None:
    session.add(
        AdminActionLogModel(
            actor_user_id=actor_user_id,
            action=f"credential_{step}",
            note=(
                f"slot={row.component}/{row.provider}; rotation={row.id}; "
                f"state={row.state}; key=••••{row.staged_last_four}; "
                f"reason={(reason or row.reason or '')[:200]}"
            )[:500],
        )
    )


def _require_reason(reason: str | None) -> str:
    reason = (reason or "").strip()
    if len(reason) < 4:
        raise LifecycleError("Give a reason: it is kept with the audit record.")
    return reason[:500]


async def _load(
    session: AsyncSession, rotation_id: int
) -> PlatformCredentialRotationModel:
    row = await session.get(
        PlatformCredentialRotationModel, rotation_id, with_for_update=True
    )
    if row is None:
        raise LookupError(rotation_id)
    return row


def _check_step(row: PlatformCredentialRotationModel, step: str) -> None:
    allowed = TRANSITIONS[step]
    if row.state not in allowed:
        raise StepNotAllowed(
            f"Cannot {step} a rotation that is {row.state}; it must be "
            f"{' or '.join(allowed)}."
        )


async def _live_row(session: AsyncSession, component: str, provider: str):
    return await session.scalar(
        select(PlatformProviderCredentialModel)
        .where(
            PlatformProviderCredentialModel.component == component,
            PlatformProviderCredentialModel.provider == provider,
        )
        .with_for_update()
    )


async def stage(
    session: AsyncSession,
    *,
    actor_user_id: int,
    component: str,
    provider: str,
    api_key: str,
    reason: str,
    label: str | None = None,
) -> RotationView:
    """Seal the new key beside the live one. Nothing uses it yet.

    One open rotation per slot: a second stage while one is in flight is
    refused, because two staged keys for one provider is how the wrong one
    gets activated.
    """
    component, provider = platform_credentials.normalise_slot(component, provider)
    reason = _require_reason(reason)
    api_key = platform_credentials.check_key_shape(api_key)
    open_row = await session.scalar(
        select(PlatformCredentialRotationModel).where(
            PlatformCredentialRotationModel.component == component,
            PlatformCredentialRotationModel.provider == provider,
            PlatformCredentialRotationModel.state.in_(OPEN_STATES),
        )
    )
    if open_row is not None:
        raise LifecycleError(
            f"Rotation {open_row.id} for {component}/{provider} is still {open_row.state}. "
            "Finish, revert or cancel it first."
        )
    now = datetime.now(UTC)
    row = PlatformCredentialRotationModel(
        component=component,
        provider=provider,
        environment=constants.ENVIRONMENT,
        state=STAGED,
        staged_encrypted_key=platform_credentials.seal(api_key),
        staged_last_four=api_key[-4:],
        label=(label or "").strip()[:128] or None,
        reason=reason,
        staged_by=actor_user_id,
        staged_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    _audit(session, actor_user_id=actor_user_id, step="staged", row=row)
    return _view(row)


async def validate(
    session: AsyncSession, *, rotation_id: int, actor_user_id: int, validator=None
) -> RotationView:
    """Ask the vendor about the staged key. ``validator`` is
    ``key_validation.validate_key`` unless a test injects one."""
    row = await _load(session, rotation_id)
    _check_step(row, "validate")
    validator = validator or key_validation.validate_key
    try:
        key = platform_credentials.unseal(row.staged_encrypted_key)
    except platform_credentials.PlatformCredentialError:
        row.state = VALIDATION_FAILED
        row.reason_code = "not_configured"
    else:
        result = await validator(row.provider, key)
        if result.outcome == "valid":
            row.state = VALIDATED
            row.reason_code = None
        elif result.outcome == "invalid":
            row.state = VALIDATION_FAILED
            row.reason_code = redaction.reason_code(result.message or "unauthorized")
        else:
            row.state = UNVERIFIED
            row.reason_code = "provider_unavailable"
    row.validated_at = datetime.now(UTC)
    row.updated_at = row.validated_at
    _audit(session, actor_user_id=actor_user_id, step="validated", row=row)
    await session.flush()
    return _view(row)


async def activate(
    session: AsyncSession, *, rotation_id: int, actor_user_id: int, force: bool = False
) -> RotationView:
    """Make the staged key the live one, keeping the old ciphertext for
    revert. An unverified key needs ``force``: the operator is choosing to
    go live on a key nobody could check."""
    row = await _load(session, rotation_id)
    _check_step(row, "activate")
    if row.state == UNVERIFIED and not force:
        raise LifecycleError(
            "The provider could not be asked about this key. Activate with force "
            "only if you have checked it another way."
        )
    live = await _live_row(session, row.component, row.provider)
    if live is None:
        live = PlatformProviderCredentialModel(
            component=row.component, provider=row.provider
        )
        session.add(live)
        row.previous_encrypted_key = None
        row.previous_last_four = None
    else:
        row.previous_encrypted_key = live.encrypted_key
        row.previous_last_four = live.key_last_four
    live.encrypted_key = row.staged_encrypted_key
    live.key_last_four = row.staged_last_four
    live.label = row.label or live.label
    live.is_active = True
    live.set_by = actor_user_id
    # What the vendor said about the staged key is now what we know about
    # the live one; unverified stays unknown rather than becoming good.
    live.last_check_ok = True if row.state == VALIDATED else None
    live.last_checked_at = row.validated_at
    live.last_check_error = None
    row.state = ACTIVE
    row.activated_at = datetime.now(UTC)
    row.updated_at = row.activated_at
    _audit(session, actor_user_id=actor_user_id, step="activated", row=row)
    await session.flush()
    return _view(row)


async def refresh_consumers(
    session: AsyncSession,
    *,
    rotation_id: int,
    actor_user_id: int,
    store=None,
    broadcast=None,
) -> RotationView:
    """Tell every worker, and mirror to the secret store. ``broadcast`` and
    ``store`` are injectable for tests."""
    row = await _load(session, rotation_id)
    _check_step(row, "refresh")
    store = store or secret_store.get_store()
    if store.backend != "database":
        try:
            key = platform_credentials.unseal(row.staged_encrypted_key)
            await store.store(row.component, row.provider, key)
        except (
            secret_store.SecretStoreError,
            platform_credentials.PlatformCredentialError,
        ) as exc:
            row.reason_code = redaction.reason_code(exc)
            _audit(session, actor_user_id=actor_user_id, step="refresh_failed", row=row)
            await session.flush()
            raise LifecycleError(
                "The key is live in Decibyl but could not be mirrored to the secret "
                "store. Fix the store's access and refresh again."
            ) from None
    await (broadcast or _broadcast_managed_tiers)()
    row.state = REFRESHED
    row.consumers_refreshed_at = datetime.now(UTC)
    row.updated_at = row.consumers_refreshed_at
    _audit(session, actor_user_id=actor_user_id, step="refreshed", row=row)
    await session.flush()
    return _view(row)


async def _broadcast_managed_tiers() -> None:
    from api.services.configuration.managed_tiers import (
        refresh_overrides as refresh_managed_tier_overrides,
    )
    from api.services.worker_sync.manager import get_worker_sync_manager
    from api.services.worker_sync.protocol import WorkerSyncEventType

    await refresh_managed_tier_overrides()
    try:
        manager = get_worker_sync_manager()
    except RuntimeError:
        logger.warning(
            "Provider key activated with no worker sync manager; other workers "
            "read the key per call and pick up tier availability on restart"
        )
        return
    await manager.broadcast(WorkerSyncEventType.MANAGED_TIERS, "update")


async def verify(
    session: AsyncSession, *, rotation_id: int, actor_user_id: int, validator=None
) -> RotationView:
    """Check the live key now that it is live. A rejection here leaves the
    rotation where it was, with the reason, and revert still available."""
    row = await _load(session, rotation_id)
    _check_step(row, "verify")
    validator = validator or key_validation.validate_key
    key = await platform_credentials.resolve_api_key(
        session, component=row.component, provider=row.provider
    )
    if not key:
        row.reason_code = "not_configured"
        await session.flush()
        raise LifecycleError(
            "No live key could be read for this slot; revert or re-stage."
        )
    if key[-4:] != row.staged_last_four:
        raise LifecycleError(
            "The live key is not the one this rotation activated; someone changed it since."
        )
    result = await validator(row.provider, key)
    live = await _live_row(session, row.component, row.provider)
    if result.outcome == "invalid":
        row.reason_code = redaction.reason_code(result.message or "unauthorized")
        if live is not None:
            live.last_check_ok = False
            live.last_checked_at = datetime.now(UTC)
            live.last_check_error = row.reason_code
        _audit(session, actor_user_id=actor_user_id, step="verify_failed", row=row)
        await session.flush()
        raise LifecycleError(
            "The provider rejects the live key. Revert to the previous key."
        )
    if result.outcome != "valid":
        row.reason_code = "provider_unavailable"
        await session.flush()
        raise LifecycleError("The provider could not be asked; verify again shortly.")
    if live is not None:
        live.last_check_ok = True
        live.last_checked_at = datetime.now(UTC)
        live.last_check_error = None
    row.state = VERIFIED
    row.reason_code = None
    row.verified_at = datetime.now(UTC)
    row.updated_at = row.verified_at
    _audit(session, actor_user_id=actor_user_id, step="verified", row=row)
    await session.flush()
    return _view(row)


async def revoke(
    session: AsyncSession, *, rotation_id: int, actor_user_id: int, reason: str
) -> RotationView:
    """Destroy the old ciphertext. After this, revert is impossible."""
    reason = _require_reason(reason)
    row = await _load(session, rotation_id)
    _check_step(row, "revoke")
    row.previous_encrypted_key = None
    row.staged_encrypted_key = None  # the live table holds it now
    row.state = REVOKED
    row.revoked_at = datetime.now(UTC)
    row.updated_at = row.revoked_at
    _audit(session, actor_user_id=actor_user_id, step="revoked", row=row, reason=reason)
    await session.flush()
    return _view(row)


async def revert(
    session: AsyncSession,
    *,
    rotation_id: int,
    actor_user_id: int,
    reason: str,
    broadcast=None,
) -> RotationView:
    """Put the previous key back. Possible only while it is still held."""
    reason = _require_reason(reason)
    row = await _load(session, rotation_id)
    _check_step(row, "revert")
    live = await _live_row(session, row.component, row.provider)
    if live is None:
        raise LifecycleError("The slot has no live row to revert.")
    if row.previous_encrypted_key:
        live.encrypted_key = row.previous_encrypted_key
        live.key_last_four = row.previous_last_four or live.key_last_four
        live.last_check_ok = None
        live.last_checked_at = None
        live.last_check_error = None
    else:
        # There was no key before this rotation: reverting takes the slot
        # out of service rather than leaving the rejected key live.
        live.is_active = False
    row.state = REVERTED
    row.reverted_at = datetime.now(UTC)
    row.updated_at = row.reverted_at
    _audit(
        session, actor_user_id=actor_user_id, step="reverted", row=row, reason=reason
    )
    await session.flush()
    await (broadcast or _broadcast_managed_tiers)()
    return _view(row)


async def cancel(
    session: AsyncSession, *, rotation_id: int, actor_user_id: int, reason: str
) -> RotationView:
    reason = _require_reason(reason)
    row = await _load(session, rotation_id)
    _check_step(row, "cancel")
    row.staged_encrypted_key = None
    row.state = CANCELLED
    row.updated_at = datetime.now(UTC)
    _audit(
        session, actor_user_id=actor_user_id, step="cancelled", row=row, reason=reason
    )
    await session.flush()
    return _view(row)


async def get(session: AsyncSession, rotation_id: int) -> RotationView:
    row = await session.get(PlatformCredentialRotationModel, rotation_id)
    if row is None:
        raise LookupError(rotation_id)
    return _view(row)


async def list_rotations(
    session: AsyncSession, *, limit: int = 50
) -> list[RotationView]:
    rows = (
        await session.scalars(
            select(PlatformCredentialRotationModel)
            .order_by(PlatformCredentialRotationModel.updated_at.desc())
            .limit(max(1, min(limit, 200)))
        )
    ).all()
    return [_view(row) for row in rows]


async def provider_keys(session: AsyncSession) -> list[dict[str, Any]]:
    """The provider keys screen's rows (screen 42): provider, environment,
    masked identifier, owner, validation time, last change and health --
    never the key."""
    keys = await platform_credentials.list_credentials(session)
    open_rows = {
        (r.component, r.provider): r
        for r in (
            await session.scalars(
                select(PlatformCredentialRotationModel).where(
                    PlatformCredentialRotationModel.state.in_(OPEN_STATES)
                )
            )
        ).all()
    }
    owners = {
        (r.component, r.provider): r.set_by
        for r in (await session.scalars(select(PlatformProviderCredentialModel))).all()
    }
    out = []
    for key in keys:
        if not key.is_active:
            health = "disabled"
        elif key.last_check_ok is True:
            health = "valid"
        elif key.last_check_ok is False:
            health = "rejected"
        else:
            health = "unchecked"
        rotation = open_rows.get((key.component, key.provider))
        out.append(
            {
                "component": key.component,
                "provider": key.provider,
                "environment": constants.ENVIRONMENT,
                "masked_key": key.masked_key,
                "label": key.label,
                "owner_user_id": owners.get((key.component, key.provider)),
                "health": health,
                "validated_at": key.last_checked_at,
                "last_changed_at": key.updated_at,
                "last_check_reason": (
                    redaction.reason_code(key.last_check_error)
                    if key.last_check_error
                    else None
                ),
                "open_rotation": rotation.id if rotation is not None else None,
                "open_rotation_state": rotation.state if rotation is not None else None,
            }
        )
    return out
