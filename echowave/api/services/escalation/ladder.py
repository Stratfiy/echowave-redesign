"""Ringing people, in order, while the caller waits -- and never in silence.

The first rungs of the no-answer ladder: each person is rung with a
per-attempt timeout (25 s by default), the whole wait is capped (75 s), and
the caller hears a short spoken update every ~25 s rather than only music.
What happens after the last person -- back to the agent with an honest
explanation, then callback, message, voicemail -- is ``fallbacks``.

**A machine is never bridged.** Where the carrier detects one (Plivo's
``machine_detection``, Twilio's ``AnsweredBy``), the outcome is ``machine``,
that leg is dropped and the next person is rung. A caller who asked for a
person and is put through to a voicemail greeting has been abandoned.

**Each person is rung at most once per escalation**, however many times this
runs: every dial is claimed through ``record.claim_attempt`` first, and a
refused claim rings nobody.

Carrier-free on purpose: the carrier is a ``Dialer`` and the caller is a
``CallerLine``, so every branch here runs in a test with fakes.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from loguru import logger

from api.services.escalation.policy import EscalationPolicy, TransferTarget
from api.utils.phone_masking import last_four

#: What one ring came to.
HUMAN = "human"
MACHINE = "machine"
NO_ANSWER = "no_answer"
BUSY = "busy"
DECLINED = "declined"
FAILED = "failed"
TIMEOUT = "timeout"
SKIPPED = "skipped"  # somebody else already dialled this attempt


class Dialer(Protocol):
    async def dial(
        self,
        target: TransferTarget,
        *,
        transfer_id: str,
        ring_timeout: int,
        briefing: str,
    ) -> None: ...

    def watch(self, transfer_id: str, timeout: float) -> "asyncio.Task[str]":
        """Start listening for this leg's outcome. Called before ``dial`` so
        an outcome that comes back at once is not missed."""
        ...

    async def drop(self, transfer_id: str) -> None:
        """Hang up a leg that will not be bridged, and forget it."""
        ...


class CallerLine(Protocol):
    async def start_hold(self) -> None: ...

    async def stop_hold(self) -> None: ...

    async def update(self, seconds_waited: int) -> None:
        """Tell the caller they are still being connected."""
        ...


Claim = Callable[[int, str, str], Awaitable[bool]]


@dataclass
class Attempt:
    n: int
    target: str
    transfer_id: str
    outcome: str
    seconds: float


@dataclass
class LadderResult:
    bridged: bool
    attempts: list[Attempt] = field(default_factory=list)
    #: The successful attempt's transfer id.
    transfer_id: str | None = None
    target: TransferTarget | None = None
    failure_reason: str | None = None
    waited_seconds: float = 0.0
    updates_spoken: int = 0


def masked(target: TransferTarget) -> str:
    return f"{target.name + ' ' if target.name else ''}{last_four(target.number)}"


async def run(
    *,
    targets: list[TransferTarget],
    policy: EscalationPolicy,
    dialer: Dialer,
    caller: CallerLine,
    claim: Claim,
    briefing: str | Callable[[TransferTarget], str],
    start_count: int = 0,
    clock: Callable[[], float] = time.monotonic,
    on_attempt: Callable[[Attempt], Awaitable[Any]] | None = None,
) -> LadderResult:
    """Ring ``targets`` in order until a person answers or the cap is spent.

    ``claim(expected_count, transfer_id, masked_target)`` must return True
    before a number is dialled; ``start_count`` is how many attempts the
    record already holds (a resumed escalation starts after them).
    ``briefing`` is one line for everybody, or a function of the person
    (each is briefed in their own language).
    """
    result = LadderResult(bridged=False)
    started = clock()
    next_update = policy.hold_update_seconds
    count = start_count

    def waited() -> float:
        return clock() - started

    await caller.start_hold()
    try:
        for target in targets:
            remaining = policy.hold_cap_seconds - waited()
            if remaining < 1:
                result.failure_reason = "hold_cap"
                break
            ring = int(min(policy.ring_timeout_seconds, remaining))
            transfer_id = str(uuid.uuid4())
            label = masked(target)
            if not await claim(count, transfer_id, label):
                logger.info(
                    "Escalation attempt {} already dialled elsewhere; skipping",
                    count + 1,
                )
                result.attempts.append(
                    Attempt(count + 1, label, transfer_id, SKIPPED, 0)
                )
                count += 1
                continue
            count += 1
            ring_started = clock()
            watcher = dialer.watch(transfer_id, ring + 2)
            try:
                await dialer.dial(
                    target,
                    transfer_id=transfer_id,
                    ring_timeout=ring,
                    briefing=briefing(target) if callable(briefing) else briefing,
                )
            except Exception as exc:  # noqa: BLE001 - next person, not a crash
                logger.warning("Dialling {} failed: {}", label, exc)
                watcher.cancel()
                attempt = Attempt(count, label, transfer_id, FAILED, 0)
                result.attempts.append(attempt)
                if on_attempt:
                    await on_attempt(attempt)
                continue

            deadline = ring_started + ring
            outcome: str | None = None
            while outcome is None:
                now = clock()
                if now >= deadline:
                    break
                until_update = next_update - waited()
                chunk = max(0.05, min(deadline - now, until_update))
                done, _ = await asyncio.wait({watcher}, timeout=chunk)
                if done:
                    try:
                        outcome = watcher.result() or TIMEOUT
                    except Exception:  # noqa: BLE001
                        outcome = FAILED
                    break
                if waited() >= next_update:
                    await caller.update(int(waited()))
                    result.updates_spoken += 1
                    next_update += policy.hold_update_seconds
            if outcome is None:
                watcher.cancel()
                outcome = TIMEOUT

            attempt = Attempt(
                count, label, transfer_id, outcome, clock() - ring_started
            )
            result.attempts.append(attempt)
            if on_attempt:
                await on_attempt(attempt)
            if outcome == HUMAN:
                result.bridged = True
                result.transfer_id = transfer_id
                result.target = target
                return result
            # Anything else -- machine, no answer, busy, declined -- is a leg
            # that must not be bridged. Drop it so a late answer does not land
            # a person in an empty conference, then try the next one.
            await dialer.drop(transfer_id)
            result.failure_reason = outcome
        else:
            if not targets:
                result.failure_reason = "no_one_to_ring"
        return result
    finally:
        result.waited_seconds = waited()
        await caller.stop_hold()


__all__ = [
    "Attempt",
    "BUSY",
    "CallerLine",
    "DECLINED",
    "Dialer",
    "FAILED",
    "HUMAN",
    "LadderResult",
    "MACHINE",
    "NO_ANSWER",
    "SKIPPED",
    "TIMEOUT",
    "masked",
    "run",
]
