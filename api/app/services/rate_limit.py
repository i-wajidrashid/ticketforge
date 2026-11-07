"""Redis fixed-window rate limits.

Fails open: if Redis is unavailable, availability wins over throttling. The
global Nginx limiter is still in front of everything.
"""

from __future__ import annotations

import logging

import redis

from api.app.config import settings
from api.app.services.errors import RateLimited

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.redis_url, decode_responses=True, socket_timeout=0.5
        )
    return _client


def enforce(key: str, limit: int, window_seconds: int = 60) -> None:
    if limit <= 0:
        return
    try:
        pipe = client().pipeline()
        pipe.incr(f"rl:{key}")
        pipe.expire(f"rl:{key}", window_seconds)
        count, _ = pipe.execute()
    except redis.RedisError:
        logger.warning("rate limiter unavailable; allowing request", exc_info=True)
        return
    if int(count) > limit:
        raise RateLimited("Too many requests. Slow down and try again.")


def reset() -> None:
    """Test hook."""
    try:
        client().flushdb()
    except redis.RedisError:
        logger.warning("could not reset rate limiter", exc_info=True)
