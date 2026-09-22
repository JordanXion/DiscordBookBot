"""ISBN detection and normalization.

Used to decide whether a ``/book`` query is a title search or a direct
edition lookup, and to give every stored book a stable natural key.
"""

from __future__ import annotations

import re

_STRIP = re.compile(r"[\s-]")
_ISBN10 = re.compile(r"^\d{9}[\dXx]$")
_ISBN13 = re.compile(r"^\d{13}$")


def normalize(value: str) -> str:
    return _STRIP.sub("", value.strip())


def _isbn10_valid(value: str) -> bool:
    if not _ISBN10.match(value):
        return False
    total = 0
    for i, ch in enumerate(value):
        digit = 10 if ch in "Xx" else int(ch)
        total += digit * (10 - i)
    return total % 11 == 0


def _isbn13_valid(value: str) -> bool:
    if not _ISBN13.match(value):
        return False
    total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(value))
    return total % 10 == 0


def looks_like_isbn(value: str) -> bool:
    """True if the query should be treated as an ISBN rather than a title."""
    candidate = normalize(value)
    return _isbn10_valid(candidate) or _isbn13_valid(candidate)


def to_isbn13(value: str) -> str | None:
    """Convert any valid ISBN to its 13-digit form, or None if invalid."""
    candidate = normalize(value)
    if _isbn13_valid(candidate):
        return candidate
    if not _isbn10_valid(candidate):
        return None
    core = "978" + candidate[:9]
    total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(core))
    check = (10 - total % 10) % 10
    return core + str(check)
