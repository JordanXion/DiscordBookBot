"""Canonical book storage.

Books are matched to existing rows by provider id first, then by ISBN-13, so
the same title looked up through two different providers collapses onto one
record instead of splitting a server's reading history in half.
"""

from __future__ import annotations

import json

import aiosqlite

from ..providers.base import Book


def _row_to_book(row: aiosqlite.Row) -> Book:
    return Book(
        source_provider=row["source_provider"],
        source_id=row["source_id"],
        title=row["title"],
        authors=json.loads(row["authors"]),
        description=row["description"],
        release_year=row["release_year"],
        pages=row["pages"],
        cover_url=row["cover_url"],
        isbn13=row["isbn13"],
        source_url=row["source_url"],
        average_rating=row["average_rating"],
        ratings_count=row["ratings_count"],
        tags=json.loads(row["tags"]),
    )


async def _find_id(conn: aiosqlite.Connection, book: Book) -> int | None:
    async with conn.execute(
        "SELECT book_id FROM book_external_ids WHERE provider = ? AND external_id = ?",
        (book.source_provider, book.source_id),
    ) as cursor:
        if row := await cursor.fetchone():
            return int(row["book_id"])

    if book.isbn13:
        async with conn.execute(
            "SELECT id FROM books WHERE isbn13 = ?", (book.isbn13,)
        ) as cursor:
            if row := await cursor.fetchone():
                return int(row["id"])
    return None


async def upsert(conn: aiosqlite.Connection, book: Book) -> int:
    """Insert or refresh a book, returning our internal id."""
    existing_id = await _find_id(conn, book)
    values = (
        book.isbn13,
        book.title,
        json.dumps(book.authors),
        book.description,
        book.release_year,
        book.pages,
        book.cover_url,
        book.average_rating,
        book.ratings_count,
        json.dumps(book.tags),
        book.source_provider,
        book.source_id,
        book.source_url,
    )

    if existing_id is None:
        async with conn.execute(
            """
            INSERT INTO books (
                isbn13, title, authors, description, release_year, pages,
                cover_url, average_rating, ratings_count, tags,
                source_provider, source_id, source_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        ) as cursor:
            book_id = int(cursor.lastrowid or 0)
    else:
        book_id = existing_id
        await conn.execute(
            """
            UPDATE books SET
                isbn13 = COALESCE(?, isbn13),
                title = ?, authors = ?, description = ?, release_year = ?,
                pages = ?, cover_url = ?, average_rating = ?, ratings_count = ?,
                tags = ?, source_provider = ?, source_id = ?, source_url = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (*values, book_id),
        )

    await conn.execute(
        """
        INSERT INTO book_external_ids (provider, external_id, book_id)
        VALUES (?, ?, ?)
        ON CONFLICT (provider, external_id) DO UPDATE SET book_id = excluded.book_id
        """,
        (book.source_provider, book.source_id, book_id),
    )
    return book_id


async def get(conn: aiosqlite.Connection, book_id: int) -> Book | None:
    async with conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)) as cursor:
        row = await cursor.fetchone()
    return _row_to_book(row) if row else None
