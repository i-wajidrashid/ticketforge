"""Publish domain events to the Django Channels layer.

The checkout service is the source of truth; the socket service is a fan-out
edge. Publishing here means a seat release reaches every open browser even when
it was caused by a Celery sweeper rather than an HTTP request.
"""

from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import async_to_sync
from channels_redis.core import RedisChannelLayer
from common.realtime import hold_group, sales_group, seat_group

from api.app.config import settings

logger = logging.getLogger(__name__)

_layer: RedisChannelLayer | None = None


def layer() -> RedisChannelLayer:
    global _layer
    if _layer is None:
        _layer = RedisChannelLayer(hosts=[settings.redis_url])
    return _layer


def _send(group: str, message: dict[str, Any]) -> None:
    try:
        async_to_sync(layer().group_send)(group, {"type": "broadcast", "payload": message})
    except Exception:
        logger.warning(
            "realtime broadcast failed", extra={"extra_fields": {"group": group}}, exc_info=True
        )


def seat_updates(event_id: int, seats: list[dict[str, Any]]) -> None:
    if seats:
        _send(seat_group(event_id), {"type": "seat_update", "seats": seats})


def hold_tick(hold_id: str, seconds_left: int) -> None:
    _send(hold_group(hold_id), {"type": "hold_tick", "seconds_left": seconds_left})


def hold_expired(hold_id: str) -> None:
    _send(hold_group(hold_id), {"type": "hold_expired"})


def sale(event_id: int, tickets: int, revenue_cents: int) -> None:
    _send(
        sales_group(event_id), {"type": "sale", "tickets": tickets, "revenue_cents": revenue_cents}
    )
