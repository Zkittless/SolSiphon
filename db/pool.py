"""
Database connection pool and shared helpers.

All writes to `audit_log` should go through `write_audit`, and always within
the same transaction as the state change it's recording -- so the log can
never drift out of sync with the tables it's describing.
"""

import json
import os

import asyncpg

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=os.environ["DATABASE_URL"],
            min_size=1,
            max_size=10,
        )
    return _pool


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Pool not initialized -- call init_pool() first")
    return _pool


async def write_audit(
    conn: asyncpg.Connection,
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    actor: str,
    metadata: dict | None = None,
) -> None:
    """
    Write one audit_log row. MUST be called with the same `conn` (and inside
    the same transaction) as the state change it documents -- pass the
    connection you're already holding, don't grab a new one from the pool.
    """
    await conn.execute(
        """
        INSERT INTO audit_log (entity_type, entity_id, action, actor, metadata)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
        entity_type,
        entity_id,
        action,
        actor,
        json.dumps(metadata or {}),
    )
