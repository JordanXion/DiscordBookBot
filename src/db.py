"""SQLite connection handling and schema migrations.

The bot owns its own canonical book records. Provider ids live in a side
table so the data survives adding or swapping a metadata provider.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

log = logging.getLogger(__name__)

# Each entry is one forward migration, applied in order. PRAGMA user_version
# tracks how many have run.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE books (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        isbn13          TEXT UNIQUE,
        title           TEXT NOT NULL,
        authors         TEXT NOT NULL DEFAULT '[]',
        description     TEXT,
        release_year    INTEGER,
        pages           INTEGER,
        cover_url       TEXT,
        average_rating  REAL,
        ratings_count   INTEGER,
        tags            TEXT NOT NULL DEFAULT '[]',
        source_provider TEXT NOT NULL,
        source_id       TEXT NOT NULL,
        source_url      TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE book_external_ids (
        provider    TEXT NOT NULL,
        external_id TEXT NOT NULL,
        book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        PRIMARY KEY (provider, external_id)
    );
    CREATE INDEX idx_external_book ON book_external_ids(book_id);

    CREATE TABLE user_books (
        guild_id    INTEGER NOT NULL,
        user_id     INTEGER NOT NULL,
        book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        status      TEXT NOT NULL CHECK (status IN ('reading', 'finished')),
        rating      REAL CHECK (rating IS NULL OR (rating >= 0.5 AND rating <= 5.0)),
        started_at  TEXT,
        finished_at TEXT,
        updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (guild_id, user_id, book_id)
    );
    CREATE INDEX idx_user_books_guild_status ON user_books(guild_id, status);
    CREATE INDEX idx_user_books_book ON user_books(guild_id, book_id);

    CREATE TABLE provider_cache (
        key        TEXT PRIMARY KEY,
        payload    TEXT NOT NULL,
        expires_at REAL NOT NULL
    );
    CREATE INDEX idx_cache_expiry ON provider_cache(expires_at);
    """,
    """
    -- SQLite cannot alter a CHECK constraint in place. Rebuild this small
    -- table to introduce the want-to-read shelf state while preserving every
    -- existing reading/finished entry.
    CREATE TABLE user_books_new (
        guild_id    INTEGER NOT NULL,
        user_id     INTEGER NOT NULL,
        book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        status      TEXT NOT NULL
                    CHECK (status IN ('want_to_read', 'reading', 'finished')),
        rating      REAL CHECK (rating IS NULL OR (rating >= 0.5 AND rating <= 5.0)),
        started_at  TEXT,
        finished_at TEXT,
        updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (guild_id, user_id, book_id)
    );
    INSERT INTO user_books_new
        SELECT guild_id, user_id, book_id, status, rating, started_at,
               finished_at, updated_at
        FROM user_books;
    DROP TABLE user_books;
    ALTER TABLE user_books_new RENAME TO user_books;
    CREATE INDEX idx_user_books_guild_status ON user_books(guild_id, status);
    CREATE INDEX idx_user_books_book ON user_books(guild_id, book_id);
    """,
]


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        # Done here rather than in connect() so the blocking filesystem call
        # stays out of the event loop.
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        # WAL keeps the nightly backup from blocking writers; NORMAL sync is
        # the right durability tradeoff for a bot on a single small VM.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.commit()
        await self._migrate()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() has not been called")
        return self._conn

    async def _migrate(self) -> None:
        async with self.conn.execute("PRAGMA user_version") as cursor:
            row = await cursor.fetchone()
        version = int(row[0]) if row else 0

        for index, script in enumerate(MIGRATIONS[version:], start=version):
            log.info("applying migration %d", index + 1)
            await self.conn.executescript(script)
            # executescript commits and cannot take a bound parameter here.
            await self.conn.execute(f"PRAGMA user_version = {index + 1}")
            await self.conn.commit()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        try:
            yield self.conn
        except Exception:
            await self.conn.rollback()
            raise
        else:
            await self.conn.commit()
