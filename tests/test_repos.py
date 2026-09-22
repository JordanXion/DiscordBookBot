from __future__ import annotations

from src.providers.base import Book
from src.repos import books as books_repo
from src.repos import cache
from src.repos import user_books as ub

GUILD = 111
ALICE = 1
BOB = 2


def make_book(**overrides) -> Book:
    data = dict(
        source_provider="hardcover",
        source_id="1234",
        title="Dune",
        authors=["Frank Herbert"],
        isbn13="9780441013593",
        release_year=1965,
    )
    data.update(overrides)
    return Book(**data)  # type: ignore[arg-type]


async def store(db, book: Book) -> int:
    async with db.transaction() as conn:
        return await books_repo.upsert(conn, book)


async def test_upsert_is_idempotent_for_same_provider_id(db) -> None:
    first = await store(db, make_book())
    second = await store(db, make_book(title="Dune (reissue)"))
    assert first == second

    stored = await books_repo.get(db.conn, first)
    assert stored is not None and stored.title == "Dune (reissue)"


async def test_books_dedupe_across_providers_by_isbn(db) -> None:
    hardcover_id = await store(db, make_book())
    openlibrary_id = await store(
        db, make_book(source_provider="openlibrary", source_id="OL1M")
    )
    assert hardcover_id == openlibrary_id


async def test_books_without_isbn_stay_separate(db) -> None:
    a = await store(db, make_book(source_id="1", isbn13=None))
    b = await store(db, make_book(source_id="2", isbn13=None))
    assert a != b


async def test_mark_reading_then_finish_preserves_start_time(db) -> None:
    book_id = await store(db, make_book())
    async with db.transaction() as conn:
        started = await ub.mark_reading(conn, GUILD, ALICE, book_id)
        finished = await ub.mark_finished(conn, GUILD, ALICE, book_id, rating=4.5)

    assert started.started_at is not None
    assert finished.started_at == started.started_at
    assert finished.status == ub.STATUS_FINISHED
    assert finished.rating == 4.5


async def test_finishing_without_reading_first_is_allowed(db) -> None:
    book_id = await store(db, make_book())
    async with db.transaction() as conn:
        entry = await ub.mark_finished(conn, GUILD, ALICE, book_id, rating=3.0)
    assert entry.status == ub.STATUS_FINISHED
    assert entry.started_at is None


async def test_rerating_reports_that_it_replaced_a_rating(db) -> None:
    book_id = await store(db, make_book())
    async with db.transaction() as conn:
        _, first = await ub.set_rating(conn, GUILD, ALICE, book_id, 4.0)
        entry, second = await ub.set_rating(conn, GUILD, ALICE, book_id, 5.0)

    assert first is False
    assert second is True
    assert entry.rating == 5.0


async def test_reading_again_clears_the_finished_timestamp(db) -> None:
    book_id = await store(db, make_book())
    async with db.transaction() as conn:
        await ub.mark_finished(conn, GUILD, ALICE, book_id, rating=4.0)
        entry = await ub.mark_reading(conn, GUILD, ALICE, book_id)
    assert entry.status == ub.STATUS_READING
    assert entry.finished_at is None


async def test_want_to_read_can_be_removed_as_unread(db) -> None:
    book_id = await store(db, make_book())
    async with db.transaction() as conn:
        wanted = await ub.mark_want_to_read(conn, GUILD, ALICE, book_id)
        removed = await ub.mark_unread(conn, GUILD, ALICE, book_id)

    assert wanted.status == ub.STATUS_WANT_TO_READ
    assert removed is True
    assert await ub.get(db.conn, GUILD, ALICE, book_id) is None


async def test_status_is_scoped_per_guild(db) -> None:
    book_id = await store(db, make_book())
    async with db.transaction() as conn:
        await ub.mark_reading(conn, GUILD, ALICE, book_id)

    assert await ub.get(db.conn, 999, ALICE, book_id) is None
    assert await ub.shelf(db.conn, 999, ALICE) == []


async def test_currently_reading_lists_only_open_books(db) -> None:
    reading_id = await store(db, make_book(source_id="1", isbn13=None, title="Dune"))
    done_id = await store(db, make_book(source_id="2", isbn13=None, title="Neuromancer"))

    async with db.transaction() as conn:
        await ub.mark_reading(conn, GUILD, ALICE, reading_id)
        await ub.mark_finished(conn, GUILD, BOB, done_id, rating=4.0)

    board = await ub.currently_reading(db.conn, GUILD)
    assert [(user, item.title) for user, item in board] == [(ALICE, "Dune")]


async def test_stats_exclude_the_asking_user(db) -> None:
    book_id = await store(db, make_book())
    async with db.transaction() as conn:
        await ub.mark_finished(conn, GUILD, ALICE, book_id, rating=4.0)
        await ub.mark_finished(conn, GUILD, BOB, book_id, rating=5.0)

    mine = await ub.stats_for_book(db.conn, GUILD, book_id, exclude_user=ALICE)
    assert mine.readers == 1
    assert mine.average_rating == 5.0

    theirs = await ub.stats_for_book(db.conn, GUILD, book_id, exclude_user=999)
    assert theirs.readers == 2
    assert theirs.average_rating == 4.5


async def test_stats_include_other_shelf_states(db) -> None:
    book_id = await store(db, make_book())
    async with db.transaction() as conn:
        await ub.mark_want_to_read(conn, GUILD, ALICE, book_id)
        await ub.mark_reading(conn, GUILD, BOB, book_id)

    stats = await ub.stats_for_book(db.conn, GUILD, book_id, exclude_user=999)
    assert (stats.shelf_users, stats.want_to_read, stats.reading, stats.readers) == (
        2,
        1,
        1,
        0,
    )


async def test_cache_round_trip_and_expiry(db) -> None:
    async with db.transaction() as conn:
        await cache.set(conn, "k", {"hello": "world"}, ttl=60)
    assert await cache.get(db.conn, "k") == {"hello": "world"}

    async with db.transaction() as conn:
        await cache.set(conn, "expired", {"x": 1}, ttl=-1)
    assert await cache.get(db.conn, "expired") is cache.MISS

    async with db.transaction() as conn:
        removed = await cache.purge_expired(conn)
    assert removed == 1
    assert await cache.get(db.conn, "k") == {"hello": "world"}


async def test_absent_key_is_a_miss(db) -> None:
    assert await cache.get(db.conn, "never-written") is cache.MISS


async def test_cached_none_is_distinct_from_a_miss(db) -> None:
    """A negative lookup must be remembered, not retried on every call."""
    async with db.transaction() as conn:
        await cache.set(conn, "known-missing", None, ttl=60)

    assert await cache.get(db.conn, "known-missing") is None
    assert await cache.get(db.conn, "known-missing") is not cache.MISS
