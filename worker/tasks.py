"""Celery tasks. Every task receives ids (never model instances), is idempotent
and is safe to retry: that is what makes at-least-once delivery survivable.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from api.app.db import session_scope
from api.app.models import Order, Refund
from api.app.realtime import hold_expired, seat_updates
from api.app.services import holds as hold_service
from api.app.services import inventory as inventory_service
from api.app.services import payments as payment_service
from api.app.services import tickets as ticket_service
from api.app.services.notifications import broadcast_refunded_order
from api.app.services.stripe_gateway import get_gateway
from celery import shared_task
from common.search import delete_event, event_document, upsert_event
from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives, send_mail
from django.db import transaction
from django.utils import timezone
from sqlalchemy import select
from web.events.models import Event, EventStatus

logger = logging.getLogger(__name__)


@shared_task(
    bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=5
)
def send_verification_email(self: Any, user_id: int) -> str:
    from web.accounts import emails
    from web.accounts.models import User

    user = User.objects.filter(pk=user_id).first()
    if user is None or user.is_verified:
        return "skipped"
    link = emails.verification_url(user.pk)
    send_mail(
        subject="Verify your TicketForge email",
        message=(
            "Welcome to TicketForge!\n\n"
            f"Verify your email to start buying tickets:\n{link}\n\n"
            "This link expires in 3 days. If you did not sign up, ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    return "sent"


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def send_password_reset_email(self: Any, user_id: int) -> str:
    from web.accounts.models import User

    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return "skipped"
    token = signing.dumps({"uid": user.pk}, salt="ticketforge.password-reset", compress=True)
    link = f"{settings.SITE_ORIGIN}/accounts/password-reset/confirm/?token={token}"
    send_mail(
        subject="Reset your TicketForge password",
        message=f"Use this link within one hour to choose a new password:\n\n{link}",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    return "sent"


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def send_order_confirmation(self: Any, order_id: str) -> str:
    from api.app import core_repo

    from worker.pdf import render_ticket_pdf

    with session_scope() as session:
        order = session.get(Order, order_id)
        if order is None:
            return "missing_order"
        tickets = ticket_service.order_tickets(session, order.id)
        user = core_repo.get_user(order.user_id)
        event = core_repo.get_event(order.event_id)
        if not tickets or user is None:
            return "nothing_to_send"
        pdfs = [
            (
                ticket.id,
                render_ticket_pdf(
                    **ticket_service.pdf_fields(session, ticket, order, owner_email=user.email)
                ),
            )
            for ticket in tickets[:10]
        ]

    subject = f"Your tickets for {event.title if event else 'your event'}"
    body = (
        f"Thanks for your order!\n\n"
        f"Order: {order.id}\n"
        f"Tickets: {len(tickets)}\n"
        f"Total: {order.total_cents / 100:.2f} {order.currency}\n\n"
        "Your PDF tickets are attached. Each QR code is valid for one entry.\n"
    )
    message = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    for ticket_id, pdf_bytes in pdfs:
        message.attach(f"ticket-{ticket_id}.pdf", pdf_bytes, "application/pdf")
    message.send()
    return "sent"


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def send_refund_email(self: Any, order_id: str) -> str:
    from api.app import core_repo
    from api.app.models import Order

    with session_scope() as session:
        order = session.get(Order, order_id)
        if order is None:
            return "missing_order"
        user = core_repo.get_user(order.user_id)
        event = core_repo.get_event(order.event_id)
    if user is None:
        return "missing_user"
    send_mail(
        subject="Your TicketForge refund is on its way",
        message=(
            f"We refunded {order.total_cents / 100:.2f} {order.currency} for "
            f"{event.title if event else 'your event'}.\n\n"
            "Depending on your bank, it can take 5-10 business days to appear.\n"
            "If your seats were still sellable they are back on sale now."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    return "sent"


@shared_task
def expire_holds() -> dict[str, Any]:
    released = hold_service.expire_holds()
    for change in released:
        if not change.get("released"):
            continue
        seat_updates(int(change["event_id"]), change.get("seats", []))
        hold_expired(str(change["hold_id"]))
    logger.info("expired holds swept", extra={"extra_fields": {"count": len(released)}})
    return {"released": len(released)}


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def index_event(self: Any, event_id: int) -> str:
    event = (
        Event.objects.select_related("venue", "organizer")
        .prefetch_related("ticket_types")
        .filter(pk=event_id)
        .first()
    )
    if event is None or event.status not in {
        EventStatus.PUBLISHED,
        EventStatus.ON_SALE,
        EventStatus.SOLD_OUT,
    }:
        delete_event(event_id)
        return "removed"
    upsert_event(event_document(event))
    return "indexed"


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def remove_event_from_index(self: Any, event_id: int) -> str:
    delete_event(event_id)
    return "removed"


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def process_refund(self: Any, refund_id: int, actor_id: int | None = None) -> dict[str, Any]:
    with session_scope() as session:
        result = payment_service.process_refund(
            session, refund_id=refund_id, actor_id=actor_id, gateway=get_gateway()
        )
        event_id = session.scalar(
            select(Order.event_id)
            .join(Refund, Refund.order_id == Order.id)
            .where(Refund.id == refund_id)
        )
        if result.get("status") == "refunded":
            order_id = session.scalar(select(Refund.order_id).where(Refund.id == refund_id))
            if order_id is not None:
                broadcast_refunded_order(session, order_id)
    if result.get("status") == "refunded" and event_id is not None:
        # A refund can return seats to sale, which may re-open a sold-out event.
        refresh_event_availability.delay(int(event_id))
    return result


@shared_task
def reject_refund(refund_id: int, actor_id: int | None = None, reason: str = "") -> dict[str, Any]:
    with session_scope() as session:
        return payment_service.reject_refund(
            session, refund_id=refund_id, actor_id=actor_id, reason=reason
        )


@shared_task(bind=True, max_retries=3)
def cancel_event_refunds(self: Any, event_id: int) -> dict[str, Any]:
    from api.app.services.errors import RefundNotAllowed
    from sqlalchemy import select

    created = 0
    with session_scope() as session:
        order_ids = list(
            session.scalars(
                select(Order.id).where(Order.event_id == event_id, Order.status == "paid")
            ).all()
        )
    for order_id in order_ids:
        try:
            with session_scope() as session:
                existing = session.scalars(
                    select(Refund).where(
                        Refund.order_id == order_id,
                        Refund.status.in_(["requested", "processing", "succeeded"]),
                    )
                ).first()
                if existing is not None:
                    continue
                order = session.get(Order, order_id)
                if order is None:
                    continue
                refund = Refund(
                    order_id=order.id,
                    amount_cents=order.total_cents,
                    status="requested",
                    reason="event cancelled",
                )
                session.add(refund)
                session.flush()
                refund_id = refund.id
            process_refund.delay(refund_id, None)
            created += 1
        except RefundNotAllowed:
            continue
    return {"refunds_created": created}


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def refresh_event_availability(self: Any, event_id: int) -> str:
    """Flip an event between on-sale and sold-out from live checkout inventory.

    Idempotent and order-independent: it always recomputes from the source of
    truth, so a burst of sales/refunds converges no matter how tasks interleave.
    """
    with session_scope() as session:
        unsold = inventory_service.unsold_units(session, event_id)
    if unsold is None:
        return "no_inventory"

    with transaction.atomic():
        event = Event.objects.select_for_update().filter(pk=event_id).first()
        if event is None:
            return "missing"
        if unsold == 0 and event.status in {EventStatus.PUBLISHED, EventStatus.ON_SALE}:
            event.status = EventStatus.SOLD_OUT
        elif unsold > 0 and event.status == EventStatus.SOLD_OUT:
            event.status = EventStatus.ON_SALE
        else:
            return "unchanged"
        # save() (not update()) so the search index picks up the new status.
        event.save(update_fields=["status", "updated_at"])
    logger.info(
        "event availability changed",
        extra={"extra_fields": {"event_id": event_id, "status": event.status, "unsold": unsold}},
    )
    return str(event.status)


@shared_task
def reconcile_payments() -> dict[str, Any]:
    with session_scope() as session:
        return payment_service.reconcile_payments(session, gateway=get_gateway())


@shared_task
def mark_finished_events() -> dict[str, Any]:
    # Save row by row (not queryset.update) so post_save fires and the search
    # signal removes each finished event from the index.
    count = 0
    finished = Event.objects.filter(
        status__in=[EventStatus.PUBLISHED, EventStatus.ON_SALE, EventStatus.SOLD_OUT],
        ends_at__lt=timezone.now(),
    )
    for event in finished.iterator(chunk_size=200):
        event.status = EventStatus.FINISHED
        event.save(update_fields=["status", "updated_at"])
        count += 1
    return {"finished": count}


@shared_task
def send_event_reminders() -> dict[str, Any]:
    from api.app import core_repo

    now = timezone.now()
    soon = Event.objects.filter(
        status__in=[EventStatus.PUBLISHED, EventStatus.ON_SALE],
        starts_at__gte=now,
        starts_at__lte=now + timedelta(hours=25),
    )
    sent = 0
    for event in soon:
        with session_scope() as session:
            orders = session.scalars(
                select(Order).where(Order.event_id == event.pk, Order.status == "paid")
            ).all()
        for order in orders:
            user = core_repo.get_user(order.user_id)
            if user is None:
                continue
            send_mail(
                subject=f"Reminder: {event.title} is almost here",
                message=f"{event.title} starts at {event.starts_at:%Y-%m-%d %H:%M} UTC.\nBring your QR ticket.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )
            sent += 1
    return {"reminders": sent}
