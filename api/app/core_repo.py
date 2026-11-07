"""Read-only access to the ``core`` database owned by Django.

FastAPI never writes here. Keeping the queries explicit (rather than importing
Django models) is what lets the two services migrate independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

from api.app.db import read_engine
from api.app.models import utcnow

SELLABLE_STATUSES = ("published", "on_sale")


@dataclass(frozen=True, slots=True)
class EventRow:
    id: int
    title: str
    slug: str
    description: str
    category: str
    starts_at: datetime
    ends_at: datetime
    status: str
    venue_id: int
    venue_name: str
    city: str
    cover_image: str | None
    organizer_name: str


@dataclass(frozen=True, slots=True)
class TicketTypeRow:
    id: int
    event_id: int
    name: str
    price_cents: int
    currency: str
    quantity_total: int
    sale_starts_at: datetime | None
    sale_ends_at: datetime | None
    max_per_order: int
    inventory_mode: str


@dataclass(frozen=True, slots=True)
class UserRow:
    id: int
    email: str
    role: str
    is_verified: bool


@dataclass(frozen=True, slots=True)
class SeatRow:
    id: int
    section_id: int
    section: str
    row: str
    number: str


def _fetchall(sql: str, params: dict[str, object]) -> list[Any]:
    with read_engine().connect() as conn:
        return list(conn.execute(text(sql), params))


def _fetchone(sql: str, params: dict[str, object]) -> Any | None:
    with read_engine().connect() as conn:
        return conn.execute(text(sql), params).first()


_EVENT_SELECT = """
    SELECT e.id, e.title, e.slug, e.description, e.category, e.starts_at, e.ends_at,
           e.status, e.venue_id, v.name AS venue_name, v.city, e.cover_image,
           o.name AS organizer_name
    FROM core_event e
    JOIN core_venue v ON v.id = e.venue_id
    JOIN core_organizer o ON o.id = e.organizer_id
"""


def get_event(event_id: int) -> EventRow | None:
    row = _fetchone(_EVENT_SELECT + " WHERE e.id = :id", {"id": event_id})
    return EventRow(**dict(row._mapping)) if row else None


def get_event_by_slug(slug: str) -> EventRow | None:
    row = _fetchone(_EVENT_SELECT + " WHERE e.slug = :slug", {"slug": slug})
    return EventRow(**dict(row._mapping)) if row else None


def list_events(*, cursor: int | None = None, limit: int = 20) -> tuple[list[EventRow], int | None]:
    limit = max(1, min(limit, 100))
    sql = (
        _EVENT_SELECT
        + " WHERE e.status IN ('published', 'on_sale', 'sold_out') AND e.ends_at > :now"
    )
    params: dict[str, object] = {"now": utcnow(), "limit": limit + 1}
    if cursor is not None:
        sql += " AND e.id > :cursor"
        params["cursor"] = cursor
    sql += " ORDER BY e.id ASC LIMIT :limit"
    rows = [EventRow(**dict(row._mapping)) for row in _fetchall(sql, params)]
    has_more = len(rows) > limit
    page = rows[:limit]
    return page, (page[-1].id if has_more and page else None)


def ticket_types_for_event(event_id: int) -> list[TicketTypeRow]:
    sql = """
        SELECT id, event_id, name, price_cents, currency, quantity_total,
               sale_starts_at, sale_ends_at, max_per_order, inventory_mode
        FROM core_ticket_type WHERE event_id = :event_id ORDER BY price_cents
    """
    return [TicketTypeRow(**dict(row._mapping)) for row in _fetchall(sql, {"event_id": event_id})]


def ticket_types_for_events(event_ids: list[int]) -> dict[int, list[TicketTypeRow]]:
    """Ticket types for many events in one query (avoids N+1 on listings)."""
    grouped: dict[int, list[TicketTypeRow]] = {event_id: [] for event_id in event_ids}
    if not event_ids:
        return grouped
    placeholders = ", ".join(f":id{index}" for index in range(len(event_ids)))
    # Only generated placeholder names are interpolated; the ids are bound.
    sql = f"""
        SELECT id, event_id, name, price_cents, currency, quantity_total,
               sale_starts_at, sale_ends_at, max_per_order, inventory_mode
        FROM core_ticket_type WHERE event_id IN ({placeholders})
        ORDER BY event_id, price_cents
    """  # noqa: S608
    params: dict[str, object] = {f"id{index}": value for index, value in enumerate(event_ids)}
    for row in _fetchall(sql, params):
        ticket_type = TicketTypeRow(**dict(row._mapping))
        grouped.setdefault(ticket_type.event_id, []).append(ticket_type)
    return grouped


def get_ticket_type(ticket_type_id: int) -> TicketTypeRow | None:
    sql = """
        SELECT id, event_id, name, price_cents, currency, quantity_total,
               sale_starts_at, sale_ends_at, max_per_order, inventory_mode
        FROM core_ticket_type WHERE id = :id
    """
    row = _fetchone(sql, {"id": ticket_type_id})
    return TicketTypeRow(**dict(row._mapping)) if row else None


def seats_for_event(event_id: int) -> list[SeatRow]:
    sql = """
        SELECT s.id, s.section_id, sec.name AS section, s.row_label AS `row`, s.number
        FROM core_seat s
        JOIN core_section sec ON sec.id = s.section_id
        JOIN core_event e ON e.venue_id = sec.venue_id
        WHERE e.id = :event_id
        ORDER BY sec.name, s.row_label, s.number
    """
    return [SeatRow(**dict(row._mapping)) for row in _fetchall(sql, {"event_id": event_id})]


def get_user(user_id: int) -> UserRow | None:
    row = _fetchone(
        "SELECT id, email, role, is_verified FROM core_user WHERE id = :id",
        {"id": user_id},
    )
    return UserRow(**dict(row._mapping)) if row else None


def can_scan_event(user_id: int, event_id: int) -> bool:
    """Door staff assigned to the event, the organizer who owns it, or an admin."""
    sql = """
        SELECT 1 FROM core_user u
        WHERE u.id = :user_id AND u.is_active = 1 AND (
            u.is_superuser = 1
            OR u.role = 'admin'
            OR EXISTS (
                SELECT 1 FROM core_staff_assignment sa
                WHERE sa.user_id = u.id AND sa.event_id = :event_id
            )
            OR EXISTS (
                SELECT 1 FROM core_event e JOIN core_organizer o ON o.id = e.organizer_id
                WHERE e.id = :event_id AND o.user_id = u.id
            )
        )
        LIMIT 1
    """
    return _fetchone(sql, {"user_id": user_id, "event_id": event_id}) is not None
