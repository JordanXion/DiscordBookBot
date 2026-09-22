"""Per-guild reading status and ratings.

Everything is scoped by guild_id so one bot instance can serve many servers
without leaking one server's shelf into another.
"""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

STATUS_READING = "reading"
STATUS_FINISHED = "finished"
STATUS_WANT_TO_READ = "want_to_read"


@dataclass(slots=True)
class Entry:
    guild_id: int
    user_id: int
    book_id: int
    status: str
    rating: float | None
    started_at: str | None
    finished_at: str | None


@dataclass(slots=True)
class ShelfItem:
    book_id: int
    title: str
    authors: str
    status: str
    rating: float | None
    finished_at: str | None


def _to_entry(row: aiosqlite.Row) -> Entry:
    return Entry(
        guild_id=row["guild_id"],
        user_id=row["user_id"],
        book_id=row["book_id"],
        status=row["status"],
        rating=row["rating"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


async def get(
    conn: aiosqlite.Connection, guild_id: int, user_id: int, book_id: int
) -> Entry | None:
    async with conn.execute(
        "SELECT * FROM user_books WHERE guild_id = ? AND user_id = ? AND book_id = ?",
        (guild_id, user_id, book_id),
    ) as cursor:
        row = await cursor.fetchone()
    return _to_entry(row) if row else None


async def mark_reading(
    conn: aiosqlite.Connection, guild_id: int, user_id: int, book_id: int
) -> Entry:
    await conn.execute(
        """
        INSERT INTO user_books (guild_id, user_id, book_id, status, started_at)
        VALUES (?, ?, ?, 'reading', datetime('now'))
        ON CONFLICT (guild_id, user_id, book_id) DO UPDATE SET
            status = 'reading',
            started_at = COALESCE(user_books.started_at, datetime('now')),
            finished_at = NULL,
            updated_at = datetime('now')
        """,
        (guild_id, user_id, book_id),
    )
    entry = await get(conn, guild_id, user_id, book_id)
    assert entry is not None
    return entry


async def mark_want_to_read(
    conn: aiosqlite.Connection, guild_id: int, user_id: int, book_id: int
) -> Entry:
    """Put a book on a user's want-to-read shelf."""
    await conn.execute(
        """
        INSERT INTO user_books (guild_id, user_id, book_id, status)
        VALUES (?, ?, ?, 'want_to_read')
        ON CONFLICT (guild_id, user_id, book_id) DO UPDATE SET
            status = 'want_to_read',
            started_at = NULL,
            finished_at = NULL,
            updated_at = datetime('now')
        """,
        (guild_id, user_id, book_id),
    )
    entry = await get(conn, guild_id, user_id, book_id)
    assert entry is not None
    return entry


async def mark_unread(
    conn: aiosqlite.Connection, guild_id: int, user_id: int, book_id: int
) -> bool:
    """Remove a book from a user's shelf entirely."""
    cursor = await conn.execute(
        "DELETE FROM user_books WHERE guild_id = ? AND user_id = ? AND book_id = ?",
        (guild_id, user_id, book_id),
    )
    return cursor.rowcount > 0


async def mark_finished(
    conn: aiosqlite.Connection,
    guild_id: int,
    user_id: int,
    book_id: int,
    rating: float | None = None,
) -> Entry:
    """Mark as finished. Works without a prior 'reading' entry, by design --
    people log books they read before the bot existed."""
    await conn.execute(
        """
        INSERT INTO user_books (guild_id, user_id, book_id, status, rating, finished_at)
        VALUES (?, ?, ?, 'finished', ?, datetime('now'))
        ON CONFLICT (guild_id, user_id, book_id) DO UPDATE SET
            status = 'finished',
            rating = COALESCE(excluded.rating, user_books.rating),
            finished_at = COALESCE(user_books.finished_at, datetime('now')),
            updated_at = datetime('now')
        """,
        (guild_id, user_id, book_id, rating),
    )
    entry = await get(conn, guild_id, user_id, book_id)
    assert entry is not None
    return entry


async def set_rating(
    conn: aiosqlite.Connection,
    guild_id: int,
    user_id: int,
    book_id: int,
    rating: float,
) -> tuple[Entry, bool]:
    """Rate a book, marking it finished if it was not already.

    Returns the entry and whether this replaced an earlier rating.
    """
    previous = await get(conn, guild_id, user_id, book_id)
    was_rated = previous is not None and previous.rating is not None
    await conn.execute(
        """
        INSERT INTO user_books (guild_id, user_id, book_id, status, rating, finished_at)
        VALUES (?, ?, ?, 'finished', ?, datetime('now'))
        ON CONFLICT (guild_id, user_id, book_id) DO UPDATE SET
            status = 'finished',
            rating = excluded.rating,
            finished_at = COALESCE(user_books.finished_at, datetime('now')),
            updated_at = datetime('now')
        """,
        (guild_id, user_id, book_id, rating),
    )
    entry = await get(conn, guild_id, user_id, book_id)
    assert entry is not None
    return entry, was_rated


async def shelf(
    conn: aiosqlite.Connection,
    guild_id: int,
    user_id: int,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[ShelfItem]:
    sql = """
        SELECT ub.book_id, b.title, b.authors, ub.status, ub.rating, ub.finished_at
        FROM user_books ub
        JOIN books b ON b.id = ub.book_id
        WHERE ub.guild_id = ? AND ub.user_id = ?
    """
    params: list[object] = [guild_id, user_id]
    if status is not None:
        sql += " AND ub.status = ?"
        params.append(status)
    sql += " ORDER BY COALESCE(ub.finished_at, ub.updated_at) DESC LIMIT ?"
    params.append(limit)

    async with conn.execute(sql, params) as cursor:
        rows = await cursor.fetchall()
    return [
        ShelfItem(
            book_id=row["book_id"],
            title=row["title"],
            authors=row["authors"],
            status=row["status"],
            rating=row["rating"],
            finished_at=row["finished_at"],
        )
        for row in rows
    ]


async def currently_reading(
    conn: aiosqlite.Connection, guild_id: int, *, limit: int = 50
) -> list[tuple[int, ShelfItem]]:
    """Everyone in the guild with an open 'reading' entry, newest first."""
    async with conn.execute(
        """
        SELECT ub.user_id, ub.book_id, b.title, b.authors, ub.status,
               ub.rating, ub.finished_at
        FROM user_books ub
        JOIN books b ON b.id = ub.book_id
        WHERE ub.guild_id = ? AND ub.status = 'reading'
        ORDER BY ub.started_at DESC
        LIMIT ?
        """,
        (guild_id, limit),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        (
            row["user_id"],
            ShelfItem(
                book_id=row["book_id"],
                title=row["title"],
                authors=row["authors"],
                status=row["status"],
                rating=row["rating"],
                finished_at=row["finished_at"],
            ),
        )
        for row in rows
    ]


@dataclass(slots=True)
class BookStats:
    shelf_users: int
    want_to_read: int
    reading: int
    readers: int
    average_rating: float | None


async def stats_for_book(
    conn: aiosqlite.Connection, guild_id: int, book_id: int, *, exclude_user: int
) -> BookStats:
    """Other members in this server who have this book on their shelf."""
    async with conn.execute(
        """
        SELECT
            COUNT(*) AS shelf_users,
            SUM(CASE WHEN status = 'want_to_read' THEN 1 ELSE 0 END) AS want_to_read,
            SUM(CASE WHEN status = 'reading' THEN 1 ELSE 0 END) AS reading,
            SUM(CASE WHEN status = 'finished' THEN 1 ELSE 0 END) AS readers,
            AVG(CASE WHEN status = 'finished' THEN rating END) AS avg_rating
        FROM user_books
        WHERE guild_id = ? AND book_id = ? AND user_id != ?
        """,
        (guild_id, book_id, exclude_user),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return BookStats(0, 0, 0, 0, None)
    return BookStats(
        int(row["shelf_users"] or 0),
        int(row["want_to_read"] or 0),
        int(row["reading"] or 0),
        int(row["readers"] or 0),
        row["avg_rating"],
    )
