"""Read-only projections over the checkout database.

Django never writes checkout tables; FastAPI owns them. Reading a projection
here keeps the server-rendered account page fast without a service-to-service
HTTP hop on every page view.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from common.db import checkout_url
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(checkout_url(), pool_pre_ping=True, pool_size=5, max_overflow=2)
    return _engine


def utc_rows(rows: Iterable[Mapping[Any, Any]]) -> list[dict[str, Any]]:
    """Checkout stores naive UTC timestamps; make them aware so templates
    render them in the site's local time instead of mislabelling UTC."""
    return [
        {
            str(key): value.replace(tzinfo=UTC)
            if isinstance(value, datetime) and value.tzinfo is None
            else value
            for key, value in row.items()
        }
        for row in rows
    ]


def account_orders(user_id: int) -> list[dict[str, Any]]:
    sql = text(
        """
        SELECT id, event_id, status, total_cents, currency, created_at
        FROM orders
        WHERE user_id = :user_id AND status IN ('paid', 'partially_refunded', 'refunded')
        ORDER BY created_at DESC
        LIMIT 100
        """
    )
    with engine().connect() as conn:
        return utc_rows(row._mapping for row in conn.execute(sql, {"user_id": user_id}))


def account_tickets(user_id: int) -> list[dict[str, Any]]:
    """Tickets with the physical seat id (core ``Seat`` pk) when reserved."""
    sql = text(
        """
        SELECT t.id, t.status, t.order_id, t.ticket_type_id, t.checked_in_at,
               es.seat_id
        FROM ticket t
        JOIN orders o ON o.id = t.order_id
        LEFT JOIN event_seat es ON es.id = t.event_seat_id
        WHERE o.user_id = :user_id
        ORDER BY o.created_at DESC, es.seat_id
        LIMIT 400
        """
    )
    with engine().connect() as conn:
        return utc_rows(row._mapping for row in conn.execute(sql, {"user_id": user_id}))


def account_refunds(user_id: int) -> dict[str, dict[str, Any]]:
    """The most recent refund per order (orders have at most one live refund)."""
    sql = text(
        """
        SELECT r.order_id, r.status, r.created_at, r.processed_at
        FROM refund r
        JOIN orders o ON o.id = r.order_id
        WHERE o.user_id = :user_id
        ORDER BY r.created_at
        """
    )
    with engine().connect() as conn:
        rows = utc_rows(row._mapping for row in conn.execute(sql, {"user_id": user_id}))
    return {str(row["order_id"]): row for row in rows}
