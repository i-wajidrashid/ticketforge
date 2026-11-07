"""Organizer actions shared by the REST API and the server-rendered dashboard.

Both front doors (DRF views for integrations, HTML views for people) call the
same functions, so a rule such as "only future events can be published" lives
in exactly one place. Functions raise :class:`OrganizerActionError`; each
caller translates it into its own error format.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from web.accounts.models import Role, User
from web.events.models import Event, EventStatus, StaffAssignment, TicketType
from web.venues.models import Seat

logger = logging.getLogger(__name__)


class OrganizerActionError(Exception):
    """A rule was broken. ``field`` names what the organizer should fix."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def assert_publishable(event: Event) -> None:
    if event.status not in {EventStatus.DRAFT, EventStatus.PUBLISHED}:
        raise OrganizerActionError("status", "Only draft or published events can be published.")
    if not event.ticket_types.exists():
        raise OrganizerActionError("ticket_types", "Add at least one ticket type first.")
    if event.starts_at <= timezone.now():
        raise OrganizerActionError("starts_at", "The start time must be in the future.")
    if event.venue.capacity is not None and event.venue.capacity <= 0:
        raise OrganizerActionError("venue", "The venue capacity must be positive.")


def publish_event(event: Event) -> Event:
    assert_publishable(event)
    event.status = EventStatus.PUBLISHED
    # save() rather than update(): the post_save signal re-indexes search.
    event.save(update_fields=["status", "updated_at"])
    return event


def cancel_event(event: Event, *, reason: str = "") -> bool:
    """Cancel and refund every paid order. Returns False if already cancelled."""
    if event.status == EventStatus.CANCELLED:
        return False
    if event.status == EventStatus.FINISHED:
        raise OrganizerActionError("status", "A finished event can’t be cancelled.")
    event.status = EventStatus.CANCELLED
    event.save(update_fields=["status", "updated_at"])

    from worker.tasks import cancel_event_refunds

    event_id = event.pk
    # Enqueue only once the status change is committed, so the worker never
    # refunds orders for an event whose cancellation was rolled back.
    transaction.on_commit(lambda: cancel_event_refunds.delay(event_id))
    logger.info("event cancelled", extra={"extra_fields": {"event_id": event_id, "reason": reason}})
    return True


def assign_staff(event: Event, email: str) -> tuple[StaffAssignment, bool]:
    user = _active_user(email)
    assignment, created = StaffAssignment.objects.get_or_create(user=user, event=event)
    if user.role == Role.CUSTOMER:
        user.role = Role.DOOR_STAFF
        user.save(update_fields=["role"])
    return assignment, created


def remove_staff(event: Event, email: str) -> int:
    user = _active_user(email)
    deleted, _ = StaffAssignment.objects.filter(user=user, event=event).delete()
    return int(deleted)


def _active_user(email: str) -> User:
    email = email.strip().lower()
    if not email:
        raise OrganizerActionError("email", "Enter the email address of a TicketForge account.")
    user = User.objects.filter(email=email, is_active=True).first()
    if user is None:
        raise OrganizerActionError(
            "email", "No account uses that email. Ask them to sign up first, then add them."
        )
    return user


def approve_refund(refund: dict[str, Any], actor_id: int | None) -> None:
    if refund["status"] not in {"requested", "pending"}:
        raise OrganizerActionError(
            "status", f"This refund is already {refund['status']}, so it can’t be approved."
        )
    from worker.tasks import process_refund

    process_refund.delay(refund["id"], actor_id)


def reject_refund(refund: dict[str, Any], actor_id: int | None, reason: str = "") -> None:
    if refund["status"] in {"succeeded", "rejected"}:
        raise OrganizerActionError(
            "status", f"This refund is already {refund['status']}, so it can’t be rejected."
        )
    from worker.tasks import reject_refund as reject_task

    reject_task.delay(refund["id"], actor_id, reason.strip()[:255])


def type_capacity(event: Event, ticket_types: list[TicketType]) -> dict[int, int]:
    """How many units each ticket type can ever sell.

    General admission: its ``quantity_total``. Reserved seating: the seats in
    the section the type sells (same name-matching rule checkout uses), or the
    whole venue when the event has a single seated type.
    """
    seated = [t for t in ticket_types if t.inventory_mode == "seated"]
    seats_by_section: dict[str, int] = {}
    if seated:
        for name in Seat.objects.filter(section__venue=event.venue).values_list(
            "section__name", flat=True
        ):
            key = name.strip().casefold()
            seats_by_section[key] = seats_by_section.get(key, 0) + 1

    capacity: dict[int, int] = {}
    for ticket_type in ticket_types:
        if ticket_type.inventory_mode == "ga":
            capacity[ticket_type.pk] = ticket_type.quantity_total
        elif len(seated) == 1:
            capacity[ticket_type.pk] = sum(seats_by_section.values())
        else:
            capacity[ticket_type.pk] = seats_by_section.get(
                ticket_type.name.strip().casefold(), ticket_type.quantity_total
            )
    return capacity
