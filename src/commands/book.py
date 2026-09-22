"""Slash commands: /book, /shelf, /reading."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..providers.base import Book, ProviderError, RateLimited
from ..repos import user_books as ub_repo
from ..ui.embeds import book_embed, reading_board_embed, shelf_embed, truncate
from ..ui.views import build_book_view

if TYPE_CHECKING:
    from ..bot import BookBot

log = logging.getLogger(__name__)

SEARCH_LIMIT = 5


def _option_for(book: Book) -> discord.SelectOption:
    detail = book.author_line
    if book.release_year:
        detail += f" · {book.release_year}"
    return discord.SelectOption(
        label=truncate(book.title, 95),
        value=book.source_id,
        description=truncate(detail, 95),
    )


class BookCog(commands.Cog):
    def __init__(self, bot: BookBot) -> None:
        self.bot = bot

    @app_commands.command(name="book", description="Look up a book by title or ISBN")
    @app_commands.describe(query="Title, author, or an ISBN-10/13")
    @app_commands.guild_only()
    async def book(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer()

        try:
            results = await self.bot.catalog.lookup(query, limit=SEARCH_LIMIT)
        except RateLimited:
            await interaction.followup.send(
                "The book service is rate limiting us right now — try again in a minute.",
                ephemeral=True,
            )
            return
        except ProviderError as exc:
            log.warning("lookup failed for %r: %s", query, exc)
            await interaction.followup.send(
                "Couldn't reach the book service just now. Try again shortly.",
                ephemeral=True,
            )
            return

        if not results:
            await interaction.followup.send(
                f"No books found for **{truncate(query, 100)}**.", ephemeral=True
            )
            return

        # Search hits are thin (Typesense documents); the embed is built from
        # a full detail fetch, which is cached for a week.
        top = results[0]
        try:
            detailed = await self.bot.catalog.detail(top.source_id) or top
        except ProviderError:
            detailed = top

        book_id = await self.bot.catalog.store(detailed)
        stats = await ub_repo.stats_for_book(
            self.bot.db.conn,
            interaction.guild_id or 0,
            book_id,
            exclude_user=interaction.user.id,
        )

        alternatives = [_option_for(b) for b in results] if len(results) > 1 else None
        view = build_book_view(
            book_id, interaction.user.id, alternatives, top.source_id
        )
        await interaction.followup.send(
            embed=book_embed(detailed, stats=stats), view=view
        )

    @app_commands.command(name="shelf", description="Show what someone has logged here")
    @app_commands.describe(member="Whose shelf to show (defaults to you)")
    @app_commands.guild_only()
    async def shelf(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ) -> None:
        target = member or interaction.user
        items = await ub_repo.shelf(
            self.bot.db.conn, interaction.guild_id or 0, target.id
        )
        await interaction.response.send_message(
            embed=shelf_embed(target, items, page=1, pages=1)
        )

    @app_commands.command(
        name="reading", description="What everyone here is reading right now"
    )
    @app_commands.guild_only()
    async def reading(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        entries = await ub_repo.currently_reading(
            self.bot.db.conn, interaction.guild.id
        )
        await interaction.response.send_message(
            embed=reading_board_embed(interaction.guild, entries)
        )


async def setup(bot: BookBot) -> None:
    await bot.add_cog(BookCog(bot))
