"""The catalogue micro-cache: expiry, the off switch, and its size bound."""

from __future__ import annotations

from api.app import cache as cache_module
from api.app.cache import TTLCache


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _counting_factory():
    calls = {"n": 0}

    def build() -> int:
        calls["n"] += 1
        return calls["n"]

    return build, calls


def test_serves_from_memory_until_the_entry_expires(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(cache_module.time, "monotonic", clock)
    cache: TTLCache[int] = TTLCache(lambda: 5.0)
    build, calls = _counting_factory()

    assert cache.get_or_set("k", build) == 1
    clock.now += 4.9
    assert cache.get_or_set("k", build) == 1
    clock.now += 0.2
    assert cache.get_or_set("k", build) == 2
    assert calls["n"] == 2


def test_zero_ttl_disables_caching():
    cache: TTLCache[int] = TTLCache(lambda: 0)
    build, calls = _counting_factory()
    cache.get_or_set("k", build)
    cache.get_or_set("k", build)
    assert calls["n"] == 2


def test_size_is_bounded_and_clear_empties_it():
    cache: TTLCache[str] = TTLCache(lambda: 60.0, max_entries=3)
    for key in range(10):
        cache.get_or_set(key, lambda key=key: f"v{key}")
    assert len(cache._entries) <= 3
    cache.clear()
    assert cache._entries == {}
