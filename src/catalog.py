"""Book lookup service: provider + cache + local storage.

Commands talk to this, never to a provider directly. It decides whether a
query is an ISBN or a title search, serves repeats from cache, and persists
whatever it hands back so reading entries always point at a stored book.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from .config import settings
from .db import Database
from .isbn import to_isbn13
from .providers.base import Book, BookProvider
from .repos import books as books_repo
from .repos import cache

log = logging.getLogger(__name__)


class Catalog:
    def __init__(self, db: Database, provider: BookProvider) -> None:
        self.db = db
        self.provider = provider

    async def lookup(self, query: str, *, limit: int = 5) -> list[Book]:
        """Resolve a user's query to candidate books, best match first."""
        query = query.strip()
        if not query:
            return []

        # Providers are handed a canonical ISBN-13, so the 10- and 13-digit
        # forms of the same book also share one cache entry.
        if (isbn13 := to_isbn13(query)) is not None:
            book = await self._by_isbn(isbn13)
            return [book] if book else []

        return await self._search(query, limit=limit)

    async def _search(self, query: str, *, limit: int) -> list[Book]:
        key = f"{self.provider.name}:search:{limit}:{query.casefold()}"
        if (cached := await cache.get(self.db.conn, key)) is not cache.MISS:
            return [Book(**item) for item in cached]

        results = await self.provider.search(query, limit=limit)
        async with self.db.transaction() as conn:
            await cache.set(
                conn, key, [asdict(b) for b in results], settings.search_cache_ttl
            )
        return results

    async def _by_isbn(self, isbn: str) -> Book | None:
        key = f"{self.provider.name}:isbn:{isbn}"
        if (cached := await cache.get(self.db.conn, key)) is not cache.MISS:
            return Book(**cached) if cached else None

        book = await self.provider.by_isbn(isbn)
        async with self.db.transaction() as conn:
            await cache.set(
                conn, key, asdict(book) if book else None, settings.book_cache_ttl
            )
        return book

    async def detail(self, source_id: str) -> Book | None:
        """Full metadata for one book. Search hits are thin, so the embed is
        always built from a detail fetch."""
        key = f"{self.provider.name}:book:{source_id}"
        if (cached := await cache.get(self.db.conn, key)) is not cache.MISS:
            return Book(**cached) if cached else None

        book = await self.provider.get(source_id)
        async with self.db.transaction() as conn:
            await cache.set(
                conn, key, asdict(book) if book else None, settings.book_cache_ttl
            )
        return book

    async def store(self, book: Book) -> int:
        """Persist a book and return our internal id."""
        async with self.db.transaction() as conn:
            return await books_repo.upsert(conn, book)

    async def load(self, book_id: int) -> Book | None:
        return await books_repo.get(self.db.conn, book_id)
