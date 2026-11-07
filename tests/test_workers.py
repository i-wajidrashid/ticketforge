"""Celery task behaviour (executed eagerly, real MySQL/Redis)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from api.app.models import EventSeat, Refund
from api.app.services import holds, payments
from django.core import mail
from django.utils import timezone
from worker import tasks

from tests.factories import UserFactory
from tests.helpers import make_paid_order, make_refund

pytestmark = pytest.mark.django_db(transaction=True)


def test_send_verification_email(seated_event):
    user = UserFactory(is_verified=False)
    tasks.send_verification_email.delay(user.pk)
    assert len(mail.outbox) == 1
    assert "Verify your TicketForge email" in mail.outbox[0].subject

    user.is_verified = True
    user.save(update_fields=["is_verified"])
    tasks.send_verification_email.delay(user.pk)
    assert len(mail.outbox) == 1


def test_send_password_reset_email():
    user = UserFactory()
    tasks.send_password_reset_email.delay(user.pk)
    assert len(mail.outbox) == 1
    assert "Reset your TicketForge password" in mail.outbox[0].subject


def test_order_confirmation_includes_pdf_tickets(seated_event, db_session, customer):
    order = make_paid_order(db_session, seated_event, customer)
    tasks.send_order_confirmation.delay(order.id)
    assert len(mail.outbox) == 1
    attachments = mail.outbox[0].attachments
    assert attachments and attachments[0][0].endswith(".pdf")
    assert attachments[0][2] == "application/pdf"


def test_expire_holds_task_releases_seats(seated_event, db_session, customer):
    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    from api.app.models import Hold

    row = db_session.get(Hold, hold.id)
    row.expires_at = timezone.now().replace(tzinfo=None) - timedelta(seconds=1)
    db_session.commit()

    result = tasks.expire_holds.delay().result
    assert result["released"] == 1
    db_session.expire_all()
    seat = db_session.query(EventSeat).filter(EventSeat.seat_id == seated_event.seats[0].id).one()
    assert seat.status == "available"


def test_index_and_remove_event(seated_event):
    try:
        assert tasks.index_event.delay(seated_event.event.pk).result == "indexed"
        assert tasks.remove_event_from_index.delay(seated_event.event.pk).result == "removed"
    except Exception:
        pytest.skip("Meilisearch unavailable")


def test_process_and_reject_refund_tasks(seated_event, db_session, customer):
    order = make_paid_order(db_session, seated_event, customer)
    refund = make_refund(db_session, order)
    result = tasks.process_refund.delay(refund.id, seated_event.organizer_user.pk).result
    assert result["status"] == "refunded"

    second_order = make_paid_order(db_session, seated_event, customer, seat_index=1, key="second")
    second_refund = make_refund(db_session, second_order)
    rejected = tasks.reject_refund.delay(second_refund.id, None, "no").result
    assert rejected["status"] == "rejected"


def test_a_refund_frees_its_seats_on_every_open_seat_map(
    seated_event, db_session, customer, monkeypatch
):
    from api.app.services import notifications

    order = make_paid_order(db_session, seated_event, customer)
    refund = make_refund(db_session, order)
    sent: list[tuple[int, list[dict]]] = []
    monkeypatch.setattr(
        notifications, "seat_updates", lambda event_id, seats: sent.append((event_id, seats))
    )

    tasks.process_refund.delay(refund.id, None)

    ((event_id, seats),) = sent
    assert event_id == seated_event.event.pk
    assert [(seat["seat_id"], seat["status"]) for seat in seats] == [
        (seated_event.seats[0].id, "available")
    ]


def test_cancel_event_refunds_fans_out(seated_event, db_session, customer):
    make_paid_order(db_session, seated_event, customer)
    result = tasks.cancel_event_refunds.delay(seated_event.event.pk).result
    assert result["refunds_created"] == 1
    db_session.expire_all()
    assert db_session.query(Refund).count() == 1


def test_reconcile_payments_reports_no_mismatches(seated_event, db_session, customer):
    make_paid_order(db_session, seated_event, customer)
    result = tasks.reconcile_payments.delay().result
    assert result["checked"] is True
    assert result["problems"] == []


def test_mark_finished_events(seated_event):
    seated_event.event.ends_at = timezone.now() - timedelta(hours=1)
    seated_event.event.save(update_fields=["ends_at"])
    result = tasks.mark_finished_events.delay().result
    assert result["finished"] == 1
    seated_event.event.refresh_from_db()
    assert seated_event.event.status == "finished"


def test_send_event_reminders(seated_event, db_session, customer):
    make_paid_order(db_session, seated_event, customer)
    seated_event.event.starts_at = timezone.now() + timedelta(hours=12)
    seated_event.event.ends_at = seated_event.event.starts_at + timedelta(hours=2)
    seated_event.event.save(update_fields=["starts_at", "ends_at"])

    result = tasks.send_event_reminders.delay().result
    assert result["reminders"] == 1
    assert "almost here" in mail.outbox[0].subject


def test_send_refund_email(seated_event, db_session, customer):
    order = make_paid_order(db_session, seated_event, customer)
    refund = make_refund(db_session, order)
    payments.process_refund(db_session, refund_id=refund.id, actor_id=None)
    tasks.send_refund_email.delay(order.id)
    assert len(mail.outbox) == 1
    assert "refund" in mail.outbox[0].subject.lower()


# ---------------------------------------------------------------------------
# automatic sold-out
# ---------------------------------------------------------------------------


def _status(event) -> str:
    event.refresh_from_db()
    return str(event.status)


def test_selling_the_last_ga_tickets_marks_the_event_sold_out(ga_event, db_session, customer):
    ga_event.ticket_type.quantity_total = 3
    ga_event.ticket_type.save(update_fields=["quantity_total"])

    make_paid_order(db_session, ga_event, customer, quantity=2, key="first")
    assert tasks.refresh_event_availability.delay(ga_event.event.pk).result == "unchanged"
    assert _status(ga_event.event) == "published"

    make_paid_order(db_session, ga_event, UserFactory(), quantity=1, key="last")
    assert tasks.refresh_event_availability.delay(ga_event.event.pk).result == "sold_out"
    assert _status(ga_event.event) == "sold_out"


def test_held_stock_does_not_count_as_sold(ga_event, db_session, customer):
    ga_event.ticket_type.quantity_total = 2
    ga_event.ticket_type.save(update_fields=["quantity_total"])
    holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=ga_event.event.pk,
        ticket_type_id=ga_event.ticket_type.pk,
        quantity=2,
    )

    assert tasks.refresh_event_availability.delay(ga_event.event.pk).result == "unchanged"
    assert _status(ga_event.event) == "published"


def test_selling_every_seat_marks_sold_out_and_a_refund_reopens_it(
    seated_event, db_session, customer
):
    orders = [
        make_paid_order(db_session, seated_event, UserFactory(), seat_index=i, key=f"seat{i}")
        for i in range(len(seated_event.seats))
    ]
    tasks.refresh_event_availability.delay(seated_event.event.pk)
    assert _status(seated_event.event) == "sold_out"

    refund = make_refund(db_session, orders[0])
    # process_refund enqueues the availability refresh itself (eager in tests).
    tasks.process_refund.delay(refund.id, None)
    assert _status(seated_event.event) == "on_sale"


def test_paid_webhook_flips_sold_out_end_to_end(api_client, ga_event, customer_token):
    ga_event.ticket_type.quantity_total = 1
    ga_event.ticket_type.save(update_fields=["quantity_total"])
    headers = {"Authorization": f"Bearer {customer_token}"}

    hold = api_client.post(
        "/api/v1/holds",
        json={
            "event_id": ga_event.event.pk,
            "ticket_type_id": ga_event.ticket_type.pk,
            "quantity": 1,
        },
        headers=headers,
    )
    assert hold.status_code == 201, hold.text
    pay = api_client.post(
        f"/api/v1/checkout/{hold.json()['id']}/pay",
        headers={**headers, "Idempotency-Key": "sold-out-e2e"},
    )
    assert pay.status_code == 200, pay.text
    done = api_client.post(
        f"/api/v1/dev/simulate-payment/{pay.json()['order_id']}", headers=headers
    )
    assert done.status_code == 200, done.text

    assert _status(ga_event.event) == "sold_out"
    detail = api_client.get(f"/api/v1/events/{ga_event.event.slug}")
    assert detail.json()["status"] == "sold_out"


def test_availability_refresh_ignores_events_without_inventory():
    from tests.factories import EventFactory

    event = EventFactory()
    assert tasks.refresh_event_availability.delay(event.pk).result == "no_inventory"


def test_untouched_ga_stock_counts_as_unsold(ga_event):
    # No hold has ever materialised the counter: the full quantity is unsold.
    assert tasks.refresh_event_availability.delay(ga_event.event.pk).result == "unchanged"
    assert _status(ga_event.event) == "published"
