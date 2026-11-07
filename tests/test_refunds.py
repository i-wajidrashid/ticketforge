from __future__ import annotations

import pytest
from api.app.models import EventSeat, Order, Refund, Ticket
from api.app.services import holds, payments

pytestmark = pytest.mark.django_db(transaction=True)


def _paid_order(db_session, seated_event, customer):
    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    payment = payments.start_payment(
        db_session, user_id=customer.pk, hold_id=hold.id, idempotency_key="pay-refund"
    )
    payments.handle_webhook_event(
        db_session,
        stripe_event_id="evt_paid",
        event_type="payment_intent.succeeded",
        payload={
            "id": "evt_paid",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": payment.payment_intent_id}},
        },
    )
    return db_session.get(Order, payment.order_id)


def test_refund_returns_the_seat_and_marks_tickets(seated_event, db_session, customer):
    order = _paid_order(db_session, seated_event, customer)
    refund = payments.request_refund(
        db_session, user_id=customer.pk, order_id=order.id, reason="can't make it"
    )
    assert refund.status == "requested"

    result = payments.process_refund(
        db_session, refund_id=refund.id, actor_id=seated_event.organizer_user.pk
    )
    assert result["status"] == "refunded"

    db_session.expire_all()
    assert db_session.get(Order, order.id).status == "refunded"
    assert db_session.get(Refund, refund.id).status == "succeeded"
    assert {ticket.status for ticket in db_session.query(Ticket).all()} == {"refunded"}
    seat = db_session.query(EventSeat).filter(EventSeat.seat_id == seated_event.seats[0].id).one()
    assert seat.status == "available"


def test_refund_is_idempotent(seated_event, db_session, customer):
    order = _paid_order(db_session, seated_event, customer)
    refund = payments.request_refund(db_session, user_id=customer.pk, order_id=order.id)
    payments.process_refund(db_session, refund_id=refund.id, actor_id=None)
    second = payments.process_refund(db_session, refund_id=refund.id, actor_id=None)
    assert second["status"] == "already_refunded"
    assert db_session.query(Refund).count() == 1


def test_cannot_request_two_refunds(seated_event, db_session, customer):
    order = _paid_order(db_session, seated_event, customer)
    payments.request_refund(db_session, user_id=customer.pk, order_id=order.id)
    with pytest.raises(Exception):
        payments.request_refund(db_session, user_id=customer.pk, order_id=order.id)


def test_rejecting_a_refund_leaves_the_order_paid(seated_event, db_session, customer):
    order = _paid_order(db_session, seated_event, customer)
    refund = payments.request_refund(db_session, user_id=customer.pk, order_id=order.id)
    result = payments.reject_refund(db_session, refund_id=refund.id, actor_id=None, reason="no")
    assert result["status"] == "rejected"
    db_session.expire_all()
    assert db_session.get(Order, order.id).status == "paid"
    assert db_session.get(Refund, refund.id).status == "rejected"
