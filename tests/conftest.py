"""Test configuration.

Settings are read at import time, so the environment must be populated
before anything under ``src`` is imported.
"""

from __future__ import annotations

import os

os.environ.setdefault("DISCORD_TOKEN", "test-discord-token")
os.environ.setdefault("HARDCOVER_TOKEN", "test-hardcover-token")
os.environ.setdefault("DEV_GUILD_ID", "")

import pytest  # noqa: E402

from src.db import Database  # noqa: E402


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()
