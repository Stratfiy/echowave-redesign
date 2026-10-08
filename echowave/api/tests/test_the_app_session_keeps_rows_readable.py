"""The app's own session factory keeps a committed row readable.

Seen in production on 8 October 2026: "Export the top 30 to Excel" failed
five times in a row with MissingGreenlet, and every agent event logged
"Could not record agent event". Both read a row's id after committing it.
With SQLAlchemy's default ``expire_on_commit=True`` that read is a lazy load
asyncio cannot do. Every test passed, because the test session in
conftest.py has always used ``expire_on_commit=False``; this pins the
production factory to the same setting so the two cannot drift again.
"""

from api.db.base_client import BaseDBClient


def test_the_production_session_factory_does_not_expire_on_commit():
    # A fresh client, not the shared ``db_client``: the db_session fixture
    # swaps the shared one's factory for the test session's during a run.
    assert BaseDBClient().async_session.kw.get("expire_on_commit") is False
