"""A small in-memory stand-in for ``redis.asyncio`` in unit tests.

Only what the ops services call: strings, hashes, a pipeline and a scan.
Values are stored as bytes, as the real client returns them.
"""

from __future__ import annotations

import fnmatch


def _b(value) -> bytes:
    if isinstance(value, bytes):
        return value
    return str(value).encode()


class _Pipeline:
    def __init__(self, owner: "FakeRedis"):
        self._owner = owner
        self._ops: list = []

    def hincrby(self, key, field, amount=1):
        self._ops.append(("hincrby", key, field, amount))
        return self

    def expire(self, key, seconds):
        self._ops.append(("expire", key, seconds))
        return self

    async def execute(self):
        out = []
        for op in self._ops:
            if op[0] == "hincrby":
                out.append(await self._owner.hincrby(op[1], op[2], op[3]))
            else:
                out.append(True)
        self._ops.clear()
        return out


class FakeRedis:
    def __init__(self, *, fail: bool = False):
        self.strings: dict[str, bytes] = {}
        self.hashes: dict[str, dict[bytes, bytes]] = {}
        self.ttl: dict[str, int | None] = {}
        self.fail = fail
        self.closed = 0

    def _check(self):
        if self.fail:
            raise ConnectionError("fake redis is down")

    async def get(self, key):
        self._check()
        return self.strings.get(key)

    async def mget(self, keys):
        self._check()
        return [self.strings.get(k) for k in keys]

    async def set(self, key, value, ex=None):
        self._check()
        self.strings[key] = _b(value)
        self.ttl[key] = ex
        return True

    async def delete(self, *keys):
        self._check()
        removed = 0
        for key in keys:
            removed += int(self.strings.pop(key, None) is not None)
        return removed

    async def scan_iter(self, match="*"):
        self._check()
        for key in list(self.strings):
            if fnmatch.fnmatch(key, match):
                yield key

    async def hincrby(self, key, field, amount=1):
        self._check()
        bucket = self.hashes.setdefault(key, {})
        value = int(bucket.get(_b(field), b"0")) + amount
        bucket[_b(field)] = _b(value)
        return value

    async def hgetall(self, key):
        self._check()
        return dict(self.hashes.get(key, {}))

    def pipeline(self):
        self._check()
        return _Pipeline(self)

    async def aclose(self):
        self.closed += 1
