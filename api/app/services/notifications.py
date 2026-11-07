"""Post-payment side effects: realtime fan-out and confirmation email."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.app import core_repo
from api.app.models import EventSeat, Order, Ticket
from api.app.realtime import sale, seat_updates

logger = logging.getLogger(__name__)


def broadcast_order_seats(session: Session, order: Order, status: str) -> None:
    """Tell every open seat map that this order's seats are now ``status``."""
    seat_pks = [
        pk
        for pk in session.scalars(select(Ticket.event_seat_id).where(Ticket.order_id == order.id))
        if pk is not None
    ]
    if not seat_pks:
        return
    rows = session.execute(
        select(EventSeat.seat_id, EventSeat.status).where(EventSeat.id.in_(seat_pks))
    ).all()
    labels = {
        seat.id: f"{seat.row}{seat.number}" for seat in core_repo.seats_for_event(order.event_id)
    }
    seat_updates(
        order.event_id,
        [
            {
                "seat_id": int(seat_id),
                "label": labels.get(int(seat_id), str(seat_id)),
                "status": status,
            }
            for seat_id, current in rows
            # A refund after the event started keeps the seat sold; say nothing then.
            if current == status
        ],
    )


def broadcast_refunded_order(session: Session, order_id: str) -> None:
    """Seats a refund returned to sale show as free again without a reload."""
    order = session.get(Order, order_id)
    if order is not None and order.status == "refunded":
        broadcast_order_seats(session, order, "available")


def broadcast_paid_order(session: Session, payment_intent_id: str) -> None:
    order = session.scalars(
        select(Order).where(Order.stripe_payment_intent_id == payment_intent_id)
    ).first()
    if order is None or order.status != "paid":
        return
    tickets = list(session.scalars(select(Ticket).where(Ticket.order_id == order.id)).all())
    broadcast_order_seats(session, order, "sold")
    sale(order.event_id, len(tickets), order.total_cents)

    from worker.tasks import refresh_event_availability, send_order_confirmation

    try:
        send_order_confirmation.delay(order.id)
    except Exception:
        logger.warning("could not enqueue confirmation email", exc_info=True)
    try:
        # The last ticket may just have sold: let the worker flip it to sold out.
        refresh_event_availability.delay(order.event_id)
    except Exception:
        logger.warning("could not enqueue availability refresh", exc_info=True)
