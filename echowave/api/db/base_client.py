from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.constants import (
    DATABASE_URL,
    DB_POOL_MAX_OVERFLOW,
    DB_POOL_SIZE,
    DB_POOL_TIMEOUT,
)


class BaseDBClient:
    """Shared async engine and session factory.

    **The pool is sized explicitly.** SQLAlchemy's defaults are 5 connections
    with 10 overflow — fifteen in total — which is invisible until concurrency
    passes it and then fails in the worst possible way: calls do not error, they
    *queue*, waiting on a connection while a live caller listens to silence. The
    symptom is latency, so it reads as a slow model rather than an exhausted
    pool, and the dashboard blames the wrong component.

    Every in-flight call touches the database several times — the balance
    reservation, the run row, turn metrics, the disposition — so the pool has to
    be sized against peak concurrent calls, not against request rate.
    """

    def __init__(self):
        self.engine = create_async_engine(
            DATABASE_URL,
            pool_size=DB_POOL_SIZE,
            max_overflow=DB_POOL_MAX_OVERFLOW,
            pool_timeout=DB_POOL_TIMEOUT,
            # Recycle before any proxy or Postgres idle timeout can close a
            # connection under us; a stale connection surfaces as a random
            # mid-call failure that is very hard to attribute.
            pool_recycle=1800,
            pool_pre_ping=True,
        )
        self.async_session = async_sessionmaker(bind=self.engine)

    async def execute_raw_query(
        self, query: str, params: dict[str, Any] = None
    ) -> list[dict[str, Any]]:
        """
        Execute a raw SQL query and return results as a list of dictionaries.

        Args:
            query: The SQL query to execute
            params: Optional dictionary of query parameters

        Returns:
            List of dictionaries containing the query results
        """
        async with self.async_session() as session:
            result = await session.execute(text(query), params or {})
            rows = result.fetchall()
            if rows:
                # Convert rows to dictionaries
                columns = result.keys()
                return [dict(zip(columns, row)) for row in rows]
            return []
