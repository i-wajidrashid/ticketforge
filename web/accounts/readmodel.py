"""Read-only projections over the checkout database.

Django never writes checkout tables -- FastAPI owns them. Reading a projection
here keeps the server-rendered account page fast without a service-to-service
HTTP hop on every page view.
"""

from __future__ import annotations

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


def account_orders(user_id: int) -> list[dict[str, Any]]:
    sql = text(
        """
        SELECT id, status, total_cents, currency, created_at
        FROM orders
        WHERE user_id = :user_id
        ORDER BY created_at DESC
        LIMIT 100
        """
    )
    with engine().connect() as conn:
        return [dict(row._mapping) for row in conn.execute(sql, {"user_id": user_id})]


def account_tickets(user_id: int) -> list[dict[str, Any]]:
    sql = text(
        """
        SELECT t.id, t.status, t.order_id, o.created_at
        FROM ticket t
        JOIN orders o ON o.id = t.order_id
        WHERE o.user_id = :user_id
        ORDER BY o.created_at DESC
        LIMIT 200
        """
    )
    with engine().connect() as conn:
        return [dict(row._mapping) for row in conn.execute(sql, {"user_id": user_id})]


def owned_ticket(user_id: int, ticket_id: str) -> dict[str, Any] | None:
    sql = text(
        """
        SELECT t.id, t.qr_token, t.status, t.order_id, o.total_cents
        FROM ticket t
        JOIN orders o ON o.id = t.order_id
        WHERE t.id = :ticket_id AND o.user_id = :user_id
        """
    )
    with engine().connect() as conn:
        row = conn.execute(sql, {"ticket_id": ticket_id, "user_id": user_id}).first()
        return dict(row._mapping) if row else None
