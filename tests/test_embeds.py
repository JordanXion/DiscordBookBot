from __future__ import annotations

import pytest

from src.providers.base import Book
from src.repos.user_books import BookStats
from src.ui.embeds import book_embed, stars, truncate
from src.ui.views import RATING_CHOICES


@pytest.mark.parametrize(
    ("rating", "expected"),
    [
        (0.5, "0.5★"),
        (1.0, "1.0★"),
        (3.5, "3.5★"),
        (4.5, "4.5★"),
        (5.0, "5.0★"),
    ],
)
def test_star_rendering(rating: float, expected: str) -> None:
    assert stars(rating) == expected


def test_unrated_renders_without_stars() -> None:
    assert stars(None) == "unrated"


def test_every_rating_choice_renders_a_decimal_star_value() -> None:
    for value in RATING_CHOICES:
        assert stars(value) == f"{value:.1f}★"


def test_rating_choices_are_half_steps_from_half_to_five() -> None:
    assert RATING_CHOICES == [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]


def test_truncate_breaks_on_a_word_boundary() -> None:
    text = "the quick brown fox jumps over the lazy dog"
    result = truncate(text, 20)
    assert result.endswith("…")
    assert len(result) <= 21
    assert not result.rstrip("…").endswith(" ")


def test_truncate_leaves_short_text_alone() -> None:
    assert truncate("short", 20) == "short"


def test_book_embed_stays_within_discord_limits() -> None:
    book = Book(
        source_provider="hardcover",
        source_id="1",
        title="T" * 400,
        authors=["Frank Herbert"],
        description="D" * 5000,
        release_year=1965,
        pages=412,
        cover_url="https://example.test/c.jpg",
        isbn13="9780441013593",
        source_url="https://hardcover.app/books/dune",
        average_rating=4.27,
        ratings_count=51234,
        tags=["Science Fiction", "Classics"],
    )
    embed = book_embed(book)

    assert len(embed) <= 6000
    assert len(embed.title or "") <= 256
    assert len(embed.description or "") <= 4096
    for field in embed.fields:
        assert len(field.value or "") <= 1024


def test_book_embed_handles_a_sparse_book() -> None:
    book = Book(source_provider="fake", source_id="1", title="Untitled")
    embed = book_embed(book)
    assert embed.description is None
    # Links are always present, even with no ISBN and no source URL.
    assert any(f.name == "Links" for f in embed.fields)


def test_book_embed_shows_isbn_and_other_shelf_statuses() -> None:
    book = Book(
        source_provider="fake", source_id="1", title="Dune", isbn13="9780441013593"
    )
    embed = book_embed(
        book,
        stats=BookStats(
            shelf_users=3, want_to_read=1, reading=1, readers=1, average_rating=4.5
        ),
    )
    values = [field.value for field in embed.fields]
    assert any("ISBN 9780441013593" in value for value in values)
    assert any("other people" in value and "want to read" in value for value in values)


def test_author_line_collapses_long_credit_lists() -> None:
    book = Book(
        source_provider="fake",
        source_id="1",
        title="Anthology",
        authors=["A", "B", "C", "D", "E"],
    )
    assert book.author_line == "A, B, C +2 more"


def test_author_line_without_authors() -> None:
    book = Book(source_provider="fake", source_id="1", title="Untitled")
    assert book.author_line == "Unknown author"
