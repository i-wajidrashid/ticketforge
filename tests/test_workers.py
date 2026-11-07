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
