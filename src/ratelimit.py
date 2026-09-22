"""A small asyncio token bucket.

Hardcover allows 60 requests/minute. Rather than reacting to 429s we simply
never exceed the budget: every outbound call waits for a token first.
"""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate_per_minute: int, burst: int) -> None:
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._refill_per_second = rate_per_minute / 60.0
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _add_tokens(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        self._updated = now
        refilled = self._tokens + elapsed * self._refill_per_second
        self._tokens = min(self._capacity, refilled)

    async def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        while True:
            async with self._lock:
                self._add_tokens()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                deficit = 1 - self._tokens
                wait = deficit / self._refill_per_second
            await asyncio.sleep(wait)
