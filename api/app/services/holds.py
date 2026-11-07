"""Seat and inventory holds: the concurrency-critical core of the platform.

Rules that make double selling impossible:
1. every seat transition is a conditional UPDATE (rows affected must be 1);
2. multi-seat holds touch seats in ascending id order (no deadlocks);
3. general admission is a single guarded decrement;
4. expired holds are released by a sweeper *and* re-checked inline at payment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from common.realtime import seat_group  # noqa: F401  (re-exported for convenience)
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from api.app import core_repo
from api.app.config import settings
from api.app.db import affected, session_scope
from api.app.models import EventSeat, GAInventory, Hold, HoldItem, utcnow
from api.app.services import rate_limit
from api.app.services.errors import (
    EventNotSellable,
    HoldExists,
    HoldNotFound,
    InvalidQuantity,
    NotFound,
    SaleWindowClosed,
    SeatUnavailable,
    SoldOut,
)
from api.app.services.inventory import ensure_event_seats, ensure_ga_inventory, seat_ticket_types
from api.app.services.locks import named_lock


@dataclass(slots=True)
class HoldItemView:
    event_seat_id: int | None
    seat_label: str | None
    ticket_type_id: int
    quantity: int


@dataclass(slots=True)
class HoldView:
    id: str
    event_id: int
    status: str
    expires_at: datetime
    seconds_left: int
    total_cents: int
    currency: str
    items: list[HoldItemView] = field(default_factory=list)


def _validate_window(ticket_type: core_repo.TicketTypeRow, now: datetime) -> None:
    if now < (ticket_type.sale_starts_at or now):
        raise SaleWindowClosed(f"Sales for {ticket_type.name} have not started yet.")
    if ticket_type.sale_ends_at and now > ticket_type.sale_ends_at:
        raise SaleWindowClosed(f"Sales for {ticket_type.name} have ended.")


def _seated_ticket_type(
    ticket_types: dict[int, core_repo.TicketTypeRow], ticket_type_id: int | None
) -> core_repo.TicketTypeRow:
    """Fallback for seats whose section maps to no ticket type (see
    ``inventory.seat_ticket_types``): the buyer must name the type."""
    if ticket_type_id is None:
        raise InvalidQuantity(
            "ticket_type_id is required when the event has multiple seated types."
        )
    ticket_type = ticket_types.get(ticket_type_id)
    if ticket_type is None or ticket_type.inventory_mode != "seated":
        raise EventNotSellable("That ticket type does not belong to this event.")
    return ticket_type


def _active_holds(session: Session, user_id: int, event_id: int) -> list[Hold]:
    return list(
        session.scalars(
            select(Hold).where(
                Hold.user_id == user_id,
                Hold.event_id == event_id,
                Hold.status == "active",
            )
        ).all()
    )


def release_hold(session: Session, hold: Hold, *, status: str = "cancelled") -> dict[str, Any]:
    """Return every seat and every unit of inventory to sale. Idempotent."""
    if hold.status != "active":
        return {"hold_id": hold.id, "event_id": hold.event_id, "seats": [], "released": False}

    released: list[dict[str, Any]] = []
    seat_items = [item for item in hold.items if item.event_seat_id is not None]
    if seat_items:
        seat_ids = [
            int(item.event_seat_id) for item in seat_items if item.event_seat_id is not None
        ]
        labels = {
            seat.id: f"{seat.row}{seat.number}" for seat in core_repo.seats_for_event(hold.event_id)
        }
        session.execute(
            update(EventSeat)
            .where(EventSeat.hold_id == hold.id, EventSeat.status == "held")
            .values(status="available", hold_id=None, version=EventSeat.version + 1)
        )
        released.extend(
            {"seat_id": seat_id, "label": labels.get(seat_id, str(seat_id)), "status": "available"}
            for seat_id in seat_ids
        )

    for item in hold.items:
        if item.event_seat_id is None:
            session.execute(
                update(GAInventory)
                .where(GAInventory.ticket_type_id == item.ticket_type_id)
                .values(available=GAInventory.available + item.quantity)
            )

    hold.status = status
    return {"hold_id": hold.id, "event_id": hold.event_id, "seats": released, "released": True}


def create_hold(
    session: Session,
    *,
    user_id: int,
    event_id: int,
    seat_ids: list[int] | None = None,
    ticket_type_id: int | None = None,
    quantity: int | None = None,
) -> HoldView:
    now = utcnow()
    event = core_repo.get_event(event_id)
    if event is None:
        raise NotFound("Event not found.")
    if event.status not in core_repo.SELLABLE_STATUSES:
        raise EventNotSellable("Tickets are not on sale for this event.")
    if event.starts_at <= now:
        raise EventNotSellable("This event has already started.")

    ticket_types = {t.id: t for t in core_repo.ticket_types_for_event(event_id)}
    if not ticket_types:
        raise EventNotSellable("This event has no ticket types yet.")

    rate_limit.enforce(f"holds:{user_id}", settings.hold_rate_limit_per_minute)

    # Materialise inventory before the hold transaction starts: the seeding path
    # uses its own connection and commits, so it must never interleave with the
    # caller's row locks.
    if seat_ids:
        ensure_event_seats(event_id)
    elif ticket_type_id is not None:
        preseed = ticket_types.get(ticket_type_id)
        if preseed is not None and preseed.inventory_mode == "ga":
            ensure_ga_inventory(preseed.id, preseed.quantity_total)

    created: Hold | None = None
    with named_lock(session, f"hold:{user_id}:{event_id}"):
        active = _active_holds(session, user_id, event_id)
        for hold in active:
            if hold.expires_at <= now:
                release_hold(session, hold, status="expired")
        session.flush()

        active = _active_holds(session, user_id, event_id)
        if len(active) >= settings.max_active_holds_per_event:
            raise HoldExists("You already have an active hold for this event.")

        if seat_ids:
            if quantity is not None:
                raise InvalidQuantity("Provide either seats or a quantity, not both.")
            seat_ids = sorted({int(seat_id) for seat_id in seat_ids})
            venue_seats = core_repo.seats_for_event(event_id)
            known = {seat.id for seat in venue_seats}
            unknown = [seat_id for seat_id in seat_ids if seat_id not in known]
            if unknown:
                raise SeatUnavailable("Some of those seats do not exist.", {"seat_ids": unknown})

            # Price every seat on the server; a client-supplied type is only a
            # fallback for seats whose section maps to no ticket type.
            pricing = seat_ticket_types(venue_seats, ticket_types.values())
            seat_types: dict[int, core_repo.TicketTypeRow] = {}
            for seat_id in seat_ids:
                mapped = pricing.get(seat_id)
                seat_types[seat_id] = (
                    ticket_types[mapped]
                    if mapped is not None
                    else _seated_ticket_type(ticket_types, ticket_type_id)
                )
            per_type: dict[int, int] = {}
            for chosen in seat_types.values():
                per_type[chosen.id] = per_type.get(chosen.id, 0) + 1
            for type_id, count in per_type.items():
                chosen = ticket_types[type_id]
                _validate_window(chosen, now)
                if count > chosen.max_per_order:
                    raise InvalidQuantity(
                        f"At most {chosen.max_per_order} {chosen.name} seats per order."
                    )

            hold = Hold(
                user_id=user_id,
                event_id=event_id,
                expires_at=now + timedelta(minutes=settings.hold_ttl_minutes),
                status="active",
            )
            session.add(hold)
            session.flush()

            for seat_id in seat_ids:
                result = session.execute(
                    update(EventSeat)
                    .where(
                        EventSeat.event_id == event_id,
                        EventSeat.seat_id == seat_id,
                        EventSeat.status == "available",
                    )
                    .values(status="held", hold_id=hold.id, version=EventSeat.version + 1)
                )
                if affected(result) != 1:
                    session.rollback()
                    raise SeatUnavailable(
                        "Someone just took one of those seats.", {"seat_ids": [seat_id]}
                    )
                session.add(
                    HoldItem(
                        hold_id=hold.id,
                        event_seat_id=_event_seat_pk(session, event_id, seat_id),
                        ticket_type_id=seat_types[seat_id].id,
                        quantity=1,
                    )
                )
            created = hold

        else:
            if quantity is None or quantity < 1:
                raise InvalidQuantity("Choose at least one ticket.")
            if ticket_type_id is None:
                raise InvalidQuantity("ticket_type_id is required for general admission.")
            selected = ticket_types.get(ticket_type_id)
            if selected is None:
                raise EventNotSellable("That ticket type does not belong to this event.")
            if selected.inventory_mode != "ga":
                raise InvalidQuantity("This ticket type requires seat selection.")
            if quantity > selected.max_per_order:
                raise InvalidQuantity(f"At most {selected.max_per_order} tickets per order.")
            _validate_window(selected, now)
            ticket_type = selected

            result = session.execute(
                update(GAInventory)
                .where(
                    GAInventory.ticket_type_id == ticket_type.id, GAInventory.available >= quantity
                )
                .values(available=GAInventory.available - quantity)
            )
            if affected(result) != 1:
                session.rollback()
                raise SoldOut("That ticket type is sold out.")

            hold = Hold(
                user_id=user_id,
                event_id=event_id,
                expires_at=now + timedelta(minutes=settings.hold_ttl_minutes),
                status="active",
            )
            session.add(hold)
            session.flush()
            session.add(
                HoldItem(
                    hold_id=hold.id,
                    event_seat_id=None,
                    ticket_type_id=ticket_type.id,
                    quantity=quantity,
                )
            )
            created = hold

    session.commit()
    if created is None:  # pragma: no cover - defensive
        raise RuntimeError("hold was not created")
    return build_hold_view(session, created)


def _event_seat_pk(session: Session, event_id: int, seat_id: int) -> int:
    pk = session.execute(
        select(EventSeat.id).where(EventSeat.event_id == event_id, EventSeat.seat_id == seat_id)
    ).scalar_one()
    return int(pk)


def load_hold(session: Session, hold_id: str, *, user_id: int | None = None) -> Hold:
    hold = session.get(Hold, hold_id)
    if hold is None or (user_id is not None and hold.user_id != user_id):
        raise HoldNotFound("Hold not found.")
    return hold


def expire_if_needed(session: Session, hold: Hold) -> Hold:
    if hold.status == "active" and hold.expires_at <= utcnow():
        release_hold(session, hold, status="expired")
        session.commit()
    return hold


def get_hold_view(session: Session, hold_id: str, *, user_id: int | None = None) -> HoldView:
    hold = load_hold(session, hold_id, user_id=user_id)
    expire_if_needed(session, hold)
    return build_hold_view(session, hold)


def cancel_hold(session: Session, hold_id: str, *, user_id: int) -> HoldView:
    hold = load_hold(session, hold_id, user_id=user_id)
    if hold.status == "active":
        release_hold(session, hold, status="cancelled")
        session.commit()
    return build_hold_view(session, hold)


def build_hold_view(session: Session, hold: Hold) -> HoldView:
    items = list(session.scalars(select(HoldItem).where(HoldItem.hold_id == hold.id)).all())
    labels: dict[int, str] = {}
    if any(item.event_seat_id for item in items):
        seat_pks = {int(item.event_seat_id) for item in items if item.event_seat_id}
        rows = session.execute(
            select(EventSeat.id, EventSeat.seat_id).where(EventSeat.id.in_(seat_pks))
        ).all()
        seat_ids = {int(seat_pk): int(seat_id) for seat_pk, seat_id in rows}
        labels = {
            seat.id: f"{seat.row}{seat.number}"
            for seat in core_repo.seats_for_event(hold.event_id)
            if seat.id in set(seat_ids.values())
        }
        label_by_pk = {pk: labels.get(seat_id, str(seat_id)) for pk, seat_id in seat_ids.items()}
    else:
        label_by_pk = {}

    ticket_types = {t.id: t for t in core_repo.ticket_types_for_event(hold.event_id)}
    total = 0
    currency = "USD"
    views: list[HoldItemView] = []
    for item in items:
        ticket_type = ticket_types.get(item.ticket_type_id)
        if ticket_type is not None:
            total += ticket_type.price_cents * item.quantity
            currency = ticket_type.currency
        views.append(
            HoldItemView(
                event_seat_id=int(item.event_seat_id) if item.event_seat_id else None,
                seat_label=label_by_pk.get(int(item.event_seat_id)) if item.event_seat_id else None,
                ticket_type_id=item.ticket_type_id,
                quantity=item.quantity,
            )
        )

    seconds_left = max(0, int((hold.expires_at - utcnow()).total_seconds()))
    return HoldView(
        id=hold.id,
        event_id=hold.event_id,
        status=hold.status,
        expires_at=hold.expires_at,
        seconds_left=seconds_left,
        total_cents=total,
        currency=currency,
        items=views,
    )


def expire_holds(now: datetime | None = None) -> list[dict[str, Any]]:
    """Celery Beat entrypoint: release every hold past its deadline."""
    moment = now or utcnow()
    released: list[dict[str, Any]] = []
    with session_scope() as session:
        # Rows come back locked FOR UPDATE, so their status cannot change under
        # us; SKIP LOCKED lets a second sweeper (or a paying customer) proceed
        # on other rows instead of queueing behind this batch.
        due = session.scalars(
            select(Hold)
            .where(Hold.status == "active", Hold.expires_at <= moment)
            .with_for_update(skip_locked=True)
            .limit(500)
        ).all()
        for hold in due:
            released.append(release_hold(session, hold, status="expired"))
    return released


def active_hold_count(event_id: int) -> int:
    with session_scope() as session:
        count = session.scalar(
            select(func.count())
            .select_from(Hold)
            .where(Hold.event_id == event_id, Hold.status == "active")
        )
    return int(count or 0)


def seat_changes_for_hold(session: Session, hold_id: str, status: str) -> list[dict[str, Any]]:
    """Seat ids + human labels for a realtime broadcast."""
    rows = session.execute(
        select(EventSeat.event_id, EventSeat.seat_id).where(EventSeat.hold_id == hold_id)
    ).all()
    if not rows:
        return []
    event_id = int(rows[0][0])
    labels = {seat.id: f"{seat.row}{seat.number}" for seat in core_repo.seats_for_event(event_id)}
    return [
        {"seat_id": int(seat_id), "label": labels.get(int(seat_id), str(seat_id)), "status": status}
        for _, seat_id in rows
    ]
