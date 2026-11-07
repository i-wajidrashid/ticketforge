"""Checkout idempotency, webhook replay safety and late-payment refunds."""

from __future__ import annotations

from datetime import timedelta

import pytest
from api.app.models import AuditLog, EventSeat, Order, Refund, Ticket, utcnow
from api.app.services import holds, payments
from api.app.services.errors import HoldExpired

pytestmark = pytest.mark.django_db(transaction=True)


def _pay(db_session, seated_event, customer, *, seat_index: int = 0, key: str = "idem-1"):
    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[seat_index].id],
    )
    return hold, payments.start_payment(
        db_session, user_id=customer.pk, hold_id=hold.id, idempotency_key=key
    )


def _succeeded(payment_intent_id: str, event_id: str = "evt_1") -> dict:
    return {
        "id": event_id,
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": payment_intent_id, "metadata": {}}},
    }


def test_pay_creates_one_order_and_payment_intent(seated_event, db_session, customer):
    hold, payment = _pay(db_session, seated_event, customer)

    assert payment.amount_cents == 5000
    assert payment.payment_intent_id.startswith("pi_fake_")
    order = db_session.get(Order, payment.order_id)
    assert order.status == "pending"
    assert order.hold_id == hold.id

    again = payments.start_payment(
        db_session, user_id=customer.pk, hold_id=hold.id, idempotency_key="idem-1"
    )
    assert again.order_id == order.id

    # A different key for the same hold must not charge twice either.
    third = payments.start_payment(
        db_session, user_id=customer.pk, hold_id=hold.id, idempotency_key="idem-2"
    )
    assert third.order_id == order.id
    assert db_session.query(Order).count() == 1


def test_paying_an_expired_hold_is_refused(seated_event, db_session, customer):
    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    row = db_session.get(__import__("api.app.models", fromlist=["Hold"]).Hold, hold.id)
    row.expires_at = utcnow() - timedelta(seconds=5)
    db_session.commit()

    with pytest.raises(HoldExpired):
        payments.start_payment(
            db_session, user_id=customer.pk, hold_id=hold.id, idempotency_key="late"
        )


def test_webhook_fulfills_order_and_allocates_tickets(seated_event, db_session, customer):
    _, payment = _pay(db_session, seated_event, customer)
    result = payments.handle_webhook_event(
        db_session,
        stripe_event_id="evt_1",
        event_type="payment_intent.succeeded",
        payload=_succeeded(payment.payment_intent_id),
    )
    assert result == {"status": "processed", "result": "fulfilled"}

    order = db_session.get(Order, payment.order_id)
    assert order.status == "paid"
    tickets = db_session.query(Ticket).filter_by(order_id=order.id).all()
    assert len(tickets) == 1
    assert tickets[0].qr_token.startswith(tickets[0].id)
    seat = db_session.query(EventSeat).filter(EventSeat.seat_id == seated_event.seats[0].id).one()
    assert seat.status == "sold"


def test_duplicate_webhook_creates_one_order_and_one_set_of_tickets(
    seated_event, db_session, customer
):
    _, payment = _pay(db_session, seated_event, customer)
    payload = _succeeded(payment.payment_intent_id)

    first = payments.handle_webhook_event(
        db_session,
        stripe_event_id="evt_dup",
        event_type="payment_intent.succeeded",
        payload=payload,
    )
    second = payments.handle_webhook_event(
        db_session,
        stripe_event_id="evt_dup",
        event_type="payment_intent.succeeded",
        payload=payload,
    )
    third = payments.handle_webhook_event(
        db_session,
        stripe_event_id="evt_dup",
        event_type="payment_intent.succeeded",
        payload=payload,
    )

    assert first["status"] == "processed"
    assert second["status"] == "duplicate"
    assert third["status"] == "duplicate"
    assert db_session.query(Order).count() == 1
    assert db_session.query(Ticket).count() == 1


def test_late_payment_after_seat_resold_is_refunded(seated_event, db_session, customer):
    from tests.factories import UserFactory

    hold, payment = _pay(db_session, seated_event, customer)
    row = db_session.get(__import__("api.app.models", fromlist=["Hold"]).Hold, hold.id)
    row.expires_at = utcnow() - timedelta(seconds=5)
    db_session.commit()
    holds.expire_holds()

    # Someone else takes the seat while the webhook is in flight.
    other = UserFactory()
    holds.create_hold(
        db_session,
        user_id=other.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )

    result = payments.handle_webhook_event(
        db_session,
        stripe_event_id="evt_late",
        event_type="payment_intent.succeeded",
        payload=_succeeded(payment.payment_intent_id, "evt_late"),
    )
    assert result["result"] == "refunded_late_payment"

    order = db_session.get(Order, payment.order_id)
    assert order.status == "refunded"
    refund = db_session.query(Refund).filter_by(order_id=order.id).one()
    assert refund.status == "succeeded"
    assert refund.stripe_refund_id.startswith("re_fake_")
    assert db_session.query(Ticket).filter_by(order_id=order.id).count() == 0
    assert db_session.query(AuditLog).filter_by(action="late_payment_refunded").count() == 1


def test_refund_before_success_webhook_is_not_double_processed(seated_event, db_session, customer):
    _, payment = _pay(db_session, seated_event, customer)

    # Out-of-order: the refund lands before the success event.
    refund_payload = {
        "id": "evt_refund_early",
        "type": "charge.refunded",
        "data": {"object": {"payment_intent": payment.payment_intent_id}},
    }
    early = payments.handle_webhook_event(
        db_session,
        stripe_event_id="evt_refund_early",
        event_type="charge.refunded",
        payload=refund_payload,
    )
    assert early["status"] == "processed"

    late = payments.handle_webhook_event(
        db_session,
        stripe_event_id="evt_success_after_refund",
        event_type="payment_intent.succeeded",
        payload=_succeeded(payment.payment_intent_id, "evt_success_after_refund"),
    )
    assert late["result"] == "already_refunded"
    assert db_session.query(Ticket).count() == 0


def test_payment_failure_marks_the_order_failed(seated_event, db_session, customer):
    _, payment = _pay(db_session, seated_event, customer)
    failed = {
        "id": "evt_fail",
        "type": "payment_intent.payment_failed",
        "data": {"object": {"id": payment.payment_intent_id}},
    }
    payments.handle_webhook_event(
        db_session,
        stripe_event_id="evt_fail",
        event_type="payment_intent.payment_failed",
        payload=failed,
    )
    assert db_session.get(Order, payment.order_id).status == "failed"


def test_refund_for_unknown_order_is_rejected(db_session, customer):
    with pytest.raises(Exception):
        payments.request_refund(db_session, user_id=customer.pk, order_id="does-not-exist")
