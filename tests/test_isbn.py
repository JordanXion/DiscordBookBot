from __future__ import annotations

import pytest

from src.isbn import looks_like_isbn, to_isbn13


@pytest.mark.parametrize(
    "value",
    ["9780441013593", "0441013597", "978-0-441-01359-3", "0 441 01359 7"],
)
def test_valid_isbns_are_detected(value: str) -> None:
    assert looks_like_isbn(value)


@pytest.mark.parametrize(
    "value",
    [
        "the hobbit",
        "1234567890",  # fails the checksum
        "9780441013594",  # fails the checksum
        "12345",
        "",
        "dune 1965",
    ],
)
def test_non_isbns_are_rejected(value: str) -> None:
    assert not looks_like_isbn(value)


def test_isbn10_converts_to_isbn13() -> None:
    assert to_isbn13("0441013597") == "9780441013593"


def test_isbn13_passes_through_normalized() -> None:
    assert to_isbn13("978-0-441-01359-3") == "9780441013593"


def test_invalid_isbn_converts_to_none() -> None:
    assert to_isbn13("not a book") is None


def test_isbn10_with_x_check_digit() -> None:
    # 043942089X is a valid ISBN-10 ending in the X check digit.
    assert looks_like_isbn("043942089X")
    assert to_isbn13("043942089X") == "9780439420891"
