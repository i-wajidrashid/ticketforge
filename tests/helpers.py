"""Shared scenario builders for tests."""

from __future__ import annotations

from api.app.models import Order, Refund
from api.app.services import holds, payments


def make_paid_order(
    db_session,
    event,
    customer,
    *,
    seat_index: int = 0,
    quantity: int | None = None,
    key: str = "pay-key",
) -> Order:
    seat_ids = None
    ticket_type_id = None
    if quantity is None:
        seat_ids = [event.seats[seat_index].id]
    else:
        ticket_type_id = event.ticket_type.pk
    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=event.event.pk if hasattr(event, "event") else event.pk,
        seat_ids=seat_ids,
        ticket_type_id=ticket_type_id,
        quantity=quantity,
    )
    payment = payments.start_payment(
        db_session, user_id=customer.pk, hold_id=hold.id, idempotency_key=f"{key}-{customer.pk}"
    )
    payments.handle_webhook_event(
        db_session,
        stripe_event_id=f"evt_{key}_{customer.pk}",
        event_type="payment_intent.succeeded",
        payload={
            "id": f"evt_{key}_{customer.pk}",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": payment.payment_intent_id}},
        },
    )
    order = db_session.get(Order, payment.order_id)
    assert order is not None
    return order


def make_refund(db_session, order: Order, *, reason: str = "test") -> Refund:
    return payments.request_refund(
        db_session, user_id=order.user_id, order_id=order.id, reason=reason
    )
