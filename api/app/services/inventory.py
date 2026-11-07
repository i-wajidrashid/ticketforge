"""Seat maps and general-admission counters.

Seat rows are materialised lazily per event. The first materialisation is a
rare, one-off operation guarded by a MySQL named lock: concurrent INSERTs into
the same unique index would otherwise be able to deadlock with the holds that
immediately follow. Once rows exist this path is a plain SELECT.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from api.app import core_repo
from api.app.db import session_scope
from api.app.models import EventSeat, GAInventory
from api.app.services.errors import NotFound
from api.app.services.locks import named_lock


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


def seat_map(event_id: int) -> list[dict[str, Any]]:
    event = core_repo.get_event(event_id)
    if event is None:
        raise NotFound("Event not found.")
    seats = core_repo.seats_for_event(event_id)
    with session_scope() as session:
        ensure_event_seats(event_id)
        statuses = seat_statuses(session, event_id)

    sections: dict[str, list[dict[str, Any]]] = {}
    for seat in seats:
        sections.setdefault(seat.section, []).append(
            {
                "seat_id": seat.id,
                "row": seat.row,
                "number": seat.number,
                "status": statuses.get(seat.id, "available"),
            }
        )
    return [
        {"name": name, "seats": sorted(items, key=lambda item: (item["row"], item["number"]))}
        for name, items in sorted(sections.items())
    ]


def ga_available(ticket_type_id: int) -> int:
    with session_scope() as session:
        available = session.execute(
            text("SELECT available FROM ga_inventory WHERE ticket_type_id = :id"),
            {"id": ticket_type_id},
        ).scalar()
    return int(available) if available is not None else 0
