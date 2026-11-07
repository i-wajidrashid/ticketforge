from __future__ import annotations

from datetime import timedelta

import pytest
from api.app.models import Hold, Order, Ticket
from api.app.services import payments
from api.app.services.errors import HoldNotFound, NotFound, RefundNotAllowed
from django.utils import timezone

from tests.factories import UserFactory
from tests.helpers import make_paid_order, make_refund

pytestmark = pytest.mark.django_db(transaction=True)


def test_webhook_for_unknown_intent_is_ignored(db_session):
    result = payments.handle_webhook_event(
        db_session,
        stripe_event_id="evt_unknown",
        event_type="payment_intent.succeeded",
        payload={
            "id": "evt_unknown",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_nope"}},
        },
    )
    assert result["result"] == "unknown_order"


def test_unknown_event_type_is_recorded(db_session):
    result = payments.handle_webhook_event(
        db_session,
        stripe_event_id="evt_other",
        event_type="customer.created",
        payload={"id": "evt_other", "type": "customer.created"},
    )
    assert result["result"] == "ignored"


def test_pay_requires_ownership(seated_event, db_session, customer):
    from api.app.services import holds

    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    with pytest.raises(HoldNotFound):
        payments.start_payment(
            db_session, user_id=UserFactory().pk, hold_id=hold.id, idempotency_key="someone-else"
        )


def test_cancelled_hold_cannot_be_paid(seated_event, db_session, customer):
    from api.app.services import holds
    from api.app.services.errors import HoldNotActive

    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    holds.cancel_hold(db_session, hold.id, user_id=customer.pk)
    with pytest.raises(HoldNotActive):
        payments.start_payment(
            db_session, user_id=customer.pk, hold_id=hold.id, idempotency_key="cancelled-key"
        )


def test_refund_requires_a_paid_order(seated_event, db_session, customer):
    from api.app.services import holds

    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    payment = payments.start_payment(
        db_session, user_id=customer.pk, hold_id=hold.id, idempotency_key="pending-refund"
    )
    with pytest.raises(RefundNotAllowed):
        payments.request_refund(db_session, user_id=customer.pk, order_id=payment.order_id)


def test_refund_after_event_start_is_rejected(seated_event, db_session, customer):
    order = make_paid_order(db_session, seated_event, customer)
    seated_event.event.starts_at = timezone.now() - timedelta(hours=1)
    seated_event.event.save(update_fields=["starts_at"])
    with pytest.raises(RefundNotAllowed):
        payments.request_refund(db_session, user_id=customer.pk, order_id=order.id)


def test_unknown_refund_ids(db_session):
    with pytest.raises(NotFound):
        payments.process_refund(db_session, refund_id=999_999, actor_id=None)
    with pytest.raises(NotFound):
        payments.reject_refund(db_session, refund_id=999_999, actor_id=None)


def test_refund_webhook_for_unknown_intent_is_ignored(db_session):
    result = payments.handle_webhook_event(
        db_session,
        stripe_event_id="evt_refund_unknown",
        event_type="charge.refunded",
        payload={
            "id": "evt_refund_unknown",
            "type": "charge.refunded",
            "data": {"object": {"payment_intent": "pi_unknown"}},
        },
    )
    assert result["result"] == "unknown_order"


def test_reconcile_flags_paid_orders_without_tickets(db_session):
    from api.app.models import utcnow

    hold = Hold(user_id=1, event_id=1, expires_at=utcnow(), status="converted")
    db_session.add(hold)
    db_session.flush()
    db_session.add(
        Order(
            user_id=1,
            event_id=1,
            hold_id=hold.id,
            total_cents=1000,
            currency="USD",
            status="paid",
            idempotency_key="orphan-order",
            stripe_payment_intent_id="pi_orphan",
        )
    )
    db_session.commit()

    result = payments.reconcile_payments(db_session)
    assert any(problem["problem"] == "paid_without_tickets" for problem in result["problems"])


def test_orders_and_tickets_for_user(seated_event, db_session, customer):
    order = make_paid_order(db_session, seated_event, customer)
    assert [item.id for item in payments.orders_for_user(db_session, customer.pk)] == [order.id]
    assert len(payments.tickets_for_order(db_session, order.id)) == 1
    assert payments.refunds_for_event(db_session, seated_event.event.pk) == []


def test_rejecting_a_succeeded_refund_is_a_noop(seated_event, db_session, customer):
    order = make_paid_order(db_session, seated_event, customer)
    refund = make_refund(db_session, order)
    payments.process_refund(db_session, refund_id=refund.id, actor_id=None)
    result = payments.reject_refund(db_session, refund_id=refund.id, actor_id=None)
    assert result["status"] == "succeeded"


def test_ticket_pdf_ownership(seated_event, db_session, customer):
    from api.app.services import tickets
    from api.app.services.errors import TicketInvalid

    order = make_paid_order(db_session, seated_event, customer)
    ticket = db_session.query(Ticket).filter_by(order_id=order.id).one()

    with pytest.raises(NotFound):
        tickets.ticket_pdf_payload(db_session, user_id=UserFactory().pk, ticket_id=ticket.id)

    ticket.status = "refunded"
    db_session.commit()
    with pytest.raises(TicketInvalid):
        tickets.ticket_pdf_payload(db_session, user_id=customer.pk, ticket_id=ticket.id)
