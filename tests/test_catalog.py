from __future__ import annotations

from src.catalog import Catalog
from src.providers.base import Book


class FakeProvider:
    """Counts calls so the tests can assert the cache actually prevents them."""

    name = "fake"

    def __init__(self, books: list[Book] | None = None) -> None:
        self.books = books or []
        self.search_calls = 0
        self.get_calls = 0
        self.isbn_calls = 0
        self.seen_isbns: list[str] = []

    async def search(self, query: str, *, limit: int = 5) -> list[Book]:
        self.search_calls += 1
        return self.books[:limit]

    async def get(self, source_id: str) -> Book | None:
        self.get_calls += 1
        return next((b for b in self.books if b.source_id == source_id), None)

    async def by_isbn(self, isbn: str) -> Book | None:
        self.isbn_calls += 1
        self.seen_isbns.append(isbn)
        return next((b for b in self.books if b.isbn13 == isbn), None)


def make_book(source_id: str = "1", **overrides) -> Book:
    data = dict(
        source_provider="fake",
        source_id=source_id,
        title="Dune",
        authors=["Frank Herbert"],
        isbn13="9780441013593",
    )
    data.update(overrides)
    return Book(**data)  # type: ignore[arg-type]


async def test_title_query_hits_search(db) -> None:
    provider = FakeProvider([make_book()])
    catalog = Catalog(db, provider)

    results = await catalog.lookup("dune")
    assert [b.title for b in results] == ["Dune"]
    assert provider.search_calls == 1
    assert provider.isbn_calls == 0


async def test_isbn_query_skips_search(db) -> None:
    provider = FakeProvider([make_book()])
    catalog = Catalog(db, provider)

    results = await catalog.lookup("0441013597")  # ISBN-10 form
    assert [b.title for b in results] == ["Dune"]
    assert provider.isbn_calls == 1
    assert provider.search_calls == 0
    assert provider.seen_isbns == ["9780441013593"]  # canonicalized for the provider


async def test_isbn10_and_isbn13_share_a_cache_entry(db) -> None:
    provider = FakeProvider([make_book()])
    catalog = Catalog(db, provider)

    await catalog.lookup("0441013597")
    await catalog.lookup("978-0-441-01359-3")
    assert provider.isbn_calls == 1


async def test_repeat_search_is_served_from_cache(db) -> None:
    provider = FakeProvider([make_book()])
    catalog = Catalog(db, provider)

    await catalog.lookup("dune")
    await catalog.lookup("DUNE")  # case-insensitive cache key
    assert provider.search_calls == 1


async def test_repeat_detail_is_served_from_cache(db) -> None:
    provider = FakeProvider([make_book()])
    catalog = Catalog(db, provider)

    assert await catalog.detail("1") is not None
    assert await catalog.detail("1") is not None
    assert provider.get_calls == 1


async def test_negative_lookup_is_cached(db) -> None:
    """A book the provider does not have must not be re-requested."""
    provider = FakeProvider([])
    catalog = Catalog(db, provider)

    assert await catalog.detail("nope") is None
    assert await catalog.detail("nope") is None
    assert provider.get_calls == 1


async def test_blank_query_makes_no_request(db) -> None:
    provider = FakeProvider([make_book()])
    catalog = Catalog(db, provider)

    assert await catalog.lookup("   ") == []
    assert provider.search_calls == 0
    assert provider.isbn_calls == 0


async def test_store_and_load_round_trip(db) -> None:
    provider = FakeProvider([make_book()])
    catalog = Catalog(db, provider)

    book_id = await catalog.store(make_book())
    loaded = await catalog.load(book_id)
    assert loaded is not None
    assert loaded.title == "Dune"
    assert loaded.authors == ["Frank Herbert"]
