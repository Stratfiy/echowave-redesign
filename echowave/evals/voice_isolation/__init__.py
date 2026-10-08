"""Offline evaluation of the inbound voice-isolation options. See README.md."""

import os

# The filters and the lock are imported from the API, whose constants module
# insists on these. Nothing here touches a database or Redis.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused/voice_isolation_eval")
os.environ.setdefault("REDIS_URL", "redis://unused:6379/0")
