"""Hardcover (https://hardcover.app) metadata provider.

Everything specific to Hardcover's GraphQL API lives here: the queries, the
Typesense search blob, and its cached_* JSON columns. Callers get plain
``Book`` objects back.

API constraints this file is written around:
  * 60 requests/minute (we budget under that via a token bucket)
  * at most 5 top-level queries per request
  * max query depth of 3
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..isbn import to_isbn13
from ..ratelimit import TokenBucket
from .base import Book, ProviderError, RateLimited

log = logging.getLogger(__name__)

ENDPOINT = "https://api.hardcover.app/v1/graphql"

# Depth 3 max: books -> image -> url is the deepest we go.
BOOK_FIELDS = """
    id
    title
    description
    pages
    release_year
    rating
    ratings_count
    slug
    cached_contributors
    cached_tags
    cached_image
    image { url }
    default_physical_edition { isbn_10 isbn_13 }
"""

SEARCH_QUERY = """
query BookSearch($query: String!, $perPage: Int!) {
  search(query: $query, query_type: "Book", per_page: $perPage, page: 1) {
    results
  }
}
"""

BOOK_BY_ID_QUERY = f"""
query BookById($id: Int!) {{
  books(where: {{id: {{_eq: $id}}}}, limit: 1) {{
    {BOOK_FIELDS}
  }}
}}
"""

# editions -> book -> image -> url would exceed the depth limit, so an ISBN
# lookup resolves the book id first and then reuses BOOK_BY_ID_QUERY.
EDITION_BY_ISBN_QUERY = """
query EditionByIsbn($isbn: String!) {
  editions(where: {isbn_13: {_eq: $isbn}}, limit: 1) {
    book_id
  }
}
"""


class HardcoverProvider:
    name = "hardcover"

    def __init__(
        self,
        token: str,
        *,
        rate_per_minute: int = 55,
        burst: int = 10,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._bucket = TokenBucket(rate_per_minute, burst)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "DiscordBookBot/0.1",
            },
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- transport ---------------------------------------------------------

    async def _execute(
        self, query: str, variables: dict[str, Any], *, attempts: int = 3
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(attempts):
            await self._bucket.acquire()
            try:
                response = await self._client.post(
                    ENDPOINT, json={"query": query, "variables": variables}
                )
            except httpx.RequestError as exc:
                last_error = ProviderError(f"network error talking to Hardcover: {exc}")
            else:
                if response.status_code == 429:
                    last_error = RateLimited("Hardcover rate limit hit")
                elif response.status_code >= 500:
                    last_error = ProviderError(
                        f"Hardcover returned {response.status_code}"
                    )
                elif response.status_code == 401:
                    # A bad or expired token will never succeed on retry.
                    raise ProviderError(
                        "Hardcover rejected the API token (401). It may have expired."
                    )
                elif response.status_code >= 400:
                    raise ProviderError(f"Hardcover returned {response.status_code}")
                else:
                    payload = response.json()
                    if payload.get("errors"):
                        message = "; ".join(
                            e.get("message", "unknown") for e in payload["errors"]
                        )
                        raise ProviderError(f"Hardcover query failed: {message}")
                    return payload.get("data") or {}

            if attempt < attempts - 1:
                await asyncio.sleep(2**attempt)

        assert last_error is not None
        raise last_error

    # -- public API --------------------------------------------------------

    async def search(self, query: str, *, limit: int = 5) -> list[Book]:
        data = await self._execute(SEARCH_QUERY, {"query": query, "perPage": limit})
        results = (data.get("search") or {}).get("results") or {}
        hits = results.get("hits") if isinstance(results, dict) else None
        if not hits:
            return []
        books = []
        for hit in hits:
            document = hit.get("document") if isinstance(hit, dict) else None
            if isinstance(document, dict):
                book = self._book_from_search_document(document)
                if book is not None:
                    books.append(book)
        return books

    async def get(self, source_id: str) -> Book | None:
        try:
            book_id = int(source_id)
        except (TypeError, ValueError):
            return None
        data = await self._execute(BOOK_BY_ID_QUERY, {"id": book_id})
        rows = data.get("books") or []
        if not rows:
            return None
        return self._book_from_row(rows[0])

    async def by_isbn(self, isbn: str) -> Book | None:
        isbn13 = to_isbn13(isbn)
        if isbn13 is None:
            return None
        data = await self._execute(EDITION_BY_ISBN_QUERY, {"isbn": isbn13})
        rows = data.get("editions") or []
        if not rows:
            return None
        book_id = rows[0].get("book_id")
        if book_id is None:
            return None
        book = await self.get(str(book_id))
        if book is not None and not book.isbn13:
            book.isbn13 = isbn13
        return book

    # -- normalization -----------------------------------------------------
    #
    # Search hits come from Typesense and detail rows come from Postgres, so
    # both are parsed defensively: the search index is not the same shape as
    # the GraphQL schema, and the API is documented as still in development.

    def _book_from_search_document(self, doc: dict[str, Any]) -> Book | None:
        source_id = doc.get("id")
        title = doc.get("title")
        if source_id is None or not title:
            return None
        authors = [a for a in _as_list(doc.get("author_names")) if isinstance(a, str)]
        isbn13 = None
        for candidate in _as_list(doc.get("isbns")):
            if isinstance(candidate, str) and (converted := to_isbn13(candidate)):
                isbn13 = converted
                break
        return Book(
            source_provider=self.name,
            source_id=str(source_id),
            title=str(title),
            authors=authors,
            description=_clean_text(doc.get("description")),
            release_year=_as_int(doc.get("release_year")),
            pages=_as_int(doc.get("pages")),
            cover_url=_image_url(doc.get("image")),
            isbn13=isbn13,
            source_url=_hardcover_url(doc.get("slug")),
            average_rating=_as_float(doc.get("rating")),
            ratings_count=_as_int(doc.get("ratings_count")),
            tags=[t for t in _as_list(doc.get("genres"))[:5] if isinstance(t, str)],
        )

    def _book_from_row(self, row: dict[str, Any]) -> Book | None:
        source_id = row.get("id")
        title = row.get("title")
        if source_id is None or not title:
            return None

        # `subtitle` is deliberately ignored: it is populated per-edition and
        # is often wrong for the work. Dune's, for instance, is "Teacher's
        # Book", which would render the title as "Dune: Teacher's Book".
        title = str(title)
        edition = row.get("default_physical_edition") or {}
        isbn13 = to_isbn13(edition.get("isbn_13") or edition.get("isbn_10") or "")

        return Book(
            source_provider=self.name,
            source_id=str(source_id),
            title=title,
            authors=_contributor_names(row.get("cached_contributors")),
            description=_clean_text(row.get("description")),
            release_year=_as_int(row.get("release_year")),
            pages=_as_int(row.get("pages")),
            cover_url=_image_url(row.get("image"))
            or _image_url(row.get("cached_image")),
            isbn13=isbn13,
            isbn10=edition.get("isbn_10"),
            source_url=_hardcover_url(row.get("slug")),
            average_rating=_as_float(row.get("rating")),
            ratings_count=_as_int(row.get("ratings_count")),
            tags=_genre_tags(row.get("cached_tags")),
        )


# -- parsing helpers -------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _hardcover_url(slug: Any) -> str | None:
    return f"https://hardcover.app/books/{slug}" if slug else None


def _image_url(value: Any) -> str | None:
    """Covers arrive as {"url": ...} from both `image` and `cached_image`."""
    if isinstance(value, dict):
        url = value.get("url")
        return url if isinstance(url, str) and url else None
    if isinstance(value, str) and value:
        return value
    return None


def _contributor_names(value: Any) -> list[str]:
    """cached_contributors is a JSON array of {"author": {"name": ...}} entries."""
    names: list[str] = []
    for entry in _as_list(value):
        if not isinstance(entry, dict):
            continue
        author = entry.get("author")
        name = author.get("name") if isinstance(author, dict) else entry.get("name")
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return names


def _genre_tags(value: Any) -> list[str]:
    """cached_tags groups tags by category, e.g. {"Genre": [{"tag": "Fantasy"}]}."""
    if not isinstance(value, dict):
        return []
    entries = value.get("Genre") or value.get("genre") or []
    tags: list[str] = []
    for entry in _as_list(entries):
        tag = entry.get("tag") if isinstance(entry, dict) else entry
        if isinstance(tag, str) and tag and tag not in tags:
            tags.append(tag)
    return tags[:5]
