from __future__ import annotations

import pytest
from api.app.services import holds, payments, tickets
from api.app.services.errors import TicketInvalid
from api.app.services.tickets import StaffNotAssigned

from tests.factories import StaffAssignmentFactory, UserFactory

pytestmark = pytest.mark.django_db(transaction=True)


def _ticket_for(db_session, seated_event, customer):
    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    payment = payments.start_payment(
        db_session, user_id=customer.pk, hold_id=hold.id, idempotency_key="scan-key"
    )
    payments.handle_webhook_event(
        db_session,
        stripe_event_id="evt_scan",
        event_type="payment_intent.succeeded",
        payload={
            "id": "evt_scan",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": payment.payment_intent_id}},
        },
    )
    return tickets.order_tickets(db_session, payment.order_id)[0]


def test_scan_once_then_reject_with_first_scan_time(seated_event, db_session, customer):
    staff = UserFactory(role="door_staff")
    StaffAssignmentFactory(user=staff, event=seated_event.event)
    ticket = _ticket_for(db_session, seated_event, customer)

    first = tickets.scan_ticket(db_session, qr_token=ticket.qr_token, staff_user_id=staff.pk)
    assert first["result"] == "valid"

    second = tickets.scan_ticket(db_session, qr_token=ticket.qr_token, staff_user_id=staff.pk)
    assert second["result"] == "already_used"
    assert second["checked_in_at"] == first["checked_in_at"]


def test_unassigned_staff_cannot_scan(seated_event, db_session, customer):
    outsider = UserFactory(role="door_staff")
    ticket = _ticket_for(db_session, seated_event, customer)
    with pytest.raises(StaffNotAssigned):
        tickets.scan_ticket(db_session, qr_token=ticket.qr_token, staff_user_id=outsider.pk)


def test_bad_qr_token_is_invalid(seated_event, db_session, customer):
    staff = UserFactory(role="door_staff")
    StaffAssignmentFactory(user=staff, event=seated_event.event)
    with pytest.raises(TicketInvalid):
        tickets.scan_ticket(db_session, qr_token="deadbeef.not-a-signature", staff_user_id=staff.pk)
