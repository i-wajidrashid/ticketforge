"""Post-payment side effects: realtime fan-out and confirmation email."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.app import core_repo
from api.app.models import EventSeat, Order, Ticket
from api.app.realtime import sale, seat_updates

logger = logging.getLogger(__name__)


def broadcast_paid_order(session: Session, payment_intent_id: str) -> None:
    order = session.scalars(
        select(Order).where(Order.stripe_payment_intent_id == payment_intent_id)
    ).first()
    if order is None or order.status != "paid":
        return
    tickets = list(session.scalars(select(Ticket).where(Ticket.order_id == order.id)).all())
    seat_ids = [ticket.event_seat_id for ticket in tickets if ticket.event_seat_id]
    if seat_ids:
        rows = (
            session.execute(select(EventSeat.seat_id).where(EventSeat.id.in_(seat_ids)))
            .scalars()
            .all()
        )
        labels = {
            seat.id: f"{seat.row}{seat.number}"
            for seat in core_repo.seats_for_event(order.event_id)
        }
        seat_updates(
            order.event_id,
            [
                {
                    "seat_id": int(seat_id),
                    "label": labels.get(int(seat_id), str(seat_id)),
                    "status": "sold",
                }
                for seat_id in rows
            ],
        )
    sale(order.event_id, len(tickets), order.total_cents)

    from worker.tasks import send_order_confirmation

    try:
        send_order_confirmation.delay(order.id)
    except Exception:
        logger.warning("could not enqueue confirmation email", exc_info=True)
