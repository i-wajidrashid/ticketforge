"""Seat maps and general-admission counters.

Seat rows are materialised lazily per event. The first materialisation is a
rare, one-off operation guarded by a MySQL named lock: concurrent INSERTs into
the same unique index would otherwise be able to deadlock with the holds that
immediately follow. Once rows exist this path is a plain SELECT.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from api.app import core_repo
from api.app.cache import TTLCache
from api.app.config import settings
from api.app.db import session_scope
from api.app.models import EventSeat, GAInventory, Hold, HoldItem
from api.app.services.errors import NotFound
from api.app.services.locks import named_lock


def seat_ticket_types(
    seats: Iterable[core_repo.SeatRow], ticket_types: Iterable[core_repo.TicketTypeRow]
) -> dict[int, int | None]:
    """Which ticket type (and therefore which price) each physical seat sells as.

    The server decides pricing, never the browser: a seat in section "Balcony"
    sells as the seated ticket type named "Balcony". When an event has a single
    seated type, every seat uses it. Seats that match nothing map to ``None``
    and the caller must name a type explicitly.
    """
    seated = [t for t in ticket_types if t.inventory_mode == "seated"]
    by_name = {t.name.strip().casefold(): t.id for t in seated}
    only = seated[0].id if len(seated) == 1 else None
    return {seat.id: by_name.get(seat.section.strip().casefold(), only) for seat in seats}


def ensure_event_seats(event_id: int) -> None:
    """Materialise one row per physical seat (idempotent, commit-before-return).

    Runs on its own connection so the caller's hold transaction is never holding
    a pooled connection while this one is in use.
    """
    seats = core_repo.seats_for_event(event_id)
    if not seats:
        return
    with session_scope() as session:
        existing = set(
            session.scalars(select(EventSeat.seat_id).where(EventSeat.event_id == event_id))
        )
        missing = [seat for seat in seats if seat.id not in existing]
        if not missing:
            return
        with named_lock(session, f"seed-seats:{event_id}"):
            existing = set(
                session.scalars(select(EventSeat.seat_id).where(EventSeat.event_id == event_id))
            )
            missing = [seat for seat in seats if seat.id not in existing]
            if missing:
                session.execute(
                    insert(EventSeat).prefix_with("IGNORE"),
                    [
                        {
                            "event_id": event_id,
                            "seat_id": seat.id,
                            "status": "available",
                            "version": 0,
                        }
                        for seat in missing
                    ],
                )


def ensure_ga_inventory(ticket_type_id: int, quantity_total: int) -> None:
    with session_scope() as session:
        exists = session.execute(
            select(GAInventory.id).where(GAInventory.ticket_type_id == ticket_type_id)
        ).first()
        if exists:
            return
        with named_lock(session, f"seed-ga:{ticket_type_id}"):
            exists = session.execute(
                select(GAInventory.id).where(GAInventory.ticket_type_id == ticket_type_id)
            ).first()
            if not exists:
                session.execute(
                    insert(GAInventory).prefix_with("IGNORE"),
                    [{"ticket_type_id": ticket_type_id, "available": quantity_total}],
                )


def seat_statuses(session: Session, event_id: int) -> dict[int, str]:
    rows = session.execute(
        select(EventSeat.seat_id, EventSeat.status).where(EventSeat.event_id == event_id)
    ).all()
    return {int(seat_id): status for seat_id, status in rows}


Layout = tuple[list[core_repo.SeatRow], dict[int, core_repo.TicketTypeRow], dict[int, int | None]]

# The physical layout and its pricing change only when an organizer edits the
# event, so they share the catalogue's short cache. Seat *statuses* never do.
_layout_cache: TTLCache[Layout] = TTLCache(lambda: settings.catalogue_cache_seconds)


def _layout(event_id: int) -> Layout:
    seats = core_repo.seats_for_event(event_id)
    ticket_types = {t.id: t for t in core_repo.ticket_types_for_event(event_id)}
    return seats, ticket_types, seat_ticket_types(seats, ticket_types.values())


def seat_map(event_id: int, *, known_to_exist: bool = False) -> list[dict[str, Any]]:
    """Every seat with its live status and price, grouped by section.

    Read-only: seats that have never been held have no ``event_seat`` row yet
    and read as available, which is exactly what they are. Rows are
    materialised by the hold path (``ensure_event_seats``), not here.
    """
    if not known_to_exist and core_repo.get_event(event_id) is None:
        raise NotFound("Event not found.")
    seats, ticket_types, pricing = _layout_cache.get_or_set(
        ("layout", event_id), lambda: _layout(event_id)
    )
    with session_scope() as session:
        statuses = seat_statuses(session, event_id)

    sections: dict[str, list[dict[str, Any]]] = {}
    for seat in seats:
        ticket_type = ticket_types.get(pricing.get(seat.id) or 0)
        sections.setdefault(seat.section, []).append(
            {
                "seat_id": seat.id,
                "row": seat.row,
                "number": seat.number,
                "status": statuses.get(seat.id, "available"),
                "ticket_type_id": ticket_type.id if ticket_type else None,
                "price_cents": ticket_type.price_cents if ticket_type else None,
            }
        )
    # Theatres letter rows outward from the stage, so the section holding the
    # earliest row letter sits nearest the stage and is listed first.
    ordered = sorted(sections.items(), key=lambda pair: (min(s["row"] for s in pair[1]), pair[0]))
    return [
        {"name": name, "seats": sorted(items, key=lambda item: (item["row"], item["number"]))}
        for name, items in ordered
    ]


def unsold_units(session: Session, event_id: int) -> int | None:
    """Units that could still become a ticket: available *or* currently held.

    Held stock counts as unsold because an abandoned hold returns to sale, so an
    event only reads as sold out once every unit has actually been paid for.
    Returns ``None`` when the event has no ticket types (nothing to sell yet).
    """
    ticket_types = core_repo.ticket_types_for_event(event_id)
    if not ticket_types:
        return None

    unsold = 0
    if any(t.inventory_mode == "seated" for t in ticket_types):
        ensure_event_seats(event_id)
        unsold += int(
            session.scalar(
                select(func.count())
                .select_from(EventSeat)
                .where(EventSeat.event_id == event_id, EventSeat.status.in_(["available", "held"]))
            )
            or 0
        )

    for ticket_type in (t for t in ticket_types if t.inventory_mode == "ga"):
        available = session.scalar(
            select(GAInventory.available).where(GAInventory.ticket_type_id == ticket_type.id)
        )
        if available is None:  # never materialised: nothing has been sold yet
            unsold += ticket_type.quantity_total
            continue
        held = session.scalar(
            select(func.coalesce(func.sum(HoldItem.quantity), 0))
            .join(Hold, Hold.id == HoldItem.hold_id)
            .where(
                HoldItem.ticket_type_id == ticket_type.id,
                HoldItem.event_seat_id.is_(None),
                Hold.status == "active",
            )
        )
        unsold += int(available) + int(held or 0)
    return unsold
