"""Buttons and selects.

Components are built on ``discord.ui.DynamicItem`` so their state lives
entirely in the custom_id. Discord keeps components on old messages clickable
forever, so a button pressed a month after it was posted -- and several bot
restarts later -- still resolves to the right book instead of failing with
"this interaction failed".
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import discord

from ..repos import user_books as ub_repo
from .embeds import stars

if TYPE_CHECKING:
    from ..bot import BookBot

# Half-star scale, 0.5 to 5.0.
RATING_CHOICES = [round(0.5 * n, 1) for n in range(1, 11)]

NO_PINGS = discord.AllowedMentions.none()


def _bot(interaction: discord.Interaction) -> BookBot:
    return interaction.client  # type: ignore[return-value]


async def _require_guild(interaction: discord.Interaction) -> int | None:
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Reading status is tracked per server, so this only works in a server.",
            ephemeral=True,
        )
        return None
    return interaction.guild_id


class ReadingButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"bk:read:(?P<book_id>\d+)",
):
    def __init__(self, book_id: int) -> None:
        self.book_id = book_id
        super().__init__(
            discord.ui.Button(
                label="Reading",
                emoji="📖",
                style=discord.ButtonStyle.primary,
                custom_id=f"bk:read:{book_id}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: Any, match: re.Match[str], /
    ) -> ReadingButton:
        return cls(int(match["book_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        guild_id = await _require_guild(interaction)
        if guild_id is None:
            return

        bot = _bot(interaction)
        existing = await ub_repo.get(
            bot.db.conn, guild_id, interaction.user.id, self.book_id
        )
        if existing is not None and existing.status == ub_repo.STATUS_READING:
            await interaction.response.send_message(
                "You already have this one marked as reading.", ephemeral=True
            )
            return

        async with bot.db.transaction() as conn:
            await ub_repo.mark_reading(
                conn, guild_id, interaction.user.id, self.book_id
            )

        book = await bot.catalog.load(self.book_id)
        title = book.title if book else "that book"
        await interaction.response.send_message(
            f"📖 **{interaction.user.display_name}** started reading **{title}**.",
            allowed_mentions=NO_PINGS,
        )


class WantToReadButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"bk:want:(?P<book_id>\d+)",
):
    def __init__(self, book_id: int) -> None:
        self.book_id = book_id
        super().__init__(
            discord.ui.Button(
                label="Want to read",
                emoji="🔖",
                style=discord.ButtonStyle.secondary,
                custom_id=f"bk:want:{book_id}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: Any, match: re.Match[str], /
    ) -> WantToReadButton:
        return cls(int(match["book_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        guild_id = await _require_guild(interaction)
        if guild_id is None:
            return

        bot = _bot(interaction)
        existing = await ub_repo.get(
            bot.db.conn, guild_id, interaction.user.id, self.book_id
        )
        if existing is not None and existing.status == ub_repo.STATUS_WANT_TO_READ:
            await interaction.response.send_message(
                "This is already on your want-to-read shelf.", ephemeral=True
            )
            return

        async with bot.db.transaction() as conn:
            await ub_repo.mark_want_to_read(
                conn, guild_id, interaction.user.id, self.book_id
            )
        await interaction.response.send_message(
            "Added to your want-to-read shelf.", ephemeral=True
        )


class FinishedButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"bk:fin:(?P<book_id>\d+)",
):
    def __init__(self, book_id: int) -> None:
        self.book_id = book_id
        super().__init__(
            discord.ui.Button(
                label="Finished",
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=f"bk:fin:{book_id}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: Any, match: re.Match[str], /
    ) -> FinishedButton:
        return cls(int(match["book_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        guild_id = await _require_guild(interaction)
        if guild_id is None:
            return

        bot = _bot(interaction)
        book = await bot.catalog.load(self.book_id)
        title = book.title if book else "this book"

        # The rating prompt is private; only the public result is shared.
        view = discord.ui.View(timeout=None)
        view.add_item(RatingSelect(self.book_id))
        await interaction.response.send_message(
            f"How would you rate **{title}**?", view=view, ephemeral=True
        )


class UnreadButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"bk:unread:(?P<book_id>\d+)",
):
    def __init__(self, book_id: int) -> None:
        self.book_id = book_id
        super().__init__(
            discord.ui.Button(
                label="Unread",
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"bk:unread:{book_id}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: Any, match: re.Match[str], /
    ) -> UnreadButton:
        return cls(int(match["book_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        guild_id = await _require_guild(interaction)
        if guild_id is None:
            return

        bot = _bot(interaction)
        async with bot.db.transaction() as conn:
            removed = await ub_repo.mark_unread(
                conn, guild_id, interaction.user.id, self.book_id
            )
        message = (
            "Removed from your shelf."
            if removed
            else "This book is not currently on your shelf."
        )
        await interaction.response.send_message(message, ephemeral=True)


class RatingSelect(
    discord.ui.DynamicItem[discord.ui.Select],
    template=r"bk:rate:(?P<book_id>\d+)",
):
    def __init__(self, book_id: int) -> None:
        self.book_id = book_id
        super().__init__(
            discord.ui.Select(
                placeholder="Pick a rating…",
                custom_id=f"bk:rate:{book_id}",
                min_values=1,
                max_values=1,
                options=[
                    discord.SelectOption(
                        label=stars(value), value=f"{value:g}"
                    )
                    for value in RATING_CHOICES
                ],
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: Any, match: re.Match[str], /
    ) -> RatingSelect:
        return cls(int(match["book_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        guild_id = await _require_guild(interaction)
        if guild_id is None:
            return

        bot = _bot(interaction)
        rating = float(self.item.values[0])

        async with bot.db.transaction() as conn:
            _, was_rated = await ub_repo.set_rating(
                conn, guild_id, interaction.user.id, self.book_id, rating
            )

        book = await bot.catalog.load(self.book_id)
        title = book.title if book else "that book"

        await interaction.response.edit_message(
            content=f"Saved — {stars(rating)} for **{title}**.", view=None
        )

        verb = "re-rated" if was_rated else "finished"
        message = (
            f"✅ **{interaction.user.display_name}** {verb} **{title}** — "
            f"{stars(rating)}"
        )
        if book and book.authors:
            message = message.replace(
                f"**{title}**", f"**{title}** by {book.author_line}", 1
            )
        await interaction.followup.send(message, allowed_mentions=NO_PINGS)


class ResultPicker(
    discord.ui.DynamicItem[discord.ui.Select],
    template=r"bk:pick:(?P<user_id>\d+)",
):
    """Disambiguation menu for the other search hits.

    Cheaper than slash-command autocomplete, which would fire a provider
    request per keystroke and burn the rate limit.
    """

    def __init__(
        self, user_id: int, options: list[discord.SelectOption] | None = None
    ) -> None:
        self.user_id = user_id
        super().__init__(
            discord.ui.Select(
                placeholder="Not the right book? Pick another…",
                custom_id=f"bk:pick:{user_id}",
                min_values=1,
                max_values=1,
                options=options or [discord.SelectOption(label="…", value="noop")],
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: Any, match: re.Match[str], /
    ) -> ResultPicker:
        # Dynamic items are reconstructed for every interaction. The original
        # component includes its options, so preserve them across restarts.
        options = list(getattr(item, "options", []))
        return cls(int(match["user_id"]), options or None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Anyone may log their own status, but only the person who ran the
        # command may change which book the message is showing.
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only whoever ran the search can change the result. "
                "Run `/book` yourself to look something up.",
                ephemeral=True,
            )
            return False
        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        source_id = self.item.values[0]
        if source_id == "noop":
            await interaction.response.defer()
            return

        await interaction.response.defer()
        bot = _bot(interaction)

        book = await bot.catalog.detail(source_id)
        if book is None:
            await interaction.followup.send(
                "That book's details could not be loaded.", ephemeral=True
            )
            return

        book_id = await bot.catalog.store(book)
        from .embeds import book_embed  # local import avoids a cycle at import time

        stats = None
        if interaction.guild_id is not None:
            stats = await ub_repo.stats_for_book(
                bot.db.conn,
                interaction.guild_id,
                book_id,
                exclude_user=interaction.user.id,
            )

        view = build_book_view(book_id, self.user_id, self.item.options, source_id)
        await interaction.edit_original_response(
            embed=book_embed(book, stats=stats), view=view
        )


def build_book_view(
    book_id: int,
    requester_id: int,
    alternatives: list[discord.SelectOption] | None = None,
    selected_source_id: str | None = None,
) -> discord.ui.View:
    """Status buttons, plus a picker when the search was ambiguous."""
    view = discord.ui.View(timeout=None)
    view.add_item(WantToReadButton(book_id))
    view.add_item(ReadingButton(book_id))
    view.add_item(FinishedButton(book_id))
    view.add_item(UnreadButton(book_id))

    if alternatives:
        options = [
            discord.SelectOption(
                label=option.label,
                value=option.value,
                description=option.description,
                default=option.value == selected_source_id,
            )
            for option in alternatives
        ]
        view.add_item(ResultPicker(requester_id, options))
    return view
