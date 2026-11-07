"""Read-only checkout projections for the organizer dashboard.

Sales numbers are derived from the checkout database (owned by FastAPI). This
module only ever issues SELECTs, and refund mutations are delegated to Celery
tasks that call the checkout service code -- so schema ownership stays intact.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import bindparam, text

from web.accounts.readmodel import engine


def event_sales(event_id: int) -> dict[str, Any]:
    summary_sql = text(
        """
        SELECT
            COALESCE(SUM(CASE WHEN o.status IN ('paid', 'partially_refunded') THEN o.total_cents ELSE 0 END), 0) AS revenue_cents,
            COUNT(DISTINCT CASE WHEN o.status IN ('paid', 'partially_refunded') THEN o.id END) AS paid_orders,
            COUNT(CASE WHEN t.status IN ('valid', 'checked_in') THEN 1 END) AS tickets_sold,
            COUNT(CASE WHEN t.status = 'checked_in' THEN 1 END) AS checked_in
        FROM orders o
        LEFT JOIN ticket t ON t.order_id = o.id
        WHERE o.event_id = :event_id
        """
    )
    daily_sql = text(
        """
        SELECT DATE(o.created_at) AS day, COUNT(*) AS orders, SUM(o.total_cents) AS revenue_cents
        FROM orders o
        WHERE o.event_id = :event_id AND o.status IN ('paid', 'partially_refunded')
        GROUP BY DATE(o.created_at)
        ORDER BY day
        """
    )
    with engine().connect() as conn:
        summary = dict(conn.execute(summary_sql, {"event_id": event_id}).one()._mapping)
        daily = [dict(row._mapping) for row in conn.execute(daily_sql, {"event_id": event_id})]
    sold = int(summary["tickets_sold"] or 0)
    checked_in = int(summary["checked_in"] or 0)
    summary["check_in_rate"] = round(checked_in / sold, 4) if sold else 0.0
    summary["sales_by_day"] = [
        {
            "day": row["day"].isoformat() if isinstance(row["day"], date) else str(row["day"]),
            "orders": int(row["orders"]),
            "revenue_cents": int(row["revenue_cents"] or 0),
        }
        for row in daily
    ]
    return summary


def refunds_for_events(event_ids: list[int]) -> list[dict[str, Any]]:
    if not event_ids:
        return []
    sql = text(
        """
        SELECT r.id, r.order_id, r.amount_cents, r.status, r.reason, r.created_at,
               o.event_id, o.user_id, o.currency
        FROM refund r
        JOIN orders o ON o.id = r.order_id
        WHERE o.event_id IN :event_ids
        ORDER BY r.created_at DESC
        LIMIT 200
        """
    ).bindparams(bindparam("event_ids", expanding=True))
    with engine().connect() as conn:
        return [dict(row._mapping) for row in conn.execute(sql, {"event_ids": event_ids})]


def refund_by_id(refund_id: int) -> dict[str, Any] | None:
    sql = text(
        """
        SELECT r.id, r.order_id, r.amount_cents, r.status, r.reason, r.created_at, o.event_id
        FROM refund r JOIN orders o ON o.id = r.order_id
        WHERE r.id = :refund_id
        """
    )
    with engine().connect() as conn:
        row = conn.execute(sql, {"refund_id": refund_id}).first()
        return dict(row._mapping) if row else None
