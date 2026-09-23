"""The nightly import: list yesterday's calls, keep the ones a person
handled and recorded, copy the audio across, transcribe it.

Idempotent by ``(organization, vendor, call id)``: a call already
transcribed is skipped, and one that failed is tried again on its own row,
so a run that dies half way is simply run again. The nightly window is the
last two Indian days, which gives every failed call one retry. One failed call
fails that call, not the night; a refused credential fails the connection,
and the connection says so in words the business can act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import DialerConnectionModel, ImportedCallModel
from api.services.dialer_import import connections
from api.services.dialer_import._base import (
    DialerAuthError,
    DialerCall,
    DialerError,
    last_four,
)

#: How long an imported call is kept. Enough for a weekly board and a month
#: of notes to look back on; the dialer keeps its own copy for as long as
#: the business pays it to.
RETENTION_DAYS = 30
#: Shorter than this is a missed connection or a wrong number, with nothing
#: to coach.
MIN_SECONDS = 20
#: A ceiling on one connection's night, so a misconfigured filter cannot
#: transcribe a month of calls at once.
MAX_CALLS_PER_RUN = 300


@dataclass
class RunResult:
    listed: int = 0
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def worth_importing(call: DialerCall) -> bool:
    return (
        call.answered
        and bool(call.recording_url)
        and call.duration_seconds >= MIN_SECONDS
        and bool(call.external_id)
    )


def recording_key(organization_id: int, connection_id: int, external_id: str) -> str:
    safe = "".join(ch for ch in external_id if ch.isalnum() or ch in "-_.")[:120]
    return f"dialer-recordings/{organization_id}/{connection_id}/{safe}.mp3"


async def import_window(
    session: AsyncSession,
    connection: DialerConnectionModel,
    since: datetime,
    until: datetime,
    *,
    adapter=None,
    storage=None,
    transcriber=None,
    now: datetime | None = None,
) -> RunResult:
    """Import one connection's calls between ``since`` and ``until``.

    ``adapter``, ``storage`` and ``transcriber`` default to the real ones;
    tests pass their own so no network is touched.
    """
    now = now or datetime.now(timezone.utc)
    adapter = adapter or connections.adapter_for(connection.vendor)
    if storage is None:
        from api.services.storage import storage_fs as storage
    result = RunResult()
    credentials = connections.credentials_of(connection)

    try:
        calls = await adapter.list_calls(credentials, since, until)
    except DialerAuthError as exc:
        _mark(connection, now, error=exc.message)
        result.errors.append(exc.message)
        return result
    except DialerError as exc:
        _mark(connection, now, error=exc.message, attention=False)
        result.errors.append(exc.message)
        return result

    result.listed = len(calls)
    wanted = [c for c in calls if worth_importing(c)][:MAX_CALLS_PER_RUN]
    result.skipped = len(calls) - len(wanted)
    existing = {
        row.external_id: row
        for row in await session.scalars(
            select(ImportedCallModel).where(
                ImportedCallModel.organization_id == connection.organization_id,
                ImportedCallModel.vendor == connection.vendor,
                ImportedCallModel.external_id.in_([c.external_id for c in wanted]),
            )
        )
    }

    for call in wanted:
        row = existing.get(call.external_id)
        if row is not None and row.status == "transcribed":
            result.skipped += 1
            continue
        # A call that failed last night is tried again on the same row.
        row = row or ImportedCallModel(
            organization_id=connection.organization_id,
            connection_id=connection.id,
            vendor=connection.vendor,
            external_id=call.external_id,
            started_at=call.started_at,
            duration_seconds=call.duration_seconds,
            direction=call.direction,
            agent_name=call.agent_name,
            agent_number=call.agent_number,
            customer_last_four=last_four(call.customer_number),
            status="pending",
            expires_at=now + timedelta(days=RETENTION_DAYS),
        )
        row.error = None
        session.add(row)
        try:
            audio = await adapter.fetch_recording(credentials, call)
            key = recording_key(
                connection.organization_id, connection.id, call.external_id
            )
            if not await storage.acreate_file_from_bytes(key, audio):
                raise DialerError("The recording could not be stored.")
            row.recording_key = key
            if transcriber is None:  # built once, and only if a call needs it
                transcriber = await _transcriber(connection.organization_id)
            transcription = await transcriber.transcribe(
                audio, filename="call.mp3", content_type="audio/mpeg", language=""
            )
            row.transcript = transcription.transcript
            row.transcription_model = transcription.model
            row.status = "transcribed"
            result.imported += 1
        except DialerAuthError as exc:
            row.status, row.error = "failed", exc.message
            _mark(connection, now, error=exc.message)
            result.failed += 1
            result.errors.append(exc.message)
            break
        except Exception as exc:  # one call's failure is not the night's
            message = getattr(exc, "user_message", None) or getattr(
                exc, "message", None
            )
            row.status, row.error = "failed", message or "Could not import this call."
            result.failed += 1
            logger.warning(
                "dialer import: call {} on connection {} failed: {}",
                call.external_id,
                connection.id,
                exc,
            )
        await session.flush()

    if connection.status != connections.NEEDS_ATTENTION or not result.errors:
        _mark(connection, now)
    return result


def _mark(
    connection: DialerConnectionModel,
    now: datetime,
    *,
    error: str | None = None,
    attention: bool = True,
) -> None:
    if error is None:
        connection.status = connections.CONNECTED
        connection.last_error = None
        connection.last_synced_at = now
    else:
        if attention:
            connection.status = connections.NEEDS_ATTENTION
        connection.last_error = error


async def _transcriber(organization_id: int):
    from api.services.gen_ai.transcription import build_transcription_service

    return await build_transcription_service(
        organization_id=organization_id, created_by="dialer_import"
    )


async def delete_recordings(keys: list[str], *, storage=None) -> None:
    if storage is None:
        from api.services.storage import storage_fs as storage
    for key in keys:
        try:
            await storage.adelete_file(key)
        except Exception as exc:  # a stray file is found by the next purge
            logger.warning("dialer import: could not delete {}: {}", key, exc)


async def purge_expired(
    session: AsyncSession, *, now: datetime | None = None, storage=None
) -> int:
    """Delete imported calls past ``expires_at``, recording first."""
    now = now or datetime.now(timezone.utc)
    rows = (
        await session.execute(
            select(ImportedCallModel.id, ImportedCallModel.recording_key).where(
                ImportedCallModel.expires_at <= now
            )
        )
    ).all()
    if not rows:
        return 0
    await delete_recordings([k for _, k in rows if k], storage=storage)
    await session.execute(
        delete(ImportedCallModel).where(
            ImportedCallModel.id.in_([row_id for row_id, _ in rows])
        )
    )
    return len(rows)


async def calls_for(
    session: AsyncSession,
    *,
    organization_id: int,
    since: datetime,
    until: datetime | None = None,
) -> list[ImportedCallModel]:
    """Transcribed calls in a window, for the coach to read."""
    query = select(ImportedCallModel).where(
        ImportedCallModel.organization_id == organization_id,
        ImportedCallModel.status == "transcribed",
        ImportedCallModel.started_at >= since,
    )
    if until is not None:
        query = query.where(ImportedCallModel.started_at <= until)
    return list(await session.scalars(query.order_by(ImportedCallModel.started_at)))
