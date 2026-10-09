"""One listener's connection: Redis in, the browser out, nothing piled up.

Two halves joined by two bounded queues. The reader takes everything off
the call's channels as fast as Redis gives it; the writer sends to the
browser as fast as the browser takes it. When the browser is slower than
the call, audio that does not fit is dropped (a gap in what the listener
hears, never a growing delay or a growing buffer), and the oldest
transcript event makes room for the newest -- the backlog on reconnect
has every final line anyway.

Receive-only by construction: nothing the browser sends is read except
the close.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from api.services.live_supervision import channels

AUDIO_BUFFER = 25  # packets, each up to 200 ms: about five seconds at most
EVENT_BUFFER = 500
#: How often the reader checks that the call is still beating, in case its
#: worker died without saying goodbye.
LIVENESS_SECONDS = 10.0


class ListenerPump:
    def __init__(
        self,
        run_id: int,
        *,
        send_text: Callable[[str], Awaitable[Any]],
        send_bytes: Callable[[bytes], Awaitable[Any]],
        audio: bool,
        redis: Any = None,
    ):
        self.run_id = run_id
        self._send_text = send_text
        self._send_bytes = send_bytes
        self.audio = audio
        self._redis = redis
        self.audio_queue: asyncio.Queue = asyncio.Queue(maxsize=AUDIO_BUFFER)
        self.event_queue: asyncio.Queue = asyncio.Queue(maxsize=EVENT_BUFFER)
        self.dropped_audio = 0
        self.dropped_events = 0
        self.ended = asyncio.Event()
        self._wake = asyncio.Event()
        self._pubsub = None

    def redis(self):
        return self._redis if self._redis is not None else channels.redis()

    async def subscribe(self) -> None:
        self._pubsub = self.redis().pubsub()
        names = [channels.events_channel(self.run_id)]
        if self.audio:
            names.append(channels.audio_channel(self.run_id))
        await self._pubsub.subscribe(*names)

    async def close(self) -> None:
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe()
                await self._pubsub.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Listener unsubscribe failed: {}", exc)
            self._pubsub = None

    def offer_audio(self, packet: bytes) -> None:
        try:
            self.audio_queue.put_nowait(packet)
        except asyncio.QueueFull:
            self.dropped_audio += 1
        self._wake.set()

    def offer_event(self, body: str) -> None:
        try:
            self.event_queue.put_nowait(body)
        except asyncio.QueueFull:
            self.dropped_events += 1
            try:
                self.event_queue.get_nowait()
                self.event_queue.put_nowait(body)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
        self._wake.set()

    def finish(self) -> None:
        self.ended.set()
        self._wake.set()

    async def read(self) -> None:
        """Redis to the queues, until the call ends."""
        audio_name = channels.audio_channel(self.run_id).encode()
        loop = asyncio.get_running_loop()
        next_check = loop.time() + LIVENESS_SECONDS
        while not self.ended.is_set():
            message = await self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if loop.time() >= next_check:
                next_check = loop.time() + LIVENESS_SECONDS
                if not await self.redis().exists(channels.meta_key(self.run_id)):
                    self.offer_event(json.dumps({"type": "ended", "seq": None}))
                    self.finish()
                    return
            if not message or message.get("type") != "message":
                continue
            data = message["data"]
            channel = message.get("channel")
            if channel == audio_name:
                self.offer_audio(data)
                continue
            body = data.decode() if isinstance(data, bytes) else str(data)
            self.offer_event(body)
            if '"type": "ended"' in body:
                self.finish()

    async def write(self) -> None:
        """The queues to the browser. Events first: words matter more."""
        while True:
            if not self.event_queue.empty():
                await self._send_text(self.event_queue.get_nowait())
                continue
            if not self.audio_queue.empty():
                await self._send_bytes(self.audio_queue.get_nowait())
                continue
            if self.ended.is_set():
                return
            self._wake.clear()
            await self._wake.wait()
