"""Provider-agnostic book types.

The bot stores its own canonical book records; providers are just where the
metadata came from. Nothing outside ``src/providers`` should know which
service answered a lookup, so adding Open Library or Google Books later is a
new module rather than a migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class Book:
    """A book, normalized away from any one provider's shape."""

    source_provider: str
    source_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    description: str | None = None
    release_year: int | None = None
    pages: int | None = None
    cover_url: str | None = None
    isbn13: str | None = None
    isbn10: str | None = None
    source_url: str | None = None
    average_rating: float | None = None
    ratings_count: int | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def author_line(self) -> str:
        if not self.authors:
            return "Unknown author"
        if len(self.authors) <= 3:
            return ", ".join(self.authors)
        return f"{', '.join(self.authors[:3])} +{len(self.authors) - 3} more"


class ProviderError(RuntimeError):
    """A provider could not answer the request."""


class RateLimited(ProviderError):
    """The provider's rate limit was hit and the request was not retried."""


@runtime_checkable
class BookProvider(Protocol):
    """What the bot needs from any metadata source."""

    name: str

    async def search(self, query: str, *, limit: int = 5) -> list[Book]: ...

    async def get(self, source_id: str) -> Book | None: ...

    async def by_isbn(self, isbn: str) -> Book | None: ...
