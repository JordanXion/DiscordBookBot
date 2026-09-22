"""Embed builders.

Deliberately provider-neutral: the source is credited by name and linked,
but nothing here assumes the metadata came from any particular service.
"""

from __future__ import annotations

import urllib.parse

import discord

from ..providers.base import Book
from ..repos.user_books import (
    STATUS_FINISHED,
    STATUS_WANT_TO_READ,
    BookStats,
    ShelfItem,
)

ACCENT = discord.Colour(0x8B5E3C)
DESCRIPTION_LIMIT = 500


def stars(rating: float | None) -> str:
    """Render a rating in the compact numeric-star style, e.g. ``4.5★``."""
    if rating is None:
        return "unrated"
    return f"{rating:.1f}★"


def rating_label(rating: float | None) -> str:
    if rating is None:
        return "unrated"
    return stars(rating)


def truncate(text: str, limit: int = DESCRIPTION_LIMIT) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:—-")
    return f"{cut}…"


def _external_links(book: Book) -> str:
    links: list[str] = []
    if book.source_url:
        links.append(f"[{book.source_provider.title()}]({book.source_url})")

    goodreads_query = urllib.parse.quote_plus(f"{book.title} {book.author_line}")
    links.append(
        f"[Goodreads](https://www.goodreads.com/search?q={goodreads_query})"
    )

    if book.isbn13:
        links.append(f"[Open Library](https://openlibrary.org/isbn/{book.isbn13})")
    return " · ".join(links)


def book_embed(book: Book, *, stats: BookStats | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=truncate(book.title, 250),
        url=book.source_url,
        colour=ACCENT,
        description=truncate(book.description) if book.description else None,
    )
    embed.set_author(name=book.author_line)

    if book.cover_url:
        embed.set_thumbnail(url=book.cover_url)

    details: list[str] = []
    if book.release_year:
        details.append(str(book.release_year))
    if book.pages:
        details.append(f"{book.pages} pages")
    if book.isbn13:
        details.append(f"ISBN {book.isbn13}")
    if details:
        embed.add_field(name="Details", value=" · ".join(details), inline=True)

    if book.average_rating:
        value = stars(book.average_rating)
        if book.ratings_count:
            value += f" ({book.ratings_count:,} ratings)"
        embed.add_field(name="Rating", value=value, inline=True)

    if book.tags:
        embed.add_field(name="Genres", value=", ".join(book.tags[:4]), inline=False)

    embed.add_field(name="Links", value=_external_links(book), inline=False)

    if stats is not None and stats.shelf_users:
        plural = "person" if stats.shelf_users == 1 else "people"
        parts = [f"**{stats.shelf_users}** other {plural} here have this on their shelf"]
        if stats.want_to_read:
            parts.append(f"{stats.want_to_read} want to read")
        if stats.reading:
            parts.append(f"{stats.reading} reading")
        if stats.readers:
            parts.append(f"{stats.readers} finished")
        line = " · ".join(parts)
        if stats.average_rating:
            line += f" — {stars(stats.average_rating)} avg."
        embed.add_field(name="In this server", value=line, inline=False)

    return embed


def shelf_embed(
    member: discord.abc.User, items: list[ShelfItem], *, page: int, pages: int
) -> discord.Embed:
    wanted = [i for i in items if i.status == STATUS_WANT_TO_READ]
    reading = [i for i in items if i.status == "reading"]
    finished = [i for i in items if i.status == STATUS_FINISHED]

    embed = discord.Embed(
        title=f"{member.display_name}'s shelf",
        colour=ACCENT,
        description=(
            f"{len(wanted)} want to read · {len(reading)} in progress · "
            f"{len(finished)} finished"
            if items
            else "Nothing logged yet."
        ),
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    if wanted:
        embed.add_field(
            name="🔖 Want to read",
            value="\n".join(f"• {truncate(i.title, 60)}" for i in wanted[:10]),
            inline=False,
        )
    if reading:
        embed.add_field(
            name="📖 Reading",
            value="\n".join(f"• {truncate(i.title, 60)}" for i in reading[:10]),
            inline=False,
        )
    if finished:
        lines = [
            f"• {truncate(i.title, 60)} — {stars(i.rating)}" for i in finished[:10]
        ]
        embed.add_field(name="✅ Finished", value="\n".join(lines), inline=False)

    if pages > 1:
        embed.set_footer(text=f"Page {page} of {pages}")
    return embed


def reading_board_embed(
    guild: discord.Guild, entries: list[tuple[int, ShelfItem]]
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Currently reading in {guild.name}",
        colour=ACCENT,
        description=None if entries else "Nobody has a book in progress right now.",
    )
    lines = []
    for user_id, item in entries[:25]:
        lines.append(f"<@{user_id}> — **{truncate(item.title, 60)}**")
    if lines:
        embed.description = "\n".join(lines)
    return embed
