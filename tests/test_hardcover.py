"""Provider parsing tests.

The Hardcover API is documented as in-development, and search results come
from a Typesense index whose shape differs from the GraphQL schema, so these
tests pin down that normalization survives missing and oddly-shaped fields.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.providers.base import ProviderError
from src.providers.hardcover import HardcoverProvider

SEARCH_PAYLOAD = {
    "data": {
        "search": {
            "results": {
                "found": 2,
                "hits": [
                    {
                        "document": {
                            "id": "1234",
                            "title": "Dune",
                            "author_names": ["Frank Herbert"],
                            "release_year": 1965,
                            "pages": 412,
                            "rating": 4.27,
                            "ratings_count": 51234,
                            "slug": "dune",
                            "isbns": ["0441013597"],
                            "genres": ["Science Fiction", "Classics"],
                            "image": {"url": "https://example.test/dune.jpg"},
                        }
                    },
                    {
                        "document": {
                            "id": "5678",
                            "title": "Dune Messiah",
                            "author_names": ["Frank Herbert"],
                            "slug": "dune-messiah",
                        }
                    },
                ],
            }
        }
    }
}

BOOK_PAYLOAD = {
    "data": {
        "books": [
            {
                "id": 1234,
                "title": "Dune",
                "subtitle": None,
                "description": "  A desert planet. ",
                "pages": 412,
                "release_year": 1965,
                "rating": "4.27",
                "ratings_count": 51234,
                "slug": "dune",
                "cached_contributors": [
                    {"author": {"name": "Frank Herbert", "slug": "frank-herbert"}},
                    {"author": {"name": "Frank Herbert"}},
                ],
                "cached_tags": {
                    "Genre": [{"tag": "Science Fiction"}, {"tag": "Classics"}]
                },
                "cached_image": {"url": "https://example.test/cached.jpg"},
                "image": {"url": "https://example.test/dune.jpg"},
                "default_physical_edition": {
                    "isbn_10": "0441013597",
                    "isbn_13": "9780441013593",
                },
            }
        ]
    }
}


def _provider(handler) -> HardcoverProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return HardcoverProvider("token", client=client)


async def test_search_normalizes_hits() -> None:
    provider = _provider(lambda request: httpx.Response(200, json=SEARCH_PAYLOAD))
    books = await provider.search("dune")

    assert [b.title for b in books] == ["Dune", "Dune Messiah"]
    first = books[0]
    assert first.authors == ["Frank Herbert"]
    assert first.isbn13 == "9780441013593"  # converted from the ISBN-10
    assert first.source_url == "https://hardcover.app/books/dune"
    assert first.cover_url == "https://example.test/dune.jpg"
    assert first.tags == ["Science Fiction", "Classics"]

    # A sparse hit must still normalize rather than being dropped.
    assert books[1].release_year is None
    assert books[1].cover_url is None


async def test_search_handles_empty_results() -> None:
    payload = {"data": {"search": {"results": {"found": 0, "hits": []}}}}
    provider = _provider(lambda request: httpx.Response(200, json=payload))
    assert await provider.search("zzzz") == []


async def test_get_normalizes_detail_row() -> None:
    provider = _provider(lambda request: httpx.Response(200, json=BOOK_PAYLOAD))
    book = await provider.get("1234")

    assert book is not None
    assert book.title == "Dune"
    assert book.description == "A desert planet."  # whitespace stripped
    assert book.authors == ["Frank Herbert"]  # duplicate contributor collapsed
    assert book.average_rating == pytest.approx(4.27)  # numeric arrives as a string
    assert book.isbn13 == "9780441013593"
    assert book.tags == ["Science Fiction", "Classics"]


async def test_subtitle_is_ignored() -> None:
    """Hardcover populates subtitle per-edition, so it corrupts work titles:
    Dune's real subtitle in the API is "Teacher's Book"."""
    payload = json.loads(json.dumps(BOOK_PAYLOAD))
    payload["data"]["books"][0]["subtitle"] = "Teacher's Book"
    provider = _provider(lambda request: httpx.Response(200, json=payload))
    book = await provider.get("1234")
    assert book is not None
    assert book.title == "Dune"


async def test_missing_book_returns_none() -> None:
    provider = _provider(
        lambda request: httpx.Response(200, json={"data": {"books": []}})
    )
    assert await provider.get("999") is None


async def test_graphql_errors_raise() -> None:
    payload = {"errors": [{"message": "field does not exist"}]}
    provider = _provider(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError, match="field does not exist"):
        await provider.get("1")


async def test_expired_token_fails_fast_without_retrying() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"message": "unauthorized"})

    provider = _provider(handler)
    with pytest.raises(ProviderError, match="expired"):
        await provider.get("1")
    assert calls == 1


async def test_by_isbn_resolves_edition_then_book() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body["query"])
        if "EditionByIsbn" in body["query"]:
            assert body["variables"]["isbn"] == "9780441013593"
            return httpx.Response(200, json={"data": {"editions": [{"book_id": 1234}]}})
        return httpx.Response(200, json=BOOK_PAYLOAD)

    provider = _provider(handler)
    # Given an ISBN-10, the edition lookup must still query the 13-digit form.
    book = await provider.by_isbn("0441013597")

    assert book is not None and book.title == "Dune"
    assert len(requests) == 2


async def test_by_isbn_unknown_returns_none() -> None:
    provider = _provider(
        lambda request: httpx.Response(200, json={"data": {"editions": []}})
    )
    assert await provider.by_isbn("9780441013593") is None


async def test_sends_bearer_token() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"data": {"books": []}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, headers={"Authorization": "Bearer secret"}
    ) as client:
        provider = HardcoverProvider("secret", client=client)
        await provider.get("1")

    assert seen["authorization"] == "Bearer secret"
