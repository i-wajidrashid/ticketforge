"""Read-only checkout projections for the organizer dashboard.

Sales numbers are derived from the checkout database (owned by FastAPI). This
module only ever issues SELECTs, and refund mutations are delegated to Celery
tasks that call the checkout service code, so schema ownership stays intact.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.utils import timezone
from sqlalchemy import bindparam, text

from web.accounts.readmodel import engine, utc_rows


def event_sales(event_id: int) -> dict[str, Any]:
    # Order-level and ticket-level figures come from separate subqueries:
    # summing order totals across an orders-to-tickets join would count a
    # multi-ticket order once per ticket.
    summary_sql = text(
        """
        SELECT
            (SELECT COALESCE(SUM(o.total_cents), 0) FROM orders o
             WHERE o.event_id = :event_id AND o.status IN ('paid', 'partially_refunded'))
                AS revenue_cents,
            (SELECT COUNT(*) FROM orders o
             WHERE o.event_id = :event_id AND o.status IN ('paid', 'partially_refunded'))
                AS paid_orders,
            (SELECT COUNT(*) FROM ticket t JOIN orders o ON o.id = t.order_id
             WHERE o.event_id = :event_id AND t.status IN ('valid', 'checked_in'))
                AS tickets_sold,
            (SELECT COUNT(*) FROM ticket t JOIN orders o ON o.id = t.order_id
             WHERE o.event_id = :event_id AND t.status = 'checked_in')
                AS checked_in
        """
    )
    daily_sql = text(
        """
        SELECT DATE(DATE_ADD(o.created_at, INTERVAL :offset MINUTE)) AS day,
               COUNT(*) AS orders, SUM(o.total_cents) AS revenue_cents
        FROM orders o
        WHERE o.event_id = :event_id AND o.status IN ('paid', 'partially_refunded')
        GROUP BY day
        ORDER BY day
        """
    )
    with engine().connect() as conn:
        summary = dict(conn.execute(summary_sql, {"event_id": event_id}).one()._mapping)
        # Bucket by the site's local calendar day, not the UTC one.
        offset = timezone.localtime().utcoffset() or timedelta(0)
        daily = [
            dict(row._mapping)
            for row in conn.execute(
                daily_sql, {"event_id": event_id, "offset": int(offset.total_seconds() // 60)}
            )
        ]
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
        return utc_rows(row._mapping for row in conn.execute(sql, {"event_ids": event_ids}))


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
        return utc_rows([row._mapping])[0] if row else None


def events_overview(event_ids: list[int]) -> dict[int, dict[str, int]]:
    """Revenue, tickets sold and check-ins for many events in one query."""
    if not event_ids:
        return {}
    sql = text(
        """
        SELECT o.event_id,
               COALESCE(SUM(CASE WHEN t.status IN ('valid', 'checked_in') THEN 1 ELSE 0 END), 0)
                   AS tickets_sold,
               COALESCE(SUM(CASE WHEN t.status = 'checked_in' THEN 1 ELSE 0 END), 0)
                   AS checked_in
        FROM orders o
        JOIN ticket t ON t.order_id = o.id
        WHERE o.event_id IN :event_ids
        GROUP BY o.event_id
        """
    ).bindparams(bindparam("event_ids", expanding=True))
    revenue_sql = text(
        """
        SELECT event_id, COALESCE(SUM(total_cents), 0) AS revenue_cents
        FROM orders
        WHERE event_id IN :event_ids AND status IN ('paid', 'partially_refunded')
        GROUP BY event_id
        """
    ).bindparams(bindparam("event_ids", expanding=True))
    overview: dict[int, dict[str, int]] = {
        event_id: {"tickets_sold": 0, "checked_in": 0, "revenue_cents": 0} for event_id in event_ids
    }
    with engine().connect() as conn:
        for row in conn.execute(sql, {"event_ids": event_ids}):
            overview[int(row.event_id)].update(
                tickets_sold=int(row.tickets_sold), checked_in=int(row.checked_in)
            )
        for row in conn.execute(revenue_sql, {"event_ids": event_ids}):
            overview[int(row.event_id)]["revenue_cents"] = int(row.revenue_cents)
    return overview


def sold_by_ticket_type(event_id: int) -> dict[int, int]:
    sql = text(
        """
        SELECT t.ticket_type_id, COUNT(*) AS sold
        FROM ticket t
        JOIN orders o ON o.id = t.order_id
        WHERE o.event_id = :event_id AND t.status IN ('valid', 'checked_in')
        GROUP BY t.ticket_type_id
        """
    )
    with engine().connect() as conn:
        return {
            int(row.ticket_type_id): int(row.sold)
            for row in conn.execute(sql, {"event_id": event_id})
        }


def recent_sales(event_id: int, limit: int = 8) -> list[dict[str, Any]]:
    sql = text(
        """
        SELECT o.id, o.total_cents, o.currency, o.created_at, COUNT(t.id) AS tickets
        FROM orders o
        JOIN ticket t ON t.order_id = o.id
        WHERE o.event_id = :event_id AND o.status IN ('paid', 'partially_refunded')
        GROUP BY o.id, o.total_cents, o.currency, o.created_at
        ORDER BY o.created_at DESC
        LIMIT :limit
        """
    )
    with engine().connect() as conn:
        return utc_rows(
            row._mapping for row in conn.execute(sql, {"event_id": event_id, "limit": limit})
        )
