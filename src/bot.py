"""Bot wiring and entrypoint."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands, tasks

from .catalog import Catalog
from .config import settings
from .db import Database
from .providers.hardcover import HardcoverProvider
from .repos import cache
from .ui.views import (
    FinishedButton,
    RatingSelect,
    ReadingButton,
    ResultPicker,
    UnreadButton,
    WantToReadButton,
)

log = logging.getLogger(__name__)

EXTENSIONS = ("src.commands.book",)


class BookBot(commands.Bot):
    def __init__(self) -> None:
        # Slash commands only: no message content, no member list. Keeping
        # intents minimal also keeps the bot verification-free as it grows
        # past 75 servers.
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

        self.db = Database(settings.db_path)
        self.provider = HardcoverProvider(
            settings.hardcover_token,
            rate_per_minute=settings.rate_limit_per_minute,
            burst=settings.rate_limit_burst,
        )
        self.catalog = Catalog(self.db, self.provider)

    async def setup_hook(self) -> None:
        await self.db.connect()

        # Registered globally so components on old messages keep working
        # across restarts.
        for item in (
            WantToReadButton,
            ReadingButton,
            FinishedButton,
            UnreadButton,
            RatingSelect,
            ResultPicker,
        ):
            self.add_dynamic_items(item)

        for extension in EXTENSIONS:
            await self.load_extension(extension)

        if settings.dev_guild_id:
            guild = discord.Object(id=settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("synced %d commands to dev guild %s", len(synced), guild.id)
        else:
            synced = await self.tree.sync()
            log.info("synced %d commands globally", len(synced))

        self.prune_cache.start()

    async def on_ready(self) -> None:
        log.info("connected as %s in %d guilds", self.user, len(self.guilds))

    async def close(self) -> None:
        self.prune_cache.cancel()
        await super().close()
        await self.provider.aclose()
        await self.db.close()

    @tasks.loop(hours=24)
    async def prune_cache(self) -> None:
        async with self.db.transaction() as conn:
            removed = await cache.purge_expired(conn)
        if removed:
            log.info("pruned %d expired cache rows", removed)

    @prune_cache.before_loop
    async def _before_prune(self) -> None:
        await self.wait_until_ready()


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    # discord.py's gateway chatter is noisy at INFO.
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def main() -> None:
    configure_logging()
    bot = BookBot()
    async with bot:
        await bot.start(settings.discord_token)


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
