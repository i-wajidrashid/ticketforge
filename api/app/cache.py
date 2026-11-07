"""A small in-process TTL cache for read-mostly public data.

Deliberately simple: each worker process keeps its own copy, entries expire
after a fixed number of seconds, and nothing is invalidated explicitly. That
is the right trade for the public catalogue (a new event may take a few
seconds to appear) and the wrong one for anything involving inventory, which
is why seat maps, holds and payments never go through here.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable


class TTLCache[T]:
    def __init__(self, ttl_seconds: Callable[[], float], max_entries: int = 512) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._entries: dict[Hashable, tuple[float, T]] = {}
        self._lock = threading.Lock()

    def get_or_set(self, key: Hashable, factory: Callable[[], T]) -> T:
        ttl = self._ttl()
        if ttl <= 0:
            return factory()
        now = time.monotonic()
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]
        # Built outside the lock: two threads may both miss and both build,
        # which costs one extra query but never blocks readers on the database.
        value = factory()
        with self._lock:
            if len(self._entries) >= self._max:
                self._entries = {k: v for k, v in self._entries.items() if v[0] > now}
                if len(self._entries) >= self._max:
                    self._entries.clear()
            self._entries[key] = (now + ttl, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
