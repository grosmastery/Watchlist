import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from typing import Any, cast

from watchlist.integrations.tmdb import TMDBError


@dataclass(frozen=True, slots=True)
class CacheEntry:
    value: Any
    expires_at: float


class TTLCache:
    def __init__(
        self, max_entries: int = 1024, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[Hashable, CacheEntry] = OrderedDict()
        self._locks: dict[Hashable, asyncio.Lock] = {}

    def _fresh(self, key: Hashable) -> CacheEntry | None:
        entry = self._entries.get(key)
        return entry if entry is not None and entry.expires_at > self._clock() else None

    def _store(self, key: Hashable, value: Any, ttl: float) -> None:
        self._entries[key] = CacheEntry(value, self._clock() + ttl)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    async def get_or_load[T](
        self, key: Hashable, ttl: float, loader: Callable[[], Awaitable[T]]
    ) -> T:
        entry = self._fresh(key)
        if entry is not None:
            return cast(T, entry.value)
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                entry = self._fresh(key)
                if entry is not None:
                    return cast(T, entry.value)
                try:
                    value = await loader()
                except TMDBError:
                    stale = self._entries.get(key)
                    if stale is None:
                        raise
                    return cast(T, stale.value)
                self._store(key, value, ttl)
                return value
        finally:
            self._locks.pop(key, None)
