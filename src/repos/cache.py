"""TTL cache for provider responses, kept in SQLite.

Book metadata barely changes, so caching detail lookups for a week keeps the
bot comfortably inside the provider's request budget and makes repeat
lookups of a popular book free.
"""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite


class _Miss:
    """Sentinel for 'not in the cache'.

    Distinct from a cached ``None``, which means 'we looked this up and the
    provider had nothing' -- worth remembering so a bad ISBN is not retried
    on every invocation.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<cache MISS>"

    def __bool__(self) -> bool:
        return False


MISS = _Miss()


async def get(conn: aiosqlite.Connection, key: str) -> Any:
    """Return the cached value, or ``MISS`` if the key is absent or expired."""
    async with conn.execute(
        "SELECT payload FROM provider_cache WHERE key = ? AND expires_at > ?",
        (key, time.time()),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return MISS
    try:
        return json.loads(row["payload"])
    except json.JSONDecodeError:
        return MISS


async def set(conn: aiosqlite.Connection, key: str, payload: Any, ttl: int) -> None:
    await conn.execute(
        """
        INSERT INTO provider_cache (key, payload, expires_at) VALUES (?, ?, ?)
        ON CONFLICT (key) DO UPDATE SET
            payload = excluded.payload, expires_at = excluded.expires_at
        """,
        (key, json.dumps(payload), time.time() + ttl),
    )


async def purge_expired(conn: aiosqlite.Connection) -> int:
    async with conn.execute(
        "DELETE FROM provider_cache WHERE expires_at <= ?", (time.time(),)
    ) as cursor:
        return cursor.rowcount or 0
